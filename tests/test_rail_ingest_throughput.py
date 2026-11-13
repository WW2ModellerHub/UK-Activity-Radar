"""Regressions for the live rail ingest path.

The Network Rail broker drops a client that cannot keep up. These tests pin
the properties that keep message handling cheap and off the socket thread.
"""
import json
import threading
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.collectors.stomp_client import MultiplexStompFeed, _FrameQueue, _MultiplexListener
from app.collectors.td import TDCollector
from app.models import Vehicle
from app.schedule_store import ScheduleStore
from app.state import RESTORED_SNAPSHOT_MINUTES, LiveState
from app.timeutils import london_tz


def _train(identifier: str, headcode: str, **fields) -> Vehicle:
    values = dict(id=identifier, type="train", name=headcode, headcode=headcode, lat=52.0, lon=-1.0, source="Network Rail TD")
    values.update(fields)
    return Vehicle(**values)


# --- live state ------------------------------------------------------------

def test_headcode_index_follows_changes_removal_and_pruning():
    state = LiveState()
    now = datetime.now(timezone.utc)
    state.upsert(_train("td:AA:1A01", "1A01", last_updated=now))
    assert state.find_train_by_headcode("1a01").id == "td:AA:1A01"

    state.upsert(_train("td:AA:1A01", "2B02", last_updated=now + timedelta(seconds=1)))
    assert state.find_train_by_headcode("1A01") is None
    assert state.find_train_by_headcode("2B02").id == "td:AA:1A01"

    state.upsert(_train("td:BB:2B02", "2B02", lat=55.0, last_updated=now))
    assert state.find_train_by_headcode("2B02", 55.0, -1.0).id == "td:BB:2B02"
    state.remove("td:BB:2B02")
    assert state.find_train_by_headcode("2B02", 55.0, -1.0).id == "td:AA:1A01"

    state.upsert(_train("td:CC:3C03", "3C03", last_updated=now - timedelta(hours=4)))
    assert state.prune_stale(now=now) == 1
    assert state.find_train_by_headcode("3C03") is None
    assert state._train_headcode_index == {"2B02": {"td:AA:1A01"}}


def test_streamed_rows_are_copies_and_share_no_scalar_state():
    state = LiveState()
    state.upsert(_train("td:AA:1A01", "1A01", estimate_path=[[52.0, -1.0], [52.1, -1.0]]))
    row = state.all(include_buses=False, include_vessels=False)[0]
    row.lat = 10.0
    row.estimate_path = None
    stored = state.get("td:AA:1A01")
    assert stored.lat == 52.0
    assert stored.estimate_path == [[52.0, -1.0], [52.1, -1.0]]


def test_restored_snapshot_train_is_hidden_once_no_live_report_replaces_it():
    state = LiveState()
    now = datetime.now(timezone.utc)
    state.upsert(_train("td:AA:OLD1", "1A01", restored_snapshot=True, last_updated=now - timedelta(minutes=RESTORED_SNAPSHOT_MINUTES + 1)))
    state.upsert(_train("td:AA:NEW1", "1A02", restored_snapshot=True, last_updated=now - timedelta(minutes=RESTORED_SNAPSHOT_MINUTES - 5)))
    # A live (not restored) train of the same age is unaffected.
    state.upsert(_train("td:AA:LIVE", "1A03", last_updated=now - timedelta(minutes=RESTORED_SNAPSHOT_MINUTES + 1)))
    ids = {row.id for row in state.all(include_buses=False, include_vessels=False)}
    assert ids == {"td:AA:NEW1", "td:AA:LIVE"}


def test_trains_where_copies_only_matching_trains():
    state = LiveState()
    state.upsert(_train("td:AA:1A01", "1A01", schedule_id=1))
    state.upsert(_train("td:AA:1A02", "1A02"))
    state.upsert(Vehicle(id="bus:1", type="bus", name="Bus", lat=52, lon=-1))
    rows = state.trains_where(lambda vehicle: bool(vehicle.schedule_id))
    assert [row.id for row in rows] == ["td:AA:1A01"]
    rows[0].lat = 10.0
    assert state.get("td:AA:1A01").lat == 52.0


# --- schedule store --------------------------------------------------------

