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


def cell_index(lon: float, lat: float) -> tuple[int, int]:
    return math.floor(lon / CELL_DEGREES), math.floor(lat / CELL_DEGREES)
