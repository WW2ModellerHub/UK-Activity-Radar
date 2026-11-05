from __future__ import annotations

import sqlite3
import json
from datetime import datetime, timedelta
from pathlib import Path

from app import schedule_store
from app.schedule_store import ScheduleStore
from app.timeutils import london_tz


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
