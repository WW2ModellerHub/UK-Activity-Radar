from __future__ import annotations

from collections import deque
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from math import asin, cos, radians, sin, sqrt
from threading import RLock

from .cool_classifier import apply_cool_classification
from .models import Event, SourceStatus, Vehicle


TRAIN_LIVE_REPORT_MINUTES = 45
TRAIN_PROJECTION_MINUTES = 90
TRAIN_ROLLOVER_GRACE_MINUTES = 10
# How long a train restored from the warm-start snapshot may be shown before a
# live report replaces it.
RESTORED_SNAPSHOT_MINUTES = 15
# A finished service stays visible briefly so a viewer sees it arrive.
TERMINATED_VISIBLE_MINUTES = 2
# A Train Describer headcode that never matched a timetable and has not moved
# for this long is usually stock stabled or laid over in a platform.
UNMATCHED_TD_IDLE_MINUTES = 20


def _train_finished(vehicle: Vehicle, now: datetime) -> bool:
    """True for a train that has finished running and should leave the map."""
    if vehicle.type != "train":
        return False
    finished_before = now - timedelta(minutes=TERMINATED_VISIBLE_MINUTES)
    if vehicle.terminated_at is not None and vehicle.terminated_at <= finished_before:
        return True
    if str(vehicle.status or "").lower().startswith("terminated") and vehicle.last_updated < finished_before:
        return True
    return bool(
        vehicle.source == "Network Rail TD"
        and not vehicle.schedule_id
        and vehicle.last_updated < now - timedelta(minutes=UNMATCHED_TD_IDLE_MINUTES)
    )


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


def _estimated_train_position(vehicle: Vehicle, now: datetime) -> tuple[float, float] | None:
    """Return the browser-equivalent position for viewport continuity.

    The stored ``lat``/``lon`` remains the confirmed SMART reporting location.
    Streaming decisions must use the moving estimate, however, or a train whose
    reporting point sits outside the viewport disappears while its rendered
    route position is still on screen.
    """
    if not (
        vehicle.estimate_started_at and vehicle.estimate_due_at
        and vehicle.estimate_due_at > vehicle.estimate_started_at
    ):
        return None
    points = []
    for raw in vehicle.estimate_path or []:
        try:
            lat, lon = float(raw[0]), float(raw[1])
        except (IndexError, TypeError, ValueError):
            continue
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            points.append((lat, lon))
    # Report endpoints are not railway geometry.  Falling back to their
    # straight chord can put a train miles away from the physical track.
    if len(points) < 2:
        return None
    total_seconds = (vehicle.estimate_due_at - vehicle.estimate_started_at).total_seconds()
    fraction = max(0.0, min(1.0, (now - vehicle.estimate_started_at).total_seconds() / total_seconds))
    legs: list[tuple[tuple[float, float], tuple[float, float], float, float]] = []
    total_distance = 0.0
    for first, second in zip(points, points[1:]):
        length = _distance_km(first[0], first[1], second[0], second[1])
        if length <= 0:
            continue
        legs.append((first, second, length, total_distance))
        total_distance += length
    if not legs or total_distance <= 0:
        return None
    target = fraction * total_distance
    first, second, length, started = next(
        (leg for leg in legs if target <= leg[3] + leg[2]),
        legs[-1],
    )
    local = max(0.0, min(1.0, (target - started) / length))
    return (
        first[0] + (second[0] - first[0]) * local,
        first[1] + (second[1] - first[1]) * local,
    )


def _train_has_current_projection(vehicle: Vehicle, now: datetime) -> bool:
    """Keep a matched service continuous through a temporary feed outage.

    A TRUST/TD timestamp remains the last *confirmed* railway report.  It must
    not, however, make a train vanish at the generic 45-minute cutoff while a
    bounded, track-shaped leg from that identified service is still current.
    The 90-minute ceiling prevents an old timetable from being presented as a
    live train indefinitely if the upstream feed stays unavailable.
    """
    if vehicle.type != "train" or not vehicle.schedule_id or not vehicle.service_date:
        return False
    if vehicle.terminated_at is not None:
        return False
    if vehicle.last_updated < now - timedelta(minutes=TRAIN_PROJECTION_MINUTES):
        return False
    if not (
        vehicle.estimate_started_at
        and vehicle.estimate_due_at
        and vehicle.estimate_due_at > vehicle.estimate_started_at
        and vehicle.estimate_due_at >= now - timedelta(minutes=TRAIN_ROLLOVER_GRACE_MINUTES)
    ):
        return False
    return _estimated_train_position(vehicle, now) is not None


