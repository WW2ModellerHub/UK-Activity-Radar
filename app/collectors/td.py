from __future__ import annotations

import json
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock

from ..config import Settings
from ..models import SourceStatus, Vehicle
from ..state import LiveState
from .stomp_client import StompFeed


class TDCollector(StompFeed):
    def __init__(self, settings: Settings, state: LiveState, station_file: Path, smart_file: Path) -> None:
        super().__init__(settings, state, name="td", topic=settings.td_topic)
        self.station_file = station_file
        self.smart_file = smart_file
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
            row = self.exact.get((area, fr, to)) or self.by_to.get((area, to)) or self.by_from.get((area, fr))
        elif msg_type == "CB":
            row = self.by_from.get((area, fr))
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
        existing = self.state.find_train_by_headcode(descr, lat, lon)
        location_name = station.get("name") or str((row or {}).get("STANME") or "TD location")
        if existing:
            existing.lat = lat; existing.lon = lon
            existing.reported_lat = lat; existing.reported_lon = lon
            existing.next_lat = existing.next_lon = None
            existing.estimate_due_at = None
            existing.last_updated = event_time
            existing.position_method = "network_report"
            existing.position_label = f"TD berth report • {area} {fr or '—'}→{to or '—'} • {location_name}"
            existing.source = "Network Rail TD"
            self.state.upsert(existing)
        else:
            self.state.upsert(Vehicle(
                id=f"td:{area}:{descr}", type="train", name=descr,
                subtitle=f"Train describer • {location_name}", lat=lat, lon=lon,
                headcode=descr, status="TD live", position_method="network_report",
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
