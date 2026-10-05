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
    locations stay at their mapped reporting point until the next movement
    message; no straight-line interpolation is drawn between railway points.
    """

    def __init__(self, settings: Settings, state: LiveState, station_file: Path, schedules: ScheduleStore) -> None:
        super().__init__(settings, state, name="network_rail", topic=settings.trust_topic)
        self.station_file = station_file
        self.schedules = schedules
        self.stations = self._load_stations()
        self.activations: dict[str, dict] = {}
        self.identities: dict[str, str] = {}
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
        self.activations[train_id] = {
            "train_uid": str(body.get("train_uid") or (schedule.train_uid if schedule else "")).strip() or None,
            "headcode": headcode,
            "origin": schedule.origin if schedule else None,
            "destination": schedule.destination if schedule else None,
            "atoc_code": schedule.atoc_code if schedule else None,
            "service_code": (schedule.service_code if schedule else None) or body.get("train_service_code"),
            "schedule_source": body.get("schedule_source"),
        }

    def _movement(self, body: dict, status: SourceStatus, *, replay: bool = False) -> bool:
        stanox = self._stanox(body)
        train_id = str(body.get("train_id") or "").strip()
        current_id = str(body.get("current_train_id") or "").strip()
        identity = current_id or self.identities.get(train_id) or train_id or "unknown"
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

        activation = self.activations.get(train_id, {})
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

        event_time = self._event_time(body)
        next_stanox = str(body.get("next_report_stanox") or "").strip()
        if next_stanox.isdigit():
            next_stanox = next_stanox.zfill(5)
        next_station = self.stations.get(next_stanox)
        next_lat = next_lon = None
        due_at = None
        next_name = None
        try:
            run_minutes = float(body.get("next_report_run_time") or 0)
        except (TypeError, ValueError):
            run_minutes = 0
        if next_station and 0.25 <= run_minutes <= 60 and _km(station, next_station) <= 120:
            next_lat, next_lon = float(next_station["lat"]), float(next_station["lon"])
            next_name = next_station.get("name") or f"STANOX {next_stanox}"
            due_at = event_time + timedelta(minutes=run_minutes)

        operator = activation.get("atoc_code")
        if operator:
            operator = f"ATOC {operator}"
        elif body.get("toc_id"):
            operator = f"TOC {body.get('toc_id')}"
        else:
            operator = "Network Rail"

        name = headcode or identity
        position_label = f"Confirmed report at {location_name}"
        vehicle = Vehicle(
            id=f"nr:{train_id or identity}",
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
        existing = self.state.get(f"nr:{train_id}")
        if existing:
            existing.status = f"Cancelled {str(body.get('canx_type') or '').title()}".strip()
            existing.last_updated = datetime.now(timezone.utc)
            existing.next_lat = existing.next_lon = None
            existing.estimate_due_at = None
            self.state.upsert(existing)

    def _identity_change(self, body: dict) -> None:
        train_id = str(body.get("train_id") or "").strip()
        revised = str(body.get("revised_train_id") or "").strip()
        if train_id and revised:
            self.identities[train_id] = revised
            if train_id in self.activations:
                self.activations[train_id]["headcode"] = _headcode_from_train_id(revised) or self.activations[train_id].get("headcode")

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
