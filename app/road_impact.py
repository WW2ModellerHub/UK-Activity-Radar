"""What a road incident or roadworks means for someone driving.

Feeds describe events in their own vocabulary ("roadMaintenance",
"laneClosures", "planned", record creation times). This turns an event into
the few facts a driver needs, in plain English:

* what      - "Road closed", "Lane closure", "Crash", "Temporary traffic lights"
* where     - "M1 southbound, J22 to J21A"
* impact    - "2 lanes closed", "Delays of about 15 min", "Queue of 2 km"
* when      - "Until Sat 15 Nov 06:00", "Tonight 20:00–06:00", "Started 25 min ago"
* why / who - "Resurfacing", "Gas works by Cadent"

and whether it is happening now, which decides if it is shown at all.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

LONDON = ZoneInfo("Europe/London")
UPCOMING_HOURS = 12

_WHAT = [
    (r"road ?clos|carriageway ?clos|roadclosed", "Road closed"),
    (r"contra ?flow", "Contraflow"),
    (r"lane ?clos|lanes ?closed|lane closure", "Lane closure"),
    (r"hard ?shoulder", "Hard shoulder closed"),
    (r"narrow ?lanes", "Narrow lanes"),
    (r"(multi|two|three|four)?[ -]?way ?signals|traffic ?(signals|lights)|portable", "Temporary traffic lights"),
    (r"stop ?go|give ?and ?take|convoy", "Stop/go boards"),
    (r"intermittent", "Short closures"),
]
_CAUSE = [
    (r"accident|collision|crash", "Crash"),
    (r"broken ?down|breakdown|vehicle ?obstruction|disabled ?vehicle", "Broken-down vehicle"),
    (r"queu|stationary ?traffic|slow ?traffic|heavy ?traffic|congestion", "Queueing traffic"),
    (r"spill|debris|object", "Debris on the road"),
    (r"flood", "Flooding"),
    (r"fire", "Fire"),
    (r"animal", "Animals on the road"),
    (r"pedestrian", "Person on the road"),
    (r"resurfac|surfacing", "Resurfacing"),
    (r"gas", "Gas works"),
    (r"water|sewer", "Water works"),
    (r"electric|power|cable", "Electricity works"),
    (r"telecom|fibre|broadband|openreach", "Telecoms works"),
    (r"bridge", "Bridge works"),
    (r"barrier|safety ?fence", "Barrier repairs"),
    (r"maintenance|repair|construction|works|improvement", "Roadworks"),
]


def _plain(text: str | None) -> str | None:
    if not text:
        return None
    text = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", str(text)).replace("_", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:1].upper() + text[1:].lower() if text else None


def _match(table, *texts) -> str | None:
    blob = " ".join(str(t or "") for t in texts).lower()
    for pattern, label in table:
        if re.search(pattern, blob):
            return label
    return None


def _dt(value) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _clock(moment: datetime, now: datetime) -> str:
    local, today = moment.astimezone(LONDON), now.astimezone(LONDON).date()
    if local.date() == today:
        return local.strftime("%H:%M")
    if local.date() == today + timedelta(days=1):
        return f"tomorrow {local.strftime('%H:%M')}"
    return local.strftime("%a %-d %b %H:%M")


def _window(event, now: datetime) -> tuple[bool, datetime | None, datetime | None]:
    """(happening now, start, end) using validity windows when the feed has them."""
    details = event.details or {}
    periods = [(_dt(a), _dt(b)) for a, b in (details.get("periods") or [])]
    periods = [(a, b) for a, b in periods if a]
    if periods:
        for start, end in periods:
            if start <= now and (end is None or now < end):
                return True, start, end
        upcoming = [(a, b) for a, b in periods if a > now]
        if upcoming:
            return False, upcoming[0][0], upcoming[0][1]
        return False, periods[-1][0], periods[-1][1]
    start, end = _dt(event.start_time), _dt(event.end_time)
    if end is not None and end <= now:
        return False, start, end
    if start is not None and start > now:
        return False, start, end
    status = str(event.status or "").lower()
    if details.get("planned") and status in {"planned", "proposed", "suspended"}:
        return False, start, end
    return True, start, end


def describe(event, now: datetime | None = None) -> dict:
    """Plain facts for one incident, roadworks or bus disruption."""
    now = now or datetime.now(timezone.utc)
    details = event.details or {}
    active, start, end = _window(event, now)

    if details.get("bus_disruption"):
        what = "Bus cancelled" if details.get("cancellation") else "Bus disruption"
        if re.search(r"stop.*clos|clos.*stop", f"{event.name} {event.reason}", re.I):
            what = "Bus stop closed"
        where = details.get("stop") or event.subtitle
        impact = _plain(details.get("line") and f"Route {details['line']}") if details.get("line") else None
        why = event.reason
    else:
        management = details.get("management")
        what = (
            ("Road closed" if details.get("road_closed") else None)
            or _match(_WHAT, management, details.get("constriction"))
            or _match(_CAUSE, details.get("cause"), event.reason, event.name)
            or ("Incident" if event.type == "incident" else "Roadworks")
        )
        road = event.road
        direction = details.get("direction") or event.direction
        location = details.get("location") or ((event.subtitle or "").split(" • ")[0] or None)
        if location and road and location.lower().startswith(road.lower()):
            where = location
        else:
            where = ", ".join(p for p in (road, direction, location) if p)
        bits = []
        lanes = details.get("lanes_closed")
        if what != "Road closed" and isinstance(lanes, int) and lanes > 0:
            bits.append(f"{lanes} lane{'s' if lanes != 1 else ''} closed")
        if details.get("delay_minutes"):
            bits.append(f"Delays of about {details['delay_minutes']} min")
        elif details.get("delay_band"):
            bits.append(f"Delays: {details['delay_band'].lower()}")
        if details.get("queue_metres"):
            km = details["queue_metres"] / 1000
            bits.append(f"Queue of {km:.1f} km" if km < 10 else f"Queue of {round(km)} km")
        impact = " · ".join(bits) or None
        cause = _match(_CAUSE, details.get("cause"), event.reason)
        why = cause if cause and cause != what else None
        if details.get("promoter"):
            why = f"{why or 'Works'} by {details['promoter']}"

    if active:
        if end and end - now < timedelta(days=60):
            when = f"Until {_clock(end, now)}"
        elif start and now - start < timedelta(hours=12) and not details.get("planned"):
            minutes = int((now - start).total_seconds() // 60)
            when = f"Started {minutes} min ago" if minutes < 90 else f"Since {_clock(start, now)}"
        else:
            when = "Now"
    elif start and start > now:
        when = f"From {_clock(start, now)}" + (f" to {_clock(end, now)}" if end else "")
    else:
        when = "Finished"

    rank = 0
    if active:
        rank = {"Road closed": 3, "Crash": 3, "Contraflow": 2, "Lane closure": 2, "Queueing traffic": 2,
                "Broken-down vehicle": 2}.get(what, 1)
        if details.get("delay_minutes") and details["delay_minutes"] >= 15:
            rank = max(rank, 3)
        if event.type == "incident" and not details.get("bus_disruption"):
            rank += 1
    # Long-running works that restrict nothing (no lanes, no closure, no
    # signals) are not worth a marker.
    # Only judged when the feed gave structured facts; other sources keep
    # their events.
    restricts = (not details) or what not in {"Roadworks", "Incident"} or bool(details.get("lanes_closed")) or bool(details.get("delay_minutes"))
    road = getattr(event, "road", None)
    title = f"{what} · {road}" if road and not details.get("bus_disruption") and road.lower() not in what.lower() else what
    return {
        "title": title, "what": what, "where": where or None, "impact": impact, "when": when, "why": why,
        "note": details.get("comment"), "active": active, "rank": rank,
        "starts_soon": bool(not active and start and now < start <= now + timedelta(hours=UPCOMING_HOURS)),
        "worth_showing": bool(
            (active or bool(not active and start and now < start <= now + timedelta(hours=UPCOMING_HOURS)))
            and (restricts or event.type == "incident" or details.get("bus_disruption"))
        ),
    }
