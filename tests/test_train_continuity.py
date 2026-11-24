"""Trains keep moving along the track between reports, as Signalbox-style maps do.

Before: half the trains on the live map had no leg. A report at a junction
(only a working time) built none, and wiped the one the train had, so it was
parked at the junction until the next station report and then rushed on.
"""
import json
from datetime import datetime, timedelta
from types import SimpleNamespace

from app.collectors.td import TDCollector
from app.models import Vehicle
from app.schedule_store import ScheduleStore
from app.state import LiveState
from app.timeutils import london_tz

TIPLOCS = [("CHFD", "12345", "Chesterfield", 53.235, -1.421),
           ("CLAYX", "12350", "Clay Cross Junction", 53.165, -1.411),
           ("LESTER", "12346", "Leicester", 52.631, -1.125)]


def _store(tmp_path, now):
    store = ScheduleStore(tmp_path / "schedule.sqlite")
    hhmm = lambda minutes: (now + timedelta(minutes=minutes)).strftime("%H%M")
    # Overnight CIF shape: Clay Cross Junction has only a working pass time.
    locations = [
        {"tiploc_code": "CHFD", "departure": hhmm(0), "public_departure": hhmm(0)},
        {"tiploc_code": "CLAYX", "pass": hhmm(6)},
        {"tiploc_code": "LESTER", "arrival": hhmm(40), "public_arrival": hhmm(40)},
    ]
    with store._connect() as conn:
        for row in TIPLOCS:
            conn.execute("INSERT INTO tiplocs(tiploc,stanox,description,lat,lon) VALUES(?,?,?,?,?)", row)
        schedule_id = conn.execute(
            """INSERT INTO schedules(train_uid,start_date,end_date,days_runs,stp_indicator,atoc_code,
                   signalling_id,origin_name,destination_name,locations_json)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            ("C2S53", (now - timedelta(days=1)).date().isoformat(), (now + timedelta(days=1)).date().isoformat(),
             "1111111", "P", "EM", "2S53", "Chesterfield", "Leicester", json.dumps(locations)),
        ).lastrowid
    # Track geometry between timing points (the real store reads it from the network).
    store.route_points = lambda sid, a, b: [[53.2, -1.42], [53.0, -1.3]]
    return store, schedule_id


def test_a_train_reported_at_a_junction_gets_its_onward_leg(tmp_path):
    now = datetime.now(london_tz()).replace(second=0, microsecond=0)
    store, schedule_id = _store(tmp_path, now)
    leg = store.estimate_leg_from(schedule_id, now.date(), "CLAYX", now + timedelta(minutes=8))
    assert leg is not None, "a junction with only a working time used to give no leg"
    assert leg["next_tiploc"] == "LESTER"
    assert leg["delay_minutes"] == 2


def test_a_train_with_no_leg_is_placed_on_its_timetable_with_its_lateness(tmp_path):
    now = datetime.now(london_tz()).replace(second=0, microsecond=0)
    store, schedule_id = _store(tmp_path, now)
    # 5 minutes late: the junction is booked at +6, so expected at +11. At +10
    # it is still approaching the junction; at +12 it is past it.
    leg = store.estimate_leg_at(schedule_id, now.date(), now + timedelta(minutes=10), delay_minutes=5)
    assert (leg["tiploc"], leg["next_tiploc"]) == ("CHFD", "CLAYX")
    assert leg["due_at"] == now + timedelta(minutes=11)
    leg = store.estimate_leg_at(schedule_id, now.date(), now + timedelta(minutes=12), delay_minutes=5)
    assert (leg["tiploc"], leg["next_tiploc"]) == ("CLAYX", "LESTER")
    assert store.estimate_leg_at(schedule_id, now.date(), now + timedelta(hours=3)) is None


def _collector(tmp_path, store, state):
    stations = tmp_path / "stations.json"
    stations.write_text(json.dumps({
        "12345": {"name": "Chesterfield", "lat": 53.235, "lon": -1.421, "tiploc": "CHFD"},
        "99999": {"name": "Somewhere Junction", "lat": 53.1, "lon": -1.35, "tiploc": "SOMEJN"},
    }), encoding="utf-8")
    smart = tmp_path / "smart.json"
    smart.write_text(json.dumps([{"TD": "DY", "FROMBERTH": "B001", "TOBERTH": "B002", "STANOX": "99999"}]), encoding="utf-8")
    return TDCollector(SimpleNamespace(td_topic="/topic/TD"), state, stations, smart, store)


def test_the_roll_forward_pass_places_a_leg_less_train_on_its_timetable(tmp_path):
    now = datetime.now(london_tz()).replace(second=0, microsecond=0)
    store, schedule_id = _store(tmp_path, now)
    state = LiveState()
    collector = _collector(tmp_path, store, state)
    train = Vehicle(id="td:DY:2S53", type="train", name="2S53", lat=53.235, lon=-1.421,
                    reported_lat=53.235, reported_lon=-1.421, headcode="2S53", train_uid="C2S53",
                    schedule_id=schedule_id, service_date=now.date().isoformat(), delay_minutes=0,
                    source="Network Rail TD", last_updated=now)
    state.upsert(train)
    collector.advance_live_estimates(now + timedelta(minutes=3))
    moved = state.get("td:DY:2S53")
    assert moved.estimate_due_at == now + timedelta(minutes=6)
    assert moved.next_location == "Clay Cross Junction"
    assert len(moved.estimate_path) >= 2
    assert moved.position_method == "estimated"


def test_a_berth_report_the_timetable_cannot_start_from_keeps_the_running_leg(tmp_path):
    now = datetime.now(london_tz()).replace(second=0, microsecond=0)
    store, schedule_id = _store(tmp_path, now)
    state = LiveState()
    collector = _collector(tmp_path, store, state)
    path = [[53.235, -1.421], [53.1, -1.35], [52.9, -1.25]]
    state.upsert(Vehicle(id="td:DY:2S53", type="train", name="2S53", lat=53.235, lon=-1.421,
                         headcode="2S53", train_uid="C2S53", schedule_id=schedule_id,
                         service_date=now.date().isoformat(), source="Network Rail TD", last_updated=now,
                         estimate_path=path, estimate_started_at=now, estimate_due_at=now + timedelta(minutes=20),
                         next_location="Leicester", next_tiploc="LESTER", next_lat=52.631, next_lon=-1.125))
    # A step at a junction berth that is not in the train's timetable.
    collector.handle_message(json.dumps({"CA_MSG": {"area_id": "DY", "from": "B001", "to": "B002", "descr": "2S53",
                                                   "time": int((now + timedelta(minutes=4)).timestamp() * 1000)}}))
    train = state.get("td:DY:2S53")
    assert train.estimate_path == path, "the leg was wiped and the train parked at the junction"
    assert train.estimate_due_at == now + timedelta(minutes=20)
    assert train.next_location == "Leicester"
