from __future__ import annotations

from collections import deque
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from math import asin, cos, radians, sin, sqrt
from threading import RLock

from .models import Event, SourceStatus, Vehicle


def _distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = radians(lat1), radians(lat2)
    dp = radians(lat2 - lat1)
    dl = radians(lon2 - lon1)
    a = sin(dp / 2) ** 2 + cos(p1) * cos(p2) * sin(dl / 2) ** 2
    return 2 * r * asin(sqrt(a))


def _inside(lat: float, lon: float, bbox: tuple[float, float, float, float] | None) -> bool:
    if not bbox:
        return True
    west, south, east, north = bbox
    return west <= lon <= east and south <= lat <= north


class LiveState:
    def __init__(self) -> None:
        self._lock = RLock()
        self._vehicles: dict[str, Vehicle] = {}
        self._events: dict[str, Event] = {}
        self._sources: dict[str, SourceStatus] = {}
        self._unmapped: deque[dict] = deque(maxlen=500)

    def upsert(self, vehicle: Vehicle) -> None:
        with self._lock:
            self._vehicles[vehicle.id] = deepcopy(vehicle)

    def upsert_event(self, event: Event) -> None:
        with self._lock:
            self._events[event.id] = deepcopy(event)

    def replace_events_for_source(self, source: str, events: list[Event]) -> None:
        """Replace one source's snapshot atomically so cleared incidents disappear."""
        with self._lock:
            current_ids = {k for k, e in self._events.items() if e.source == source}
            incoming_ids = {e.id for e in events}
            for old_id in current_ids - incoming_ids:
                self._events.pop(old_id, None)
            for event in events:
                self._events[event.id] = deepcopy(event)

    def get(self, activity_id: str) -> Vehicle | Event | None:
        with self._lock:
            item = self._vehicles.get(activity_id) or self._events.get(activity_id)
            return deepcopy(item) if item is not None else None

    def get_event(self, event_id: str) -> Event | None:
        with self._lock:
            item = self._events.get(event_id)
            return deepcopy(item) if item is not None else None

    def find_train_by_headcode(self, headcode: str, lat: float | None = None, lon: float | None = None) -> Vehicle | None:
        headcode = (headcode or "").strip().upper()
        if not headcode:
            return None
        with self._lock:
            candidates = [v for v in self._vehicles.values() if v.type == "train" and (v.headcode or "").upper() == headcode]
            if not candidates:
                return None
            if lat is not None and lon is not None:
                candidates.sort(key=lambda v: _distance_km(v.lat, v.lon, lat, lon))
            else:
                candidates.sort(key=lambda v: v.last_updated, reverse=True)
            return deepcopy(candidates[0])

    def all(
        self,
        max_age_minutes: int = 45,
        bbox: tuple[float, float, float, float] | None = None,
        include_buses: bool = True,
        bus_limit: int | None = None,
        include_vessels: bool = True,
        vessel_limit: int | None = None,
    ) -> list[Vehicle]:
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(minutes=max_age_minutes)
        bus_cutoff = now - timedelta(minutes=5)
        vessel_cutoff = now - timedelta(minutes=10)

        with self._lock:
            result: list[Vehicle] = []
            buses: list[Vehicle] = []
            vessels: list[Vehicle] = []
            for stored in self._vehicles.values():
                if stored.type == "bus":
                    if not include_buses or stored.last_updated < bus_cutoff:
                        continue
                elif stored.type in {"vessel", "sar"} and stored.source == "AISStream":
                    if not include_vessels or stored.last_updated < vessel_cutoff:
                        continue
                elif stored.last_updated < cutoff:
                    continue

                if not _inside(stored.lat, stored.lon, bbox):
                    continue

                v = deepcopy(stored)
                # TRUST is a reporting-point feed, not GPS. Never draw a train on
                # a straight geometric chord between reports: that can cut across
                # fields/towns instead of following the railway. Rail stays at the
                # last confirmed mapped report until the next real movement.
                if (
                    v.type != "train"
                    and v.reported_lat is not None and v.reported_lon is not None
                    and v.next_lat is not None and v.next_lon is not None
                    and v.estimate_started_at and v.estimate_due_at
                    and v.estimate_due_at > v.estimate_started_at
                ):
                    total = (v.estimate_due_at - v.estimate_started_at).total_seconds()
                    elapsed = (now - v.estimate_started_at).total_seconds()
                    fraction = max(0.0, min(0.97, elapsed / total))
                    v.lat = v.reported_lat + (v.next_lat - v.reported_lat) * fraction
                    v.lon = v.reported_lon + (v.next_lon - v.reported_lon) * fraction
                    if fraction > 0.02:
                        v.position_method = "estimated"
                        v.position_label = f"Estimated between confirmed reports • {v.position_label}"

                if v.type == "bus":
                    buses.append(v)
                elif v.type in {"vessel", "sar"} and v.source == "AISStream":
                    vessels.append(v)
                else:
                    result.append(v)

            buses.sort(key=lambda x: x.last_updated, reverse=True)
            if bus_limit is not None:
                buses = buses[:bus_limit]
            vessels.sort(key=lambda x: x.last_updated, reverse=True)
            if vessel_limit is not None:
                vessels = vessels[:vessel_limit]
            result.extend(buses)
            result.extend(vessels)
            return result

    def all_events(self, bbox: tuple[float, float, float, float] | None = None) -> list[Event]:
        with self._lock:
            rows = [deepcopy(e) for e in self._events.values() if _inside(e.lat, e.lon, bbox)]
        rows.sort(key=lambda x: x.last_updated, reverse=True)
        return rows

    def all_activities(
        self,
        bbox: tuple[float, float, float, float] | None = None,
        include_buses: bool = True,
        bus_limit: int | None = None,
        include_vessels: bool = True,
        vessel_limit: int | None = None,
    ) -> list[Vehicle | Event]:
        return self.all(
            bbox=bbox, include_buses=include_buses, bus_limit=bus_limit,
            include_vessels=include_vessels, vessel_limit=vessel_limit,
        ) + self.all_events(bbox=bbox)

    def set_tracking_count(self, activity_id: str, count: int) -> None:
        """Update the live viewer count for an activity without changing its source timestamp."""
        with self._lock:
            item = self._vehicles.get(activity_id) or self._events.get(activity_id)
            if item is not None:
                item.tracking_count = max(0, int(count))

    def remove(self, vehicle_id: str) -> None:
        with self._lock:
            self._vehicles.pop(vehicle_id, None)

    def set_source(self, status: SourceStatus) -> None:
        with self._lock:
            self._sources[status.name] = deepcopy(status)

    def source(self, name: str) -> SourceStatus | None:
        with self._lock:
            item = self._sources.get(name)
            return deepcopy(item) if item is not None else None

    def sources(self) -> list[SourceStatus]:
        with self._lock:
            return deepcopy(list(self._sources.values()))

    def add_unmapped(self, item: dict) -> None:
        with self._lock:
            self._unmapped.appendleft(deepcopy(item))

    def unmapped(self) -> list[dict]:
        with self._lock:
            return deepcopy(list(self._unmapped))

    def count_type(self, type_name: str, max_age_minutes: int | None = None) -> int:
        now = datetime.now(timezone.utc)
        with self._lock:
            vehicles = [v for v in self._vehicles.values() if v.type == type_name]
            if max_age_minutes is not None:
                cutoff = now - timedelta(minutes=max_age_minutes)
                vehicles = [v for v in vehicles if v.last_updated >= cutoff]
            return len(vehicles) + sum(e.type == type_name for e in self._events.values())

    def summary(self) -> dict:
        vehicles = self.all()
        events = self.all_events()
        activities = [*vehicles, *events]
        keys = {"train", "bus", "vessel", "sar", "aircraft", "satellite", "gritter", "rescue", "road", "incident", "flood", "weather", "earthquake", "river", "tide", "wave", "grid", "airquality", "bike", "space", "outage", "camera", "other"}
        return {
            "visible": len(activities),
            "moving": sum(v.type in {"train", "bus", "vessel", "sar", "aircraft", "satellite", "gritter"} for v in vehicles),
            "tracking": sum(x.tracking_count for x in activities),
            "interesting": sum(x.interesting for x in activities),
            "events": len(events),
            "by_type": {key: sum(x.type == key for x in activities) for key in keys},
        }


state = LiveState()
