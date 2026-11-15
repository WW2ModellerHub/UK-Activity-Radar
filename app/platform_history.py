"""Which platform a train usually uses at a station.

Big stations often announce the platform only minutes before departure, but
the same timetabled train mostly uses the same one day after day. Every
platform reported by the live railway feed is remembered per train and
station; once there are a few days of history and they mostly agree, the
board can say "usually platform 6" before the announcement.
"""
from __future__ import annotations

import sqlite3
import time
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from threading import RLock

MIN_DAYS = 3
MIN_SHARE = 0.6
LOOK_BACK = 20
KEEP_DAYS = 120
CACHE_SECONDS = 600


class PlatformHistory:
    def __init__(self, path: Path | str) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._connection = sqlite3.connect(str(path), check_same_thread=False)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute(
            """CREATE TABLE IF NOT EXISTS observations (
                 train_uid TEXT NOT NULL, tiploc TEXT NOT NULL, service_date TEXT NOT NULL, platform TEXT NOT NULL,
                 PRIMARY KEY (train_uid, tiploc, service_date))"""
        )
        self._connection.execute("DELETE FROM observations WHERE service_date < ?", ((date.today() - timedelta(days=KEEP_DAYS)).isoformat(),))
        self._connection.commit()
        self._cache: dict[tuple[str, str], tuple[float, dict | None]] = {}

    @staticmethod
    def _clean(train_uid, tiploc, platform=None) -> tuple[str, str, str]:
        return str(train_uid or "").strip().upper(), str(tiploc or "").strip().upper(), str(platform or "").strip().upper()

    def record(self, train_uid, service_date, tiploc, platform) -> None:
        uid, code, value = self._clean(train_uid, tiploc, platform)
        day = str(service_date or "").strip()[:10]
        if not (uid and code and value and len(day) == 10 and len(value) <= 4):
            return
        with self._lock:
            self._connection.execute("INSERT OR REPLACE INTO observations VALUES (?,?,?,?)", (uid, code, day, value))
            self._connection.commit()
            self._cache.pop((uid, code), None)

    def usual(self, train_uid, tiploc) -> dict | None:
        """``{"platform", "seen", "of"}`` when the recent days mostly agree."""
        uid, code, _ = self._clean(train_uid, tiploc)
        if not uid or not code:
            return None
        now = time.monotonic()
        with self._lock:
            hit = self._cache.get((uid, code))
            if hit and now - hit[0] < CACHE_SECONDS:
                return hit[1]
            rows = self._connection.execute(
                "SELECT platform FROM observations WHERE train_uid=? AND tiploc=? ORDER BY service_date DESC LIMIT ?",
                (uid, code, LOOK_BACK),
            ).fetchall()
            result = None
            if len(rows) >= MIN_DAYS:
                platform, seen = Counter(row[0] for row in rows).most_common(1)[0]
                if seen / len(rows) >= MIN_SHARE:
                    result = {"platform": platform, "seen": seen, "of": len(rows)}
            self._cache[(uid, code)] = (now, result)
            return result

    def close(self) -> None:
        with self._lock:
            self._connection.close()
