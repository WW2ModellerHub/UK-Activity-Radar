from __future__ import annotations

import sqlite3
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from app import schedule_store
from app.schedule_store import ScheduleStore
from app.timeutils import london_tz


def _schedule(store, uid="A12345", *, start="2026-11-01", end="2026-11-30",
              days="1111111", stp="P", headcode="1F10", operator="EM", source="SCHEDULE", locations=None):
    if locations is None:
        locations = [{"tiploc_code": "LESTER", "scheduled_departure": "0809"}]
    with store._connect() as conn:
        return conn.execute(
            """INSERT INTO schedules(train_uid,start_date,end_date,days_runs,stp_indicator,
                   signalling_id,atoc_code,source,locations_json)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (uid, start, end, days, stp, headcode, operator, source, json.dumps(locations)),
        ).lastrowid


def test_completed_build_does_not_fail_when_windows_holds_disposable_sidecar(tmp_path, monkeypatch):
    build = tmp_path / "schedule.building.sqlite"
    wal = Path(str(build) + "-wal")
    wal.write_bytes(b"already checkpointed")
    real_unlink = Path.unlink

    def sometimes_locked(path, *args, **kwargs):
        if path == wal:
            raise PermissionError("scanner still holds the disposable WAL")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", sometimes_locked)

    removed = schedule_store._remove_build_sidecars(build)

    assert removed is False
    assert wal.exists()


def test_vstp_only_rows_do_not_masquerade_as_full_timetable(tmp_path, monkeypatch):
    store = ScheduleStore(tmp_path / "schedule.sqlite")
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            "INSERT INTO schedules(train_uid, source) VALUES(?, 'VSTP')",
            ("VSTP01",),
        )

    downloaded = []

    def fake_download(username, password, destination, *, progress=None):
        downloaded.append(destination)
        destination.write_bytes(b"complete schedule fixture")
        return {"url": "fixture", "bytes": destination.stat().st_size}

    monkeypatch.setattr(schedule_store, "_download_schedule", fake_download)
    monkeypatch.setattr(schedule_store, "ensure_naptan_cache", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        schedule_store,
        "ingest_schedule_file",
        lambda *args, **kwargs: {"schedules": 50_000, "tiplocs": 1_000},
    )

    result = schedule_store.sync_schedule_data("user", "password", tmp_path)

    assert downloaded == [tmp_path / "schedule-full.dat"]
    assert result["cached"] is False


def test_completed_full_timetable_is_reused(tmp_path, monkeypatch):
    store = ScheduleStore(tmp_path / "schedule.sqlite")
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            "INSERT INTO schedules(train_uid, source) VALUES(?, 'SCHEDULE')",
            ("FULL01",),
        )
        conn.execute(
            "INSERT INTO meta(key, value) VALUES('last_sync', '2026-10-14T12:00:00Z')"
        )

    monkeypatch.setattr(
        schedule_store,
        "_download_schedule",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected download")),
    )

    result = schedule_store.sync_schedule_data("user", "password", tmp_path)

    assert result["cached"] is True
    assert result["schedules"] == 1
    assert result["last_sync"] == "2026-10-14T12:00:00Z"


def test_headcode_lookup_uses_live_location_before_reused_service(tmp_path):
    store = ScheduleStore(tmp_path / "schedule.sqlite")
    now = datetime.now(london_tz()).replace(second=0, microsecond=0)
    start = (now - timedelta(days=1)).date().isoformat()
    end = (now + timedelta(days=1)).date().isoformat()
    nearby = (now + timedelta(minutes=4)).strftime("%H%M")
    with store._connect() as conn:
        conn.executemany(
            """INSERT INTO schedules(
                   train_uid,start_date,end_date,days_runs,stp_indicator,atoc_code,
                   signalling_id,origin_name,destination_name,locations_json
               ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            [
                ("WRONG1", start, end, "1111111", "P", "XC", "2S53", "Wrong", "Route", json.dumps([
                    {"tiploc_code": "OTHER", "scheduled_departure": nearby},
                ])),
                ("RIGHT1", start, end, "1111111", "P", "EM", "2S53", "Sheffield", "Leicester", json.dumps([
                    {"tiploc_code": "CHFD", "scheduled_departure": nearby},
                    {"tiploc_code": "LESTER", "scheduled_arrival": (now + timedelta(minutes=40)).strftime("%H%M")},
                ])),
            ],
        )

    match = store.lookup_headcode("2s53", at_time=now, tiploc="CHFD")

    assert match is not None
    assert match.train_uid == "RIGHT1"
    assert match.origin == "Sheffield"
    assert match.destination == "Leicester"


def test_headcode_lookup_refuses_to_guess_without_live_location(tmp_path):
    store = ScheduleStore(tmp_path / "schedule.sqlite")
    now = datetime.now(london_tz()).replace(second=0, microsecond=0)
    with store._connect() as conn:
        conn.execute(
            """INSERT INTO schedules(train_uid,start_date,end_date,days_runs,stp_indicator,
                   signalling_id,origin_name,destination_name,locations_json)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            ("WRONG1", (now-timedelta(days=1)).date().isoformat(),
             (now+timedelta(days=1)).date().isoformat(), "1111111", "P", "1P02",
             "London Liverpool Street", "Norwich", json.dumps([{"tiploc_code":"STFD","scheduled_departure":now.strftime("%H%M")}]))
        )

    assert store.lookup_headcode("1P02", at_time=now) is None


def test_headcode_lookup_refuses_implausible_time_at_same_location(tmp_path):
    store = ScheduleStore(tmp_path / "schedule.sqlite")
    now = datetime.now(london_tz()).replace(second=0, microsecond=0)
    far_time = (now + timedelta(hours=6)).strftime("%H%M")
    with store._connect() as conn:
        conn.execute(
            """INSERT INTO schedules(train_uid,start_date,end_date,days_runs,stp_indicator,
                   signalling_id,origin_name,destination_name,locations_json)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            ("STALE1", (now-timedelta(days=1)).date().isoformat(),
             (now+timedelta(days=1)).date().isoformat(), "1111111", "P", "1P02",
             "Wrong", "Route", json.dumps([{"tiploc_code":"HINCKLY","scheduled_departure":far_time}]))
        )

    assert store.lookup_headcode("1P02", at_time=now, tiploc="HINCKLY") is None


def test_effective_schedule_honours_cancellation_without_headcode_or_locations(tmp_path):
    store = ScheduleStore(tmp_path / "schedule.sqlite")
    _schedule(store, stp="C", start="2026-11-05", end="2026-11-05", headcode=None, locations=[])
    _schedule(store)  # A later inserted base record must not resurrect it.
    assert store.effective_rows(date(2026, 11, 5), train_uids=["A12345"]) == []
    assert store.lookup_headcode("1F10", at_time=datetime(2026, 11, 5, 8, 10, tzinfo=london_tz()), tiploc="LESTER") is None
    assert len(store.effective_rows(date(2026, 11, 6), train_uids=["A12345"])) == 1


def test_effective_schedule_overlay_can_remove_station_or_change_headcode(tmp_path):
    store = ScheduleStore(tmp_path / "schedule.sqlite")
    overlay = _schedule(store, stp="O", headcode="1F12", locations=[
        {"tiploc_code": "OTHER", "scheduled_departure": "0809"},
    ])
    _schedule(store)
    rows = store.effective_rows(date(2026, 11, 5), train_uids=["A12345"])
    assert [row["id"] for row in rows] == [overlay]
    assert store.effective_rows(date(2026, 11, 5), headcode="1F10") == []
    assert store.lookup_headcode("1F12", at_time=datetime(2026, 11, 5, 8, 10, tzinfo=london_tz()), tiploc="LESTER") is None


def test_effective_schedule_resolves_days_before_overlay_priority(tmp_path):
    store = ScheduleStore(tmp_path / "schedule.sqlite")
    _schedule(store, stp="C", days="0000011")
    base = _schedule(store)
    assert [row["id"] for row in store.effective_rows(date(2026, 11, 5), train_uids=["A12345"])] == [base]
    assert store.effective_rows(date(2026, 11, 7), train_uids=["A12345"]) == []


def test_effective_schedule_prefers_vstp_revision_within_same_stp_kind(tmp_path):
    store = ScheduleStore(tmp_path / "schedule.sqlite")
    revision = _schedule(store, stp="O", source="VSTP")
    _schedule(store, stp="O")
    _schedule(store)
    assert [row["id"] for row in store.effective_rows(date(2026, 11, 5), train_uids=["A12345"])] == [revision]


def test_activation_uses_origin_service_day_instead_of_latest_uid_version(tmp_path):
    store = ScheduleStore(tmp_path / "schedule.sqlite")
    correct = _schedule(store, days="0001000")  # Thursday only.
    _schedule(store, days="0000100", headcode="1F99")
    match = store.lookup_activation({
        "train_uid": "A12345", "schedule_start_date": "2026-11-01", "schedule_end_date": "2026-11-30",
        "tp_origin_timestamp": "2026-11-05",
    })
    assert match is not None
    assert match.schedule_id == correct
    assert match.signalling_id == "1F10"
    assert match.running_date == "2026-11-05"


def test_activation_origin_timestamp_corrects_summer_midnight_service_date(tmp_path):
    store = ScheduleStore(tmp_path / "schedule.sqlite")
    correct = _schedule(store, start="2026-07-05", end="2026-07-05", days="0000001")
    _schedule(store, start="2026-07-04", end="2026-07-04", days="0000010", headcode="1F99")
    # 23:10 UTC on Saturday is 00:10 BST on Sunday.
    epoch = int(datetime(2026, 7, 4, 23, 10, tzinfo=timezone.utc).timestamp() * 1000)
    match = store.lookup_activation({"train_uid": "A12345", "tp_origin_timestamp": "2026-07-04", "origin_dep_timestamp": str(epoch)})
    assert match is not None and match.schedule_id == correct
    assert match.running_date == "2026-07-05"


def test_activation_does_not_return_a_cancelled_service(tmp_path):
    store = ScheduleStore(tmp_path / "schedule.sqlite")
    _schedule(store)
    _schedule(store, stp="C", locations=[])
    assert store.lookup_activation({"train_uid": "A12345", "tp_origin_timestamp": "2026-11-05"}) is None


def test_headcode_lookup_matches_stanox_when_reference_has_no_tiploc(tmp_path):
    store = ScheduleStore(tmp_path / "schedule.sqlite")
    _schedule(store)
    with store._connect() as conn:
        conn.execute("INSERT INTO tiplocs(tiploc,stanox) VALUES('LESTER','12345')")
    match = store.lookup_headcode("1F10", stanox="12345", at_time=datetime(2026, 11, 5, 8, 10, tzinfo=london_tz()))
    assert match is not None and match.train_uid == "A12345"


def test_headcode_lookup_rejects_ambiguous_repeated_headcode(tmp_path):
    store = ScheduleStore(tmp_path / "schedule.sqlite")
    _schedule(store, uid="A12345")
    _schedule(store, uid="B12345", locations=[{"tiploc_code": "LESTER", "scheduled_departure": "0814"}])
    assert store.lookup_headcode("1F10", tiploc="LESTER", at_time=datetime(2026, 11, 5, 8, 10, tzinfo=london_tz())) is None


def test_headcode_lookup_operator_disambiguates_otherwise_matching_services(tmp_path):
    store = ScheduleStore(tmp_path / "schedule.sqlite")
    _schedule(store, uid="A12345", operator="EM")
    _schedule(store, uid="B12345", operator="XC")
    match = store.lookup_headcode("1F10", tiploc="LESTER", atoc_code="EM", at_time=datetime(2026, 11, 5, 8, 10, tzinfo=london_tz()))
    assert match is not None and match.train_uid == "A12345"


def test_headcode_lookup_carries_previous_day_service_through_midnight(tmp_path):
    store = ScheduleStore(tmp_path / "schedule.sqlite")
    _schedule(store, start="2026-11-04", end="2026-11-04", days="0010000", locations=[
        {"tiploc_code": "ORIGIN", "scheduled_departure": "2340"},
        {"tiploc_code": "LESTER", "scheduled_arrival": "0008", "scheduled_departure": "0010"},
    ])
    match = store.lookup_headcode("1F10", tiploc="LESTER", at_time=datetime(2026, 11, 5, 0, 11, tzinfo=london_tz()))
    assert match is not None and match.running_date == "2026-11-04"


def test_timed_locations_keep_midnight_and_half_minutes_in_service_sequence(tmp_path):
    store = ScheduleStore(tmp_path / "schedule.sqlite")
    _schedule(store, locations=[
        {"tiploc_code": "ORIGIN", "scheduled_departure": "2358H", "public_departure": "2358"},
        {"tiploc_code": "LESTER", "scheduled_arrival": "2359H", "scheduled_departure": "0001", "public_departure": "0000"},
        {"tiploc_code": "JUNCTION", "scheduled_pass": "0010H"},
        {"tiploc_code": "DEST", "scheduled_arrival": "0020"},
    ])
    row = store.effective_rows(date(2026, 11, 5), train_uids=["A12345"])[0]
    calls = store.timed_locations(row, date(2026, 11, 5))
    assert calls[0]["_departure_at"].second == 30
    assert calls[1]["_arrival_at"].date() == date(2026, 11, 5)
    assert calls[1]["_departure_at"] == datetime(2026, 11, 6, 0, 1, tzinfo=london_tz())
    assert calls[1]["_public_departure_at"] == datetime(2026, 11, 6, 0, 0, tzinfo=london_tz())
    assert calls[2]["_pass_at"].second == 30
    assert calls[3]["_arrival_at"].date() == date(2026, 11, 6)


def test_smart_departure_offset_starts_train_ahead_of_reporting_point(tmp_path):
    store = ScheduleStore(tmp_path / "schedule.sqlite")
    schedule_id = _schedule(store, locations=[
        {"tiploc_code": "HINCKLY", "scheduled_departure": "0730"},
        {"tiploc_code": "NARBORO", "scheduled_arrival": "0740"},
    ])
    with store._connect() as conn:
        conn.executemany(
            "INSERT INTO tiplocs(tiploc,description,lat,lon) VALUES(?,?,?,?)",
            [
                ("HINCKLY", "Hinckley", 52.535, -1.371),
                ("NARBORO", "Narborough", 52.571, -1.203),
            ],
        )
    observed = datetime(2026, 11, 5, 7, 32, tzinfo=london_tz())

    leg = store.estimate_leg_from(
        schedule_id, date(2026, 11, 5), "HINCKLY", observed,
        event_code="B", berth_offset_seconds=-82,
    )

    assert leg is not None
    assert leg["event_kind"] == "departure"
    assert leg["started_at"] == observed - timedelta(seconds=82)
    assert leg["due_at"] == observed + timedelta(minutes=10, seconds=-82)
    assert leg["path"] == [[52.535, -1.371], [52.571, -1.203]]
    assert leg["next_tiploc"] == "NARBORO"


def test_confirmed_arrival_advances_after_published_station_dwell(tmp_path):
    store = ScheduleStore(tmp_path / "schedule.sqlite")
    schedule_id = _schedule(store, locations=[
        {"tiploc_code": "HINCKLY", "scheduled_departure": "0750"},
        {"tiploc_code": "LESTER", "scheduled_arrival": "0800", "scheduled_departure": "0801"},
        {"tiploc_code": "MARKTH", "scheduled_arrival": "0815"},
    ])
    with store._connect() as conn:
        conn.executemany(
            "INSERT INTO tiplocs(tiploc,description,lat,lon) VALUES(?,?,?,?)",
            [
                ("HINCKLY", "Hinckley", 52.535, -1.371),
                ("LESTER", "Leicester", 52.631, -1.125),
                ("MARKTH", "Market Harborough", 52.478, -0.908),
            ],
        )
    actual_arrival = datetime(2026, 11, 5, 8, 3, tzinfo=london_tz())

    leg = store.estimate_departure_leg_after_arrival(
        schedule_id, date(2026, 11, 5), "LESTER", actual_arrival,
    )

    assert leg is not None
    assert leg["started_at"] == datetime(2026, 11, 5, 8, 4, tzinfo=london_tz())
    assert leg["due_at"] == datetime(2026, 11, 5, 8, 18, tzinfo=london_tz())
    assert leg["next_name"] == "Market Harborough"
    assert leg["next_tiploc"] == "MARKTH"
    assert leg["path"] == [[52.631, -1.125], [52.478, -0.908]]


def test_smart_arrival_offset_keeps_train_on_approach_leg(tmp_path):
    store = ScheduleStore(tmp_path / "schedule.sqlite")
    schedule_id = _schedule(store, locations=[
        {"tiploc_code": "HINCKLY", "scheduled_departure": "0730"},
        {"tiploc_code": "NARBORO", "scheduled_arrival": "0740", "scheduled_departure": "0741"},
        {"tiploc_code": "LESTER", "scheduled_arrival": "0750"},
    ])
    with store._connect() as conn:
        conn.executemany(
            "INSERT INTO tiplocs(tiploc,description,lat,lon) VALUES(?,?,?,?)",
            [
                ("HINCKLY", "Hinckley", 52.535, -1.371),
                ("NARBORO", "Narborough", 52.571, -1.203),
                ("LESTER", "Leicester", 52.631, -1.125),
            ],
        )
    observed = datetime(2026, 11, 5, 7, 39, 34, tzinfo=london_tz())

    leg = store.estimate_leg_from(
        schedule_id, date(2026, 11, 5), "NARBORO", observed,
        event_code="A", berth_offset_seconds=26,
    )

    assert leg is not None
    assert leg["event_kind"] == "arrival"
    assert leg["path"] == [[52.535, -1.371], [52.571, -1.203]]
    assert leg["due_at"] == datetime(2026, 11, 5, 7, 40, tzinfo=london_tz())
    assert leg["started_at"] == datetime(2026, 11, 5, 7, 30, tzinfo=london_tz())
    assert leg["next_name"] == "Narborough"


def test_call_index_retains_overnight_and_next_day_rows(tmp_path):
    store = ScheduleStore(tmp_path / "schedule.sqlite")
    yesterday = _schedule(store, uid="YESTER", start="2026-11-04", end="2026-11-04")
    tomorrow = _schedule(store, uid="TOMORR", start="2026-11-06", end="2026-11-06")
    _schedule(store, uid="OLDROW", start="2026-11-03", end="2026-11-03")
    with store._connect() as conn:
        store._rebuild_call_index_on(conn, "2026-11-05")
        ids = {row[0] for row in conn.execute("SELECT schedule_id FROM schedule_calls")}
    assert ids == {yesterday, tomorrow}


def test_headcode_lookup_uses_index_instead_of_scanning_national_timetable(tmp_path):
    store = ScheduleStore(tmp_path / "schedule.sqlite")
    with store._connect() as conn:
        plan = list(conn.execute("EXPLAIN QUERY PLAN SELECT train_uid FROM schedules WHERE UPPER(TRIM(signalling_id))=?", ("1F10",)))
    assert any("idx_sched_headcode_normalized" in row[3] for row in plan)


def test_vstp_update_preserves_other_service_days_and_nested_headcode(tmp_path):
    store = ScheduleStore(tmp_path / "schedule.sqlite")
    for service_day in ("2026-11-05", "2026-11-06"):
        store.upsert_vstp({
            "CIF_train_uid": "V12345", "CIF_stp_indicator": "N",
            "schedule_start_date": service_day, "schedule_end_date": service_day,
            "schedule_segment": {"signalling_id": "1F10", "CIF_train_service_code": "12345678", "schedule_location": [
                {"tiploc_code": "LESTER", "scheduled_departure": "0809"},
            ]},
        })
    today = store.lookup_uid("V12345", running_date=date(2026, 11, 5))
    tomorrow = store.lookup_uid("V12345", running_date=date(2026, 11, 6))
    assert today is not None and tomorrow is not None
    assert today.signalling_id == tomorrow.signalling_id == "1F10"
    assert today.service_code == tomorrow.service_code == "12345678"
    store.upsert_vstp({"CIF_train_uid": "V12345", "CIF_stp_indicator": "N", "schedule_start_date": "2026-11-06", "transaction_type": "Delete"})
    assert store.lookup_uid("V12345", running_date=date(2026, 11, 6)) is None
    assert store.lookup_uid("V12345", running_date=date(2026, 11, 5)) is not None
