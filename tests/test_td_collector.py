import json
from datetime import datetime, timedelta
from types import SimpleNamespace

from app.collectors.td import TDCollector
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
