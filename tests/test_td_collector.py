import json
from datetime import datetime, timedelta
from types import SimpleNamespace

from app.collectors.td import TDCollector
from app.models import Vehicle
from app.schedule_store import ScheduleStore
from app.state import LiveState
from app.timeutils import london_tz


def test_td_movement_is_enriched_with_matching_schedule(tmp_path):
    now = datetime.now(london_tz()).replace(second=0, microsecond=0)
    station_file = tmp_path / "stations.json"
    station_file.write_text(json.dumps({
        "12345": {"name": "Chesterfield", "lat": 53.235, "lon": -1.421, "tiploc": "CHFD"},
    }), encoding="utf-8")
    smart_file = tmp_path / "smart.json"
    smart_file.write_text(json.dumps([{
        "TD": "DY", "FROMBERTH": "A001", "TOBERTH": "A002", "STANOX": "12345",
    }]), encoding="utf-8")
    store = ScheduleStore(tmp_path / "schedule.sqlite")
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
            ("C2S53", (now-timedelta(days=1)).date().isoformat(),
             (now+timedelta(days=1)).date().isoformat(), "1111111", "P", "EM",
             "2S53", "Sheffield", "Leicester", json.dumps(locations)),
        )
    state = LiveState()
    collector = TDCollector(
        SimpleNamespace(td_topic="/topic/TD"), state, station_file, smart_file, store,
    )

    collector.handle_message(json.dumps({"CA_MSG": {
        "area_id": "DY", "from": "A001", "to": "A002", "descr": "2S53",
        "time": int(now.timestamp() * 1000),
    }}))

    train = state.get("td:DY:2S53")
    assert train is not None
    assert train.train_uid == "C2S53"
    assert train.operator == "East Midlands Railway"
    assert train.origin == "Sheffield"
    assert train.destination == "Leicester"
    assert train.next_location == "Leicester"
    assert train.estimate_path == [[53.235, -1.421], [52.631, -1.125]]
    assert train.estimate_due_at > train.estimate_started_at
    first_started_at = train.estimate_started_at
    collector.handle_message(json.dumps({"CA_MSG": {
        "area_id": "DY", "from": "A001", "to": "A002", "descr": "2S53",
        "time": int((now + timedelta(seconds=10)).timestamp() * 1000),
    }}))
    assert state.get("td:DY:2S53").estimate_started_at == first_started_at


def bare_collector(tmp_path):
    station_file = tmp_path / "stations.json"
    station_file.write_text(json.dumps({
        "12345": {"name": "Leicester", "lat": 52.631, "lon": -1.125, "tiploc": "LESTER"},
        "12346": {"name": "Harborough", "lat": 52.478, "lon": -0.908, "tiploc": "MARKTH"},
    }), encoding="utf-8")
    smart_file = tmp_path / "smart.json"
    smart_file.write_text(json.dumps([
        {"TD": "DY", "FROMBERTH": "A001", "TOBERTH": "A002", "STANOX": "12345"},
        {"TD": "DY", "FROMBERTH": "A002", "TOBERTH": "A003", "STANOX": "12346"},
    ]), encoding="utf-8")
    state = LiveState()
    return TDCollector(SimpleNamespace(td_topic="/topic/TD"), state, station_file, smart_file), state


def td_message(collector, now, typ="CA", **fields):
    body = {"area_id": "DY", "from": "A001", "to": "A002", "descr": "1F10", "time": int(now.timestamp()*1000)}
    body.update(fields)
    collector.handle_message(json.dumps({f"{typ}_MSG": body}))


def test_td_does_not_move_unrelated_same_headcode_across_the_country(tmp_path):
    collector, state = bare_collector(tmp_path)
    now = datetime.now(london_tz())
    state.upsert(Vehicle(
        id="nr:unrelated", type="train", name="1F10", headcode="1F10", train_uid="FARAWAY",
        lat=55.9, lon=-3.1, last_updated=now-timedelta(seconds=30),
    ))
    td_message(collector, now)
    assert state.get("nr:unrelated").lat == 55.9
    assert state.get("nr:unrelated").train_uid == "FARAWAY"
    assert state.get("td:DY:1F10").lat == 52.631


def test_td_keeps_exact_uid_when_enriching_nearby_trust_train(tmp_path):
    collector, state = bare_collector(tmp_path)
    now = datetime.now(london_tz())
    state.upsert(Vehicle(
        id="nr:known", type="train", name="1F10", headcode="1F10", train_uid="EXACT1",
        lat=52.631, lon=-1.125, last_updated=now-timedelta(seconds=10),
        rail_event_type="Arrival", destination="Sheffield", source="Network Rail TRUST",
        next_lat=53, next_lon=-1, next_location="Old next report",
    ))
    td_message(collector, now)
    train = state.get("nr:known")
    assert train.train_uid == "EXACT1"
    assert train.destination == "Sheffield"
    assert train.rail_event_type is None
    assert train.rail_stanox == "12345"
    assert train.next_location is None


