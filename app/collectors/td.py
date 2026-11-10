from __future__ import annotations

import json
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock

from ..config import Settings
from ..models import SourceStatus, Vehicle
from ..schedule_store import ScheduleStore
from ..state import LiveState
from ..train_identity import operator_name
from .stomp_client import StompFeed
from .network_rail import _km


class TDCollector(StompFeed):
    def __init__(self, settings: Settings, state: LiveState, station_file: Path, smart_file: Path, schedules: ScheduleStore | None = None) -> None:
        super().__init__(settings, state, name="td", topic=settings.td_topic)
        self.station_file = station_file
        self.smart_file = smart_file
        self.schedules = schedules
        self.stations: dict[str, dict] = {}
        self.exact: dict[tuple[str, str, str], dict] = {}
        self.by_from: dict[tuple[str, str], dict] = {}
        self.by_to: dict[tuple[str, str], dict] = {}
        self._pending: deque[dict] = deque(maxlen=12000)
        self._pending_lock = RLock()
        self.reload_references()

    def reload_references(self) -> None:
        try:
            raw = json.loads(self.station_file.read_text(encoding="utf-8"))
            self.stations = raw if isinstance(raw, dict) else {}
        except Exception:
            self.stations = {}
        try:
            rows = json.loads(self.smart_file.read_text(encoding="utf-8"))
            rows = rows if isinstance(rows, list) else []
        except Exception:
            rows = []
        self.exact.clear(); self.by_from.clear(); self.by_to.clear()
        for row in rows:
            if not isinstance(row, dict):
                continue
            td = str(row.get("TD") or row.get("td") or "").strip().upper()
            fr = str(row.get("FROMBERTH") or row.get("fromberth") or "").strip().upper()
            to = str(row.get("TOBERTH") or row.get("toberth") or "").strip().upper()
            if td and fr and to:
                self.exact[(td, fr, to)] = row
            if td and fr:
                self.by_from[(td, fr)] = row
            if td and to:
                self.by_to[(td, to)] = row
        if self.stations and (self.exact or self.by_from or self.by_to):
            self._replay_pending()

    @staticmethod
    def _row_stanox(row: dict | None) -> str:
        if not row:
            return ""
        value = str(row.get("STANOX") or row.get("stanox") or "").strip()
        return value.zfill(5) if value.isdigit() else value

    def _resolve(self, area: str, msg_type: str, fr: str, to: str) -> tuple[dict | None, dict | None]:
        row = None
        if msg_type == "CA":
            # The origin berth describes where the train *was*. Falling back
            # to it can keep a departed train pinned to a station indefinitely.
            row = self.exact.get((area, fr, to)) or self.by_to.get((area, to))
        elif msg_type == "CC":
            row = self.by_to.get((area, to))
        stanox = self._row_stanox(row)
        return row, self.stations.get(stanox) if stanox else None

    def _process_msg(self, msg: dict, status: SourceStatus, *, replay: bool = False) -> bool:
        area = str(msg.get("area_id") or "").strip().upper()
        typ = str(msg.get("msg_type") or "").strip().upper()
        fr = str(msg.get("from") or "").strip().upper()
        to = str(msg.get("to") or "").strip().upper()
        descr = str(msg.get("descr") or "").strip().upper()
        # CB means a description was cancelled out of a berth, not that a train
        # arrived there. Keep the last confirmed report until the next CA/CC.
        if typ == "CB":
            return True
        if typ not in {"CA", "CC"}:
            return True
        row, station = self._resolve(area, typ, fr, to)
        if not station or not descr:
            if not replay:
                status.movements_unmapped += 1
                with self._pending_lock:
                    self._pending.append(dict(msg))
            return False

        if replay and status.movements_unmapped > 0:
            status.movements_unmapped -= 1
        status.movements_mapped += 1
        try:
            event_time = datetime.fromtimestamp(int(msg.get("time")) / 1000, tz=timezone.utc)
        except Exception:
            event_time = datetime.now(timezone.utc)
        lat, lon = float(station["lat"]), float(station["lon"])
        location_name = station.get("name") or str((row or {}).get("STANME") or "TD location")
        schedule = None
        if self.schedules is not None and self.schedules.ready():
            try:
                schedule = self.schedules.lookup_headcode(
                    descr,
                    at_time=event_time,
                    tiploc=station.get("tiploc"),
                    stanox=self._row_stanox(row),
                )
            except Exception:
                schedule = None
        existing = self.state.find_train_by_uid(schedule.train_uid) if schedule else None
        if existing is None:
            existing = self.state.find_train_by_headcode(descr, lat, lon)
        if existing:
            elapsed = (event_time - existing.last_updated).total_seconds()
            distance = _km({"lat": existing.lat, "lon": existing.lon}, station)
            # Headcodes are reused throughout Britain. Only associate a fresh
            # nearby report, allowing up to 240 km/h between confirmed points.
            max_distance = min(100.0, 5.0 + max(0.0, elapsed) / 15.0)
            conflict = bool(schedule and existing.train_uid and existing.train_uid != schedule.train_uid)
            wrong_day = bool(schedule and existing.service_date and schedule.running_date and existing.service_date != schedule.running_date)
            if conflict or wrong_day or elapsed > 1800 or distance > max_distance:
                existing = None
            elif elapsed < 0:
                return True
        if existing:
            existing.lat = lat; existing.lon = lon
            existing.reported_lat = lat; existing.reported_lon = lon
            existing.next_lat = existing.next_lon = None
            existing.estimate_due_at = None
            existing.estimate_started_at = None
            existing.next_location = None
            existing.last_updated = event_time
            existing.position_method = "network_report"
            existing.position_label = f"TD berth report • {area} {fr or '—'}→{to or '—'} • {location_name}"
            existing.source = "Network Rail TD"
            existing.source_id = f"{area}:{to}"
            existing.rail_event_type = None
            existing.rail_tiploc = station.get("tiploc")
            existing.rail_stanox = self._row_stanox(row)
            if schedule:
                existing.train_uid = schedule.train_uid
                existing.origin = schedule.origin or existing.origin
                existing.destination = schedule.destination or existing.destination
                existing.operator = operator_name(schedule.atoc_code, existing.operator)
                existing.train_category = schedule.train_category
                existing.power_type = schedule.power_type
                existing.timing_load = schedule.timing_load
                existing.booked_speed = schedule.booked_speed
                existing.operating_characteristics = schedule.operating_characteristics
                existing.service_date = schedule.running_date or existing.service_date
                existing.schedule_id = schedule.schedule_id or existing.schedule_id
            self.state.upsert(existing)
        else:
            self.state.upsert(Vehicle(
                id=f"td:{area}:{descr}", type="train", name=descr,
                subtitle=(
                    f"{schedule.origin} → {schedule.destination}"
                    if schedule and schedule.origin and schedule.destination
                    else f"Train describer • {location_name}"
                ), lat=lat, lon=lon,
                operator=operator_name(schedule.atoc_code) if schedule else None,
                origin=schedule.origin if schedule else None,
                destination=schedule.destination if schedule else None,
                headcode=descr, status="TD live", position_method="network_report",
                train_uid=schedule.train_uid if schedule else None,
                service_date=schedule.running_date if schedule else None,
                schedule_id=schedule.schedule_id if schedule else None,
                rail_tiploc=station.get("tiploc"),
                rail_stanox=self._row_stanox(row),
                train_category=schedule.train_category if schedule else None,
                power_type=schedule.power_type if schedule else None,
                timing_load=schedule.timing_load if schedule else None,
                booked_speed=schedule.booked_speed if schedule else None,
                operating_characteristics=schedule.operating_characteristics if schedule else None,
                position_label=f"TD berth report • {area} {fr or '—'}→{to or '—'}",
                source="Network Rail TD", source_id=f"{area}:{to or fr}", last_updated=event_time,
            ))
        return True

    def _replay_pending(self) -> None:
        with self._pending_lock:
            pending = list(self._pending)
            self._pending.clear()
        if not pending:
            return
        status = self.state.source(self.name) or SourceStatus(name=self.name, enabled=True, connected=True, mode="live")
        unresolved: list[dict] = []
        for msg in pending:
            if not self._process_msg(msg, status, replay=True):
                unresolved.append(msg)
        with self._pending_lock:
            for msg in unresolved[-self._pending.maxlen:]:
                self._pending.append(msg)
        status.message = f"TD live • {status.messages_seen:,} messages • {status.movements_mapped:,} SMART-mapped steps • {status.movements_unmapped:,} unresolved"
        self.state.set_source(status)

    def handle_message(self, payload: str) -> None:
        try:
            decoded = json.loads(payload)
        except json.JSONDecodeError:
            return
        messages = decoded if isinstance(decoded, list) else [decoded]
        status = self.state.source(self.name) or SourceStatus(name=self.name, enabled=True, connected=True, mode="live")
        for wrapper in messages:
            if not isinstance(wrapper, dict):
                continue
            status.messages_seen += 1
            key = next((k for k in ("CA_MSG", "CB_MSG", "CC_MSG") if isinstance(wrapper.get(k), dict)), None)
            if not key:
                continue
            msg = dict(wrapper[key])
            msg.setdefault("msg_type", key[:2])
            self._process_msg(msg, status)
        status.last_message_at = datetime.now(timezone.utc)
        status.connected = True
        status.message = f"TD live • {status.messages_seen:,} messages • {status.movements_mapped:,} SMART-mapped steps • {status.movements_unmapped:,} unresolved"
        self.state.set_source(status)
