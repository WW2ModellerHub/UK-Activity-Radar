"""The national railway network as a drawable map layer.

The bundled track geometry (``assets/rail_track_geometry.geojson``) is also
the routing graph for live trains. Here it is simplified into three levels of
detail so the browser can draw every line clearly:

* level 0: the whole of Great Britain in one response (national zooms);
* levels 1 and 2: finer geometry, served in half-degree cells so a phone only
  downloads the area in view and responses can be cached by URL.
"""
from __future__ import annotations

import json
import math
import threading
from pathlib import Path

# Douglas-Peucker tolerance in degrees for each level (~300 m, ~30 m, ~10 m).
LEVEL_TOLERANCE = {0: 0.003, 1: 0.0003, 2: 0.0001}
CELL_DEGREES = 0.5


def level_for_zoom(zoom: float) -> int:
    return 0 if zoom < 9 else 1 if zoom < 12 else 2


def _simplify(points: list[tuple[float, float]], tolerance: float) -> list[tuple[float, float]]:
    if len(points) < 3:
        return points
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    limit = tolerance * tolerance
    while stack:
        first, last = stack.pop()
        (ay, ax), (by, bx) = points[first], points[last]
        dx, dy = bx - ax, by - ay
        length = dx * dx + dy * dy
        best, best_index = 0.0, -1
        for index in range(first + 1, last):
            py, px = points[index]
            if length == 0:
                distance = (px - ax) ** 2 + (py - ay) ** 2
            else:
                t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length))
                distance = (px - ax - t * dx) ** 2 + (py - ay - t * dy) ** 2
            if distance > best:
                best, best_index = distance, index
        if best > limit:
            keep[best_index] = True
            stack.append((first, best_index))
            stack.append((best_index, last))
    return [point for point, kept in zip(points, keep) if kept]


