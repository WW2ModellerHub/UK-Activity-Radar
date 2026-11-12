import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.collectors.network_rail import TrustCollector
from app.models import Vehicle
from app.schedule_store import ScheduleStore
from app.state import LiveState
from app.timeutils import london_tz


def make_collector(tmp_path, *, timetable=True):
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    local = now.astimezone(london_tz())
    station_file = tmp_path / "stations.json"
    station_file.write_text(json.dumps({
        "12345": {"name": "Leicester", "lat": 52.631, "lon": -1.125, "tiploc": "LESTER"},
        "12346": {"name": "Market Harborough", "lat": 52.478, "lon": -0.908, "tiploc": "MARKTH"},
    }), encoding="utf-8")
    store = ScheduleStore(tmp_path / "schedules.sqlite")
    if timetable:
        with store._connect() as conn:
            conn.execute("INSERT INTO tiplocs(tiploc,stanox,description,lat,lon) VALUES('LESTER','12345','Leicester',52.631,-1.125)")
            conn.execute("INSERT INTO tiplocs(tiploc,stanox,description,lat,lon) VALUES('MARKTH','12346','Market Harborough',52.478,-0.908)")
            conn.execute(
                """INSERT INTO schedules(train_uid,start_date,end_date,days_runs,stp_indicator,atoc_code,
                       signalling_id,origin_name,destination_name,locations_json)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                ("C1F10", (local-timedelta(days=1)).date().isoformat(),
                 (local+timedelta(days=1)).date().isoformat(), "1111111", "P", "EM", "1F10",
                 "St Pancras International", "Sheffield", json.dumps([
                     {"tiploc_code": "LESTER", "scheduled_departure": local.strftime("%H%M")},
                     {"tiploc_code": "MARKTH", "scheduled_arrival": (local+timedelta(minutes=15)).strftime("%H%M")},
                 ])),
            )
    state = LiveState()
    collector = TrustCollector(SimpleNamespace(trust_topic="/topic/TRUST"), state, station_file, store)
    return collector, state, now


def movement(collector, now, **fields):
    body = {
        "train_id": "251F10MU05", "loc_stanox": "12345", "event_type": "ARRIVAL",
        "actual_timestamp": int(now.timestamp()*1000), "toc_id": "28",
    }
    body.update(fields)
    collector.handle_message(json.dumps({"header": {"msg_type": "0003"}, "body": body}))


def test_missed_activation_recovers_uid_from_confirmed_stanox_and_time(tmp_path):
    collector, state, now = make_collector(tmp_path)
    movement(collector, now)
    train = state.get("nr:251F10MU05")
    assert train.train_uid == "C1F10"
    assert train.destination == "Sheffield"
    assert train.service_date == now.astimezone(london_tz()).date().isoformat()
    assert train.rail_event_type == "Arrival"
    assert train.rail_stanox == "12345"
    assert train.operator == "East Midlands Railway"


def test_live_activation_is_visible_before_the_first_movement_report(tmp_path):
    collector, state, now = make_collector(tmp_path)
    collector.handle_message(json.dumps({"header": {"msg_type": "0001"}, "body": {
        "train_id": "251F10MU05", "train_uid": "C1F10", "toc_id": "28",
        "origin_dep_timestamp": int(now.timestamp() * 1000),
        "creation_timestamp": int(now.timestamp() * 1000),
    }}))
    train = state.get("nr:251F10MU05")
    assert train is not None
    assert train.position_method == "estimated"
    assert train.status == "Activated • awaiting first movement report"
    assert train.next_location == "Market Harborough"
    assert train.estimate_path == []


def test_unknown_station_does_not_attach_a_headcode_schedule(tmp_path):
    collector, state, now = make_collector(tmp_path)
    collector.stations["99999"] = {"name": "Elsewhere", "lat": 55, "lon": -3}
    movement(collector, now, loc_stanox="99999")
    train = state.get("nr:251F10MU05")
    assert train.train_uid is None
    assert train.destination is None


def test_operator_mismatch_does_not_attach_schedule(tmp_path):
    collector, state, now = make_collector(tmp_path)
    movement(collector, now, toc_id="27")
    assert state.get("nr:251F10MU05").train_uid is None


def test_movement_after_restart_keeps_restored_identity_without_schedule(tmp_path):
    collector, state, now = make_collector(tmp_path, timetable=False)
    state.upsert(Vehicle(
        id="nr:251F10MU05", type="train", name="1F10", headcode="1F10",
        train_uid="RESTORED", origin="London", destination="Sheffield",
        service_date=now.date().isoformat(), operator="East Midlands Railway",
        lat=52.631, lon=-1.125, last_updated=now-timedelta(minutes=2),
    ))
    movement(collector, now)
    train = state.get("nr:251F10MU05")
    assert train.train_uid == "RESTORED"
    assert train.destination == "Sheffield"
    assert train.operator == "East Midlands Railway"


def test_out_of_order_movement_cannot_move_train_back_to_station(tmp_path):
    collector, state, now = make_collector(tmp_path)
    movement(collector, now, loc_stanox="12346", event_type="PASS")
    movement(collector, now-timedelta(minutes=3))
    train = state.get("nr:251F10MU05")
    assert train.rail_stanox == "12346"
    assert train.rail_event_type == "Pass"
    assert train.last_updated == now


def test_arrival_hands_over_to_onward_estimate_after_booked_dwell(tmp_path):
    collector, state, now = make_collector(tmp_path)
    local = now.astimezone(london_tz())
    with collector.schedules._connect() as conn:
        row = conn.execute("SELECT id,locations_json FROM schedules WHERE train_uid='C1F10'").fetchone()
        locations = json.loads(row["locations_json"])
        locations[0]["scheduled_arrival"] = (local-timedelta(minutes=1)).strftime("%H%M")
        conn.execute("UPDATE schedules SET locations_json=? WHERE id=?", (json.dumps(locations), row["id"]))
    movement(collector, now, next_report_stanox="12346", next_report_run_time="15")
    train = state.get("nr:251F10MU05")
    assert train.next_location == "Market Harborough"
    assert train.next_tiploc == "MARKTH"
    assert train.estimate_started_at == now + timedelta(minutes=1)
    assert train.estimate_due_at == now + timedelta(minutes=16)
    assert "published station dwell" in train.position_label


def test_departure_includes_a_mapped_railway_estimate_path(tmp_path):
    collector, state, now = make_collector(tmp_path)
    with collector.schedules._connect() as conn:
        conn.execute("INSERT OR REPLACE INTO tiplocs(tiploc,stanox,lat,lon) VALUES('LESTER','12345',52.631,-1.125)")
        conn.execute("INSERT OR REPLACE INTO tiplocs(tiploc,stanox,lat,lon) VALUES('MIDWAY','12347',52.55,-1.02)")
        conn.execute("INSERT OR REPLACE INTO tiplocs(tiploc,stanox,lat,lon) VALUES('MARKTH','12346',52.478,-0.908)")
        row = conn.execute("SELECT id,locations_json FROM schedules WHERE train_uid='C1F10'").fetchone()
        locations = json.loads(row["locations_json"])
        locations.insert(1, {"tiploc_code":"MIDWAY", "scheduled_pass":"1008"})
        conn.execute("UPDATE schedules SET locations_json=? WHERE id=?", (json.dumps(locations), row["id"]))
    movement(
        collector, now, event_type="DEPARTURE", next_report_stanox="12346",
        next_report_run_time="15",
    )
    train = state.get("nr:251F10MU05")
    assert train.estimate_path == [[52.631, -1.125], [52.55, -1.02], [52.478, -0.908]]


def test_identity_change_reuses_original_vehicle_and_retains_uid(tmp_path):
    collector, state, now = make_collector(tmp_path)
    movement(collector, now)
    collector.handle_message(json.dumps({"header": {"msg_type": "0007"}, "body": {
        "train_id": "251F10MU05", "revised_train_id": "251F11MU05",
    }}))
    movement(collector, now+timedelta(seconds=10), train_id="251F11MU05")
    train = state.get("nr:251F10MU05")
    assert train.headcode == "1F11"
    assert train.train_uid == "C1F10"
    assert state.get("nr:251F11MU05") is None


def test_late_activation_enriches_existing_position_immediately(tmp_path):
    collector, state, now = make_collector(tmp_path, timetable=False)
    movement(collector, now)
    collector.handle_message(json.dumps({"header": {"msg_type": "0001"}, "body": {
        "train_id": "251F10MU05", "train_uid": "KNOWN1", "toc_id": "28",
    }}))
    assert state.get("nr:251F10MU05").train_uid == "KNOWN1"
    assert state.get("nr:251F10MU05").last_updated == now


def test_trust_reuses_a_td_marker_for_the_same_scheduled_service(tmp_path):
    collector, state, now = make_collector(tmp_path)
    state.upsert(Vehicle(
        id="td:DY:1F10", type="train", name="1F10", headcode="1F10",
        train_uid="C1F10", service_date=now.astimezone(london_tz()).date().isoformat(),
        lat=52.631, lon=-1.125, source="Network Rail TD",
        last_updated=now-timedelta(seconds=10),
    ))
    collector.handle_message(json.dumps({"header": {"msg_type": "0001"}, "body": {
        "train_id": "251F10MU05", "train_uid": "C1F10", "toc_id": "28",
        "origin_dep_timestamp": int(now.timestamp() * 1000),
        "creation_timestamp": int(now.timestamp() * 1000),
    }}))
    assert state.get("td:DY:1F10").destination == "Sheffield"
    assert state.get("nr:251F10MU05") is None
    movement(collector, now)
    assert state.get("td:DY:1F10").source == "Network Rail TRUST"
    assert state.get("nr:251F10MU05") is None
