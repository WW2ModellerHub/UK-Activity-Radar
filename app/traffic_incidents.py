"""Live road incidents from TomTom, for the part of the map people look at.

National Highways' free closures feed lists planned works and the odd
emergency closure, but not crashes, breakdowns or queues. TomTom's Incident
Details service does, with the delay, the queue length and the stretch
affected. It is called only for the area on screen when the Incidents or
Roadworks layer is on, in half-degree cells cached for two minutes, under a
daily request cap.
"""
from __future__ import annotations

import math
import threading
import time
from datetime import datetime, timezone

import requests

from .models import Event

CELL_DEGREES = 0.5
CELL_SECONDS = 120
CELL_FORGET_SECONDS = 15 * 60
MAX_CELLS_PER_VIEW = 12
SOURCE = "TomTom Traffic"
FIELDS = ("{incidents{type,geometry{type,coordinates},properties{id,iconCategory,magnitudeOfDelay,"
          "events{description,code,iconCategory},startTime,endTime,from,to,length,delay,roadNumbers,timeValidity}}}")

# TomTom iconCategory -> (layer, plain cause, management)
CATEGORY = {
    1: ("incident", "Accident", None),
    2: ("incident", "Fog", None),
    3: ("incident", "Dangerous conditions", None),
    4: ("incident", "Heavy rain", None),
    5: ("incident", "Ice", None),
    6: ("incident", "Queueing traffic", None),
    7: ("incident", None, "Lane closure"),
    8: ("incident", None, "Road closed"),
    9: ("road", "Roadworks", None),
    10: ("incident", "Strong wind", None),
    11: ("incident", "Flooding", None),
    14: ("incident", "Broken-down vehicle", None),
}


def _cells(bbox: tuple[float, float, float, float]) -> list[tuple[int, int]]:
    west, south, east, north = bbox
    return [
        (cx, cy)
        for cx in range(math.floor(west / CELL_DEGREES), math.floor(east / CELL_DEGREES) + 1)
        for cy in range(math.floor(south / CELL_DEGREES), math.floor(north / CELL_DEGREES) + 1)
    ]


