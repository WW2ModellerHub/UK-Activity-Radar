"""How each train operator is running right now, in one word.

From the live trains: the share running five or more minutes late, how late
they are, and cancellations. An operator with too few trains reporting is
listed as unknown rather than guessed.
"""
from __future__ import annotations

import re
from datetime import date, datetime

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
    # Calibrated on a normal evening: a third of trains a few minutes late is
    # everyday running, not a severe day.
    if share >= 0.5 or (share >= 0.3 and average >= 20) or cancelled >= max(3, 0.15 * (reporting + cancelled)):
        return "severe", "Severe delays"
    if share >= 0.2 or cancelled >= 2:
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


# National Rail's operator codes that are not plain timetable codes.
_TOC_NAMES = {"LN": "West Midlands Trains", "WM": "West Midlands Trains", "SX": "Greater Anglia", "LD": "Lumo"}


_DISRUPTION_URL = re.compile(r"/service-disruptions/([a-z0-9-]+?)-(\d{8})/?$")
RECENT_DAYS = 21   # the feed keeps linking incidents long cleared; older links are dropped


def _incident_link(url: str, today: date) -> dict | None:
    """'https://www.nationalrail.co.uk/service-disruptions/crewe-20261125/' -> {"place": "Crewe", ...}."""
    match = _DISRUPTION_URL.search(str(url or ""))
    if not match or not str(url).startswith("https://"):
        return None
    try:
        started = datetime.strptime(match.group(2), "%Y%m%d").date()
    except ValueError:
        return None
    if (today - started).days > RECENT_DAYS:
        return None
    place = " ".join(word.capitalize() for word in match.group(1).split("-"))
    return {"place": place, "since": started.isoformat(), "url": url}


def official_status(indicators, today: date | None = None) -> dict[str, dict]:
    """{operator name: {"text": ..., "disrupted": bool, "incidents": [{place, since, url}]}} from
    National Rail's service indicators (Disruptions API, one call for every operator)."""
    from .station_knowledge import plain
    today = today or datetime.now().date()
    out: dict[str, dict] = {}
    for row in indicators or []:
        if not isinstance(row, dict):
            continue
        code = str(row.get("tocCode") or "").strip().upper()
        name = _TOC_NAMES.get(code) or ATOC_OPERATOR_NAMES.get(code) or str(row.get("tocName") or "").strip()
        if not name:
            continue
        disrupted = str(row.get("tocStatus") or "").strip().casefold() not in {"good service", ""}
        text = plain(row.get("tocStatusDescription")) or ("Good service" if not disrupted else "Disruption")
        links = [link for link in (_incident_link((g or {}).get("customURL"), today) for g in row.get("tocServiceGroup") or [])
                 if link] if disrupted else []
        entry = out.setdefault(name, {"text": text, "disrupted": disrupted, "incidents": []})
        if disrupted and not entry["disrupted"]:
            entry.update(text=text, disrupted=True)
        elif disrupted and text not in entry["text"]:
            entry["text"] = f"{entry['text']}; {text}"
        for link in links:
            if link["url"] not in {i["url"] for i in entry["incidents"]}:
                entry["incidents"].append(link)
    return out


def with_official(rows: list[dict], official: dict[str, dict]) -> list[dict]:
    """Add National Rail's own status to each operator; an officially disrupted operator is never 'Good service'."""
    seen = set()
    for row in rows:
        seen.add(row["operator"])
        entry = official.get(row["operator"])
        if not entry:
            continue
        row["official"] = entry
        if entry["disrupted"] and row["status"] in {"good", "unknown"}:
            row["status"], row["label"] = "minor", "Disruption"
    for name, entry in official.items():
        if entry["disrupted"] and name not in seen and name in PASSENGER_OPERATORS.values():
            rows.append({"operator": name, "status": "minor", "label": "Disruption", "running": 0, "late": 0,
                         "cancelled": 0, "average_delay": 0, "worst": [], "official": entry})
    rows.sort(key=lambda row: (_RANK[row["status"]], -(row["late"] + row["cancelled"]), row["operator"]))
    return rows
