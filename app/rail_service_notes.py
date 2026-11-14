"""What the railway has said about a service beyond its position.

TRUST reports cancellations and actual platforms for services that may not be
on the map at all (a train cancelled before it starts never moves). Boards and
train panels look services up here by timetable identity: (train UID, running
date).
"""
from __future__ import annotations

import time
from threading import Lock

KEEP_SECONDS = 36 * 3600
MAX_SERVICES = 60_000

# TRUST cancellation types, in words a traveller understands.
CANCELLATION_KIND = {
    "ON CALL": "whole",
    "AT ORIGIN": "whole",
    "EN ROUTE": "part",
    "OUT OF PLAN": "part",
}


class RailServiceNotes:
    def __init__(self) -> None:
        self._lock = Lock()
        self._notes: dict[tuple[str, str], dict] = {}

    @staticmethod
    def _key(train_uid: str | None, service_date: str | None) -> tuple[str, str] | None:
        uid = str(train_uid or "").strip()
        day = str(service_date or "").strip()[:10]
        return (uid, day) if uid and day else None

    def _entry(self, key: tuple[str, str]) -> dict:
        entry = self._notes.get(key)
        if entry is None:
            if len(self._notes) >= MAX_SERVICES:
                self._prune_unlocked(force=True)
            entry = self._notes[key] = {"cancelled": None, "platforms": {}}
        entry["touched"] = time.time()
        return entry

    def cancel(self, train_uid, service_date, *, tiplocs=(), location: str | None = None,
               kind: str | None = None, at: str | None = None) -> bool:
        key = self._key(train_uid, service_date)
        if key is None:
            return False
        with self._lock:
            self._entry(key)["cancelled"] = {
                "tiplocs": sorted({str(code).strip().upper() for code in tiplocs if code}),
                "location": location,
                "scope": CANCELLATION_KIND.get(str(kind or "").strip().upper(), "whole"),
                "at": at,
            }
        return True

    def reinstate(self, train_uid, service_date) -> bool:
        key = self._key(train_uid, service_date)
        if key is None:
            return False
        with self._lock:
            entry = self._notes.get(key)
            if entry is None or entry.get("cancelled") is None:
                return False
            entry["cancelled"] = None
            entry["touched"] = time.time()
        return True

    def platform(self, train_uid, service_date, tiploc: str | None, platform: str | None) -> None:
        key = self._key(train_uid, service_date)
        code = str(tiploc or "").strip().upper()
        value = str(platform or "").strip()
        if key is None or not code or not value:
            return
        with self._lock:
            self._entry(key)["platforms"][code] = value

    def get(self, train_uid, service_date) -> dict | None:
        key = self._key(train_uid, service_date)
        if key is None:
            return None
        with self._lock:
            entry = self._notes.get(key)
            if entry is None:
                return None
            cancelled = entry.get("cancelled")
            return {
                "cancelled": dict(cancelled) if cancelled else None,
                "platforms": dict(entry.get("platforms") or {}),
            }

    def _prune_unlocked(self, force: bool = False) -> None:
        cutoff = time.time() - KEEP_SECONDS
        for key in [key for key, entry in self._notes.items() if entry.get("touched", 0) < cutoff]:
            self._notes.pop(key, None)
        if force and len(self._notes) >= MAX_SERVICES:
            oldest = sorted(self._notes, key=lambda key: self._notes[key].get("touched", 0))
            for key in oldest[: len(oldest) // 4]:
                self._notes.pop(key, None)

    def prune(self) -> None:
        with self._lock:
            self._prune_unlocked()

    def __len__(self) -> int:
        with self._lock:
            return len(self._notes)


def cancelled_from_index(note: dict | None, codes: list[str]) -> int | None:
    """Index in ``codes`` from which a service no longer runs, or None.

    A whole-service cancellation, or one at a location the timetable does not
    show, cancels every call (index 0).
    """
    cancelled = (note or {}).get("cancelled")
    if not cancelled:
        return None
    if cancelled.get("scope") == "part":
        wanted = set(cancelled.get("tiplocs") or [])
        for index, code in enumerate(codes):
            if str(code or "").strip().upper() in wanted:
                return index
    return 0


def call_is_cancelled(note: dict | None, codes: list[str], index: int, kind: str) -> bool:
    """Whether the arrival or departure at ``codes[index]`` is cancelled.

    A part-cancelled train still arrives at the place it was cut short.
    """
    start = cancelled_from_index(note, codes)
    if start is None:
        return False
    return index > start if kind == "arrival" and start > 0 else index >= start