class LiveState:
    def __init__(self) -> None:
        self._lock = RLock()
        self._vehicles: dict[str, Vehicle] = {}
        self._train_uid_index: dict[str, set[str]] = {}
        self._train_headcode_index: dict[str, set[str]] = {}
        self._events: dict[str, Event] = {}
        self._sources: dict[str, SourceStatus] = {}
        self._unmapped: deque[dict] = deque(maxlen=500)
        self._revision = 0

    @property
    def revision(self) -> int:
        """Monotonic change marker used to avoid resending unchanged map snapshots."""
        with self._lock:
            return self._revision

    def _touch_unlocked(self) -> None:
        self._revision += 1

    def _index_unlocked(self, vehicle: Vehicle) -> None:
        if vehicle.type != 'train':
            return
        if vehicle.train_uid:
            self._train_uid_index.setdefault(str(vehicle.train_uid).strip(), set()).add(vehicle.id)
        code = (vehicle.headcode or '').strip().upper()
        if code:
            self._train_headcode_index.setdefault(code, set()).add(vehicle.id)

    def _unindex_unlocked(self, vehicle: Vehicle) -> None:
        if vehicle.type != 'train':
            return
        if vehicle.train_uid:
            uid = str(vehicle.train_uid).strip()
            self._train_uid_index.get(uid, set()).discard(vehicle.id)
            if not self._train_uid_index.get(uid):
                self._train_uid_index.pop(uid, None)
        code = (vehicle.headcode or '').strip().upper()
        if code:
            self._train_headcode_index.get(code, set()).discard(vehicle.id)
            if not self._train_headcode_index.get(code):
                self._train_headcode_index.pop(code, None)

    def upsert(self, vehicle: Vehicle) -> None:
        stored = apply_cool_classification(deepcopy(vehicle))
        with self._lock:
            previous=self._vehicles.get(vehicle.id)
            if previous is not None and stored.type == 'train' and stored.last_updated < previous.last_updated:
                return
            if stored.type == 'train' and stored.train_uid:
                # One railway service must keep one map identity. TD and TRUST
                # can briefly create separate records before the timetable
                # match is known; once both carry the same UID/service date the
                # visible marker used to flip between ids on every report,
                # which the browser saw as a remove + add (a "blinking" train).
                incumbent = self._duplicate_service_unlocked(stored)
                if incumbent is not None:
                    self._drop_vehicle_unlocked(stored.id)
                    if stored.last_updated < incumbent.last_updated:
                        # The incumbent already holds a newer report for this
                        # service; the late duplicate adds nothing visible.
                        self._touch_unlocked()
                        return
                    stored.id = incumbent.id
                    previous = incumbent
            if previous is not None:
                self._unindex_unlocked(previous)
            self._vehicles[stored.id] = stored
            self._index_unlocked(stored)
            self._touch_unlocked()

    def _duplicate_service_unlocked(self, stored: Vehicle) -> Vehicle | None:
        uid = str(stored.train_uid or '').strip()
        for key in sorted(self._train_uid_index.get(uid, set())):
            other = self._vehicles.get(key)
            if (
                other is None or key == stored.id or other.type != 'train'
                or not stored.service_date or other.service_date != stored.service_date
            ):
                continue
            return other
        return None

    def _drop_vehicle_unlocked(self, vehicle_id: str) -> None:
        removed = self._vehicles.pop(vehicle_id, None)
        if removed is not None:
            self._unindex_unlocked(removed)

    def upsert_event(self, event: Event) -> None:
        stored = apply_cool_classification(deepcopy(event))
        with self._lock:
            self._events[event.id] = stored
            self._touch_unlocked()

    def replace_events_for_source(self, source: str, events: list[Event]) -> None:
        """Replace one source's snapshot atomically so cleared incidents disappear."""
        classified = [apply_cool_classification(deepcopy(event)) for event in events]
        with self._lock:
            current_ids = {k for k, e in self._events.items() if e.source == source}
            incoming_ids = {e.id for e in classified}
            for old_id in current_ids - incoming_ids:
                self._events.pop(old_id, None)
            for event in classified:
                self._events[event.id] = event
            self._touch_unlocked()

    def get(self, activity_id: str) -> Vehicle | Event | None:
        with self._lock:
            item = self._vehicles.get(activity_id) or self._events.get(activity_id)
            copy = deepcopy(item) if item is not None else None
        return apply_cool_classification(copy) if copy is not None else None

    def get_event(self, event_id: str) -> Event | None:
        with self._lock:
            item = self._events.get(event_id)
            copy = deepcopy(item) if item is not None else None
        return apply_cool_classification(copy) if copy is not None else None

    def find_train_by_headcode(self, headcode: str, lat: float | None = None, lon: float | None = None) -> Vehicle | None:
        headcode = (headcode or "").strip().upper()
        if not headcode:
            return None
        with self._lock:
            # Indexed: this runs for every Train Describer step, and a scan
            # of every bus and train per message starved the feed thread.
            candidates = [
                self._vehicles[key] for key in self._train_headcode_index.get(headcode, ())
                if key in self._vehicles
            ]
            if not candidates:
                return None
            if lat is not None and lon is not None:
                candidates.sort(key=lambda v: _distance_km(v.lat, v.lon, lat, lon))
            else:
                candidates.sort(key=lambda v: v.last_updated, reverse=True)
            copy = deepcopy(candidates[0])
        return apply_cool_classification(copy)

    def _train_for_uid_unlocked(self, uid: str, service_date: str | None = None) -> Vehicle | None:
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(minutes=TRAIN_LIVE_REPORT_MINUTES)
        candidates = [self._vehicles[key] for key in self._train_uid_index.get(uid, set()) if key in self._vehicles]
        candidates = [item for item in candidates if (item.last_updated >= cutoff or _train_has_current_projection(item, now))
                      and (service_date is None or item.service_date == service_date)]
        return max(candidates, key=lambda item: item.last_updated, default=None)

    def find_train_by_uid(self, train_uid: str, service_date: str | None = None) -> Vehicle | None:
        uid=str(train_uid or '').strip()
        if not uid:
            return None
        with self._lock:
            item=self._train_for_uid_unlocked(uid, service_date)
            copy=deepcopy(item) if item is not None else None
        return apply_cool_classification(copy) if copy is not None else None

    def trains_by_uids(self, train_uids: set[str]) -> dict[str, Vehicle]:
        wanted={str(uid).strip() for uid in train_uids if str(uid).strip()}
        if not wanted:
            return {}
        with self._lock:
            found={}
            for uid in wanted:
                item=self._train_for_uid_unlocked(uid)
                if item is not None:
                    found[uid]=deepcopy(item)
        return found

    def trains_by_services(self, services: set[tuple[str, str]]) -> dict[tuple[str, str], Vehicle]:
        with self._lock:
            found = {}
            for uid, service_date in services:
                item = self._train_for_uid_unlocked(uid, service_date)
                if item is not None:
                    found[(uid, service_date)] = deepcopy(item)
            return found

    def all(
        self,
        max_age_minutes: int = 45,
        bbox: tuple[float, float, float, float] | None = None,
        train_bbox: tuple[float, float, float, float] | None = None,
        include_trains: bool = True,
        train_limit: int | None = None,
        cool_trains_only: bool = False,
        include_buses: bool = True,
        bus_limit: int | None = None,
        include_vessels: bool = True,
        vessel_limit: int | None = None,
        train_priority_ids: set[str] | frozenset[str] | None = None,
    ) -> list[Vehicle]:
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(minutes=max_age_minutes)
        bus_cutoff = now - timedelta(minutes=5)
        vessel_cutoff = now - timedelta(minutes=10)
        cool_cutoff = now - timedelta(minutes=60)
        restored_cutoff = now - timedelta(minutes=RESTORED_SNAPSHOT_MINUTES)

        with self._lock:
            regular_refs: list[Vehicle] = []
            train_refs: list[Vehicle] = []
            bus_refs: list[Vehicle] = []
            vessel_refs: list[Vehicle] = []
            for stored in self._vehicles.values():
                if stored.type == "train":
                    if (
                        not include_trains
                        or (stored.last_updated < cutoff and not _train_has_current_projection(stored, now))
                        or (cool_trains_only and not stored.cool_candidate)
                    ):
                        continue
                    if stored.train_uid:
                        current = self._train_for_uid_unlocked(str(stored.train_uid).strip(), stored.service_date)
                        if current is not None and current.id != stored.id:
                            continue
                    if _train_finished(stored, now):
                        continue
                    if stored.restored_snapshot and stored.last_updated < restored_cutoff:
                        # A warm-start row that no live report has replaced is
                        # not a live train: it has usually finished its journey
                        # during the restart. Do not show it for 45-90 minutes.
                        continue
                elif stored.type == "bus":
                    if not include_buses or stored.last_updated < bus_cutoff:
                        continue
                elif stored.type in {"vessel", "sar"} and stored.source == "AISStream":
                    if not include_vessels or stored.last_updated < vessel_cutoff:
                        continue
                elif stored.last_updated < cutoff:
                    continue

                effective_bbox = train_bbox if stored.type == "train" and train_bbox is not None else bbox
                if not _inside(stored.lat, stored.lon, effective_bbox):
                    # Only walk the estimate path when the confirmed point is
                    # off-screen; doing it for every train on every call was
                    # a fifth of this function's cost.
                    visible_position = _estimated_train_position(stored, now) if stored.type == "train" else None
                    if not (visible_position and _inside(visible_position[0], visible_position[1], effective_bbox)):
                        continue

                if stored.type == "train":
                    train_refs.append(stored)
                elif stored.type == "bus":
                    bus_refs.append(stored)
                elif stored.type in {"vessel", "sar"} and stored.source == "AISStream":
                    vessel_refs.append(stored)
                else:
                    regular_refs.append(stored)

            # Apply high-volume limits before copying/classifying records.  The
            # old order deep-copied every visible bus and only then discarded
            # most of them, which made each browser update needlessly costly.
            # When more trains are eligible than the stream cap, ordering by
            # report time alone reshuffles the selection on every TD step: a
            # train that has not reported for a few seconds falls below the
            # cut, is removed from the browser, then returns on its next step.
            # Trains already on this viewer's map (and the one being watched)
            # keep their place while they remain eligible.
            priority = train_priority_ids or ()
            train_refs.sort(key=lambda x: (x.id in priority, x.last_updated), reverse=True)
            bus_refs.sort(key=lambda x: x.last_updated, reverse=True)
            vessel_refs.sort(key=lambda x: x.last_updated, reverse=True)
            if train_limit is not None:
                train_refs = train_refs[:train_limit]
            if bus_limit is not None:
                bus_refs = bus_refs[:bus_limit]
            if vessel_limit is not None:
                vessel_refs = vessel_refs[:vessel_limit]
            # Shallow copies: every Vehicle field is an immutable scalar
            # except ``estimate_path``, which writers replace rather than
            # mutate. Deep-copying thousands of paths while holding the state
            # lock (once a second per viewer) blocked the live rail feed.
            result = [item.model_copy() for item in regular_refs]
            trains = [item.model_copy() for item in train_refs]
            buses = [item.model_copy() for item in bus_refs]
            vessels = [item.model_copy() for item in vessel_refs]

        for v in [*result, *trains, *buses, *vessels]:
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

            # Classification is stored at ingest. Re-run it only when a former
            # candidate has just aged out; re-scoring thousands of unchanged
            # buses on every frame was pure overhead.
            if v.cool_candidate and v.last_updated < cool_cutoff:
                apply_cool_classification(v, now=now)

        result.extend(trains)
        result.extend(buses)
        result.extend(vessels)
        return result

    def trains_where(self, predicate) -> list[Vehicle]:
        """Copy only the trains a maintenance pass actually needs to change.

        ``predicate`` is evaluated on stored rows under the lock and must be
        cheap and side-effect free. This replaces copying every train once a
        second just to find the handful whose timetable leg has elapsed.
        """
        with self._lock:
            return [
                deepcopy(item) for item in self._vehicles.values()
                if item.type == "train" and predicate(item)
            ]

    def all_events(
        self,
        bbox: tuple[float, float, float, float] | None = None,
        exclude_types: set[str] | None = None,
    ) -> list[Event]:
        now = datetime.now(timezone.utc)
        cool_cutoff = now - timedelta(minutes=60)
        excluded = exclude_types or set()
        with self._lock:
            rows = [
                deepcopy(e) for e in self._events.values()
                if e.type not in excluded and _inside(e.lat, e.lon, bbox)
            ]
        for row in rows:
            if row.cool_candidate and row.last_updated < cool_cutoff:
                apply_cool_classification(row, now=now)
        rows.sort(key=lambda x: x.last_updated, reverse=True)
        return rows

    def all_activities(
        self,
        bbox: tuple[float, float, float, float] | None = None,
        include_trains: bool = True,
        train_limit: int | None = None,
        cool_trains_only: bool = False,
        include_buses: bool = True,
        bus_limit: int | None = None,
        include_vessels: bool = True,
        vessel_limit: int | None = None,
    ) -> list[Vehicle | Event]:
        return self.all(
            bbox=bbox, include_trains=include_trains, train_limit=train_limit,
            cool_trains_only=cool_trains_only,
            include_buses=include_buses, bus_limit=bus_limit,
            include_vessels=include_vessels, vessel_limit=vessel_limit,
        ) + self.all_events(bbox=bbox)

    def cool_candidates(self, limit: int = 25, category: str | None = None) -> list[Vehicle | Event]:
        """Return a small national priority list without copying routine traffic."""
        now = datetime.now(timezone.utc)
        vehicle_cutoff = now - timedelta(minutes=45)
        event_cutoff = now - timedelta(minutes=60)
        wanted = (category or "").strip().lower()
        with self._lock:
            candidates: list[Vehicle | Event] = [
                vehicle for vehicle in self._vehicles.values()
                if vehicle.cool_candidate
                and vehicle.last_updated >= vehicle_cutoff
                and not _train_finished(vehicle, now)
                and (not wanted or str(vehicle.cool_category or "").lower() == wanted)
            ]
            candidates.extend(
                event for event in self._events.values()
                if event.cool_candidate
                and event.last_updated >= event_cutoff
                and (not wanted or str(event.cool_category or "").lower() == wanted)
            )
            candidates.sort(
                key=lambda item: (int(item.cool_score or 0), item.last_updated),
                reverse=True,
            )
            return deepcopy(candidates[:max(1, min(int(limit), 50))])

    def set_tracking_count(self, activity_id: str, count: int) -> None:
        """Update the live viewer count for an activity without changing its source timestamp."""
        with self._lock:
            item = self._vehicles.get(activity_id) or self._events.get(activity_id)
            if item is not None:
                new_count = max(0, int(count))
                if item.tracking_count != new_count:
                    item.tracking_count = new_count
                    self._touch_unlocked()

    def remove(self, vehicle_id: str) -> None:
        with self._lock:
            if vehicle_id in self._vehicles:
                self._drop_vehicle_unlocked(vehicle_id)
                self._touch_unlocked()

    def prune_stale(self, *, now: datetime | None = None) -> int:
        """Remove expired vehicle identities using source-appropriate retention.

        Normal map queries already hide stale rows. This maintenance path also
        releases them from memory so a public single-process service does not
        grow forever as vehicle identifiers change through the day.
        """
        current = now or datetime.now(timezone.utc)
        retention = {
            "bus": timedelta(minutes=15),
            "aircraft": timedelta(minutes=30),
            "sar": timedelta(minutes=30),
            "vessel": timedelta(minutes=30),
            "train": timedelta(hours=3),
        }
        removed = 0
        with self._lock:
            for activity_id, vehicle in list(self._vehicles.items()):
                keep_for = retention.get(vehicle.type, timedelta(hours=3))
                if vehicle.last_updated >= current - keep_for:
                    continue
                self._drop_vehicle_unlocked(activity_id)
                removed += 1
            if removed:
                self._touch_unlocked()
        return removed

    def set_source(self, status: SourceStatus) -> None:
        with self._lock:
            self._sources[status.name] = deepcopy(status)
            self._touch_unlocked()

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
            vehicles = [v for v in self._vehicles.values() if v.type == type_name and not _train_finished(v, now)]
            if max_age_minutes is not None:
                cutoff = now - timedelta(minutes=max_age_minutes)
                vehicles = [
                    v for v in vehicles
                    if v.last_updated >= cutoff
                    or (v.type == "train" and _train_has_current_projection(v, now))
                ]
            return len(vehicles) + sum(e.type == type_name for e in self._events.values())

    def type_counts(self, max_age_minutes: dict[str, int] | None = None) -> dict[str, int]:
        """Count every activity type in one pass, with optional vehicle freshness limits."""
        now = datetime.now(timezone.utc)
        ages = max_age_minutes or {}
        cutoffs = {type_name: now - timedelta(minutes=minutes) for type_name, minutes in ages.items()}
        counts: dict[str, int] = {}
        with self._lock:
            for vehicle in self._vehicles.values():
                if _train_finished(vehicle, now):
                    continue
                type_cutoff = cutoffs.get(vehicle.type)
                if (
                    type_cutoff is not None
                    and vehicle.last_updated < type_cutoff
                    and not (vehicle.type == "train" and _train_has_current_projection(vehicle, now))
                ):
                    continue
                counts[vehicle.type] = counts.get(vehicle.type, 0) + 1
            # Match count_type: event counts are snapshots and are not affected
            # by a vehicle freshness limit supplied for the same type.
            for event in self._events.values():
                counts[event.type] = counts.get(event.type, 0) + 1
        return counts

    def summary(self) -> dict:
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(minutes=TRAIN_LIVE_REPORT_MINUTES)
        bus_cutoff = now - timedelta(minutes=5)
        vessel_cutoff = now - timedelta(minutes=10)
        cool_cutoff = now - timedelta(minutes=60)
        keys = {"train", "bus", "vessel", "sar", "aircraft", "satellite", "gritter", "rescue", "road", "incident", "flood", "weather", "earthquake", "river", "tide", "wave", "grid", "airquality", "bike", "space", "outage", "camera", "other"}
        by_type = {key: 0 for key in keys}
        visible = moving = tracking = interesting = cool = events_total = 0
        moving_types = {"train", "bus", "vessel", "sar", "aircraft", "satellite", "gritter"}

        # Summary figures do not need model copies, route interpolation, or a
        # second Cool classification pass.  With a national BODS snapshot that
        # removed tens of thousands of deep copies from every WebSocket tick.
        with self._lock:
            for vehicle in self._vehicles.values():
                if _train_finished(vehicle, now):
                    continue
                if vehicle.type == "bus":
                    if vehicle.last_updated < bus_cutoff:
                        continue
                elif vehicle.type in {"vessel", "sar"} and vehicle.source == "AISStream":
                    if vehicle.last_updated < vessel_cutoff:
                        continue
                elif vehicle.last_updated < cutoff and not (
                    vehicle.type == "train" and _train_has_current_projection(vehicle, now)
                ):
                    continue
                visible += 1
                if vehicle.type in moving_types:
                    moving += 1
                tracking += vehicle.tracking_count
                interesting += int(vehicle.interesting)
                cool += int(bool(vehicle.cool_candidate and vehicle.last_updated >= cool_cutoff))
                if vehicle.type in by_type:
                    by_type[vehicle.type] += 1

            for event in self._events.values():
                events_total += 1
                visible += 1
                tracking += event.tracking_count
                interesting += int(event.interesting)
                cool += int(bool(event.cool_candidate and event.last_updated >= cool_cutoff))
                if event.type in by_type:
                    by_type[event.type] += 1

        return {
            "visible": visible,
            "moving": moving,
            "tracking": tracking,
            "interesting": interesting,
            "cool": cool,
            "events": events_total,
            "by_type": by_type,
        }


state = LiveState()