def test_td_berth_cancellation_is_not_a_fresh_position(tmp_path):
    collector, state = bare_collector(tmp_path)
    now = datetime.now(london_tz())
    td_message(collector, now, **{"from": "A002", "to": "A003"})
    td_message(collector, now+timedelta(seconds=10), "CB", **{"from": "A001", "to": ""})
    train = state.get("td:DY:1F10")
    assert train.lat == 52.478
    assert train.last_updated == now.replace(microsecond=(now.microsecond//1000)*1000)


def test_td_older_report_does_not_restore_obsolete_berth(tmp_path):
    collector, state = bare_collector(tmp_path)
    now = datetime.now(london_tz()).replace(microsecond=0)
    td_message(collector, now)
    td_message(collector, now-timedelta(seconds=15))
    assert state.get("td:DY:1F10").last_updated == now


def test_td_step_with_only_origin_mapping_keeps_an_explicit_estimated_position(tmp_path):
    collector, state = bare_collector(tmp_path)
    td_message(collector, datetime.now(london_tz()), to="UNKNOWN")
    train = state.get("td:DY:1F10")
    assert train is not None
    assert train.lat == 52.631
    assert train.position_method == "estimated"
    assert "unresolved step" in train.position_label


def test_unmapped_td_step_holds_the_same_marker_until_next_mapped_berth(tmp_path):
    collector, state = bare_collector(tmp_path)
    now = datetime.now(london_tz()).replace(microsecond=0)
    td_message(collector, now)
    td_message(collector, now + timedelta(seconds=12), **{"from": "UNKNOWN", "to": "UNKNOWN"})
    train = state.get("td:DY:1F10")
    assert train.lat == 52.631
    assert train.last_updated == now + timedelta(seconds=12)
    assert train.status == "TD live • next berth unresolved"


def test_td_uses_smart_departure_offset_instead_of_restarting_at_station(tmp_path):
    now = datetime.now(london_tz()).replace(second=0, microsecond=0)
    station_file = tmp_path / "stations.json"
    station_file.write_text(json.dumps({
        "59318": {"name": "Hinckley", "lat": 52.535, "lon": -1.371, "tiploc": "HINCKLY"},
    }), encoding="utf-8")
    smart_file = tmp_path / "smart.json"
    smart_file.write_text(json.dumps([{
        "TD": "WG", "FROMBERTH": "2976", "TOBERTH": "2970", "STANOX": "59318",
        "EVENT": "B", "BERTHOFFSET": "-82",
    }]), encoding="utf-8")
    store = ScheduleStore(tmp_path / "schedule.sqlite")
    with store._connect() as conn:
        conn.executemany(
            "INSERT INTO tiplocs(tiploc,stanox,description,lat,lon) VALUES(?,?,?,?,?)",
            [
                ("HINCKLY", "59318", "Hinckley", 52.535, -1.371),
                ("NARBORO", "59301", "Narborough", 52.571, -1.203),
                ("LESTER", "59300", "Leicester", 52.631, -1.125),
            ],
        )
        conn.execute(
            """INSERT INTO schedules(train_uid,start_date,end_date,days_runs,stp_indicator,atoc_code,
                   signalling_id,origin_name,destination_name,locations_json)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                "C1P04", (now-timedelta(days=1)).date().isoformat(),
                (now+timedelta(days=1)).date().isoformat(), "1111111", "P", "XC",
                "1P04", "Birmingham", "Leicester", json.dumps([
                    {"tiploc_code": "HINCKLY", "scheduled_departure": now.strftime("%H%M")},
                    {"tiploc_code": "NARBORO", "scheduled_arrival": (now+timedelta(minutes=10)).strftime("%H%M")},
                    {"tiploc_code": "LESTER", "scheduled_arrival": (now+timedelta(minutes=20)).strftime("%H%M")},
                ]),
            ),
        )
    state = LiveState()
    collector = TDCollector(SimpleNamespace(td_topic="/topic/TD"), state, station_file, smart_file, store)

    collector.handle_message(json.dumps({"CA_MSG": {
        "area_id": "WG", "from": "2976", "to": "2970", "descr": "1P04",
        "time": int(now.timestamp() * 1000),
    }}))

    train = state.get("td:WG:1P04")
    assert train is not None
    assert train.rail_event_type == "Departure"
    assert train.estimate_started_at == now - timedelta(seconds=82)
    assert train.estimate_due_at == now + timedelta(minutes=10, seconds=-82)
    assert train.next_tiploc == "NARBORO"
    assert "corrected -82s" in train.position_label

    collector.handle_message(json.dumps({"CA_MSG": {
        "area_id": "WG", "from": "UNKNOWN", "to": "UNKNOWN", "descr": "1P04",
        "time": int((now + timedelta(minutes=10)).timestamp() * 1000),
    }}))

    advanced = state.get("td:WG:1P04")
    assert advanced.reported_lat == 52.571
    assert advanced.reported_lon == -1.203
    assert advanced.next_location == "Leicester"
    assert advanced.next_tiploc == "LESTER"
    assert advanced.estimate_started_at == now + timedelta(minutes=10, seconds=-82)
    assert advanced.position_method == "estimated"
    assert "matched published railway schedule" in advanced.position_label
