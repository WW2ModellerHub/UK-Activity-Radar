"""How each train operator is running right now, in one word.

From the live trains: the share running five or more minutes late, how late
they are, and cancellations. An operator with too few trains reporting is
listed as unknown rather than guessed.
"""
from __future__ import annotations

from .train_identity import ATOC_OPERATOR_NAMES

LATE_MINUTES = 5
MIN_TRAINS = 4
PASSENGER_OPERATORS = {name.casefold(): name for name in ATOC_OPERATOR_NAMES.values()}
PASSENGER_OPERATORS.update({"northern trains": "Northern", "lumo": "Lumo"})
_RANK = {"severe": 0, "minor": 1, "good": 2, "unknown": 3}


def _status(reporting: int, late: int, cancelled: int, average: float) -> tuple[str, str]:
    if reporting + cancelled < MIN_TRAINS:
        return "unknown", "Too few trains reporting"
    share = (late + cancelled) / max(1, reporting + cancelled)
    if share >= 0.4 or (share >= 0.25 and average >= 20) or cancelled >= max(3, 0.15 * (reporting + cancelled)):
        return "severe", "Severe delays"
    if share >= 0.15 or cancelled >= 2:
        return "minor", "Minor delays"
    return "good", "Good service"


def operator_status(trains) -> list[dict]:
    """One row per passenger operator, worst first."""
    groups: dict[str, list] = {}
    for train in trains:
        name = PASSENGER_OPERATORS.get(str(getattr(train, "operator", "") or "").strip().casefold())
        if name and not getattr(train, "terminated_at", None):
            groups.setdefault(name, []).append(train)
    rows = []
    for name, members in groups.items():
        cancelled = [t for t in members if getattr(t, "cancelled_at", None)]
        reporting = [t for t in members if not getattr(t, "cancelled_at", None) and getattr(t, "delay_minutes", None) is not None]
        late = [t for t in reporting if (t.delay_minutes or 0) >= LATE_MINUTES]
        average = round(sum(t.delay_minutes for t in late) / len(late)) if late else 0
        status, label = _status(len(reporting), len(late), len(cancelled), average)
        worst = sorted(late, key=lambda t: -(t.delay_minutes or 0))[:5]
        rows.append({
            "operator": name, "status": status, "label": label,
            "running": len(reporting) + len(cancelled), "late": len(late), "cancelled": len(cancelled),
            "average_delay": average,
            "worst": [{"id": t.id, "name": t.name, "destination": getattr(t, "destination", None),
                       "delay_minutes": int(t.delay_minutes)} for t in worst]
                     + [{"id": t.id, "name": t.name, "destination": getattr(t, "destination", None), "cancelled": True}
                        for t in cancelled[:3]],
        })
    rows.sort(key=lambda row: (_RANK[row["status"]], -(row["late"] + row["cancelled"]), row["operator"]))
    return rows