class RailLineLayer:
    def __init__(self, geojson_path: Path) -> None:
        self.geojson_path = geojson_path
        self._lock = threading.Lock()
        self._levels: dict[int, list[tuple[int, tuple[float, float, float, float], list[list[float]]]]] = {}
        self._national: dict | None = None
        self._cells: dict[tuple[int, int, int], dict] = {}
        self._loaded = False
        # Network graph for "which line is this?" questions: fragment ends
        # that meet, and a coarse grid of which fragments pass each cell.
        self._ends: dict[tuple[float, float], list[int]] = {}
        self._grid: dict[tuple[int, int], set[int]] = {}
        self._stretch_cache: dict[int, tuple[tuple[int, ...], list[tuple[float, float]]]] = {}

    def _load(self) -> None:
        with self._lock:
            if self._loaded:
                return
            try:
                payload = json.loads(self.geojson_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                payload = {"features": []}
            raw: list[list[tuple[float, float]]] = []
            for feature in payload.get("features") or []:
                geometry = (feature or {}).get("geometry") or {}
                kind = geometry.get("type")
                parts = geometry.get("coordinates") or []
                if kind == "LineString":
                    parts = [parts]
                elif kind != "MultiLineString":
                    continue
                for part in parts:
                    line = [(float(c[1]), float(c[0])) for c in part if isinstance(c, list) and len(c) >= 2]
                    if len(line) >= 2:
                        raw.append(line)
            for level, tolerance in LEVEL_TOLERANCE.items():
                digits = 4 if level == 0 else 5
                rows = []
                for line_id, line in enumerate(raw):
                    simple = _simplify(line, tolerance)
                    lats = [point[0] for point in simple]
                    lons = [point[1] for point in simple]
                    rows.append((
                        line_id,
                        (min(lons), min(lats), max(lons), max(lats)),
                        [[round(lat, digits), round(lon, digits)] for lat, lon in simple],
                    ))
                self._levels[level] = rows
            for line_id, _bbox, points in self._levels.get(1, []):
                for end in (points[0], points[-1]):
                    self._ends.setdefault(_end_key(end), []).append(line_id)
                for (alat, alon), (blat, blon) in zip(points, points[1:]):
                    for gx in range(_grid(min(alon, blon)), _grid(max(alon, blon)) + 1):
                        for gy in range(_grid(min(alat, blat)), _grid(max(alat, blat)) + 1):
                            self._grid.setdefault((gx, gy), set()).add(line_id)
            self._loaded = True

    def national(self) -> dict:
        self._load()
        if self._national is None:
            self._national = {
                "level": 0,
                "lines": [{"id": line_id, "points": points} for line_id, _bbox, points in self._levels.get(0, [])],
            }
        return self._national

    def cell(self, level: int, cx: int, cy: int) -> dict:
        """Lines that touch one CELL_DEGREES square; ``cx``/``cy`` index lon/lat."""
        if level not in (1, 2):
            raise ValueError("cells are served for levels 1 and 2")
        if not (-360 <= cx <= 360 and -180 <= cy <= 180):
            raise ValueError("cell is outside the world")
        self._load()
        key = (level, cx, cy)
        cached = self._cells.get(key)
        if cached is not None:
            return cached
        west, south = cx * CELL_DEGREES, cy * CELL_DEGREES
        east, north = west + CELL_DEGREES, south + CELL_DEGREES
        lines = [
            {"id": line_id, "points": points}
            for line_id, (min_lon, min_lat, max_lon, max_lat), points in self._levels.get(level, [])
            if min_lon <= east and max_lon >= west and min_lat <= north and max_lat >= south
        ]
        result = {"level": level, "cell": [cx, cy], "lines": lines}
        with self._lock:
            if len(self._cells) > 4000:
                self._cells.clear()
            self._cells[key] = result
        return result


    # ---- Line sections -------------------------------------------------
    def _points(self, line_id: int) -> list[tuple[float, float]]:
        rows = self._levels.get(1, [])
        if not (0 <= line_id < len(rows)):
            return []
        return [(point[0], point[1]) for point in rows[line_id][2]]

    def nearest_line(self, lat: float, lon: float, max_km: float) -> tuple[int, float] | None:
        """The track fragment closest to a point, if one is within ``max_km``."""
        self._load()
        reach = max(1, math.ceil(max_km / 4.0))
        gx0, gy0 = _grid(lon), _grid(lat)
        candidates: set[int] = set()
        for gx in range(gx0 - reach, gx0 + reach + 1):
            for gy in range(gy0 - reach, gy0 + reach + 1):
                candidates |= self._grid.get((gx, gy), set())
        best: tuple[int, float] | None = None
        for line_id in candidates:
            distance, _along = project(self._points(line_id), lat, lon)
            if distance <= max_km and (best is None or distance < best[1]):
                best = (line_id, distance)
        return best

    def stretch(self, line_id: int, max_km: float = 80.0) -> tuple[tuple[int, ...], list[tuple[float, float]]]:
        """The plain run of track a fragment belongs to, between junctions.

        Fragments are joined end to end while exactly two meet, so the result
        reads like a signal box section: from one junction to the next.
        """
        self._load()
        cached = self._stretch_cache.get(line_id)
        if cached is not None:
            return cached
        points = self._points(line_id)
        if not points:
            return (), []
        used = [line_id]
        for forwards in (True, False):
            while _length_km(points) < max_km:
                end = points[-1] if forwards else points[0]
                meeting = self._ends.get(_end_key(end), [])
                others = [other for other in meeting if other not in used]
                # Stop at a junction (more than two fragments meet), at the
                # end of the line, or where the chain would loop back.
                if len(set(meeting)) != 2 or len(others) != 1:
                    break
                nxt = self._points(others[0])
                if not nxt:
                    break
                if forwards:
                    if _end_key(nxt[0]) != _end_key(end):
                        nxt.reverse()
                    points = points + nxt[1:]
                else:
                    if _end_key(nxt[-1]) != _end_key(end):
                        nxt.reverse()
                    points = nxt[:-1] + points
                used.append(others[0])
        result = (tuple(sorted(used)), points)
        with self._lock:
            if len(self._stretch_cache) > 5000:
                self._stretch_cache.clear()
            for member in used:
                self._stretch_cache[member] = result
        return result


def _end_key(point) -> tuple[float, float]:
    return (round(float(point[0]), 3), round(float(point[1]), 3))


def _grid(value: float) -> int:
    return math.floor(value / 0.05)


def _xy(lat: float, lon: float, ref_lat: float) -> tuple[float, float]:
    return lon * 111.32 * math.cos(math.radians(ref_lat)), lat * 110.57


def _length_km(points) -> float:
    if len(points) < 2:
        return 0.0
    ref = points[0][0]
    total = 0.0
    for (alat, alon), (blat, blon) in zip(points, points[1:]):
        ax, ay = _xy(alat, alon, ref)
        bx, by = _xy(blat, blon, ref)
        total += math.hypot(bx - ax, by - ay)
    return total


def project(points, lat: float, lon: float) -> tuple[float, float]:
    """(distance from the line, distance along it) in km, for a point."""
    if not points:
        return math.inf, 0.0
    px, py = _xy(lat, lon, lat)
    best = (math.inf, 0.0)
    along = 0.0
    for (alat, alon), (blat, blon) in zip(points, points[1:]):
        ax, ay = _xy(alat, alon, lat)
        bx, by = _xy(blat, blon, lat)
        dx, dy = bx - ax, by - ay
        segment = math.hypot(dx, dy)
        t = 0.0 if segment == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (segment * segment)))
        distance = math.hypot(px - (ax + t * dx), py - (ay + t * dy))
        if distance < best[0]:
            best = (distance, along + t * segment)
        along += segment
    if len(points) == 1:
        ax, ay = _xy(points[0][0], points[0][1], lat)
        best = (math.hypot(px - ax, py - ay), 0.0)
    return best


def cell_index(lon: float, lat: float) -> tuple[int, int]:
    return math.floor(lon / CELL_DEGREES), math.floor(lat / CELL_DEGREES)
