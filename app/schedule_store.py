from __future__ import annotations

import gzip
import json
import os
import sqlite3
import tempfile
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Callable

import requests

from .reference_data import FeedDownloadError, ensure_naptan_cache, merge_station_file

SCHEDULE_URLS = [
    "https://publicdatafeeds.networkrail.co.uk/ntrod/CifFileAuthenticate?type=CIF_ALL_FULL_DAILY&day=toc-full",
    "https://datafeeds.networkrail.co.uk/ntrod/CifFileAuthenticate?type=CIF_ALL_FULL_DAILY&day=toc-full",
]
USER_AGENT = "UKActivityRadar/0.7.2"


@dataclass
class ScheduleInfo:
    train_uid: str
    signalling_id: str | None = None
    atoc_code: str | None = None
    service_code: str | None = None
    origin: str | None = None
    destination: str | None = None
    origin_tiploc: str | None = None
    destination_tiploc: str | None = None


class ScheduleStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
                CREATE TABLE IF NOT EXISTS tiplocs(
                    tiploc TEXT PRIMARY KEY,
                    stanox TEXT,
                    crs TEXT,
                    description TEXT,
                    lat REAL,
                    lon REAL
                );
                CREATE INDEX IF NOT EXISTS idx_tiplocs_stanox ON tiplocs(stanox);
                CREATE INDEX IF NOT EXISTS idx_tiplocs_crs ON tiplocs(crs);
                CREATE TABLE IF NOT EXISTS schedules(
                    id INTEGER PRIMARY KEY,
                    train_uid TEXT NOT NULL,
                    start_date TEXT,
                    end_date TEXT,
                    days_runs TEXT,
                    stp_indicator TEXT,
                    atoc_code TEXT,
                    signalling_id TEXT,
                    service_code TEXT,
                    origin_tiploc TEXT,
                    destination_tiploc TEXT,
                    origin_name TEXT,
                    destination_name TEXT,
                    locations_json TEXT,
                    source TEXT DEFAULT 'SCHEDULE'
                );
                CREATE INDEX IF NOT EXISTS idx_sched_uid ON schedules(train_uid);
                CREATE INDEX IF NOT EXISTS idx_sched_dates ON schedules(train_uid,start_date,end_date);
                """
            )

    def ready(self) -> bool:
        if not self.db_path.exists():
            return False
        try:
            with self._connect() as conn:
                n = conn.execute("SELECT COUNT(*) FROM schedules").fetchone()[0]
            return n > 0
        except sqlite3.Error:
            return False

    def count(self) -> int:
        try:
            with self._connect() as conn:
                return int(conn.execute("SELECT COUNT(*) FROM schedules").fetchone()[0])
        except sqlite3.Error:
            return 0

    def last_sync(self) -> str | None:
        try:
            with self._connect() as conn:
                row = conn.execute("SELECT value FROM meta WHERE key='last_sync'").fetchone()
            return row[0] if row else None
        except sqlite3.Error:
            return None

    def lookup_activation(self, body: dict) -> ScheduleInfo | None:
        uid = str(body.get("train_uid") or "").strip()
        if not uid:
            return None
        start = str(body.get("schedule_start_date") or "").strip()
        end = str(body.get("schedule_end_date") or "").strip()
        run_date = str(body.get("tp_origin_timestamp") or "").strip()

        with self._connect() as conn:
            rows: list[sqlite3.Row]
            if start and end:
                rows = conn.execute(
                    "SELECT * FROM schedules WHERE train_uid=? AND start_date=? AND end_date=? ORDER BY CASE stp_indicator WHEN 'N' THEN 1 WHEN 'O' THEN 2 WHEN 'P' THEN 3 ELSE 4 END LIMIT 8",
                    (uid, start, end),
                ).fetchall()
            elif run_date:
                rows = conn.execute(
                    "SELECT * FROM schedules WHERE train_uid=? AND start_date<=? AND end_date>=? ORDER BY CASE stp_indicator WHEN 'N' THEN 1 WHEN 'O' THEN 2 WHEN 'P' THEN 3 ELSE 4 END LIMIT 8",
                    (uid, run_date, run_date),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM schedules WHERE train_uid=? ORDER BY id DESC LIMIT 8", (uid,)).fetchall()
        if not rows:
            return None
        row = rows[0]
        return ScheduleInfo(
            train_uid=uid,
            signalling_id=row["signalling_id"],
            atoc_code=row["atoc_code"],
            service_code=row["service_code"],
            origin=row["origin_name"],
            destination=row["destination_name"],
            origin_tiploc=row["origin_tiploc"],
            destination_tiploc=row["destination_tiploc"],
        )

    def upsert_vstp(self, schedule: dict) -> None:
        # VSTP schema varies slightly from the overnight JSON; normalize the
        # useful fields and retain the schedule as a short-lived database row.
        uid = str(schedule.get("CIF_train_uid") or schedule.get("train_uid") or schedule.get("schedule_id") or "").strip()
        if not uid:
            return
        locations = schedule.get("schedule_location") or schedule.get("locations") or []
        if not isinstance(locations, list):
            locations = []
        origin = locations[0] if locations else {}
        dest = locations[-1] if locations else {}
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM schedules WHERE source='VSTP' AND train_uid=?",
                (uid,),
            )
            conn.execute(
                """INSERT INTO schedules(train_uid,start_date,end_date,days_runs,stp_indicator,atoc_code,signalling_id,service_code,
                   origin_tiploc,destination_tiploc,origin_name,destination_name,locations_json,source)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?, 'VSTP')""",
                (
                    uid,
                    schedule.get("schedule_start_date"), schedule.get("schedule_end_date"), schedule.get("schedule_days_runs"),
                    schedule.get("CIF_stp_indicator") or schedule.get("stp_indicator"), schedule.get("atoc_code"),
                    schedule.get("signalling_id") or schedule.get("CIF_headcode"), schedule.get("CIF_train_service_code"),
                    origin.get("tiploc_code"), dest.get("tiploc_code"), origin.get("tiploc_code"), dest.get("tiploc_code"),
                    json.dumps(locations, separators=(",", ":")),
                ),
            )


def _download_schedule(username: str, password: str, out_path: Path, progress: Callable[[str, dict], None] | None = None) -> dict:
    errors: list[str] = []
    for url in SCHEDULE_URLS:
        try:
            if progress:
                progress("download-start", {"host": url.split('/')[2]})
            with requests.get(
                url,
                auth=(username, password),
                headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
                allow_redirects=True,
                stream=True,
                timeout=(20, 240),
            ) as r:
                if r.status_code >= 400:
                    raise FeedDownloadError(
                        f"HTTP {r.status_code}. Check that SCHEDULE is enabled under My Feeds in your Network Rail account."
                    )
                tmp = out_path.with_suffix(out_path.suffix + ".download")
                r.raw.decode_content = False
                expected = int(r.headers.get("content-length") or 0)
                total = 0
                last_report = 0
                with tmp.open("wb") as f:
                    while True:
                        chunk = r.raw.read(1024 * 1024)
                        if not chunk:
                            break
                        f.write(chunk)
                        total += len(chunk)
                        if progress and (total - last_report >= 5 * 1024 * 1024 or total == expected):
                            progress("download", {"bytes": total, "expected": expected})
                            last_report = total
                tmp.replace(out_path)
                if out_path.stat().st_size < 1024:
                    raise FeedDownloadError("SCHEDULE download was unexpectedly small; check feed permissions.")
                head = out_path.read_bytes()[:4096]
                if head[:2] != b"\x1f\x8b" and b"{" not in head:
                    raise FeedDownloadError("SCHEDULE response was not gzip/JSON data; check Network Rail SCHEDULE access.")
                if progress:
                    progress("download-complete", {"bytes": out_path.stat().st_size})
                return {"url": url, "bytes": out_path.stat().st_size}
        except Exception as exc:
            errors.append(f"{url.split('/')[2]}: {exc}")
    raise FeedDownloadError(" | ".join(errors))


def _open_schedule(path: Path):
    magic = path.read_bytes()[:2]
    if magic == b"\x1f\x8b":
        return gzip.open(path, "rt", encoding="utf-8-sig", errors="replace")
    return path.open("rt", encoding="utf-8-sig", errors="replace")


def _location_list(schedule: dict) -> list[dict]:
    direct = schedule.get("schedule_location")
    if isinstance(direct, list):
        return direct
    segment = schedule.get("schedule_segment") or {}
    if isinstance(segment, dict) and isinstance(segment.get("schedule_location"), list):
        return segment["schedule_location"]
    return []


def _segment(schedule: dict) -> dict:
    segment = schedule.get("schedule_segment")
    return segment if isinstance(segment, dict) else {}


def _display_name(tiploc: str | None, names: dict[str, str]) -> str | None:
    if not tiploc:
        return None
    return names.get(tiploc) or tiploc


def ingest_schedule_file(schedule_path: Path, store: ScheduleStore, naptan: dict, station_file: Path, progress: Callable[[str, dict], None] | None = None) -> dict:
    tmp_db = store.db_path.with_suffix(".building.sqlite")
    tmp_db.unlink(missing_ok=True)
    build = ScheduleStore(tmp_db)

    tiploc_rows: dict[str, dict] = {}
    schedules_buffer: list[tuple] = []
    schedule_count = 0
    tiploc_count = 0

    with build._connect() as conn, _open_schedule(schedule_path) as f:
        # This is a disposable build database: favour ingest speed, then atomically
        # replace the production SQLite file only after a successful index.
        conn.execute("PRAGMA synchronous=OFF")
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.execute("PRAGMA cache_size=-150000")
        conn.execute("DELETE FROM schedules")
        conn.execute("DELETE FROM tiplocs")
        for line_no, line in enumerate(f, 1):
            if progress and line_no % 10000 == 0:
                progress("index", {"lines": line_no, "schedules": schedule_count, "tiplocs": tiploc_count})
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue

            tip = record.get("TiplocV1") if isinstance(record, dict) else None
            if isinstance(tip, dict):
                code = str(tip.get("tiploc_code") or "").strip().upper()
                if code:
                    tiploc_rows[code] = {
                        "tiploc": code,
                        "stanox": str(tip.get("stanox") or "").strip().zfill(5) if tip.get("stanox") else None,
                        "crs": str(tip.get("crs_code") or "").strip().upper() or None,
                        "description": str(tip.get("tps_description") or tip.get("description") or code).strip(),
                    }
                    tiploc_count += 1
                continue

            sched = record.get("JsonScheduleV1") if isinstance(record, dict) else None
            if not isinstance(sched, dict) or str(sched.get("transaction_type") or "").lower() == "delete":
                continue
            uid = str(sched.get("CIF_train_uid") or "").strip()
            if not uid:
                continue
            segment = _segment(sched)
            locs = _location_list(sched)
            origin = locs[0] if locs else {}
            destination = locs[-1] if locs else {}
            origin_tiploc = str(origin.get("tiploc_code") or "").strip().upper() or None
            destination_tiploc = str(destination.get("tiploc_code") or "").strip().upper() or None
            schedules_buffer.append((
                uid,
                sched.get("schedule_start_date"), sched.get("schedule_end_date"), sched.get("schedule_days_runs"),
                sched.get("CIF_stp_indicator"), sched.get("atoc_code"),
                segment.get("signalling_id") or segment.get("CIF_headcode"),
                segment.get("CIF_train_service_code"),
                origin_tiploc, destination_tiploc,
                origin_tiploc, destination_tiploc,
                json.dumps(locs, separators=(",", ":")),
            ))
            schedule_count += 1
            if len(schedules_buffer) >= 4000:
                conn.executemany(
                    """INSERT INTO schedules(train_uid,start_date,end_date,days_runs,stp_indicator,atoc_code,signalling_id,service_code,
                       origin_tiploc,destination_tiploc,origin_name,destination_name,locations_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    schedules_buffer,
                )
                schedules_buffer.clear()
        if schedules_buffer:
            conn.executemany(
                """INSERT INTO schedules(train_uid,start_date,end_date,days_runs,stp_indicator,atoc_code,signalling_id,service_code,
                   origin_tiploc,destination_tiploc,origin_name,destination_name,locations_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                schedules_buffer,
            )

        by_tiploc = naptan.get("by_tiploc", {})
        by_crs = naptan.get("by_crs", {})
        station_additions: dict[str, dict] = {}
        for code, row in tiploc_rows.items():
            point = by_tiploc.get(code) or (by_crs.get(row["crs"]) if row.get("crs") else None)
            lat = float(point["lat"]) if point else None
            lon = float(point["lon"]) if point else None
            conn.execute(
                "INSERT OR REPLACE INTO tiplocs(tiploc,stanox,crs,description,lat,lon) VALUES(?,?,?,?,?,?)",
                (code, row.get("stanox"), row.get("crs"), row.get("description"), lat, lon),
            )
            if row.get("stanox") and point:
                station_additions[row["stanox"]] = {
                    "name": row.get("description") or point.get("name") or code,
                    "lat": lat,
                    "lon": lon,
                    "tiploc": code,
                    "crs": row.get("crs"),
                    "source": "SCHEDULE TIPLOC + NaPTAN",
                }

        # Resolve human-readable origin/destination names in SQL to avoid
        # loading hundreds of thousands of schedule rows into Python memory.
        conn.execute(
            """UPDATE schedules SET
               origin_name=COALESCE((SELECT description FROM tiplocs WHERE tiploc=schedules.origin_tiploc), origin_tiploc),
               destination_name=COALESCE((SELECT description FROM tiplocs WHERE tiploc=schedules.destination_tiploc), destination_tiploc)"""
        )
        conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('last_sync',?)", (datetime.utcnow().isoformat() + "Z",))
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    if schedule_count == 0:
        tmp_db.unlink(missing_ok=True)
        raise ValueError("SCHEDULE file parsed successfully but contained zero schedule records")

    # Replace the old database only after a successful full ingest. On Windows a
    # short-lived reader may briefly hold the old SQLite file, so retry instead
    # of destroying a good existing index.
    for suffix in ("-wal", "-shm"):
        Path(str(tmp_db) + suffix).unlink(missing_ok=True)
    last_error = None
    for _ in range(30):
        try:
            os.replace(tmp_db, store.db_path)
            last_error = None
            break
        except PermissionError as exc:
            last_error = exc
            time.sleep(0.25)
    if last_error is not None:
        tmp_db.unlink(missing_ok=True)
        raise last_error
    total_stations = merge_station_file(station_file, station_additions)
    return {
        "schedules": schedule_count,
        "tiplocs": len(tiploc_rows),
        "geocoded_stanox": len(station_additions),
        "total_mapped_stanox": total_stations,
    }


def sync_schedule_data(username: str, password: str, data_dir: Path, *, force: bool = False, progress: Callable[[str, dict], None] | None = None) -> dict:
    data_dir.mkdir(parents=True, exist_ok=True)
    store = ScheduleStore(data_dir / "schedule.sqlite")
    schedule_path = data_dir / "schedule-full.dat"

    # Daily schedule file; avoid a huge download on every launch.
    if store.ready() and not force:
        age = time.time() - store.db_path.stat().st_mtime
        if age < 20 * 3600:
            if progress:
                progress("cached", {"schedules": store.count()})
            return {"cached": True, "schedules": store.count(), "last_sync": store.last_sync()}

    # If a previous build finished downloading the large daily file but was
    # closed during indexing, reuse the complete file instead of downloading it
    # again. Partial downloads use a .download suffix and are never reused.
    reuse_download = False
    if schedule_path.exists() and not force:
        try:
            age = time.time() - schedule_path.stat().st_mtime
            head = schedule_path.read_bytes()[:4096]
            reuse_download = schedule_path.stat().st_size > 1024 * 1024 and age < 20 * 3600 and (head[:2] == b"\x1f\x8b" or b"{" in head)
        except OSError:
            reuse_download = False
    if reuse_download:
        download = {"url": "local-cache", "bytes": schedule_path.stat().st_size}
        if progress:
            progress("download-reuse", {"bytes": schedule_path.stat().st_size})
    else:
        download = _download_schedule(username, password, schedule_path, progress=progress)
    if progress:
        progress("naptan", {})
    naptan = ensure_naptan_cache(data_dir / "naptan_rail.json", force=False)
    if progress:
        progress("index-start", {})
    result = ingest_schedule_file(schedule_path, store, naptan, data_dir / "stations.json", progress=progress)
    result.update(download)
    result["cached"] = False
    return result
