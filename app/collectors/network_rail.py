from __future__ import annotations

import json
from collections import deque
from threading import RLock
from datetime import datetime, timedelta, timezone
from math import asin, cos, radians, sin, sqrt
from pathlib import Path

from ..config import Settings
from ..models import SourceStatus, Vehicle
from ..schedule_store import ScheduleInfo, ScheduleStore
from ..state import LiveState
from ..train_identity import ATOC_OPERATOR_NAMES, operator_name, trust_operator_name
from .stomp_client import StompFeed


def _km(a: dict, b: dict) -> float:
    r = 6371.0
    lat1, lon1, lat2, lon2 = map(radians, [float(a["lat"]), float(a["lon"]), float(b["lat"]), float(b["lon"])])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    x = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 2 * r * asin(sqrt(x))


def _headcode_from_train_id(train_id: str) -> str | None:
    train_id = (train_id or "").strip()
    if len(train_id) >= 6:
        candidate = train_id[2:6].upper()
        if candidate and candidate != "0000":
            return candidate
    return None


class TrustCollector(StompFeed):
    """Real-time TRUST activation/movement feed.

    TRUST is a reporting-point feed rather than continuous GPS. Confirmed
    locations remain the source of truth; when the timetable exposes mapped
    timing points to the next report, clients may show a bounded estimate along
    that railway-shaped path until the next real movement arrives.
    """

    def __init__(self, settings: Settings, state: LiveState, station_file: Path, schedules: ScheduleStore) -> None:
        super().__init__(settings, state, name="network_rail", topic=settings.trust_topic)
        self.station_file = station_file
        self.schedules = schedules
        self.stations = self._load_stations()
        self.activations: dict[str, dict] = {}
        self.identities: dict[str, str] = {}
        self._canonical_ids: dict[str, str] = {}
        self._pending_movements: deque[dict] = deque(maxlen=5000)
        self._pending_lock = RLock()

    def _load_stations(self) -> dict[str, dict]:
        if not self.station_file.exists():
            return {}
        try:
            raw = json.loads(self.station_file.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else {}
        except Exception:
            return {}

    def reload_references(self) -> int:
        self.stations = self._load_stations()
        if self.stations:
            self._replay_pending()
        return len(self.stations)

    def _replay_pending(self) -> None:
        with self._pending_lock:
            pending = list(self._pending_movements)
            self._pending_movements.clear()
        if not pending:
            return
        status = self.state.source(self.name) or SourceStatus(name=self.name, enabled=True, connected=True, mode="live")
        still_unmapped: list[dict] = []
        for body in pending:
            if not self._movement(body, status, replay=True):
                still_unmapped.append(body)
        with self._pending_lock:
            for body in still_unmapped[-self._pending_movements.maxlen:]:
                self._pending_movements.append(body)
        status.message = (
            f"TRUST live • {status.messages_seen:,} messages • {status.movements_mapped:,} mapped • "
            f"{status.movements_unmapped:,} unmapped • {len(self.stations):,} mapped STANOX"
        )
        self.state.set_source(status)

    @staticmethod
    def _event_time(body: dict) -> datetime:
        for key in ("actual_timestamp", "planned_timestamp", "creation_timestamp", "msg_queue_timestamp"):
            raw = body.get(key)
            if raw:
                try:
                    value = datetime.fromtimestamp(int(raw) / 1000, tz=timezone.utc)
                    now = datetime.now(timezone.utc)
                    # Some TRUST timestamp fields are documented as occasionally
                    # one hour ahead during BST. Correct only that narrow case.
                    if now + timedelta(minutes=15) < value < now + timedelta(minutes=90):
                        value -= timedelta(hours=1)
                    return value
                except (TypeError, ValueError, OSError):
                    pass
        return datetime.now(timezone.utc)

    @staticmethod
    def _stanox(body: dict) -> str:
        stanox = str(body.get("loc_stanox") or body.get("reporting_stanox") or "").strip()
        if stanox == "00000":
            stanox = str(body.get("original_loc_stanox") or "").strip()
        return stanox.zfill(5) if stanox.isdigit() else stanox

    def _activation(self, body: dict) -> None:
        train_id = str(body.get("train_id") or "").strip()
        if not train_id:
            return
        schedule = self.schedules.lookup_activation(body) if self.schedules.ready() else None
        headcode = None
        if schedule:
            headcode = schedule.signalling_id
        if not headcode:
            wtt = str(body.get("schedule_wtt_id") or "").strip()
            headcode = wtt[:4] if len(wtt) >= 4 else _headcode_from_train_id(train_id)
        canonical_id = self._canonical_ids.get(train_id, train_id)
        previous = self.activations.get(canonical_id, {})
        activation = {
            "train_uid": str(body.get("train_uid") or (schedule.train_uid if schedule else "")).strip() or None,
            "headcode": headcode,
            "origin": schedule.origin if schedule else None,
            "destination": schedule.destination if schedule else None,
            "atoc_code": schedule.atoc_code if schedule else None,
            "trust_operator": trust_operator_name(body.get("toc_id")),
            "service_code": (schedule.service_code if schedule else None) or body.get("train_service_code"),
            "train_category": schedule.train_category if schedule else None,
            "power_type": schedule.power_type if schedule else None,
            "timing_load": schedule.timing_load if schedule else None,
            "booked_speed": schedule.booked_speed if schedule else None,
            "operating_characteristics": schedule.operating_characteristics if schedule else None,
            "schedule_source": body.get("schedule_source"),
            "service_date": schedule.running_date if schedule else None,
            "schedule_id": schedule.schedule_id if schedule else None,
        }
        self.activations[canonical_id] = {
            key: value if value is not None else previous.get(key)
            for key, value in activation.items()
        }
        existing = self.state.get(f"nr:{canonical_id}")
        if isinstance(existing, Vehicle):
            self._enrich_existing(existing, self.activations[canonical_id])
            self.state.upsert(existing)
        elif schedule and schedule.schedule_id and schedule.running_date:
            try:
                service_day = datetime.fromisoformat(schedule.running_date).date()
                leg = self.schedules.initial_estimate_leg(schedule.schedule_id, service_day)
                activated_at = self._event_time(body)
            except (TypeError, ValueError):
                leg = None
            if leg and leg["started_at"] - timedelta(minutes=60) <= activated_at <= leg["due_at"] + timedelta(minutes=20):
                operator = operator_name(schedule.atoc_code, trust_operator_name(body.get("toc_id")) or "Rail operator")
                self.state.upsert(Vehicle(
                    id=f"nr:{canonical_id}", type="train", name=headcode or train_id,
                    subtitle=(f"{schedule.origin} → {schedule.destination}" if schedule.origin and schedule.destination else "Activated railway service"),
                    lat=leg["lat"], lon=leg["lon"], operator=operator,
                    origin=schedule.origin, destination=schedule.destination,
                    headcode=headcode, train_uid=schedule.train_uid,
                    service_date=schedule.running_date, schedule_id=schedule.schedule_id,
                    train_category=schedule.train_category, power_type=schedule.power_type,
                    timing_load=schedule.timing_load, booked_speed=schedule.booked_speed,
                    operating_characteristics=schedule.operating_characteristics,
                    next_location=leg["next_name"], status="Activated • awaiting first movement report",
                    position_method="estimated",
                    position_label="Timetable estimate from a live Network Rail activation; awaiting the first confirmed movement",
                    source="Network Rail TRUST activation", source_id=train_id,
                    last_updated=activated_at,
                    reported_lat=leg["lat"], reported_lon=leg["lon"],
                    next_lat=leg["next_lat"], next_lon=leg["next_lon"],
                    estimate_path=leg["path"], estimate_started_at=leg["started_at"], estimate_due_at=leg["due_at"],
                    rail_tiploc=leg["tiploc"],
                ))

    @staticmethod
    def _enrich_existing(vehicle: Vehicle, activation: dict) -> None:
        for field in (
            "train_uid", "headcode", "origin", "destination", "train_category",
            "power_type", "timing_load", "booked_speed", "operating_characteristics",
            "service_date", "schedule_id",
        ):
            if activation.get(field) is not None:
                setattr(vehicle, field, activation[field])
        if activation.get("atoc_code"):
            vehicle.operator = operator_name(activation["atoc_code"], vehicle.operator)
        elif activation.get("trust_operator"):
            vehicle.operator = activation["trust_operator"]
        vehicle.name = vehicle.headcode or vehicle.name
        if vehicle.origin and vehicle.destination:
            vehicle.subtitle = f"{vehicle.origin} → {vehicle.destination}"

    def _recover_activation(
        self, train_id: str, identity: str, body: dict, station: dict,
        stanox: str, event_time: datetime, existing: Vehicle | None,
    ) -> dict:
        """Recover a missed activation using a confirmed location and call time.

        Restarts can happen hours after activation. A movement must retain a
        restored UID, and an unknown UID needs more evidence than its headcode.
        """
        activation = dict(self.activations.get(train_id, {}))
        if existing:
            for field in (
                "train_uid", "headcode", "origin", "destination", "train_category",
                "power_type", "timing_load", "booked_speed", "operating_characteristics",
                "service_date", "schedule_id",
            ):
                if activation.get(field) is None:
                    activation[field] = getattr(existing, field)
        headcode = activation.get("headcode") or _headcode_from_train_id(identity)
        schedule = None
        if self.schedules.ready() and not activation.get("schedule_id"):
            if activation.get("train_uid"):
                schedule = self.schedules.lookup_activation({
                    "train_uid": activation["train_uid"],
                    "tp_origin_timestamp": activation.get("service_date") or event_time.isoformat(),
                })
            elif headcode and (station.get("tiploc") or stanox):
                planned_body = {"actual_timestamp": body.get("planned_timestamp")}
                call_time = self._event_time(planned_body) if body.get("planned_timestamp") else event_time
                expected_operator = trust_operator_name(body.get("toc_id"))
                atoc_code = next((code for code, name in ATOC_OPERATOR_NAMES.items() if name == expected_operator), None)
                schedule = self.schedules.lookup_headcode(
                    headcode,
                    at_time=call_time, tiploc=station.get("tiploc"), stanox=stanox,
                    atoc_code=atoc_code,
                )
                if schedule and body.get("train_service_code") and schedule.service_code and str(body["train_service_code"]).strip() != str(schedule.service_code).strip():
                    schedule = None
        if schedule:
            activation.update({
                "train_uid": schedule.train_uid,
                "headcode": headcode or schedule.signalling_id,
                "atoc_code": schedule.atoc_code,
                "origin": schedule.origin, "destination": schedule.destination,
                "train_category": schedule.train_category, "power_type": schedule.power_type,
                "timing_load": schedule.timing_load, "booked_speed": schedule.booked_speed,
                "operating_characteristics": schedule.operating_characteristics,
                "service_date": schedule.running_date, "schedule_id": schedule.schedule_id,
            })
        if activation:
            self.activations[train_id] = activation
        return activation

    def _movement(self, body: dict, status: SourceStatus, *, replay: bool = False) -> bool:
        stanox = self._stanox(body)
        train_id = str(body.get("train_id") or "").strip()
        current_id = str(body.get("current_train_id") or "").strip()
        if not train_id:
            return False
        canonical_id = self._canonical_ids.get(train_id, train_id)
        if current_id:
            self._canonical_ids[current_id] = canonical_id
        identity = current_id or self.identities.get(canonical_id) or train_id
        existing = self.state.get(f"nr:{canonical_id}")
        existing = existing if isinstance(existing, Vehicle) else None
        event_time = self._event_time(body)
        if existing and event_time < existing.last_updated:
            return True
        station = self.stations.get(stanox)
        if not station:
            if not replay:
                status.movements_unmapped += 1
                self.state.add_unmapped({
                    "stanox": stanox,
                    "train_id": train_id,
                    "headcode": _headcode_from_train_id(identity),
                    "event_type": body.get("event_type"),
                    "seen_at": datetime.now(timezone.utc).isoformat(),
                })
                with self._pending_lock:
                    self._pending_movements.append(dict(body))
            return False

        activation = self._recover_activation(
            canonical_id, identity, body, station, stanox, event_time, existing,
        )
        headcode = activation.get("headcode") or _headcode_from_train_id(identity)
        variation = body.get("timetable_variation")
        try:
            delay = int(variation) if variation not in (None, "") else None
        except (TypeError, ValueError):
            delay = None

        event_type = str(body.get("event_type") or body.get("planned_event_type") or "Movement").title()
        variation_status = str(body.get("variation_status") or "Reported").replace("_", " ").title()
        platform = str(body.get("platform") or "").strip()
        location_name = station.get("name") or f"STANOX {stanox}"
        subtitle = f"{event_type} • {location_name}"
        if activation.get("origin") and activation.get("destination"):
            subtitle = f"{activation['origin']} → {activation['destination']}"
        if platform:
            subtitle += f" • P{platform}"

        next_stanox = str(body.get("next_report_stanox") or "").strip()
        if next_stanox.isdigit():
            next_stanox = next_stanox.zfill(5)
        next_station = self.stations.get(next_stanox)
        next_lat = next_lon = None
        estimate_path = None
        due_at = None
        next_name = None
        try:
            run_minutes = float(body.get("next_report_run_time") or 0)
        except (TypeError, ValueError):
            run_minutes = 0
        if event_type in {"Departure", "Pass"} and next_station and 0.25 <= run_minutes <= 60 and _km(station, next_station) <= 120:
            next_lat, next_lon = float(next_station["lat"]), float(next_station["lon"])
            next_name = next_station.get("name") or f"STANOX {next_stanox}"
            due_at = event_time + timedelta(minutes=run_minutes)
            route_points = self.schedules.route_points(
                activation.get("schedule_id"), station.get("tiploc"), next_station.get("tiploc")
            ) if self.schedules.ready() else []
            start_point = [float(station["lat"]), float(station["lon"])]
            end_point = [float(next_station["lat"]), float(next_station["lon"])]
            if not route_points or route_points[0] != start_point:
                route_points.insert(0, start_point)
            if route_points[-1] != end_point:
                route_points.append(end_point)
            estimate_path = route_points

        operator = activation.get("atoc_code")
        if operator:
            operator = operator_name(operator)
        else:
            operator = (
                activation.get("trust_operator")
                or trust_operator_name(body.get("toc_id"))
                or (existing.operator if existing else None)
                or "Rail operator"
            )

        name = headcode or identity
        position_label = f"Confirmed report at {location_name}"
        vehicle = Vehicle(
            id=f"nr:{canonical_id}",
            type="train",
            name=name,
            subtitle=subtitle,
            lat=float(station["lat"]),
            lon=float(station["lon"]),
            operator=operator,
            origin=activation.get("origin"),
            destination=activation.get("destination"),
            headcode=headcode,
            train_uid=activation.get("train_uid"),
            service_date=activation.get("service_date"),
            schedule_id=activation.get("schedule_id"),
            rail_event_type=event_type,
            rail_tiploc=station.get("tiploc"),
            rail_stanox=stanox,
            train_category=activation.get("train_category"),
            power_type=activation.get("power_type"),
            timing_load=activation.get("timing_load"),
            booked_speed=activation.get("booked_speed"),
            operating_characteristics=activation.get("operating_characteristics"),
            next_location=next_name,
            status=variation_status if str(body.get("train_terminated", "false")).lower() != "true" else "Terminated",
            delay_minutes=delay,
            position_method="network_report",
            position_label=position_label,
            source="Network Rail TRUST",
            source_id=stanox,
            tracking_count=0,
            interesting=False,
            last_updated=event_time,
            reported_lat=float(station["lat"]),
            reported_lon=float(station["lon"]),
            next_lat=next_lat,
            next_lon=next_lon,
            estimate_path=estimate_path,
            estimate_started_at=event_time if due_at else None,
            estimate_due_at=due_at,
        )
        self.state.upsert(vehicle)
        status.movements_mapped += 1
        if replay and status.movements_unmapped > 0:
            status.movements_unmapped -= 1
        return True

    def _cancellation(self, body: dict) -> None:
        train_id = str(body.get("train_id") or "").strip()
        existing = self.state.get(f"nr:{self._canonical_ids.get(train_id, train_id)}")
        if existing:
            event_time = self._event_time(body)
            if event_time < existing.last_updated:
                return
            existing.status = f"Cancelled {str(body.get('canx_type') or '').title()}".strip()
            existing.last_updated = event_time
            existing.next_lat = existing.next_lon = None
            existing.estimate_path = None
            existing.estimate_due_at = None
            self.state.upsert(existing)

    def _identity_change(self, body: dict) -> None:
        train_id = str(body.get("train_id") or "").strip()
        revised = str(body.get("revised_train_id") or "").strip()
        if train_id and revised:
            canonical_id = self._canonical_ids.get(train_id, train_id)
            self._canonical_ids[revised] = canonical_id
            self.identities[canonical_id] = revised
            if canonical_id in self.activations:
                self.activations[canonical_id]["headcode"] = _headcode_from_train_id(revised) or self.activations[canonical_id].get("headcode")

    def handle_message(self, payload: str) -> None:
        try:
            messages = json.loads(payload)
            if isinstance(messages, dict):
                messages = [messages]
        except json.JSONDecodeError:
            return
        if not isinstance(messages, list):
            return

        now = datetime.now(timezone.utc)
        status = self.state.source(self.name) or SourceStatus(name=self.name, enabled=True, connected=True, mode="live")
        for message in messages:
            if not isinstance(message, dict):
                continue
            status.messages_seen += 1
            header = message.get("header") or {}
            body = message.get("body") or {}
            if not isinstance(header, dict) or not isinstance(body, dict):
                continue
            msg_type = str(header.get("msg_type") or "")
            if msg_type == "0001":
                self._activation(body)
            elif msg_type == "0002":
                self._cancellation(body)
            elif msg_type == "0003":
                self._movement(body, status)
            elif msg_type == "0007":
                self._identity_change(body)

        status.last_message_at = now
        status.connected = True
        status.mode = "live"
        status.message = (
            f"TRUST live • {status.messages_seen:,} messages • {status.movements_mapped:,} mapped • "
            f"{status.movements_unmapped:,} unmapped • {len(self.stations):,} mapped STANOX"
        )
        self.state.set_source(status)


# Backwards-compatible name used by the v0.3 main module.
NetworkRailCollector = TrustCollector
