from __future__ import annotations

import json
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock

from ..config import Settings
from ..models import SourceStatus, Vehicle
from ..schedule_store import ScheduleStore
from ..state import TRAIN_PROJECTION_MINUTES, LiveState
from ..train_identity import operator_name
from .stomp_client import StompFeed
from .network_rail import _km


class TDCollector(StompFeed):
    # Train Describer steps are positional: when processing falls behind, the
    # newest steps matter and a stale backlog may be discarded.
    drop_stale_backlog = True
    backlog_dropped = 0
    PATH_REFRESH_SECONDS = 300.0
    ROLLOVER_RETRY_SECONDS = 30.0

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
        self._last_path_refresh = float("-inf")
        self._rollover_retry_at: dict[str, tuple[datetime, float]] = {}
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

    @staticmethod
    def _message_time(msg: dict) -> datetime:
        try:
            return datetime.fromtimestamp(int(msg.get("time")) / 1000, tz=timezone.utc)
        except (TypeError, ValueError, OSError):
            return datetime.now(timezone.utc)

    @staticmethod
    def _row_offset_seconds(row: dict | None) -> int:
        try:
            # Network Rail SMART defines this as the signed difference from
            # the raw berth event to the corresponding TRUST event time.
            return max(-900, min(900, int(float((row or {}).get("BERTHOFFSET") or 0))))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _row_event_kind(row: dict | None) -> str | None:
        event = str((row or {}).get("EVENT") or "").strip().upper()
        if event in {"A", "C"}:
            return "Arrival"
        if event in {"B", "D"}:
            return "Departure"
        return None

    def _resolve(self, area: str, msg_type: str, fr: str, to: str) -> tuple[dict | None, dict | None, bool]:
        row = None
        fallback = False
        if msg_type == "CA":
            row = self.exact.get((area, fr, to)) or self.by_to.get((area, to))
            # SMART occasionally knows the origin berth but not the berth a CA
            # step moved into.  Suppressing that message entirely makes a live
            # train disappear.  Retain the last mapped berth as an explicitly
            # estimated lower-bound position until a better report arrives.
            if row is None:
                row = self.by_from.get((area, fr))
                fallback = row is not None
        elif msg_type == "CC":
            row = self.by_to.get((area, to))
        stanox = self._row_stanox(row)
        return row, self.stations.get(stanox) if stanox else None, fallback

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
        event_time = self._message_time(msg)
        row, station, fallback = self._resolve(area, typ, fr, to)
        if not station or not descr:
            # A SMART gap must not make a train blink off the map.  If this is
            # clearly the same TD-area/headcode we already hold, keep the last
            # confirmed position fresh until a mappable berth arrives.
            existing = self.state.find_train_by_headcode(descr) if descr else None
            if (
                existing
                and existing.source == "Network Rail TD"
                and str(existing.source_id or "").startswith(f"{area}:")
                and event_time >= existing.last_updated
            ):
                advanced = self._advance_schedule_estimate(existing, event_time)
                existing.restored_snapshot = False
                existing.last_updated = event_time
                existing.status = "TD live • next berth unresolved"
                existing.position_method = "estimated"
                existing.position_label = (
                    f"Live TD step • following the matched published railway schedule while {area} "
                    f"{fr or '—'}→{to or '—'} is not in SMART"
                    if advanced or existing.estimate_path else
                    f"Holding the last mapped TD berth while {area} {fr or '—'}→{to or '—'} is unresolved"
                )
                self.state.upsert(existing)
            if not replay:
                status.movements_unmapped += 1
                with self._pending_lock:
                    self._pending.append(dict(msg))
            return False

        if replay and status.movements_unmapped > 0:
            status.movements_unmapped -= 1
        status.movements_mapped += 1
        lat, lon = float(station["lat"]), float(station["lon"])
        location_name = station.get("name") or str((row or {}).get("STANME") or "TD location")
        event_kind = self._row_event_kind(row)
        berth_offset = self._row_offset_seconds(row)
        source_id = f"{area}:{fr if fallback else (to or fr)}"
        schedule = None
        if self.schedules is not None and self.schedules.ready():
            schedule = self._known_schedule(descr, station, self._row_stanox(row), event_time)
        if schedule is None and self.schedules is not None and self.schedules.ready():
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
            existing.restored_snapshot = False
            preserve_leg = bool(
                not fallback
                and existing.source == "Network Rail TD"
                and existing.source_id == source_id
                and existing.rail_event_type == event_kind
                and existing.rail_stanox == self._row_stanox(row)
                and existing.estimate_started_at
                and existing.estimate_due_at
                and existing.estimate_due_at > event_time
            )
            existing.lat = lat; existing.lon = lon
            existing.reported_lat = lat; existing.reported_lon = lon
            if not preserve_leg:
                existing.next_lat = existing.next_lon = None
                existing.estimate_path = None
                existing.estimate_due_at = None
                existing.estimate_started_at = None
                existing.next_location = None
                existing.next_tiploc = None
            existing.last_updated = event_time
            existing.position_method = "estimated" if fallback else "network_report"
            offset_note = f" • corrected {berth_offset:+d}s" if event_kind and berth_offset else ""
            existing.position_label = (
                f"Last mapped TD berth before unresolved step • {area} {fr or '—'}→{to or '—'} • {location_name}"
                if fallback else f"TD {str(event_kind or 'berth').lower()} report{offset_note} • {area} {fr or '—'}→{to or '—'} • {location_name}"
            )
            existing.source = "Network Rail TD"
            existing.source_id = source_id
            existing.rail_event_type = event_kind
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
                leg = self._schedule_leg(
                    schedule.schedule_id, schedule.running_date, station.get("tiploc"), event_time,
                    event_code=(row or {}).get("EVENT"), berth_offset_seconds=berth_offset,
                )
                if leg and (not fallback or event_kind == "Departure") and not preserve_leg:
                    existing.reported_lat = leg["lat"]; existing.reported_lon = leg["lon"]
                    existing.next_lat = leg["next_lat"]; existing.next_lon = leg["next_lon"]
                    existing.next_location = leg["next_name"]
                    existing.next_tiploc = leg.get("next_tiploc")
                    existing.estimate_path = leg["path"]
                    existing.estimate_started_at = leg["started_at"]
                    existing.estimate_due_at = leg["due_at"]
            self.state.upsert(existing)
        else:
            leg = self._schedule_leg(
                schedule.schedule_id if schedule else None,
                schedule.running_date if schedule else None,
                station.get("tiploc"), event_time,
                event_code=(row or {}).get("EVENT"), berth_offset_seconds=berth_offset,
            ) if (not fallback or event_kind == "Departure") else None
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
                headcode=descr, status="TD live • next berth unresolved" if fallback else "TD live",
                position_method="estimated" if fallback else "network_report",
                train_uid=schedule.train_uid if schedule else None,
                service_date=schedule.running_date if schedule else None,
                schedule_id=schedule.schedule_id if schedule else None,
                rail_event_type=event_kind,
                rail_tiploc=station.get("tiploc"),
                rail_stanox=self._row_stanox(row),
                train_category=schedule.train_category if schedule else None,
                power_type=schedule.power_type if schedule else None,
                timing_load=schedule.timing_load if schedule else None,
                booked_speed=schedule.booked_speed if schedule else None,
                operating_characteristics=schedule.operating_characteristics if schedule else None,
                position_label=(
                    f"Last mapped TD berth before unresolved step • {area} {fr or '—'}→{to or '—'}"
                    if fallback else f"TD {str(event_kind or 'berth').lower()} report"
                    f"{f' • corrected {berth_offset:+d}s' if event_kind and berth_offset else ''}"
                    f" • {area} {fr or '—'}→{to or '—'}"
                ),
                source="Network Rail TD", source_id=source_id, last_updated=event_time,
                reported_lat=leg["lat"] if leg else lat,
                reported_lon=leg["lon"] if leg else lon,
                next_lat=leg["next_lat"] if leg else None,
                next_lon=leg["next_lon"] if leg else None,
                next_location=leg["next_name"] if leg else None,
                next_tiploc=leg.get("next_tiploc") if leg else None,
                estimate_path=leg["path"] if leg else None,
                estimate_started_at=leg["started_at"] if leg else None,
                estimate_due_at=leg["due_at"] if leg else None,
            ))
        return True

    def _known_schedule(self, descr: str, station: dict, stanox: str, event_time: datetime):
        """Reuse the timetable identity of a train this describer already tracks.

        The national headcode search costs tens of milliseconds and ran for
        every berth step. A nearby, recent train with the same headcode whose
        matched schedule calls at this location is the same service; only new
        or ambiguous trains need the full search.
        """
        prior = self.state.find_train_by_headcode(descr, float(station["lat"]), float(station["lon"]))
        if not prior or not prior.schedule_id or not prior.train_uid or not prior.service_date:
            return None
        elapsed = (event_time - prior.last_updated).total_seconds()
        if elapsed < 0 or elapsed > 1800:
            return None
        if _km({"lat": prior.lat, "lon": prior.lon}, station) > min(100.0, 5.0 + elapsed / 15.0):
            return None
        codes = {str(station.get("tiploc") or "")} | set(self.schedules.tiplocs_for_stanox(stanox))
        try:
            return self.schedules.schedule_calling_at(prior.schedule_id, prior.service_date, codes)
        except Exception:
            return None

    def _schedule_leg(
        self, schedule_id, running_date, tiploc, observed_at, *,
        event_code=None, berth_offset_seconds=None,
    ):
        if not self.schedules or not self.schedules.ready() or not schedule_id or not running_date:
            return None
        try:
            service_day = datetime.fromisoformat(str(running_date)).date()
            return self.schedules.estimate_leg_from(
                schedule_id, service_day, tiploc, observed_at,
                event_code=event_code, berth_offset_seconds=berth_offset_seconds,
            )
        except (TypeError, ValueError):
            return None

    def _schedule_departure_after_arrival(self, vehicle: Vehicle, observed_at: datetime):
        if not self.schedules or not self.schedules.ready() or not vehicle.schedule_id or not vehicle.service_date:
            return None
        try:
            service_day = datetime.fromisoformat(str(vehicle.service_date)).date()
            return self.schedules.estimate_departure_leg_after_arrival(
                vehicle.schedule_id, service_day, vehicle.next_tiploc or vehicle.rail_tiploc, observed_at,
            )
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _apply_estimate_leg(vehicle: Vehicle, leg: dict) -> None:
        vehicle.lat = leg["lat"]
        vehicle.lon = leg["lon"]
        vehicle.reported_lat = leg["lat"]
        vehicle.reported_lon = leg["lon"]
        vehicle.next_lat = leg["next_lat"]
        vehicle.next_lon = leg["next_lon"]
        vehicle.next_location = leg["next_name"]
        vehicle.next_tiploc = leg.get("next_tiploc")
        vehicle.estimate_path = leg["path"]
        vehicle.estimate_started_at = leg["started_at"]
        vehicle.estimate_due_at = leg["due_at"]

    def _advance_schedule_estimate(self, vehicle: Vehicle, observed_at: datetime) -> bool:
        """Bridge SMART gaps using the already matched service timetable.

        TD remains the liveness signal.  This only advances a train after its
        current mapped leg has elapsed and only along the same identified
        schedule; the next mapped TD/TRUST report replaces the estimate.
        """
        advanced = False
        for step in range(4):
            due_at = vehicle.estimate_due_at
            next_tiploc = str(vehicle.next_tiploc or "").strip().upper()
            if not due_at or observed_at <= due_at or not next_tiploc:
                break
            leg = (
                self._schedule_departure_after_arrival(vehicle, due_at)
                if step == 0 and str(vehicle.rail_event_type or "").lower() == "arrival"
                else self._schedule_leg(
                    vehicle.schedule_id, vehicle.service_date, next_tiploc, due_at,
                    event_code="D", berth_offset_seconds=0,
                )
            )
            if not leg or leg.get("due_at") <= due_at:
                break
            self._apply_estimate_leg(vehicle, leg)
            advanced = True
        return advanced

    def advance_live_estimates(self, observed_at: datetime | None = None) -> int:
        """Roll elapsed timetable legs forward without waiting for a click.

        TD and TRUST are reporting-point feeds rather than GPS.  A browser can
        animate a known leg locally, but once that leg ends it needs the next
        mapped timetable leg.  Previously that rollover only happened when a
        later (often unmapped) TD message happened to arrive, so ordinary map
        markers could sit at a station for several minutes and then jump.  Run
        this small maintenance pass independently of viewers and only publish
        rows whose identified schedule genuinely advanced.
        """
        now = observed_at or datetime.now(timezone.utc)
        tick = time.monotonic()
        # Geometry can improve after a deploy or reference sync, so current
        # legs are re-checked - but occasionally, not for every train every
        # second, which kept one CPU busy and starved the live feed.
        refresh_paths = tick - self._last_path_refresh >= self.PATH_REFRESH_SECONDS
        if refresh_paths:
            self._last_path_refresh = tick
        oldest = now - timedelta(minutes=TRAIN_PROJECTION_MINUTES)
        retry_at = self._rollover_retry_at

        def needs_work(vehicle: Vehicle) -> bool:
            if (
                not vehicle.schedule_id or vehicle.last_updated < oldest
                or str(vehicle.status or "").lower().startswith("terminated")
            ):
                return False
            due_at = vehicle.estimate_due_at
            if due_at and vehicle.service_date and now > due_at:
                # A leg with no onward timetable leg is retried sparingly.
                held = retry_at.get(vehicle.id)
                return not (held and held[0] == due_at and held[1] > tick)
            return bool(
                refresh_paths and vehicle.estimate_started_at and due_at
                and vehicle.rail_tiploc and vehicle.next_tiploc
            )

        trains = self.state.trains_where(needs_work)
        if len(retry_at) > 20000:
            retry_at.clear()
        advanced = 0
        for vehicle in trains:
            # A deploy can introduce better geometry while a train is already
            # midway through its current leg. Replace that leg immediately;
            # do not wait for the next TD report or for somebody to click it.
            event_type = str(vehicle.rail_event_type or '').strip().lower()
            if (
                refresh_paths
                and vehicle.schedule_id and vehicle.estimate_started_at and vehicle.estimate_due_at
                and vehicle.estimate_due_at > now
                and vehicle.rail_tiploc and vehicle.next_tiploc
                and event_type not in {'arrival'}
            ):
                refreshed_path = self.schedules.route_points(
                    vehicle.schedule_id, vehicle.rail_tiploc, vehicle.next_tiploc,
                )
                if len(refreshed_path) >= 2 and refreshed_path != (vehicle.estimate_path or []):
                    vehicle.estimate_path = refreshed_path
                    self.state.upsert(vehicle)
                    advanced += 1
            if (
                vehicle.type != "train"
                or not vehicle.schedule_id
                or not vehicle.service_date
                or not vehicle.estimate_due_at
                or now <= vehicle.estimate_due_at
            ):
                continue
            if self._advance_schedule_estimate(vehicle, now):
                # Keep last_updated as the last real railway report.  The new
                # estimate fields change the websocket payload and therefore
                # move every viewer, without pretending this was fresh GPS.
                vehicle.position_method = "estimated"
                vehicle.position_label = (
                    f"Estimated along the matched published railway schedule after the "
                    f"last confirmed {vehicle.source or 'railway'} report"
                )
                self.state.upsert(vehicle)
                retry_at.pop(vehicle.id, None)
                advanced += 1
            else:
                retry_at[vehicle.id] = (vehicle.estimate_due_at, tick + self.ROLLOVER_RETRY_SECONDS)
        return advanced

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
        if self.backlog_dropped:
            status.message += f" • {self.backlog_dropped:,} stale frames skipped under load"
        self.state.set_source(status)