def _when(value) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def parse_incidents(payload: dict) -> list[Event]:
    """TomTom Incident Details v5 -> events with plain facts in ``details``."""
    events: list[Event] = []
    for feature in (payload or {}).get("incidents") or []:
        props = (feature or {}).get("properties") or {}
        geometry = (feature or {}).get("geometry") or {}
        coords = geometry.get("coordinates") or []
        if geometry.get("type") == "Point":
            coords = [coords]
        points = [(float(c[1]), float(c[0])) for c in coords if isinstance(c, list) and len(c) >= 2]
        if not points or str(props.get("timeValidity") or "present") != "present":
            continue
        lat, lon = points[len(points) // 2]
        if not (49 <= lat <= 61.5 and -9.5 <= lon <= 3.5):
            continue
        category = int(props.get("iconCategory") or 0)
        layer, cause, management = CATEGORY.get(category, ("incident", None, None))
        descriptions = [str(e.get("description")).strip() for e in props.get("events") or [] if e.get("description")]
        roads = [str(r) for r in props.get("roadNumbers") or [] if r]
        road = roads[0] if roads else None
        start, end = str(props.get("from") or "").strip(), str(props.get("to") or "").strip()
        location = f"{start} to {end}" if start and end and start != end else (start or end or None)
        delay = props.get("delay")
        length = props.get("length")
        magnitude = int(props.get("magnitudeOfDelay") or 0)
        incident_id = str(props.get("id") or f"{lat:.5f},{lon:.5f},{category}")
        headline = descriptions[0] if descriptions else (cause or management or "Traffic incident")
        events.append(Event(
            id=f"tomtom:{incident_id}",
            type=layer,
            name=f"{road} — {headline}" if road else headline,
            subtitle=location or "",
            lat=lat, lon=lon,
            category="Live traffic incident" if layer == "incident" else "Roadworks",
            status="Active",
            road=road,
            reason=headline,
            start_time=_when(props.get("startTime")),
            end_time=_when(props.get("endTime")),
            position_method="static",
            position_label="TomTom live traffic",
            source=SOURCE,
            source_id=incident_id,
            details={
                "cause": cause,
                "management": management,
                "delay_minutes": round(float(delay) / 60) if isinstance(delay, (int, float)) and delay >= 60 else None,
                "queue_metres": float(length) if category == 6 and isinstance(length, (int, float)) and length > 0 else None,
                "affected_metres": float(length) if isinstance(length, (int, float)) else None,
                "comment": "; ".join(dict.fromkeys(descriptions)) or None,
                "location": location,
                "magnitude": magnitude,
                "road_closed": category == 8 or magnitude == 4,
                "planned": category == 9,
                "line": [[round(a, 5), round(b, 5)] for a, b in points[:: max(1, len(points) // 60)]],
            },
            interesting=layer == "incident" and magnitude >= 2,
            last_updated=datetime.now(timezone.utc),
        ))
    return events


class TrafficIncidentService:
    def __init__(self, settings, state, session: requests.Session | None = None, daily_limit: int = 2000) -> None:
        self.settings = settings
        self.state = state
        self._session = session or requests.Session()
        self._lock = threading.Lock()
        self._fetched: dict[tuple[int, int], float] = {}
        self._day = ""
        self._used = 0
        self.daily_limit = daily_limit

    @property
    def ready(self) -> bool:
        return bool(getattr(self.settings, "tomtom_traffic_api_key", ""))

    def _budget_ok(self) -> bool:
        today = datetime.now(timezone.utc).date().isoformat()
        if today != self._day:
            self._day, self._used = today, 0
        return self._used < self.daily_limit

    def ensure(self, bbox: tuple[float, float, float, float]) -> int:
        """Fetch any stale cells in view; returns how many were fetched."""
        if not self.ready:
            return 0
        cells = _cells(bbox)
        if len(cells) > MAX_CELLS_PER_VIEW:
            return 0
        now = time.monotonic()
        with self._lock:
            due = [cell for cell in cells if now - self._fetched.get(cell, -1e9) > CELL_SECONDS]
            for cell in due:
                self._fetched[cell] = now  # claim it; a failed fetch retries after CELL_SECONDS
            forgotten = [cell for cell, at in self._fetched.items() if now - at > CELL_FORGET_SECONDS]
            for cell in forgotten:
                self._fetched.pop(cell, None)
        for cell in forgotten:
            self.state.replace_events_with_prefix(f"tomtom:{cell[0]},{cell[1]}:", [])
        fetched = 0
        for cx, cy in due:
            with self._lock:
                if not self._budget_ok():
                    break
                self._used += 1
            west, south = cx * CELL_DEGREES, cy * CELL_DEGREES
            try:
                response = self._session.get(
                    f"{self.settings.tomtom_traffic_base_url}/traffic/services/5/incidentDetails",
                    params={
                        "key": self.settings.tomtom_traffic_api_key,
                        "bbox": f"{west},{south},{west + CELL_DEGREES},{south + CELL_DEGREES}",
                        "fields": FIELDS, "language": "en-GB", "timeValidityFilter": "present",
                    },
                    timeout=(4, 10),
                )
                response.raise_for_status()
                events = parse_incidents(response.json())
            except (requests.RequestException, ValueError):
                continue
            # Keep each incident in the one cell holding its midpoint (TomTom
            # returns everything touching the box), keyed by cell so a cell's
            # refresh replaces exactly what that cell showed before.
            prefix = f"tomtom:{cx},{cy}:"
            mine = []
            for event in events:
                if west <= event.lon < west + CELL_DEGREES and south <= event.lat < south + CELL_DEGREES:
                    event.id = prefix + event.source_id
                    mine.append(event)
            self.state.replace_events_with_prefix(prefix, mine)
            fetched += 1
        return fetched
