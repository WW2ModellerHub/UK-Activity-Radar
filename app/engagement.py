from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from .models import Activity


SENSITIVE_TYPES = {"sar", "rescue"}
DISRUPTION_TYPES = {"incident", "road", "weather", "flood", "outage"}


def _is_sensitive(item: Activity) -> bool:
    category = str(item.cool_category or "").lower()
    return item.type in SENSITIVE_TYPES or category == "rescue"


def _broad_region(lat: float, lon: float) -> str:
    """Return a deliberately broad, non-navigational UK area label."""
    if lat >= 56.0:
        return "Scotland"
    if lon <= -5.0 and lat >= 54.0:
        return "Northern Ireland or the Irish Sea"
    if lon <= -3.0 and lat < 54.0:
        return "Wales or the western approaches"
    if lat >= 54.5:
        return "Northern Britain"
    if lat >= 52.5:
        return "Central Britain"
    return "Southern Britain"


def safe_share_payload(item: Activity) -> dict:
    """Public, bounded activity facts with no precise coordinates."""
    sensitive = _is_sensitive(item)
    reason = item.cool_explanation or getattr(item, "reason", None) or item.subtitle or item.status
    impact = getattr(item, "impact", None)
    if impact:
        # Roads: say what it is and what it means, not the feed's wording.
        reason = " · ".join(x for x in (impact.get("where"), impact.get("impact"), impact.get("when")) if x)
    payload = {
        "activity_id": item.id,
        "kind": item.kind,
        "type": item.type,
        "name": (impact or {}).get("title") or item.name,
        "subtitle": item.subtitle,
        "status": item.status,
        "operator": getattr(item, "operator", None),
        "route": getattr(item, "route", None),
        "origin": getattr(item, "origin", None),
        "destination": getattr(item, "destination", None),
        "headcode": getattr(item, "headcode", None),
        "road": getattr(item, "road", None),
        "severity": getattr(item, "severity", None),
        "reason": reason,
        "badge": item.cool_badge,
        "interest_score": int(item.cool_score or 0),
        "source": item.source,
        "freshness": item.last_updated.isoformat(),
        "position_confidence": item.position_method,
        "location": _broad_region(item.lat, item.lon),
        "sensitive": sensitive,
        "safety_note": (
            "Broad-area context only. Never travel towards or interfere with an active response."
            if sensitive
            else "No precise coordinates are included on this public page."
        ),
    }
    return {key: value for key, value in payload.items() if value not in (None, "")}


def _km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    from math import asin, cos, radians, sin, sqrt
    p1, p2 = radians(lat1), radians(lat2)
    h = sin((p2 - p1) / 2) ** 2 + cos(p1) * cos(p2) * sin(radians(lon2 - lon1) / 2) ** 2
    return 2 * 6371 * asin(sqrt(h))


def _disruption_score(item: Activity) -> int:
    if item.type not in DISRUPTION_TYPES:
        return -1
    impact = getattr(item, "impact", None)
    if impact is not None:
        # Roads and incidents: only what is happening now and gets in the
        # way (a crash, a closure, lanes shut, real delay). A bus stop closed
        # next January is not "Right now".
        if not impact.get("active") or impact.get("rank", 0) < 2:
            return -1
        return 100 + 30 * int(impact.get("rank", 0))
    text = " ".join(
        str(value or "")
        for value in (
            getattr(item, "severity", None),
            getattr(item, "reason", None),
            item.name,
            item.subtitle,
        )
    ).lower()
    score = int(item.cool_score or 0)
    if any(word in text for word in ("severe", "major", "closed", "danger", "red warning")):
        score += 60
    elif any(word in text for word in ("high", "serious", "lane closed", "amber")):
        score += 35
    else:
        score += 10
    return score


def build_today_payload(
    activities: Iterable[Activity],
    since: datetime | None = None,
    lat: float | None = None,
    lon: float | None = None,
) -> dict:
    rows = list(activities)
    candidates = [row for row in rows if not _is_sensitive(row) and (row.cool_candidate or row.interesting)]
    candidates.sort(key=lambda row: (int(row.cool_score or 0), row.last_updated), reverse=True)

    new_rows: list[Activity] = []
    if since is not None:
        if since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)
        new_rows = [row for row in candidates if row.last_updated > since]

    scored = [(row, _disruption_score(row)) for row in rows]
    scored = [(row, score) for row, score in scored if score >= 0]
    if lat is not None and lon is not None:
        # Near the visitor first: the most serious within 50 km, nearest
        # first among equals. Nothing local means nothing listed.
        nearby = [(row, score, _km(lat, lon, row.lat, row.lon)) for row, score in scored]
        nearby = [item for item in nearby if item[2] <= 50]
        nearby.sort(key=lambda item: (-item[1], item[2]))
        disruptions = [row for row, _score, _dist in nearby][:3]
    else:
        scored.sort(key=lambda item: (item[1], item[0].last_updated), reverse=True)
        disruptions = [row for row, _score in scored][:3]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "best": safe_share_payload(candidates[0]) if candidates else None,
        "new_since": [safe_share_payload(row) for row in new_rows[:5]],
        "disruptions": [safe_share_payload(row) for row in disruptions],
        "counts": {
            "live": len(rows),
            "interesting": len(candidates),
            "new_since": len(new_rows),
            "disruptions": len(disruptions),
        },
    }
