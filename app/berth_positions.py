"""Where each Train Describer berth is, learned from the trains that pass it.

Network Rail's SMART data places only the berth steps at reporting points
(stations, junctions): over half of all TD steps have no position, so a train
coasts on its timetable between stations. Live maps such as Signalbox place
nearly every berth. This learns them from the feed itself:

* A train is followed between two placed reports A and B along the track.
* Each unplaced step it makes in between is put on that track by its time,
  with the train speeding up from a stop and braking for one (running_profile).
* A berth is trusted once several trains agree on it (MIN_SEEN within
  AGREE_KM of their median).

Then every step into a learned berth is a position report, every kilometre or
two, as on Signalbox.
"""
from __future__ import annotations

import json
import os
import threading
import time
from math import cos, radians, sqrt

MIN_SEEN = 3          # trains that must agree before a berth is used
AGREE_KM = 0.5        # how closely they must agree
KEEP_SEEN = 9         # observations kept per berth (most recent)
MAX_BERTHS = 60000


def _xy(lat: float, lon: float, lat0: float) -> tuple[float, float]:
    """Kilometres on a flat projection around lat0: fine over a few tens of km."""
    return lon * 111.32 * cos(radians(lat0)), lat * 110.57


def path_length(path) -> float:
    lat0 = path[0][0]
    points = [_xy(p[0], p[1], lat0) for p in path]
    return sum(sqrt((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) for a, b in zip(points, points[1:]))


def point_at(path, fraction: float) -> tuple[float, float]:
    """The point a share of the way along a path."""
    lat0 = path[0][0]
    points = [_xy(p[0], p[1], lat0) for p in path]
    pieces = [sqrt((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) for a, b in zip(points, points[1:])]
    target = max(0.0, min(1.0, fraction)) * sum(pieces)
    for (a, b), length in zip(zip(path, path[1:]), pieces):
        if target <= length and length > 0:
            local = target / length
            return a[0] + (b[0] - a[0]) * local, a[1] + (b[1] - a[1]) * local
        target -= length
    return path[-1][0], path[-1][1]


def project(path, lat: float, lon: float) -> tuple[float, float] | None:
    """(share of the way along the path, distance off it in km) of the nearest point."""
    if not path or len(path) < 2:
        return None
    lat0 = path[0][0]
    px, py = _xy(lat, lon, lat0)
    points = [_xy(p[0], p[1], lat0) for p in path]
    total = sum(sqrt((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) for a, b in zip(points, points[1:]))
    if total <= 0:
        return None
    best, walked = None, 0.0
    for a, b in zip(points, points[1:]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        length2 = dx * dx + dy * dy
        local = 0.0 if length2 == 0 else max(0.0, min(1.0, ((px - a[0]) * dx + (py - a[1]) * dy) / length2))
        cx, cy = a[0] + dx * local, a[1] + dy * local
        off = sqrt((px - cx) ** 2 + (py - cy) ** 2)
        if best is None or off < best[1]:
            best = ((walked + sqrt(length2) * local) / total, off)
        walked += sqrt(length2)
    return best


class BerthPositions:
    def __init__(self, path: str | None = None) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._seen: dict[str, list[list[float]]] = {}
        self._dirty = False
        self._saved_at = time.monotonic()
        self.learned_steps = 0
        self.placed_steps = 0
        self._corrections: list[float] = []   # km a train was off when it reached a learned berth
        # Which berth trains step into next, by berth: a train may not be
        # drawn past the next berth until it steps into it (signals).
        self._next: dict[str, dict[str, int]] = {}
        self.capped = 0
        self.load()

    @staticmethod
    def key(area: str, berth: str) -> str:
        return f"{str(area).strip().upper()}:{str(berth).strip().upper()}"

    def observe(self, key: str, lat: float, lon: float) -> None:
        with self._lock:
            seen = self._seen.setdefault(key, [])
            seen.append([round(lat, 6), round(lon, 6)])
            del seen[:-KEEP_SEEN]
            if len(self._seen) > MAX_BERTHS:
                self._seen.pop(next(iter(self._seen)))
            self._dirty = True
        self.learned_steps += 1

    def position(self, key: str) -> tuple[float, float] | None:
        """The agreed position of a berth, or None until enough trains agree."""
        with self._lock:
            seen = list(self._seen.get(key) or [])
        if len(seen) < MIN_SEEN:
            return None
        lats, lons = sorted(p[0] for p in seen), sorted(p[1] for p in seen)
        lat, lon = lats[len(lats) // 2], lons[len(lons) // 2]
        close = [p for p in seen if _km(p[0], p[1], lat, lon) <= AGREE_KM]
        if len(close) < MIN_SEEN:
            return None
        return sum(p[0] for p in close) / len(close), sum(p[1] for p in close) / len(close)

    def note_next(self, key: str, following: str) -> None:
        if not key or not following or key == following:
            return
        with self._lock:
            seen = self._next.setdefault(key, {})
            seen[following] = seen.get(following, 0) + 1
            if len(seen) > 8:   # keep the commonest
                for drop in sorted(seen, key=seen.get)[: len(seen) - 8]:
                    seen.pop(drop, None)
            if len(self._next) > MAX_BERTHS:
                self._next.pop(next(iter(self._next)))
            self._dirty = True

    def next_berth(self, key: str) -> str | None:
        """The berth trains step into after this one, when they agree (2+ times, 70%+)."""
        with self._lock:
            seen = dict(self._next.get(key) or {})
        if not seen:
            return None
        best = max(seen, key=seen.get)
        total = sum(seen.values())
        return best if seen[best] >= 2 and seen[best] >= 0.7 * total else None

    def note_correction(self, km: float) -> None:
        self._corrections.append(round(km, 3))
        del self._corrections[:-2000]

    def report(self) -> dict:
        with self._lock:
            berths = len(self._seen)
            trusted_keys = list(self._seen)
        trusted = sum(1 for key in trusted_keys if self.position(key) is not None)
        corrections = sorted(self._corrections)
        quantile = (lambda q: corrections[min(len(corrections) - 1, int(len(corrections) * q))]) if corrections else (lambda q: None)
        return {"berths_seen": berths, "berths_trusted": trusted, "steps_learned": self.learned_steps,
                "steps_placed": self.placed_steps, "next_berths_known": len(self._next), "capped": self.capped,
                # How far off trains were drawn when they reached a learned berth (km): lower is smoother.
                "correction_km": {"median": quantile(0.5), "p90": quantile(0.9), "n": len(corrections)}}

    def load(self) -> None:
        if not self._path:
            return
        try:
            with open(self._path, encoding="utf-8") as handle:
                saved = json.load(handle)
        except (OSError, ValueError):
            return
        if isinstance(saved, dict):
            positions = saved.get("positions") if isinstance(saved.get("positions"), dict) else saved
            following = saved.get("next") if isinstance(saved.get("next"), dict) else {}
            with self._lock:
                self._seen = {str(k): [list(map(float, p))[:2] for p in v if isinstance(p, list) and len(p) >= 2]
                              for k, v in positions.items() if isinstance(v, list)}
                self._next = {str(k): {str(a): int(b) for a, b in v.items()} for k, v in following.items() if isinstance(v, dict)}

    def save(self, force: bool = False) -> None:
        """At most once a minute (and on shutdown)."""
        if not self._path or not self._dirty or (not force and time.monotonic() - self._saved_at < 60):
            return
        with self._lock:
            snapshot = {"positions": {k: list(v) for k, v in self._seen.items()},
                        "next": {k: dict(v) for k, v in self._next.items()}}
            self._dirty = False
            self._saved_at = time.monotonic()
        try:
            temporary = f"{self._path}.tmp"
            with open(temporary, "w", encoding="utf-8") as handle:
                json.dump(snapshot, handle, separators=(",", ":"))
            os.replace(temporary, self._path)
        except OSError:
            self._dirty = True


def _km(lat1, lon1, lat2, lon2) -> float:
    a, b = _xy(lat1, lon1, lat1), _xy(lat2, lon2, lat1)
    return sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2)
