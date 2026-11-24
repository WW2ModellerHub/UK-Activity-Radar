"""Plain facts from the National Rail Knowledgebase station feed (Rail Data Marketplace).

The feed is long and nested (accessibility, staffing, ticket office hours,
toilets, lifts, parking...). This keeps what a traveller asks before going:
can I get to the platform, is anyone there, can I buy a ticket, is there a
toilet, can I park or leave a bike. Only facts the feed states are shown.
"""
from __future__ import annotations

import html
import re
from datetime import datetime

_TAGS = re.compile(r"<[^>]+>")
_DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def plain(value) -> str | None:
    """HTML notes -> one line of text."""
    if value is None:
        return None
    text = html.unescape(_TAGS.sub(" ", str(value)))
    text = " ".join(text.split())
    return text or None


def _get(data, *path):
    for key in path:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


def hours_on(opening_times, day: str) -> str | None:
    """'05:20–22:30', '24 hours' or 'Closed' for one day, from the feed's opening times."""
    for block in opening_times or []:
        if not isinstance(block, dict) or day not in (block.get("daysOfTheWeek") or []):
            continue
        status = str(block.get("openingStatus") or "")
        if "24" in status:
            return "24 hours"
        if "closed" in status.lower():
            return "Closed"
        periods = [p for p in block.get("openPeriod") or [] if isinstance(p, dict)]
        spans = [f"{str(p.get('startTime') or '')[:5]}–{str(p.get('endTime') or '')[:5]}" for p in periods
                 if p.get("startTime") and p.get("endTime")]
        if spans:
            return ", ".join(spans)
    return None


def _step_free(station: dict) -> tuple[str, str] | None:
    category = str(_get(station, "stationAccessibility", "stepFreeCategory", "category") or "").strip()
    if not category:
        return None
    letter = category[:1].upper()
    meaning = {"A": "Step-free to all platforms", "B": "Step-free to some platforms, or with help",
               "C": "Not step-free"}.get(letter)
    text = category.split(",", 1)[-1].strip() if "," in category else category
    return ("Step-free access", meaning or text[:1].upper() + text[1:])


def facts(station: dict | None, today: datetime | None = None) -> list[tuple[str, str]]:
    """[(label, value)] in the order a traveller asks, only what the feed states."""
    if not isinstance(station, dict):
        return []
    day = (today or datetime.now()).strftime("%A")
    out: list[tuple[str, str]] = []
    step = _step_free(station)
    if step:
        out.append(step)
    staffing = str(station.get("staffingLevel") or "").strip()
    staff_hours = hours_on(_get(station, "staffAssistance", "staffHelp", "openingTimes"), day)
    if staffing:
        out.append(("Staff", staffing + (f" · today {staff_hours}" if staff_hours and staff_hours != "24 hours" else "")))
    office = _get(station, "ticketBuying", "ticketOffice") or {}
    if office.get("available"):
        hours = hours_on(office.get("openingTimes"), day)
        out.append(("Ticket office", f"Open today {hours}" if hours and hours != "Closed" else "Closed today" if hours == "Closed" else "Yes"))
    elif office.get("available") is False:
        out.append(("Ticket office", "None"))
    if _get(station, "ticketBuying", "ticketMachinesAvailable"):
        out.append(("Ticket machines", "Yes"))
    toilets = _get(station, "toiletsAndChanging", "toilets") or {}
    if toilets.get("available"):
        extras = [x for x, flag in (("accessible", toilets.get("accessibleToiletsAvailable")),
                                    ("baby changing", toilets.get("babyChangingAvailable")),
                                    ("Changing Places", toilets.get("changingPlacesToiletsAvailable"))) if flag]
        out.append(("Toilets", "Yes" + (f", including {', '.join(extras)}" if extras else "")))
    elif toilets.get("available") is False:
        out.append(("Toilets", "None"))
    lifts = station.get("lifts") or {}
    if lifts.get("available"):
        count = len([x for x in lifts.get("liftsInfo") or [] if isinstance(x, dict)])
        out.append(("Lifts", f"{count} lift{'s' if count != 1 else ''}" if count else "Yes"))
    if _get(station, "stationAccessibility", "wheelchairsAvailable"):
        out.append(("Wheelchairs", "Available to borrow"))
    if _get(station, "stationAccessibility", "trainRamp", "available"):
        out.append(("Boarding ramp", "Kept at the station"))
    parks = station.get("carParks") or {}
    if parks.get("parkingSpacesAvailable"):
        spaces, accessible = parks.get("numberOfSpaces"), parks.get("numberOfAccessibleSpaces")
        value = f"{spaces:,} spaces" if isinstance(spaces, int) and spaces else "Yes"
        if isinstance(accessible, int) and accessible:
            value += f" ({accessible} accessible)"
        out.append(("Car park", value))
    cycling = station.get("cycling") or {}
    if cycling.get("cycleStorageAvailable"):
        spaces = _get(cycling, "spaces", "numberOfSpaces")
        out.append(("Cycle parking", f"{spaces:,} spaces" + (", sheltered" if cycling.get("sheltered") else "")
                    if isinstance(spaces, int) and spaces else "Yes"))
    if _get(station, "transportLinks", "taxi", "available"):
        out.append(("Taxis", "Taxi rank"))
    if _get(station, "stationFacilities", "wifi", "available"):
        out.append(("Wi-Fi", "Yes"))
    if _get(station, "stationFacilities", "refreshments", "available"):
        out.append(("Food and drink", plain(_get(station, "stationFacilities", "refreshments", "notes")) or "Yes"))
    if _get(station, "loungesAndWaiting", "waitingFacility", "available"):
        out.append(("Waiting room", "Yes"))
    helpline = plain(_get(station, "staffAssistance", "helpline", "operatorContactDetails", "note"))
    if helpline and re.search(r"\d{4}", helpline):
        out.append(("Help line", helpline))
    return out


def replacement_bus_note(station: dict | None) -> str | None:
    """Where replacement buses go from, in the station's own words."""
    if not isinstance(station, dict) or not _get(station, "transportLinks", "replacementBus", "available"):
        return None
    return plain(_get(station, "transportLinks", "replacementBus", "notes"))


def operator(station: dict | None) -> str | None:
    return str(_get(station, "stationOperator", "name") or "").strip() or None if isinstance(station, dict) else None
