"""How a train speeds up and slows down along a timetable leg.

A leg's booked time includes the time lost starting from a stand and
stopping at the next stop. Network Rail's sectional running times (see
scripts/build_running_allowances.py) give that loss for each traction:
"start from a stand" minus "passing at line speed". With steady
acceleration the loss is half the time spent accelerating, so a train that
loses 40 s starting spends about 80 s getting up to speed.

``distance_fraction`` turns time along a leg into distance along it with
that shape (speed up, run, slow down) instead of a constant speed, so a
train no longer leaves a platform at full speed or stops dead at the next.
The browser does the same sum (motion-engine.js ``legFraction``).
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

TABLE = Path(__file__).resolve().parent / "reference" / "running_allowances.json"
# Without a match: a typical multiple unit, and a heavy freight train.
PASSENGER = (40, 30)
FREIGHT = (70, 45)
MAX_SHARE = 0.8      # acceleration plus braking never take more of a leg than this

# CIF timing loads for diesel units are letters (CIF end-user specification).
_DMU_LETTERS = {"A": "14x", "E": "158", "N": "165-0", "S": "150", "T": "165-1", "V": "220", "X": "159"}


@lru_cache(maxsize=1)
def _table() -> dict[str, list[int]]:
    try:
        return json.loads(TABLE.read_text(encoding="utf-8")).get("allowances_s") or {}
    except (OSError, ValueError, AttributeError):
        return {}


def allowances(power_type: str | None, timing_load: str | None, category: str | None = None) -> tuple[int, int]:
    """(seconds lost starting, seconds lost stopping) for this train."""
    table = _table()
    power = str(power_type or "").strip().upper()
    load = str(timing_load or "").strip().upper()
    names = []
    if power == "HST":
        names.append("traction:HST")
    if power == "DMU" and load in _DMU_LETTERS:
        names.append(f"traction:{_DMU_LETTERS[load]}")
    if load:
        names.append(f"traction:{load}")
        if load.isdigit() and power in {"D", "E", "ED", "DEM"}:
            names.append(f"load:{load}")
    for name in names:
        if name in table:
            start, stop = table[name]
            return int(start), int(stop)
    heavy = (load.isdigit() and power in {"D", "E", "ED"} and int(load) >= 400) or str(category or "").upper()[:1] in {"E", "J", "P"}
    return FREIGHT if heavy else PASSENGER


def ramps(total_s: float, starts_from_stand: bool, stops_at_end: bool, allowance: tuple[int, int]) -> tuple[float, float]:
    """Seconds spent accelerating and braking on a leg of ``total_s`` seconds."""
    accel = 2.0 * allowance[0] if starts_from_stand else 0.0
    brake = 2.0 * allowance[1] if stops_at_end else 0.0
    limit = MAX_SHARE * max(0.0, float(total_s))
    if accel + brake > limit > 0:
        scale = limit / (accel + brake)
        accel, brake = accel * scale, brake * scale
    elif limit <= 0:
        accel = brake = 0.0
    return round(accel, 1), round(brake, 1)


def distance_fraction(elapsed_s: float, total_s: float, accel_s: float = 0.0, brake_s: float = 0.0) -> float:
    """Share of the leg's distance covered after ``elapsed_s`` of ``total_s``."""
    if total_s <= 0:
        return 1.0
    t = max(0.0, min(float(total_s), float(elapsed_s)))
    accel, brake = max(0.0, accel_s or 0.0), max(0.0, brake_s or 0.0)
    if accel + brake >= total_s:
        accel = brake = 0.0
    speed = 1.0 / (total_s - accel / 2 - brake / 2)   # cruising speed, in legs per second
    if accel and t < accel:
        return speed * t * t / (2 * accel)
    if brake and t > total_s - brake:
        left = total_s - t
        return 1.0 - speed * left * left / (2 * brake)
    return speed * (t - accel / 2)
