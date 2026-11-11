from __future__ import annotations

import gzip
import json
import os
import sqlite3
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, time as clock_time, timedelta
from pathlib import Path
from typing import Any, Iterable, Callable

import requests

from .reference_data import FeedDownloadError, ensure_naptan_cache, merge_station_file
from .timeutils import london_tz

SCHEDULE_URLS = [
    "https://publicdatafeeds.networkrail.co.uk/ntrod/CifFileAuthenticate?type=CIF_ALL_FULL_DAILY&day=toc-full",
    "https://datafeeds.networkrail.co.uk/ntrod/CifFileAuthenticate?type=CIF_ALL_FULL_DAILY&day=toc-full",
]
USER_AGENT = "UKActivityRadar/1.4.0"


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
    train_category: str | None = None
    power_type: str | None = None
    timing_load: str | None = None
    booked_speed: str | None = None
    operating_characteristics: str | None = None
    running_date: str | None = None
    schedule_id: int | None = None


class ScheduleStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # The national SCHEDULE file identifies every timing point, while
        # NaPTAN only geocodes passenger stations. The independently cached
        # rail geography fills those non-station TIPLOC gaps so map paths use
        # junctions and timing points instead of long station-to-station
        # chords. It is loaded lazily because reference sync can finish after
        # this store is constructed during application startup.
        self.rail_geo_path = self.db_path.parent / "public_rail_geo.json"
        self._rail_geo_lock = threading.RLock()
        self._rail_geo_mtime_ns: int | None = None
        self._rail_geo_by_tiploc: dict[str, dict[str, Any]] = {}
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
                    train_category TEXT,
                    power_type TEXT,
                    timing_load TEXT,
                    booked_speed TEXT,
                    operating_characteristics TEXT,
                    source TEXT DEFAULT 'SCHEDULE'
                );
                CREATE INDEX IF NOT EXISTS idx_sched_uid ON schedules(train_uid);
                CREATE INDEX IF NOT EXISTS idx_sched_dates ON schedules(train_uid,start_date,end_date);
                CREATE INDEX IF NOT EXISTS idx_sched_active_dates ON schedules(start_date,end_date);
                CREATE INDEX IF NOT EXISTS idx_sched_headcode_normalized ON schedules(UPPER(TRIM(signalling_id)));
                CREATE TABLE IF NOT EXISTS schedule_calls(
                    id INTEGER PRIMARY KEY,
                    schedule_id INTEGER NOT NULL,
                    tiploc TEXT NOT NULL,
                    scheduled_arrival TEXT,
                    scheduled_departure TEXT,
                    public_arrival TEXT,
                    public_departure TEXT,
                    platform TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_schedule_calls_tiploc ON schedule_calls(tiploc,schedule_id);
                """
            )
            existing = {row[1] for row in conn.execute("PRAGMA table_info(schedules)")}
            for column in ("train_category", "power_type", "timing_load", "booked_speed", "operating_characteristics"):
                if column not in existing:
                    conn.execute(f"ALTER TABLE schedules ADD COLUMN {column} TEXT")

    def _fallback_tiploc_geography(self) -> dict[str, dict[str, Any]]:
        """Return cached broad TIPLOC geography without making a network call."""
        try:
            mtime_ns = self.rail_geo_path.stat().st_mtime_ns
        except OSError:
            return {}
        with self._rail_geo_lock:
            if self._rail_geo_mtime_ns == mtime_ns:
                return self._rail_geo_by_tiploc
            try:
                payload = json.loads(self.rail_geo_path.read_text(encoding="utf-8"))
                raw = payload.get("by_tiploc", {}) if isinstance(payload, dict) else {}
                parsed: dict[str, dict[str, Any]] = {}
                for raw_code, raw_point in raw.items():
                    if not isinstance(raw_point, dict):
                        continue
                    code = str(raw_code or "").strip().upper()
                    try:
                        lat, lon = float(raw_point["lat"]), float(raw_point["lon"])
                    except (KeyError, TypeError, ValueError):
                        continue
                    if not code or not (49.0 <= lat <= 61.8 and -9.9 <= lon <= 3.5):
                        continue
                    parsed[code] = {
                        "name": str(raw_point.get("name") or code),
                        "lat": lat,
                        "lon": lon,
                        "source": str(raw_point.get("source") or "Cached rail geography"),
                    }
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                # Keep the previous last-known-good mapping when a concurrent
                # atomic refresh briefly makes the new file unreadable.
                return self._rail_geo_by_tiploc
            self._rail_geo_mtime_ns = mtime_ns
            self._rail_geo_by_tiploc = parsed
            return parsed

    def location_geography(self, codes: Iterable[str]) -> dict[str, dict[str, Any]]:
        """Resolve timetable TIPLOCs using official rows plus broad rail data.

        The SQLite timetable remains authoritative for names and identifiers.
        Coordinates missing from its passenger-station geography are filled
        from the local rail reference cache. No request is made from this hot
        path, so live updates are not held up by a third-party service.
        """
        wanted = {str(code or "").strip().upper() for code in codes} - {""}
        if not wanted:
            return {}
        placeholders = ",".join("?" for _ in wanted)
        result: dict[str, dict[str, Any]] = {}
        try:
            with self._connect() as conn:
                for row in conn.execute(
                    f"SELECT tiploc,description,lat,lon FROM tiplocs WHERE tiploc IN ({placeholders})",
                    tuple(wanted),
                ):
                    code = str(row["tiploc"] or "").strip().upper()
                    result[code] = {
                        "name": str(row["description"] or code),
                        "lat": row["lat"],
                        "lon": row["lon"],
                        "source": "Network Rail timetable geography",
                    }
        except (OSError, sqlite3.Error):
            result = {}

        fallback = self._fallback_tiploc_geography()
        for code in wanted:
            point = result.get(code)
            missing_coordinate = (
                point is None or point.get("lat") is None or point.get("lon") is None
            )
            if not missing_coordinate:
                point["lat"], point["lon"] = float(point["lat"]), float(point["lon"])
                continue
            alternate = fallback.get(code)
            if alternate is None:
                continue
            result[code] = {
                "name": (point or {}).get("name") or alternate["name"],
                "lat": float(alternate["lat"]),
                "lon": float(alternate["lon"]),
                "source": alternate["source"],
            }
        return result

    def call_index_ready(self) -> bool:
        try:
            with self._connect() as conn:
                meta = dict(conn.execute("SELECT key,value FROM meta WHERE key IN ('schedule_calls_date','schedule_calls_version')"))
            return meta.get('schedule_calls_version') == '2' and meta.get('schedule_calls_date') == datetime.now(london_tz()).date().isoformat()
        except sqlite3.Error:
            return False

    @staticmethod
    def _rebuild_call_index_on(conn: sqlite3.Connection, active_date: str | None = None) -> int:
        active_date=active_date or datetime.now(london_tz()).date().isoformat()
        service_date = date.fromisoformat(active_date)
        # Keep calls from trains spanning midnight and tomorrow's early
        # departures. Consumers still resolve the actual service day and STP
        # variation; this is only a lookup index, not a list of running trains.
        first_date = (service_date - timedelta(days=1)).isoformat()
        last_date = (service_date + timedelta(days=1)).isoformat()
        conn.execute("DELETE FROM schedule_calls")
        conn.execute(
            """INSERT INTO schedule_calls(schedule_id,tiploc,scheduled_arrival,scheduled_departure,public_arrival,public_departure,platform)
               SELECT s.id,
                      UPPER(TRIM(json_extract(j.value,'$.tiploc_code'))),
                      json_extract(j.value,'$.scheduled_arrival'),
                      json_extract(j.value,'$.scheduled_departure'),
                      json_extract(j.value,'$.public_arrival'),
                      json_extract(j.value,'$.public_departure'),
                      json_extract(j.value,'$.platform')
               FROM schedules AS s, json_each(s.locations_json) AS j
               WHERE (s.start_date IS NULL OR s.start_date='' OR s.start_date<=?)
                 AND (s.end_date IS NULL OR s.end_date='' OR s.end_date>=?)
                 AND json_extract(j.value,'$.tiploc_code') IS NOT NULL""",
            (last_date,first_date),
        )
        count=int(conn.execute("SELECT COUNT(*) FROM schedule_calls").fetchone()[0])
        conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('schedule_calls_version','2')")
        conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('schedule_calls_date',?)",(active_date,))
        conn.commit()
        return count

    def ensure_call_index(self) -> int:
        if self.call_index_ready():
            return 0
        with self._connect() as conn:
            conn.execute("PRAGMA busy_timeout=60000")
            return self._rebuild_call_index_on(conn)

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
        uid = str(body.get("train_uid") or "").strip().upper()
        if not uid:
            return None
        # TRUST's originating departure timestamp is more reliable around BST
        # midnight than its historical, truncated tp_origin_timestamp field.
        service_date = (
            self.service_date(body.get("origin_dep_timestamp"))
            or self.service_date(body.get("tp_origin_timestamp"))
            or datetime.now(london_tz()).date()
        )
        return self.lookup_uid(uid, running_date=service_date)

    @staticmethod
    def service_date(value: Any) -> date | None:
        """Normalize a TRUST service date or epoch timestamp to a UK date."""
        if isinstance(value, datetime):
            return value.astimezone(london_tz()).date() if value.tzinfo else value.date()
        if isinstance(value, date):
            return value
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            if raw.replace(".", "", 1).isdigit():
                seconds = float(raw)
                if seconds <= 0:
                    return None
                if seconds >= 100_000_000_000:
                    seconds /= 1000
                return datetime.fromtimestamp(seconds, tz=london_tz()).date()
            if len(raw) == 10:
                return date.fromisoformat(raw)
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return parsed.astimezone(london_tz()).date() if parsed.tzinfo else parsed.date()
        except (OverflowError, OSError, TypeError, ValueError):
            return None

    @staticmethod
    def _runs_on(row: sqlite3.Row, running_date: date) -> bool:
        days = str(row["days_runs"] or "").strip()
        return not days or (len(days) == 7 and days[running_date.weekday()] == "1")

    def effective_rows(
        self,
        running_date: date,
        *,
        train_uids: Iterable[str] | None = None,
        headcode: str | None = None,
    ) -> list[sqlite3.Row]:
        """Return the applicable schedule for each UID on its origin date.

        Fetch every variation for candidate UIDs before filtering locations or
        headcodes: cancellations often have neither, and an overlay can remove
        a station call or change the headcode. Filtering those first resurrects
        the permanent schedule which the variation superseded.
        """
        day = running_date.isoformat()
        code = str(headcode or "").strip().upper()
        with self._connect() as conn:
            uids = None if train_uids is None else sorted({
                str(uid or "").strip().upper() for uid in train_uids if str(uid or "").strip()
            })
            if code:
                matched = {row[0] for row in conn.execute(
                    """SELECT DISTINCT train_uid FROM schedules
                       WHERE UPPER(TRIM(signalling_id))=?
                         AND (start_date IS NULL OR start_date='' OR start_date<=?)
                         AND (end_date IS NULL OR end_date='' OR end_date>=?)""",
                    (code, day, day),
                )}
                uids = sorted(matched if uids is None else matched.intersection(uids))
            if uids == []:
                return []
            rows = []
            batches = [None] if uids is None else [uids[i:i + 500] for i in range(0, len(uids), 500)]
            for batch in batches:
                sql = """SELECT * FROM schedules
                         WHERE (start_date IS NULL OR start_date='' OR start_date<=?)
                           AND (end_date IS NULL OR end_date='' OR end_date>=?)"""
                params: list[Any] = [day, day]
                if batch is not None:
                    sql += " AND train_uid IN (" + ",".join("?" for _ in batch) + ")"
                    params.extend(batch)
                rows.extend(conn.execute(sql, params).fetchall())
        applicable: dict[str, sqlite3.Row] = {}
        priorities = {"C": 0, "N": 1, "O": 2, "P": 3, "": 3}

        def priority(row: sqlite3.Row) -> tuple:
            return (
                priorities.get(str(row["stp_indicator"] or "").strip().upper(), 4),
                0 if row["source"] == "VSTP" else 1,
                # More recent/specific revisions win within the same STP kind.
                str(row["start_date"] or ""),
                int(row["id"]),
            )

        for row in rows:
            if not self._runs_on(row, running_date):
                continue
            uid = str(row["train_uid"] or "").strip()
            previous = applicable.get(uid)
            rank = priority(row)
            previous_rank = priority(previous) if previous is not None else None
            if previous is None or rank[:2] < previous_rank[:2] or (
                rank[:2] == previous_rank[:2] and rank[2:] > previous_rank[2:]
            ):
                applicable[uid] = row
        return [
            row for row in applicable.values()
            if str(row["stp_indicator"] or "").strip().upper() != "C"
            and (not code or str(row["signalling_id"] or "").strip().upper() == code)
        ]

    def lookup_uid(self, uid: str, *, running_date: date | None = None) -> ScheduleInfo | None:
        service_date = running_date or datetime.now(london_tz()).date()
        rows = self.effective_rows(service_date, train_uids=[uid])
        return self._info_from_row(rows[0], service_date) if rows else None

    def route_points(
        self,
        schedule_id: int | None,
        from_tiploc: str | None,
        to_tiploc: str | None,
    ) -> list[list[float]]:
        """Return mapped timetable points between two reporting locations.

        TRUST tells us the confirmed reporting point and the next reporting
        point, but not a continuous GPS trace.  The SCHEDULE index normally
        contains junctions and timing points between them.  Keeping those
        points gives the presentation layer a substantially more railway-like
        path while retaining the confirmed endpoints as the source of truth.
        """
        if not schedule_id:
            return []
        start = str(from_tiploc or "").strip().upper()
        end = str(to_tiploc or "").strip().upper()
        if not start or not end:
            return []
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT locations_json FROM schedules WHERE id=?", (int(schedule_id),)
                ).fetchone()
                if row is None:
                    return []
                try:
                    locations = json.loads(row["locations_json"] or "[]")
                except (TypeError, json.JSONDecodeError):
                    return []
                codes = [
                    str(item.get("tiploc_code") or "").strip().upper()
                    for item in locations if isinstance(item, dict)
                ]
                try:
                    first = codes.index(start)
                    last = codes.index(end, first + 1)
                except ValueError:
                    return []
                wanted = [code for code in codes[first:last + 1] if code]
                if not wanted:
                    return []
        except (OSError, sqlite3.Error, TypeError, ValueError):
            return []
        geography = self.location_geography(wanted)
        geo = {
            code: [float(point["lat"]), float(point["lon"])]
            for code, point in geography.items()
            if point.get("lat") is not None and point.get("lon") is not None
        }
        points: list[list[float]] = []
        for code in wanted:
            point = geo.get(code)
            if point and (not points or point != points[-1]):
                points.append(point)
        return points

    def initial_estimate_leg(self, schedule_id: int | None, running_date: date) -> dict | None:
        """Resolve the first mapped timed leg for a newly activated service."""
        if not schedule_id:
            return None
        try:
            with self._connect() as conn:
                row = conn.execute("SELECT * FROM schedules WHERE id=?", (int(schedule_id),)).fetchone()
                if row is None:
                    return None
                locations = self.timed_locations(row, running_date)
                codes = {
                    str(item.get("tiploc_code") or "").strip().upper()
                    for item in locations if isinstance(item, dict)
                } - {""}
                if not codes:
                    return None
        except (OSError, sqlite3.Error, TypeError, ValueError):
            return None
        geo = self.location_geography(codes)

        mapped: list[tuple[dict, dict, datetime]] = []
        for location in locations:
            code = str(location.get("tiploc_code") or "").strip().upper()
            point = geo.get(code)
            when = (
                location.get("_departure_at")
                or location.get("_pass_at")
                or location.get("_arrival_at")
            )
            if point and isinstance(when, datetime):
                mapped.append((location, point, when))
        for index in range(len(mapped) - 1):
            first_location, first_point, started_at = mapped[index]
            second_location, second_point, due_at = mapped[index + 1]
            if due_at <= started_at or due_at - started_at > timedelta(hours=2):
                continue
            first_code = str(first_location.get("tiploc_code") or "").strip().upper()
            second_code = str(second_location.get("tiploc_code") or "").strip().upper()
            path = self.route_points(schedule_id, first_code, second_code)
            if not path:
                path = [[first_point["lat"], first_point["lon"]], [second_point["lat"], second_point["lon"]]]
            return {
                "lat": first_point["lat"], "lon": first_point["lon"],
                "next_lat": second_point["lat"], "next_lon": second_point["lon"],
                "next_name": second_point["name"], "next_tiploc": second_code, "path": path,
                "started_at": started_at, "due_at": due_at,
                "tiploc": first_code,
            }
        return None

    def estimate_leg_from(
        self,
        schedule_id: int | None,
        running_date: date,
        from_tiploc: str | None,
        observed_at: datetime,
        *,
        event_code: str | None = None,
        berth_offset_seconds: int | float | None = None,
    ) -> dict | None:
        """Build the timetable leg occupied at a confirmed signalling report.

        SMART maps a TD berth step to an arrival/departure at a reporting
        location and supplies a signed offset from the raw berth timestamp to
        the corresponding TRUST event.  Arrival steps therefore belong to the
        *approach* leg, while departure steps belong to the following leg.  A
        previous implementation always started the following leg at the raw TD
        timestamp; at locations such as Narborough (departure offset -111s)
        that visibly left a train several miles behind its real progress.

        Callers without SMART event metadata retain the conservative legacy
        behaviour: start the following booked leg at ``observed_at``.
        """
        if not schedule_id or not from_tiploc:
            return None
        start_code = str(from_tiploc).strip().upper()
        try:
            with self._connect() as conn:
                row = conn.execute("SELECT * FROM schedules WHERE id=?", (int(schedule_id),)).fetchone()
                if row is None:
                    return None
                locations = self.timed_locations(row, running_date)
                codes = {
                    str(item.get("tiploc_code") or "").strip().upper()
                    for item in locations if isinstance(item, dict)
                } - {""}
                if not codes:
                    return None
        except (OSError, sqlite3.Error, TypeError, ValueError):
            return None
        geo = self.location_geography(codes)

        def call_time(location: dict) -> datetime | None:
            return location.get("_departure_at") or location.get("_pass_at") or location.get("_arrival_at")

        event = str(event_code or "").strip().upper()
        event_kind = "arrival" if event in {"A", "C"} else "departure" if event in {"B", "D"} else ""
        try:
            offset = max(-900, min(900, int(float(berth_offset_seconds or 0))))
        except (TypeError, ValueError):
            offset = 0
        recorded_at = observed_at + timedelta(seconds=offset) if event_kind else observed_at

        matching_indices = [
            index for index, location in enumerate(locations)
            if str(location.get("tiploc_code") or "").strip().upper() == start_code
        ]
        # Circular/duplicated schedules can call at one TIPLOC more than once.
        # Prefer the occurrence whose booked event is nearest the corrected
        # report time instead of silently snapping to the first occurrence.
        def event_time_for(location: dict) -> datetime | None:
            if event_kind == "arrival":
                return location.get("_arrival_at") or location.get("_pass_at") or call_time(location)
            if event_kind == "departure":
                return location.get("_departure_at") or location.get("_pass_at") or call_time(location)
            return call_time(location)

        start_index = min(
            matching_indices,
            key=lambda index: abs((event_time_for(locations[index]) - recorded_at).total_seconds())
            if isinstance(event_time_for(locations[index]), datetime) else float("inf"),
            default=None,
        )
        if start_index is None:
            return None
        start_location = locations[start_index]
        start_point = geo.get(start_code)
        start_time = event_time_for(start_location)
        if not start_point or not isinstance(start_time, datetime):
            return None

        if event_kind == "arrival":
            for previous_location in reversed(locations[:start_index]):
                previous_code = str(previous_location.get("tiploc_code") or "").strip().upper()
                previous_point = geo.get(previous_code)
                previous_time = call_time(previous_location)
                if not previous_point or not isinstance(previous_time, datetime) or previous_time >= start_time:
                    continue
                runtime = start_time - previous_time
                if runtime < timedelta(seconds=30) or runtime > timedelta(hours=1):
                    continue
                path = self.route_points(schedule_id, previous_code, start_code)
                first = [previous_point["lat"], previous_point["lon"]]
                last = [start_point["lat"], start_point["lon"]]
                if not path or path[0] != first:
                    path.insert(0, first)
                if path[-1] != last:
                    path.append(last)
                return {
                    "lat": previous_point["lat"], "lon": previous_point["lon"],
                    "next_lat": start_point["lat"], "next_lon": start_point["lon"],
                    "next_name": start_point["name"], "next_tiploc": start_code, "path": path,
                    "started_at": recorded_at - runtime, "due_at": recorded_at,
                    "tiploc": previous_code, "report_tiploc": start_code,
                    "event_kind": event_kind, "berth_offset_seconds": offset,
                }
            return None

        for next_location in locations[start_index + 1:]:
            next_code = str(next_location.get("tiploc_code") or "").strip().upper()
            next_point = geo.get(next_code)
            next_time = call_time(next_location)
            if not next_point or not isinstance(next_time, datetime) or next_time <= start_time:
                continue
            runtime = next_time - start_time
            if runtime < timedelta(seconds=30) or runtime > timedelta(hours=1):
                continue
            path = self.route_points(schedule_id, start_code, next_code)
            first = [start_point["lat"], start_point["lon"]]
            last = [next_point["lat"], next_point["lon"]]
            if not path or path[0] != first:
                path.insert(0, first)
            if path[-1] != last:
                path.append(last)
            return {
                "lat": start_point["lat"], "lon": start_point["lon"],
                "next_lat": next_point["lat"], "next_lon": next_point["lon"],
                "next_name": next_point["name"], "next_tiploc": next_code, "path": path,
                "started_at": recorded_at, "due_at": recorded_at + runtime,
                "tiploc": start_code, "report_tiploc": start_code,
                "event_kind": event_kind or None, "berth_offset_seconds": offset,
            }
        return None

    def estimate_departure_leg_after_arrival(
        self,
        schedule_id: int | None,
        running_date: date,
        at_tiploc: str | None,
        observed_arrival_at: datetime,
    ) -> dict | None:
        """Start the onward leg after the service's published station dwell.

        TRUST often reports an arrival several minutes before its next
        departure/pass event.  Holding the marker until that later message can
        leave a train visibly behind.  The actual arrival is authoritative;
        only the short booked dwell and onward railway path are inferred.
        """
        if not schedule_id or not at_tiploc:
            return None
        code = str(at_tiploc).strip().upper()
        try:
            with self._connect() as conn:
                row = conn.execute("SELECT * FROM schedules WHERE id=?", (int(schedule_id),)).fetchone()
                if row is None:
                    return None
                locations = self.timed_locations(row, running_date)
        except (OSError, sqlite3.Error, TypeError, ValueError):
            return None

        candidates: list[tuple[float, datetime]] = []
        for location in locations:
            if str(location.get("tiploc_code") or "").strip().upper() != code:
                continue
            arrival = location.get("_arrival_at")
            departure = location.get("_departure_at")
            if not isinstance(arrival, datetime) or not isinstance(departure, datetime):
                continue
            dwell = departure - arrival
            if dwell < timedelta(0) or dwell > timedelta(minutes=30):
                continue
            candidates.append((abs((arrival - observed_arrival_at).total_seconds()), observed_arrival_at + dwell))
        if not candidates:
            return None
        _, estimated_departure = min(candidates, key=lambda item: item[0])
        return self.estimate_leg_from(
            schedule_id, running_date, code, estimated_departure,
            event_code="D", berth_offset_seconds=0,
        )

    @staticmethod
    def _clock_minutes(value: Any) -> float | None:
        raw = str(value or "").strip().upper().replace(":", "")
        half = raw.endswith("H")
        if half:
            raw = raw[:-1]
        if len(raw) == 3:
            raw = "0" + raw
        if len(raw) != 4 or not raw.isdigit():
            return None
        hour, minute = int(raw[:2]), int(raw[2:])
        if hour > 23 or minute > 59:
            return None
        return hour * 60 + minute + (0.5 if half else 0)

    @classmethod
    def timed_locations(cls, row: sqlite3.Row, running_date: date) -> list[dict]:
        """Attach actual call dates without wrapping yesterday into tomorrow."""
        try:
            locations = json.loads(row["locations_json"] or "[]")
        except (TypeError, json.JSONDecodeError):
            return []
        if not isinstance(locations, list):
            return []
        midnight = datetime.combine(running_date, clock_time(), tzinfo=london_tz())
        elapsed_day = 0
        previous_minutes = None
        result = []
        for item in locations:
            if not isinstance(item, dict):
                continue
            location = dict(item)
            for kind in ("arrival", "pass", "departure"):
                minutes = cls._clock_minutes(item.get(f"scheduled_{kind}"))
                if minutes is None and kind != "pass":
                    minutes = cls._clock_minutes(item.get(f"public_{kind}"))
                if minutes is None:
                    continue
                if previous_minutes is not None and minutes < previous_minutes:
                    elapsed_day += 1
                previous_minutes = minutes
                location[f"_{kind}_at"] = midnight + timedelta(days=elapsed_day, minutes=minutes)
            for kind in ("arrival", "departure"):
                minutes = cls._clock_minutes(item.get(f"public_{kind}"))
                anchor = location.get(f"_{kind}_at")
                if minutes is None or anchor is None:
                    continue
                candidates = [midnight + timedelta(days=elapsed_day + offset, minutes=minutes) for offset in (-1, 0, 1)]
                location[f"_public_{kind}_at"] = min(candidates, key=lambda value: abs((value - anchor).total_seconds()))
            result.append(location)
        return result

    @staticmethod
    def _info_from_row(row: sqlite3.Row, running_date: date | None = None) -> ScheduleInfo:
        return ScheduleInfo(
            train_uid=row["train_uid"],
            signalling_id=row["signalling_id"],
            atoc_code=row["atoc_code"],
            service_code=row["service_code"],
            origin=row["origin_name"],
            destination=row["destination_name"],
            origin_tiploc=row["origin_tiploc"],
            destination_tiploc=row["destination_tiploc"],
            train_category=row["train_category"],
            power_type=row["power_type"],
            timing_load=row["timing_load"],
            booked_speed=row["booked_speed"],
            operating_characteristics=row["operating_characteristics"],
            running_date=running_date.isoformat() if running_date else None,
            schedule_id=int(row["id"]),
        )

    def lookup_headcode(
        self,
        headcode: str,
        *,
        running_date: date | None = None,
        at_time: datetime | None = None,
        tiploc: str | None = None,
        stanox: str | None = None,
        atoc_code: str | None = None,
    ) -> ScheduleInfo | None:
        """Resolve a live TD describer to today's most plausible schedule.

        TD messages do not contain the TRUST train UID.  The current railway
        location is therefore the strongest discriminator, followed by the
        closest published call time.  This avoids attaching an arbitrary old
        service merely because it reused the same four-character headcode.
        """
        code = str(headcode or "").strip().upper()
        if not code:
            return None
        now = at_time or datetime.now(london_tz())
        if now.tzinfo is None:
            now = now.replace(tzinfo=london_tz())
        else:
            now = now.astimezone(london_tz())
        location_codes = {str(tiploc or "").strip().upper()} - {""}
        if stanox:
            with self._connect() as conn:
                location_codes.update(str(row[0]).strip().upper() for row in conn.execute(
                    "SELECT tiploc FROM tiplocs WHERE stanox=?", (str(stanox).strip().zfill(5),),
                ))
        # A four-character signalling ID is reused across the network and
        # across service days. Without the live berth's TIPLOC it is not an
        # identity and must never be used to attach a passenger journey.
        if not location_codes:
            return None
        dates = [running_date] if running_date else [now.date() - timedelta(days=1), now.date(), now.date() + timedelta(days=1)]
        operator = str(atoc_code or "").strip().upper()
        ranked: list[tuple[float, sqlite3.Row, date]] = []
        for service_date in dates:
            for row in self.effective_rows(service_date, headcode=code):
                if operator and str(row["atoc_code"] or "").strip().upper() != operator:
                    continue
                distances = []
                for location in self.timed_locations(row, service_date):
                    if str(location.get("tiploc_code") or "").strip().upper() not in location_codes:
                        continue
                    for kind in ("arrival", "departure", "pass"):
                        event_time = location.get(f"_{kind}_at")
                        if event_time is not None:
                            distances.append(abs((event_time - now).total_seconds()) / 60)
                distance = min(distances, default=float("inf"))
                # A headcode is reused throughout the day. A location hours
                # away in the timetable is not a verified service identity.
                if distance <= 180:
                    ranked.append((distance, row, service_date))
        if not ranked:
            return None
        ranked.sort(key=lambda item: item[0])
        if len(ranked) > 1 and ranked[1][0] - ranked[0][0] < 10:
            return None
        return self._info_from_row(ranked[0][1], ranked[0][2])

    def upsert_vstp(self, schedule: dict) -> None:
        # VSTP schema varies slightly from the overnight JSON; normalize the
        # useful fields and retain the schedule as a short-lived database row.
        uid = str(schedule.get("CIF_train_uid") or schedule.get("train_uid") or schedule.get("schedule_id") or "").strip()
        if not uid:
            return
        segment = schedule.get("schedule_segment") if isinstance(schedule.get("schedule_segment"), dict) else schedule
        locations = schedule.get("schedule_location") or segment.get("schedule_location") or schedule.get("locations") or []
        if not isinstance(locations, list):
            locations = []
        origin = locations[0] if locations else {}
        dest = locations[-1] if locations else {}
        start = schedule.get("schedule_start_date")
        indicator = schedule.get("CIF_stp_indicator") or schedule.get("stp_indicator")
        with self._connect() as conn:
            # One UID may have distinct short-notice variations on different
            # dates. Replacing tomorrow's row must not delete today's service.
            old_ids=[row[0] for row in conn.execute(
                "SELECT id FROM schedules WHERE source='VSTP' AND train_uid=? AND COALESCE(start_date,'')=? AND COALESCE(stp_indicator,'')=?",
                (uid, start or '', indicator or ''),
            ).fetchall()]
            if old_ids:
                conn.executemany("DELETE FROM schedule_calls WHERE schedule_id=?",((row_id,) for row_id in old_ids))
                conn.executemany("DELETE FROM schedules WHERE id=?", ((row_id,) for row_id in old_ids))
            if str(schedule.get('transaction_type') or '').lower() == 'delete':
                return
            cursor=conn.execute(
                """INSERT INTO schedules(train_uid,start_date,end_date,days_runs,stp_indicator,atoc_code,signalling_id,service_code,
                   origin_tiploc,destination_tiploc,origin_name,destination_name,locations_json,train_category,power_type,
                   timing_load,booked_speed,operating_characteristics,source)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'VSTP')""",
                (
                    uid,
                    start, schedule.get("schedule_end_date"), schedule.get("schedule_days_runs"),
                    indicator, schedule.get("atoc_code"),
                    segment.get("signalling_id") or segment.get("CIF_headcode") or schedule.get("signalling_id") or schedule.get("CIF_headcode"),
                    segment.get("CIF_train_service_code") or schedule.get("CIF_train_service_code"),
                    origin.get("tiploc_code"), dest.get("tiploc_code"), origin.get("tiploc_code"), dest.get("tiploc_code"),
                    json.dumps(locations, separators=(",", ":")),
                    segment.get("CIF_train_category"), segment.get("CIF_power_type"), segment.get("CIF_timing_load"),
                    segment.get("CIF_speed"), segment.get("CIF_operating_characteristics"),
                ),
            )
            schedule_id=cursor.lastrowid
            conn.executemany(
                """INSERT INTO schedule_calls(schedule_id,tiploc,scheduled_arrival,scheduled_departure,public_arrival,public_departure,platform)
                   VALUES(?,?,?,?,?,?,?)""",
                [(
                    schedule_id, str(loc.get('tiploc_code') or '').strip().upper(),
                    loc.get('scheduled_arrival'),loc.get('scheduled_departure'),
                    loc.get('public_arrival'),loc.get('public_departure'),loc.get('platform'),
                ) for loc in locations if str(loc.get('tiploc_code') or '').strip()]
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


def _remove_build_sidecars(db_path: Path, *, attempts: int = 1) -> bool:
    """Remove disposable SQLite journals without failing a completed build.

    A virus scanner or short-lived diagnostic reader can retain a Windows file
    handle after SQLite has checkpointed and closed the build database. Once
    journal_mode has returned to DELETE, these sidecars contain no required
    data and can safely be left for the next maintenance pass.
    """
    all_removed = True
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(db_path) + suffix)
        for attempt in range(max(1, attempts)):
            try:
                sidecar.unlink(missing_ok=True)
                break
            except OSError:
                if attempt + 1 < attempts:
                    time.sleep(0.1)
        if sidecar.exists():
            all_removed = False
    return all_removed


def ingest_schedule_file(schedule_path: Path, store: ScheduleStore, naptan: dict, station_file: Path, progress: Callable[[str, dict], None] | None = None) -> dict:
    tmp_db = store.db_path.with_suffix(".building.sqlite")
    if not _remove_build_sidecars(tmp_db, attempts=30):
        raise PermissionError(f"A previous schedule build journal is still locked: {tmp_db}")
    for attempt in range(30):
        try:
            tmp_db.unlink(missing_ok=True)
            break
        except PermissionError:
            if attempt == 29:
                raise
            time.sleep(0.1)
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
                segment.get("CIF_train_category"), segment.get("CIF_power_type"), segment.get("CIF_timing_load"),
                segment.get("CIF_speed"), segment.get("CIF_operating_characteristics"),
            ))
            schedule_count += 1
            if len(schedules_buffer) >= 4000:
                conn.executemany(
                    """INSERT INTO schedules(train_uid,start_date,end_date,days_runs,stp_indicator,atoc_code,signalling_id,service_code,
                       origin_tiploc,destination_tiploc,origin_name,destination_name,locations_json,train_category,power_type,
                       timing_load,booked_speed,operating_characteristics) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    schedules_buffer,
                )
                schedules_buffer.clear()
        if schedules_buffer:
            conn.executemany(
                """INSERT INTO schedules(train_uid,start_date,end_date,days_runs,stp_indicator,atoc_code,signalling_id,service_code,
                   origin_tiploc,destination_tiploc,origin_name,destination_name,locations_json,train_category,power_type,
                   timing_load,booked_speed,operating_characteristics) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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
        ScheduleStore._rebuild_call_index_on(conn)
        conn.commit()
        # Make the completed build self-contained before its atomic handoff.
        # Switching back to DELETE also removes WAL dependence; retry briefly
        # for Windows scanners/readers that may have opened the disposable DB.
        journal_ready = False
        journal_error: Exception | None = None
        for _ in range(30):
            try:
                checkpoint = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                mode = conn.execute("PRAGMA journal_mode=DELETE").fetchone()
                if (not checkpoint or int(checkpoint[0]) == 0) and mode and str(mode[0]).lower() == "delete":
                    journal_ready = True
                    break
            except sqlite3.Error as exc:
                journal_error = exc
            time.sleep(0.25)
        if not journal_ready:
            raise RuntimeError(f"Could not finalise the schedule build journal: {journal_error or 'database remained busy'}")

    if schedule_count == 0:
        tmp_db.unlink(missing_ok=True)
        raise ValueError("SCHEDULE file parsed successfully but contained zero schedule records")

    # Replace the old database only after a successful full ingest. On Windows a
    # short-lived reader may briefly hold the old SQLite file, so retry instead
    # of destroying a good existing index.
    _remove_build_sidecars(tmp_db)
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
    # VSTP writes short-notice schedules into the same database.  Those rows
    # make ``ready()`` true, but they are not the national daily timetable.
    # Only a completed full ingest writes ``last_sync``.
    full_sync = store.last_sync()
    if store.ready() and full_sync and not force:
        age = time.time() - store.db_path.stat().st_mtime
        if age < 20 * 3600:
            if progress:
                progress("cached", {"schedules": store.count()})
            return {"cached": True, "schedules": store.count(), "last_sync": full_sync}

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
