"""Cancellations and platform changes reach the map, boards and train panel."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.models import Vehicle
from app.rail_service_notes import RailServiceNotes, call_is_cancelled
from app.schedule_store import ScheduleStore
from app.smart_places import SmartPlacesService
from app.state import CANCELLED_VISIBLE_MINUTES, LiveState
from app.timeutils import london_tz

from tests.test_trust_collector import make_collector, movement


def _trust(collector, msg_type, **body):
    body.setdefault("train_id", "251F10MU05")
    collector.handle_message(json.dumps({"header": {"msg_type": msg_type}, "body": body}))


def _train_ids(state):
    return {row.id for row in state.all(include_buses=False, include_vessels=False) if row.type == "train"}


def test_cancelled_train_is_struck_through_then_leaves_the_map(tmp_path):
    collector, state, now = make_collector(tmp_path)
    movement(collector, now, event_type="DEPARTURE")
    train = state.get("nr:251F10MU05")
    _trust(collector, "0002", loc_stanox="12345", canx_type="EN ROUTE",
           canx_timestamp=int(now.timestamp() * 1000))
    cancelled = state.get("nr:251F10MU05")
    assert cancelled.status == "Cancelled"
    assert cancelled.cancelled_at is not None
    assert cancelled.estimate_path is None
    note = state.rail_notes.get(train.train_uid, train.service_date)
    assert note["cancelled"]["scope"] == "part"
    assert "LESTER" in note["cancelled"]["tiplocs"]
    assert cancelled.id in _train_ids(state)

    state._vehicles[cancelled.id].cancelled_at = datetime.now(timezone.utc) - timedelta(minutes=CANCELLED_VISIBLE_MINUTES + 1)
    assert cancelled.id not in _train_ids(state)


def test_a_train_cancelled_before_it_runs_is_remembered_and_can_be_reinstated(tmp_path):
    collector, state, now = make_collector(tmp_path)
    day = now.astimezone(london_tz()).date().isoformat()
    _trust(collector, "0001", train_uid="C1F10", toc_id="28", tp_origin_timestamp=day,
           origin_dep_timestamp=int((now + timedelta(hours=2)).timestamp() * 1000),
           creation_timestamp=int(now.timestamp() * 1000))
    _trust(collector, "0002", loc_stanox="12345", canx_type="AT ORIGIN")
    assert state.rail_notes.get("C1F10", day)["cancelled"]["scope"] == "whole"
    _trust(collector, "0005", loc_stanox="12345")
    assert state.rail_notes.get("C1F10", day)["cancelled"] is None


def test_reported_platform_is_kept_with_the_booked_one(tmp_path):
    collector, state, now = make_collector(tmp_path)
    with collector.schedules._connect() as conn:
        row = conn.execute("SELECT id, locations_json FROM schedules").fetchone()
        locations = json.loads(row[1])
        locations[0]["platform"] = "2"
        conn.execute("UPDATE schedules SET locations_json=? WHERE id=?", (json.dumps(locations), row[0]))
    movement(collector, now, event_type="DEPARTURE", platform=" 4")
    train = state.get("nr:251F10MU05")
    assert (train.platform, train.booked_platform) == ("4", "2")
    assert state.rail_notes.get(train.train_uid, train.service_date)["platforms"] == {"LESTER": "4"}


def test_which_calls_a_cancellation_removes():
    codes = ["A", "B", "C", "D"]
    notes = RailServiceNotes()
    notes.cancel("U1", "2026-11-13", tiplocs={"C"}, kind="EN ROUTE")
    part = notes.get("U1", "2026-11-13")
    assert [call_is_cancelled(part, codes, i, "departure") for i in range(4)] == [False, False, True, True]
    # It still arrives where it was cut short.
    assert [call_is_cancelled(part, codes, i, "arrival") for i in range(4)] == [False, False, False, True]
    notes.cancel("U2", "2026-11-13", tiplocs={"A"}, kind="AT ORIGIN")
    whole = notes.get("U2", "2026-11-13")
    assert all(call_is_cancelled(whole, codes, i, "arrival") for i in range(4))
    assert not call_is_cancelled(None, codes, 2, "departure")


def _board_service(tmp_path: Path):
    store = ScheduleStore(tmp_path / "schedule.sqlite")
    now = datetime.now(london_tz()).replace(second=0, microsecond=0)
    day = now.date().isoformat()
    with store._connect() as con:
        con.execute("INSERT INTO tiplocs(tiploc,stanox,crs,description,lat,lon) VALUES(?,?,?,?,?,?)",
                    ("LESTER", "12345", "LEI", "LEICESTER", 52.631, -1.125))
        for uid, offset in (("CANX01", 12), ("MOVED1", 20), ("NORMAL", 25)):
            locations = [
                {"tiploc_code": "LESTER", "scheduled_departure": (now + timedelta(minutes=offset)).strftime("%H%M"), "platform": "2"},
                {"tiploc_code": "DEST", "scheduled_arrival": (now + timedelta(minutes=offset + 50)).strftime("%H%M")},
            ]
            con.execute(
                """INSERT INTO schedules(train_uid,start_date,end_date,days_runs,stp_indicator,atoc_code,signalling_id,origin_name,destination_name,locations_json)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (uid, day, day, "1111111", "P", "EM", "1F10", "Leicester", "Sheffield", json.dumps(locations)),
            )
    state = LiveState()
    return SmartPlacesService(tmp_path, store, state), state, day


def test_board_marks_cancelled_services_and_platform_changes(tmp_path: Path):
    service, state, day = _board_service(tmp_path)
    state.rail_notes.cancel("CANX01", day, tiplocs={"LESTER"}, kind="AT ORIGIN")
    state.rail_notes.platform("MOVED1", day, "LESTER", "5")
    rows = {row["train_uid"]: row for row in service._scheduled_station_calls("LEI", 10)["departures"]}
    assert rows["CANX01"]["status"] == "Cancelled" and rows["CANX01"]["cancelled"] is True
    assert rows["MOVED1"]["platform"] == "5"
    assert rows["MOVED1"]["platform_changed"] is True and rows["MOVED1"]["booked_platform"] == "2"
    assert rows["NORMAL"]["status"] == "Scheduled" and "platform_changed" not in rows["NORMAL"]


def test_cancelled_service_is_not_given_a_live_delay(tmp_path: Path):
    service, state, day = _board_service(tmp_path)
    state.upsert(Vehicle(id="nr:x", type="train", name="1F10", train_uid="CANX01", service_date=day,
                         lat=52.6, lon=-1.1, delay_minutes=7, source="Network Rail TRUST"))
    state.rail_notes.cancel("CANX01", day, kind="ON CALL")
    rows = {row["train_uid"]: row for row in service._scheduled_station_calls("LEI", 10)["departures"]}
    assert rows["CANX01"]["status"] == "Cancelled"
    assert "delay_minutes" not in rows["CANX01"]