def _store_with_schedule(tmp_path, *, stp="P"):
    store = ScheduleStore(tmp_path / "schedule.sqlite")
    now = datetime.now(london_tz()).replace(second=0, microsecond=0)
    locations = [
        {"tiploc_code": "CHFD", "scheduled_departure": (now + timedelta(minutes=2)).strftime("%H%M")},
        {"tiploc_code": "LESTER", "scheduled_arrival": (now + timedelta(minutes=45)).strftime("%H%M")},
    ]
    with store._connect() as conn:
        conn.execute("INSERT INTO tiplocs(tiploc,stanox,description,lat,lon) VALUES('CHFD','12345','Chesterfield',53.235,-1.421)")
        conn.execute("INSERT INTO tiplocs(tiploc,stanox,description,lat,lon) VALUES('LESTER','12346','Leicester',52.631,-1.125)")
        conn.execute(
            """INSERT INTO schedules(train_uid,start_date,end_date,days_runs,stp_indicator,atoc_code,
                   signalling_id,origin_name,destination_name,locations_json)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            ("C2S53", (now - timedelta(days=1)).date().isoformat(), (now + timedelta(days=1)).date().isoformat(),
             "1111111", stp, "EM", "2S53", "Sheffield", "Leicester", json.dumps(locations)),
        )
        schedule_id = conn.execute("SELECT id FROM schedules").fetchone()[0]
    return store, schedule_id, now


def test_schedule_calling_at_confirms_only_locations_on_a_current_schedule(tmp_path):
    store, schedule_id, now = _store_with_schedule(tmp_path)
    day = now.date().isoformat()
    info = store.schedule_calling_at(schedule_id, day, {"LESTER"})
    assert info.train_uid == "C2S53" and info.schedule_id == schedule_id and info.running_date == day
    assert store.schedule_calling_at(schedule_id, day, {"EUSTON"}) is None
    assert store.schedule_calling_at(schedule_id, day, set()) is None
    assert store.schedule_calling_at(999999, day, {"LESTER"}) is None
    assert store.tiplocs_for_stanox("12345") == frozenset({"CHFD"})


def test_cancelled_schedule_is_never_confirmed(tmp_path):
    store, schedule_id, now = _store_with_schedule(tmp_path, stp="C")
    assert store.schedule_calling_at(schedule_id, now.date().isoformat(), {"LESTER"}) is None


def test_ready_does_not_query_the_database_for_every_message(tmp_path, monkeypatch):
    store, _schedule_id, _now = _store_with_schedule(tmp_path)
    assert store.ready() is True
    monkeypatch.setattr(store, "_connect", lambda: (_ for _ in ()).throw(AssertionError("ready() hit the database")))
    assert all(store.ready() for _ in range(100))


def test_empty_store_is_not_cached_as_ready(tmp_path):
    store = ScheduleStore(tmp_path / "schedule.sqlite")
    assert store.ready() is False
    with store._connect() as conn:
        conn.execute("INSERT INTO schedules(train_uid,signalling_id) VALUES('A1','1A01')")
    assert store.ready() is True


def test_unroutable_leg_is_not_searched_again_immediately(tmp_path, monkeypatch):
    store, schedule_id, _now = _store_with_schedule(tmp_path)
    assert store.route_points(schedule_id, "CHFD", "LESTER") == []
    monkeypatch.setattr(store, "_connect", lambda: (_ for _ in ()).throw(AssertionError("route searched again")))
    assert store.route_points(schedule_id, "CHFD", "LESTER") == []
    store.clear_route_cache()
    monkeypatch.undo()
    assert store.route_points(schedule_id, "CHFD", "LESTER") == []


# --- train describer -------------------------------------------------------

def _td_collector(tmp_path):
    store, _schedule_id, now = _store_with_schedule(tmp_path)
    station_file = tmp_path / "stations.json"
    station_file.write_text(json.dumps({
        "12345": {"name": "Chesterfield", "lat": 53.235, "lon": -1.421, "tiploc": "CHFD"},
        "12346": {"name": "Leicester", "lat": 52.631, "lon": -1.125, "tiploc": "LESTER"},
        "12399": {"name": "Elsewhere", "lat": 53.236, "lon": -1.420, "tiploc": "ELSEWH"},
    }), encoding="utf-8")
    smart_file = tmp_path / "smart.json"
    smart_file.write_text(json.dumps([
        {"TD": "DY", "FROMBERTH": "A001", "TOBERTH": "A002", "STANOX": "12345"},
        {"TD": "DY", "FROMBERTH": "A002", "TOBERTH": "A003", "STANOX": "12345"},
        {"TD": "DY", "FROMBERTH": "A003", "TOBERTH": "A004", "STANOX": "12399"},
    ]), encoding="utf-8")
    state = LiveState()
    collector = TDCollector(SimpleNamespace(td_topic="/topic/TD"), state, station_file, smart_file, store)
    return collector, state, store, now


def _step(collector, now, fr, to, seconds):
    collector.handle_message(json.dumps({"CA_MSG": {
        "area_id": "DY", "from": fr, "to": to, "descr": "2S53",
        "time": int((now + timedelta(seconds=seconds)).timestamp() * 1000),
    }}))


def test_identified_train_does_not_repeat_the_national_headcode_search(tmp_path, monkeypatch):
    collector, state, store, now = _td_collector(tmp_path)
    searches = []
    original = store.lookup_headcode

    def counting(*args, **kwargs):
        searches.append(kwargs.get("tiploc"))
        return original(*args, **kwargs)

    monkeypatch.setattr(store, "lookup_headcode", counting)
    _step(collector, now, "A001", "A002", 0)
    assert searches == ["CHFD"]
    assert state.get("td:DY:2S53").train_uid == "C2S53"

    # The next berth is still on the matched schedule: no national search.
    _step(collector, now, "A002", "A003", 20)
    assert searches == ["CHFD"]
    train = state.get("td:DY:2S53")
    assert train.train_uid == "C2S53" and train.operator == "East Midlands Railway"

    # A location that is not on the matched schedule falls back to the search.
    _step(collector, now, "A003", "A004", 40)
    assert searches == ["CHFD", "ELSEWH"]


def test_elapsed_leg_without_an_onward_leg_is_retried_sparingly(tmp_path, monkeypatch):
    collector, state, _store, _now = _td_collector(tmp_path)
    now = datetime.now(timezone.utc)
    state.upsert(_train(
        "td:DY:9Z99", "9Z99", schedule_id=4242, service_date=now.date().isoformat(), train_uid="Z9",
        next_tiploc="NOWHERE", rail_tiploc="CHFD",
        estimate_started_at=now - timedelta(minutes=5), estimate_due_at=now - timedelta(minutes=1),
        last_updated=now - timedelta(minutes=5),
    ))
    attempts = []
    monkeypatch.setattr(collector, "_advance_schedule_estimate", lambda vehicle, at: attempts.append(vehicle.id) or False)
    for _ in range(5):
        collector.advance_live_estimates(now)
    assert attempts == ["td:DY:9Z99"]


# --- broker session --------------------------------------------------------

def test_listener_hands_frames_to_the_worker_instead_of_handling_them_inline():
    handled = []
    feed = SimpleNamespace(name="td", topic="/topic/TD_ALL_SIG_AREA", handle_message=handled.append,
                           set_status=lambda *args, **kwargs: None)
    hub = MultiplexStompFeed(SimpleNamespace(credentials_present=True), [feed])
    _MultiplexListener(hub).on_message(SimpleNamespace(headers={"subscription": "ukradar-td"}, body="frame-1"))
    assert handled == []
    assert hub.backlog() == 1

    worker = threading.Thread(target=hub._work, daemon=True)
    worker.start()
    deadline = time.time() + 2
    while not handled and time.time() < deadline:
        time.sleep(0.01)
    hub._stop.set()
    worker.join(timeout=2)
    assert handled == ["frame-1"]
    assert hub.backlog() == 0


def test_slow_handler_never_blocks_the_socket_reader():
    release = threading.Event()
    feed = SimpleNamespace(name="td", topic="/topic/TD_ALL_SIG_AREA", drop_stale_backlog=True,
                           handle_message=lambda body: release.wait(5), set_status=lambda *args, **kwargs: None)
    hub = MultiplexStompFeed(SimpleNamespace(credentials_present=True), [feed])
    worker = threading.Thread(target=hub._work, daemon=True)
    worker.start()
    listener = _MultiplexListener(hub)
    started = time.perf_counter()
    for index in range(500):
        listener.on_message(SimpleNamespace(headers={"subscription": "ukradar-td"}, body=f"frame-{index}"))
    elapsed = time.perf_counter() - started
    release.set()
    hub._stop.set()
    worker.join(timeout=2)
    assert elapsed < 1.0


def test_frame_queue_drops_only_stale_positional_frames():
    td = SimpleNamespace(name="td", drop_stale_backlog=True, backlog_dropped=0)
    trust = SimpleNamespace(name="network_rail")
    frames = _FrameQueue(droppable_limit=3)
    for index in range(5):
        frames.put(td, f"td-{index}")
    for index in range(5):
        frames.put(trust, f"trust-{index}")
    assert frames.dropped == 2 and td.backlog_dropped == 2
    drained = []
    while (item := frames.get(0)) is not None:
        drained.append(item[1])
    # TRUST is complete and in order; TD kept its newest steps.
    assert drained == [f"trust-{index}" for index in range(5)] + ["td-2", "td-3", "td-4"]
