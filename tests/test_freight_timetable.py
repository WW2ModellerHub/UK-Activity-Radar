"""Freight trains: matched to their working timetable, and what they carry."""
from __future__ import annotations

import json
from datetime import date, datetime

from app.schedule_store import ScheduleStore, _vstp_location
from app.timeutils import london_tz
from app.train_identity import freight_profile


def _freight(store: ScheduleStore, locations: list[dict], *, uid="F12345", headcode="6M87") -> int:
    with store._connect() as conn:
        return conn.execute(
            """INSERT INTO schedules(train_uid,start_date,end_date,days_runs,stp_indicator,signalling_id,atoc_code,
                   source,locations_json,train_category,power_type,timing_load,booked_speed)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (uid, "2026-11-01", "2026-11-30", "1111111", "P", headcode, "ZZ", "SCHEDULE",
             json.dumps(locations), "J4", "D", "2400", "060"),
        ).lastrowid


# The overnight CIF JSON as Network Rail publishes it: working times are
# "arrival", "departure" and "pass", and a freight train has no public times.
MIDDLETON_TO_BASFORD = [
    {"tiploc_code": "MDLTNTW", "departure": "1405", "public_departure": None},
    {"tiploc_code": "WGSTGPJ", "arrival": None, "departure": None, "pass": "1928H"},
    {"tiploc_code": "NNTNANJ", "pass": "1955"},
    {"tiploc_code": "BASFDHY", "arrival": "2240", "public_arrival": None},
]


def test_a_freight_train_with_only_working_times_matches_its_live_headcode(tmp_path):
    store = ScheduleStore(tmp_path / "schedule.sqlite")
    _freight(store, MIDDLETON_TO_BASFORD)

    found = store.lookup_headcode(
        "6M87", at_time=datetime(2026, 11, 21, 19, 31, tzinfo=london_tz()), tiploc="WGSTGPJ")

    assert found is not None
    assert found.train_uid == "F12345"
    assert found.train_category == "J4"


def test_working_times_do_not_become_public_calls(tmp_path):
    store = ScheduleStore(tmp_path / "schedule.sqlite")
    schedule_id = _freight(store, MIDDLETON_TO_BASFORD)
    with store._connect() as conn:
        row = conn.execute("SELECT * FROM schedules WHERE id=?", (schedule_id,)).fetchone()

    locations = store.timed_locations(row, date(2026, 11, 21))

    # Station boards read _{kind}_at: a freight train must not appear on them.
    assert not any(key in loc for loc in locations for key in ("_arrival_at", "_departure_at", "_pass_at"))
    assert locations[1]["_working_pass_at"] == datetime(2026, 11, 21, 19, 28, 30, tzinfo=london_tz())
    assert locations[3]["_working_arrival_at"].hour == 22


def test_live_vstp_messages_are_stored_in_the_overnight_shape(tmp_path):
    store = ScheduleStore(tmp_path / "schedule.sqlite")
    store.upsert_vstp({
        "CIF_train_uid": " 12345", "CIF_stp_indicator": "N", "transaction_type": "Create",
        "schedule_start_date": "2026-11-21", "schedule_end_date": "2026-11-21", "schedule_days_runs": "0000010",
        "schedule_segment": [{
            "signalling_id": "6Z42", "CIF_train_category": "B7", "CIF_power_type": "D",
            "CIF_timing_load": "3200", "CIF_speed": "060",
            "schedule_location": [
                {"scheduled_departure_time": "180000", "scheduled_arrival_time": "      ", "scheduled_pass_time": "      ",
                 "public_departure_time": "000000", "CIF_platform": "",
                 "location": {"tiploc": {"tiploc_id": "HUMBROR"}}},
                {"scheduled_pass_time": "193030", "location": {"tiploc": {"tiploc_id": "KNGSBYJ"}}},
                {"scheduled_arrival_time": "195900", "location": {"tiploc": {"tiploc_id": "KNGSBYO"}}},
            ],
        }],
    })

    found = store.lookup_headcode(
        "6Z42", at_time=datetime(2026, 11, 21, 19, 33, tzinfo=london_tz()), tiploc="KNGSBYJ")

    assert found is not None and found.train_uid == "12345"
    assert found.train_category == "B7" and found.timing_load == "3200"
    assert (found.origin, found.destination) == ("HUMBROR", "KNGSBYO")


def test_vstp_location_keeps_overnight_rows_unchanged():
    row = {"tiploc_code": "LESTER", "departure": "0809", "public_departure": "0809", "platform": "4"}
    assert _vstp_location(row) == row


def test_freight_profile_reads_cargo_weight_traction_and_speed():
    assert freight_profile("B7", "D", "3200", "060") == {
        "category": "B7", "cargo": "Petroleum products", "tonnes": 3200, "traction": "Diesel", "max_mph": 60}
    # A unit's timing load is its class, not a weight.
    assert freight_profile("PP", "EMU", "325", "100")["tonnes"] is None
    # Passenger trains have no freight profile.
    assert freight_profile("OO", "DMU", "170", "100") is None
