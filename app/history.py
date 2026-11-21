"""What the radar has seen, kept day by day.

Every few minutes the live state is sampled:

* trains: one row per train per day with its latest and worst lateness and
  whether it was cancelled, so we can say "this train is usually 6 minutes
  late" and give daily punctuality by operator;
* unusual finds (Discover): one row per thing per day, so each day has a
  permanent archive page of the steam trains, test trains and military
  aircraft that were out.

Only what was observed is stored; nothing is back-filled or estimated.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from threading import RLock
from zoneinfo import ZoneInfo

LONDON = ZoneInfo("Europe/London")
KEEP_DAYS = 400
LATE_MINUTES = 5


def today() -> str:
    return datetime.now(LONDON).date().isoformat()


class HistoryStore:
    def __init__(self, path: Path | str) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(
            """CREATE TABLE IF NOT EXISTS train_days (
                 day TEXT NOT NULL, train_key TEXT NOT NULL, headcode TEXT, operator TEXT,
                 origin TEXT, destination TEXT, last_delay INTEGER, worst_delay INTEGER,
                 cancelled INTEGER NOT NULL DEFAULT 0, samples INTEGER NOT NULL DEFAULT 0,
                 PRIMARY KEY (day, train_key));
               CREATE INDEX IF NOT EXISTS train_days_key ON train_days(train_key, day);
               CREATE TABLE IF NOT EXISTS sightings (
                 day TEXT NOT NULL, item_id TEXT NOT NULL, type TEXT, name TEXT, category TEXT,
                 subtype TEXT, badge TEXT, explanation TEXT, place TEXT, score INTEGER,
                 first_seen TEXT, last_seen TEXT, PRIMARY KEY (day, item_id));"""
        )
        cutoff = (date.today() - timedelta(days=KEEP_DAYS)).isoformat()
        self._db.execute("DELETE FROM train_days WHERE day < ?", (cutoff,))
        self._db.execute("DELETE FROM sightings WHERE day < ?", (cutoff,))
        self._db.commit()

    # ---- recording -------------------------------------------------------
    @staticmethod
    def train_key(train) -> str | None:
        """The same timetabled service from day to day: its UID, else headcode and route."""
        uid = str(getattr(train, "train_uid", "") or "").strip().upper()
        if uid:
            return f"uid:{uid}"
        code = str(getattr(train, "headcode", "") or "").strip().upper()
        if code and getattr(train, "origin", None) and getattr(train, "destination", None):
            return f"hc:{code}:{str(train.origin).upper()}:{str(train.destination).upper()}"
        return None

    def record(self, rows: list, *, day: str | None = None, now: datetime | None = None) -> tuple[int, int]:
        """Sample the live state; returns (trains, sightings) written."""
        day = day or today()
        stamp = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
        trains = sightings = 0
        with self._lock:
            for row in rows:
                if getattr(row, "restored_snapshot", False):
                    continue
                if getattr(row, "type", None) == "train":
                    key = self.train_key(row)
                    delay = getattr(row, "delay_minutes", None)
                    cancelled = 1 if getattr(row, "cancelled_at", None) else 0
                    if key and (delay is not None or cancelled):
                        self._db.execute(
                            """INSERT INTO train_days VALUES (?,?,?,?,?,?,?,?,?,1)
                               ON CONFLICT(day, train_key) DO UPDATE SET
                                 last_delay=COALESCE(excluded.last_delay, last_delay),
                                 worst_delay=MAX(COALESCE(worst_delay, -999), COALESCE(excluded.worst_delay, -999)),
                                 cancelled=MAX(cancelled, excluded.cancelled), samples=samples+1""",
                            (day, key, row.headcode, row.operator, row.origin, row.destination, delay, delay, cancelled))
                        trains += 1
                if getattr(row, "cool_candidate", False):
                    self._db.execute(
                        """INSERT INTO sightings VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                           ON CONFLICT(day, item_id) DO UPDATE SET last_seen=excluded.last_seen,
                             score=MAX(score, excluded.score), place=COALESCE(excluded.place, place)""",
                        (day, row.id, row.type, row.name, getattr(row, "cool_category", None),
                         getattr(row, "cool_subtype", None), getattr(row, "cool_badge", None),
                         getattr(row, "cool_explanation", None), _place(row), int(getattr(row, "cool_score", 0) or 0),
                         stamp, stamp))
                    sightings += 1
            self._db.commit()
        return trains, sightings

    # ---- reading ---------------------------------------------------------
    def usually(self, train, *, days: int = 30) -> dict | None:
        """How this service has run on earlier days: {"days", "late", "cancelled", "average"}."""
        key = self.train_key(train)
        if not key:
            return None
        since = (date.today() - timedelta(days=days)).isoformat()
        with self._lock:
            rows = self._db.execute(
                "SELECT last_delay, cancelled FROM train_days WHERE train_key=? AND day>=? AND day<?",
                (key, since, today())).fetchall()
        if len(rows) < 3:
            return None
        delays = [d for d, c in rows if not c and d is not None]
        return {"days": len(rows), "late": sum(1 for d in delays if d >= LATE_MINUTES),
                "cancelled": sum(c for _, c in rows),
                "average": round(sum(max(0, d) for d in delays) / len(delays), 1) if delays else None}

    def operator_days(self, operator: str, *, days: int = 30) -> list[dict]:
        since = (date.today() - timedelta(days=days)).isoformat()
        with self._lock:
            rows = self._db.execute(
                """SELECT day, COUNT(*), SUM(CASE WHEN cancelled=0 AND last_delay<? THEN 1 ELSE 0 END), SUM(cancelled)
                   FROM train_days WHERE operator=? AND day>=? GROUP BY day ORDER BY day""",
                (LATE_MINUTES, operator, since)).fetchall()
        return [{"day": d, "trains": n, "on_time": ok, "cancelled": c} for d, n, ok, c in rows]

    def sightings_on(self, day: str) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                """SELECT item_id, type, name, category, subtype, badge, explanation, place, score, first_seen, last_seen
                   FROM sightings WHERE day=? ORDER BY score DESC, first_seen""", (day,)).fetchall()
        keys = ("id", "type", "name", "category", "subtype", "badge", "explanation", "place", "score", "first_seen", "last_seen")
        return [dict(zip(keys, row)) for row in rows]

    def sighting_days(self, *, limit: int = 60) -> list[tuple[str, int]]:
        with self._lock:
            return self._db.execute(
                "SELECT day, COUNT(*) FROM sightings GROUP BY day ORDER BY day DESC LIMIT ?", (limit,)).fetchall()


def _place(row) -> str | None:
    label = str(getattr(row, "position_label", "") or "")
    if not label or any(word in label.lower() for word in ("berth", "estimated", "ads-b", "feed", "td ")):
        return None
    import re
    label = re.sub(r"^(?:confirmed report at|last reported at|reported at|near)\s+", "", label, flags=re.I).strip()
    if label.isupper():
        label = " ".join(word.capitalize() for word in label.split())
    return label[:80] or None
