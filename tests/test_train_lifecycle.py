"""Finished trains leave the map.

Busy termini filled up with services that had already arrived: TRUST's
"terminated" status was overwritten by later berth steps, and signalling-only
headcodes lingered for 45 minutes in their platforms.
"""
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.collectors.td import TDCollector
from app.models import Vehicle
from app.schedule_store import ScheduleStore
from app.state import TERMINATED_VISIBLE_MINUTES, UNMATCHED_TD_IDLE_MINUTES, LiveState
from app.timeutils import london_tz

from tests.test_trust_collector import make_collector, movement


def _train_ids(state: LiveState) -> set[str]:
    return {row.id for row in state.all(include_buses=False, include_vessels=False) if row.type == "train"}


def test_trust_termination_hides_the_train_after_a_short_arrival_window(tmp_path):
    collector, state, now = make_collector(tmp_path)
    movement(collector, now, event_type="ARRIVAL", train_terminated="true")
    train = next(iter(state.all(include_buses=False, include_vessels=False)))
    assert train.terminated_at is not None
    assert train.id in _train_ids(state)  # still visible as it arrives

    stored = state._vehicles[train.id]
    stored.terminated_at = datetime.now(timezone.utc) - timedelta(minutes=TERMINATED_VISIBLE_MINUTES + 1)
    assert train.id not in _train_ids(state)
    assert state.summary()["by_type"]["train"] == 0
    assert state.type_counts({"train": 45}).get("train", 0) == 0


def test_a_later_berth_step_does_not_revive_a_terminated_train():
    state = LiveState()
    now = datetime.now(timezone.utc)
    train = Vehicle(
        id="nr:1F10", type="train", name="1F10", headcode="1F10", lat=52.48, lon=-1.9,
        source="Network Rail TRUST", status="Terminated", last_updated=now - timedelta(minutes=10),
        terminated_at=now - timedelta(minutes=10), schedule_id=7, service_date=now.date().isoformat(),
    )
    state.upsert(train)
    # A Train Describer step moves the stock between platform berths and
    # rewrites the status, as the TD collector does for unresolved berths.
    stepped = state.get("nr:1F10")
    stepped.status = "TD live • next berth unresolved"
    stepped.last_updated = now
    stepped.source = "Network Rail TD"
    state.upsert(stepped)
    assert "nr:1F10" not in _train_ids(state)


def test_unmatched_signalling_train_leaves_after_idling_but_matched_train_stays():
    state = LiveState()
    now = datetime.now(timezone.utc)
    idle = now - timedelta(minutes=UNMATCHED_TD_IDLE_MINUTES + 5)
    state.upsert(Vehicle(id="td:BN:5Z99", type="train", name="5Z99", lat=52.48, lon=-1.9,
                         source="Network Rail TD", last_updated=idle))
    state.upsert(Vehicle(id="td:BN:1M42", type="train", name="1M42", lat=52.48, lon=-1.9,
                         source="Network Rail TD", last_updated=idle,
                         schedule_id=9, service_date=now.date().isoformat(), train_uid="M42"))
    state.upsert(Vehicle(id="td:BN:2K10", type="train", name="2K10", lat=52.48, lon=-1.9,
                         source="Network Rail TD", last_updated=now - timedelta(minutes=5)))
    assert _train_ids(state) == {"td:BN:1M42", "td:BN:2K10"}


def test_a_matched_train_reporting_at_its_destination_is_terminated(tmp_path):
    now = datetime.now(london_tz()).replace(second=0, microsecond=0)
    store = ScheduleStore(tmp_path / "schedule.sqlite")
    with store._connect() as conn:
        conn.execute("INSERT INTO tiplocs(tiploc,stanox,description,lat,lon) VALUES('CHFD','12345','Chesterfield',53.235,-1.421)")
        conn.execute("INSERT INTO tiplocs(tiploc,stanox,description,lat,lon) VALUES('LESTER','12346','Leicester',52.631,-1.125)")
        conn.execute(
            """INSERT INTO schedules(train_uid,start_date,end_date,days_runs,stp_indicator,atoc_code,
                   signalling_id,origin_name,destination_name,origin_tiploc,destination_tiploc,locations_json)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("C2S53", (now - timedelta(days=1)).date().isoformat(), (now + timedelta(days=1)).date().isoformat(),
             "1111111", "P", "EM", "2S53", "Chesterfield", "Leicester", "CHFD", "LESTER", json.dumps([
                 {"tiploc_code": "CHFD", "scheduled_departure": (now - timedelta(minutes=44)).strftime("%H%M")},
                 {"tiploc_code": "LESTER", "scheduled_arrival": now.strftime("%H%M")},
             ])),
        )
    stations = tmp_path / "stations.json"
    stations.write_text(json.dumps({
        "12345": {"name": "Chesterfield", "lat": 53.235, "lon": -1.421, "tiploc": "CHFD"},
        "12346": {"name": "Leicester", "lat": 52.631, "lon": -1.125, "tiploc": "LESTER"},
    }), encoding="utf-8")
    smart = tmp_path / "smart.json"
    smart.write_text(json.dumps([
        {"TD": "DY", "FROMBERTH": "A001", "TOBERTH": "A002", "STANOX": "12345"},
        {"TD": "LR", "FROMBERTH": "B001", "TOBERTH": "B002", "STANOX": "12346"},
    ]), encoding="utf-8")
    state = LiveState()
    collector = TDCollector(SimpleNamespace(td_topic="/topic/TD"), state, stations, smart, store)

    def step(area, fr, to, at):
        collector.handle_message(json.dumps({"CA_MSG": {
            "area_id": area, "from": fr, "to": to, "descr": "2S53", "time": int(at.timestamp() * 1000),
        }}))

    step("DY", "A001", "A002", now - timedelta(minutes=44))
    train = state.find_train_by_headcode("2S53")
    assert train is not None and train.terminated_at is None
    step("LR", "B001", "B002", now)
    train = state.find_train_by_headcode("2S53")
    assert train.terminated_at is not None
    # The rollover pass must not project a finished service onwards.
    assert not collector.advance_live_estimates(datetime.now(timezone.utc) + timedelta(hours=1))
