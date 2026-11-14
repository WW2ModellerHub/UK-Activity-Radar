"""Trains on a stretch of line, and the stretches where trains are late.

Both answers come from the bundled track geometry (see ``rail_lines``): a
stretch is the plain run of track between two junctions, named after the
passenger stations nearest its ends.
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Iterable

from .rail_lines import RailLineLayer, project
from .state import _estimated_train_position

ON_LINE_KM = 0.6
LATE_MINUTES = 5


def _position(vehicle, now: datetime) -> tuple[float, float]:
    estimate = _estimated_train_position(vehicle, now)
    return estimate if estimate is not None else (float(vehicle.lat), float(vehicle.lon))


def _nearest_station(stations: list[dict], lat: float, lon: float, within_km: float = 8.0) -> str | None:
    best_name, best = None, within_km
    scale = math.cos(math.radians(lat))
    for row in stations:
        distance = math.hypot((row["lat"] - lat) * 110.57, (row["lon"] - lon) * 111.32 * scale)
        if distance < best:
            best_name, best = row["name"], distance
    return best_name


def stretch_name(stations: list[dict], points) -> tuple[str | None, str | None]:
    if not points:
        return None, None
    start = _nearest_station(stations, *points[0])
    end = _nearest_station(stations, *points[-1])
    return start, (end if end != start else None)


def _thin(points, limit: int) -> list[list[float]]:
    step = max(1, math.ceil(len(points) / limit))
    kept = list(points[::step])
    if kept[-1] != points[-1]:
        kept.append(points[-1])
    return [[round(lat, 5), round(lon, 5)] for lat, lon in kept]


def _train_row(vehicle, along_km: float) -> dict:
    return {
        "id": vehicle.id,
        "name": vehicle.name,
        "origin": vehicle.origin,
        "destination": vehicle.destination,
        "operator": vehicle.operator,
        "delay_minutes": vehicle.delay_minutes,
        "cancelled": bool(getattr(vehicle, "cancelled_at", None)),
        "along_km": round(along_km, 2),
    }


def line_at(layer: RailLineLayer, trains: Iterable, stations: list[dict], lat: float, lon: float,
            reach_km: float, now: datetime) -> dict:
    """The stretch of line at a tapped point and the trains on it, in order."""
    hit = layer.nearest_line(lat, lon, reach_km)
    if hit is None:
        return {"found": False}
    _ids, points = layer.stretch(hit[0])
    rows = []
    for vehicle in trains:
        tlat, tlon = _position(vehicle, now)
        distance, along = project(points, tlat, tlon)
        if distance <= ON_LINE_KM:
            rows.append(_train_row(vehicle, along))
    rows.sort(key=lambda row: row["along_km"])
    start, end = stretch_name(stations, points)
    return {
        "found": True,
        "from": start,
        "to": end,
        "length_km": round(sum(
            math.hypot((b[0] - a[0]) * 110.57, (b[1] - a[1]) * 111.32 * math.cos(math.radians(a[0])))
            for a, b in zip(points, points[1:])
        ), 1),
        "points": _thin(points, 800),
        "trains": rows,
    }


def delay_hotspots(layer: RailLineLayer, trains: Iterable, stations: list[dict], now: datetime,
                   limit: int = 10) -> list[dict]:
    """Stretches of line with the most lateness right now."""
    groups: dict[tuple[int, ...], dict] = {}
    for vehicle in trains:
        delay = vehicle.delay_minutes
        cancelled = bool(getattr(vehicle, "cancelled_at", None))
        if not cancelled and (delay is None or delay < LATE_MINUTES):
            continue
        tlat, tlon = _position(vehicle, now)
        hit = layer.nearest_line(tlat, tlon, 1.0)
        if hit is None:
            continue
        ids, points = layer.stretch(hit[0])
        group = groups.setdefault(ids, {"points": points, "late": 0, "cancelled": 0, "total_delay": 0,
                                        "worst": 0, "trains": []})
        if cancelled:
            group["cancelled"] += 1
        else:
            group["late"] += 1
            group["total_delay"] += int(delay)
            group["worst"] = max(group["worst"], int(delay))
        group["trains"].append(vehicle.id)
    ranked = sorted(groups.values(), key=lambda g: (g["total_delay"] + 15 * g["cancelled"]), reverse=True)[:limit]
    out = []
    for group in ranked:
        points = group["points"]
        start, end = stretch_name(stations, points)
        middle = points[len(points) // 2]
        out.append({
            "from": start,
            "to": end,
            "late": group["late"],
            "cancelled": group["cancelled"],
            "average_delay": round(group["total_delay"] / group["late"]) if group["late"] else 0,
            "worst_delay": group["worst"],
            "train_ids": group["trains"][:20],
            "center": [round(middle[0], 5), round(middle[1], 5)],
            "points": _thin(points, 300),
        })
    return out
