from __future__ import annotations

import io
import struct
import threading
import time
import zipfile
from datetime import datetime, timedelta, timezone
from typing import Iterable, Iterator
from xml.etree import ElementTree as ET

import requests

from ..models import SourceStatus, Vehicle


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _first_text(node: ET.Element, name: str) -> str | None:
    for child in node.iter():
        if _local(child.tag) == name and child.text:
            text = child.text.strip()
            if text:
                return text
    return None


def _parse_float(value: str | None) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _parse_time(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    try:
        clean = value.strip().replace("Z", "+00:00")
        parsed = datetime.fromisoformat(clean)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return datetime.now(timezone.utc)


def parse_siri_vm(xml_bytes: bytes) -> list[Vehicle]:
    """Parse one SIRI-VM document into common Vehicle objects."""
    root = ET.fromstring(xml_bytes)
    out: list[Vehicle] = []

    for activity in root.iter():
        if _local(activity.tag) != "VehicleActivity":
            continue

        recorded_at = _parse_time(_first_text(activity, "RecordedAtTime"))
        item_id = _first_text(activity, "ItemIdentifier") or ""

        journey = None
        for child in activity.iter():
            if _local(child.tag) == "MonitoredVehicleJourney":
                journey = child
                break
        if journey is None:
            continue

        lat = _parse_float(_first_text(journey, "Latitude"))
        lon = _parse_float(_first_text(journey, "Longitude"))
        if lat is None or lon is None or not (49.0 <= lat <= 61.5 and -9.5 <= lon <= 3.5):
            continue

        operator = _first_text(journey, "OperatorRef") or "Unknown operator"
        line = _first_text(journey, "PublishedLineName") or _first_text(journey, "LineRef") or "Bus"
        vehicle_ref = _first_text(journey, "VehicleRef") or item_id or _first_text(journey, "VehicleJourneyRef") or "unknown"
        journey_ref = _first_text(journey, "VehicleJourneyRef")
        origin = _first_text(journey, "OriginName")
        destination = _first_text(journey, "DestinationName")
        bearing = _parse_float(_first_text(journey, "Bearing"))
        velocity = _parse_float(_first_text(journey, "Velocity"))
        occupancy = _first_text(journey, "Occupancy")
        # SIRI-VM publishers often include a MonitoredCall/OnwardCall StopPointRef.
        # Preserve the first published stop reference so a clicked stop can focus
        # a bus that is actually reporting that stop as current/next, rather than
        # merely a geographically nearby vehicle.
        next_stop_ref = _first_text(journey, "StopPointRef") or None

        subtitle = " → ".join(x for x in (origin, destination) if x)
        if not subtitle:
            subtitle = f"Route {line} • {operator}"

        out.append(Vehicle(
            id=f"bus:{operator}:{vehicle_ref}",
            type="bus",
            name=f"Bus {line}",
            subtitle=subtitle,
            lat=lat,
            lon=lon,
            operator=operator,
            route=line,
            origin=origin,
            destination=destination,
            status="Live GPS",
            speed=velocity,
            speed_unit="m/s" if velocity is not None else None,
            position_method="gps",
            position_label="Live GPS from DfT BODS",
            source="DfT Bus Open Data Service",
            source_id=item_id or vehicle_ref,
            vehicle_ref=vehicle_ref,
            journey_ref=journey_ref,
            bearing=bearing,
            occupancy=occupancy,
            next_location=next_stop_ref,
            last_updated=recorded_at,
        ))

    return out



def _prediction_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def parse_siri_stop_predictions(xml_bytes: bytes) -> list[dict]:
    """Extract stop-specific arrival/departure predictions when publishers include them.

    BODS requires SIRI-VM vehicle locations. Some publishers additionally include
    MonitoredCall / OnwardCall expected times; those are useful for a genuine
    stop countdown. Absence is normal; the UI only focuses a live vehicle when
    the feed identifies the clicked stop as that vehicle's current/next stop.
    It never presents an arbitrary nearby bus as due.
    """
    root = ET.fromstring(xml_bytes)
    out: list[dict] = []
    now = datetime.now(timezone.utc)
    for activity in root.iter():
        if _local(activity.tag) != "VehicleActivity":
            continue
        journey = next((c for c in activity.iter() if _local(c.tag) == "MonitoredVehicleJourney"), None)
        if journey is None:
            continue
        route = _first_text(journey, "PublishedLineName") or _first_text(journey, "LineRef")
        destination = _first_text(journey, "DestinationName")
        operator = _first_text(journey, "OperatorRef")
        vehicle_ref = _first_text(journey, "VehicleRef")
        journey_ref = _first_text(journey, "VehicleJourneyRef")
        for call in journey.iter():
            if _local(call.tag) not in {"MonitoredCall", "OnwardCall"}:
                continue
            stop_ref = _first_text(call, "StopPointRef")
            if not stop_ref:
                continue
            expected = (_prediction_time(_first_text(call, "ExpectedArrivalTime")) or
                        _prediction_time(_first_text(call, "ExpectedDepartureTime")) or
                        _prediction_time(_first_text(call, "AimedArrivalTime")) or
                        _prediction_time(_first_text(call, "AimedDepartureTime")))
            if expected is None or expected < now - timedelta(minutes=2) or expected > now + timedelta(hours=3):
                continue
            out.append({
                "stop_ref": stop_ref, "route": route, "destination": destination,
                "operator": operator, "vehicle_ref": vehicle_ref, "journey_ref": journey_ref,
                "expected": expected.isoformat(),
                "minutes": max(0, int((expected-now).total_seconds()//60)),
            })
    return out


def parse_siri_predictions_from_payload(payload: bytes, content_type: str = "") -> list[dict]:
    ct=(content_type or "").lower()
    if payload[:4] == b"PK\x03\x04" or "zip" in ct:
        archive=zipfile.ZipFile(io.BytesIO(payload)); out=[]
        for name in archive.namelist():
            if name.endswith('/') or not name.lower().endswith((".xml", ".siri")):
                continue
            try: out.extend(parse_siri_stop_predictions(archive.read(name)))
            except Exception: continue
        return out
    stripped=payload.lstrip()
    if stripped.startswith(b"<") or "xml" in ct:
        try: return parse_siri_stop_predictions(payload)
        except Exception: return []
    return []

# --- Minimal GTFS-Realtime protobuf reader ---------------------------------
# BODS' documented /api/v1/gtfsrtdatafeed/ endpoint returns a protobuf FeedMessage.
# Keeping a tiny reader here avoids exposing the API key in the browser and avoids
# depending on generated GTFS bindings just to extract vehicle positions.


def _read_varint(data: bytes, offset: int) -> tuple[int, int]:
    value = 0
    shift = 0
    while offset < len(data):
        b = data[offset]
        offset += 1
        value |= (b & 0x7F) << shift
        if not (b & 0x80):
            return value, offset
        shift += 7
        if shift > 70:
            raise ValueError("protobuf varint is too long")
    raise ValueError("truncated protobuf varint")


def _protobuf_fields(data: bytes) -> Iterator[tuple[int, int, object]]:
    i = 0
    while i < len(data):
        key, i = _read_varint(data, i)
        field_no, wire = key >> 3, key & 7
        if field_no <= 0:
            raise ValueError("invalid protobuf field number")
        if wire == 0:
            value, i = _read_varint(data, i)
            yield field_no, wire, value
        elif wire == 1:
            if i + 8 > len(data):
                raise ValueError("truncated protobuf fixed64")
            yield field_no, wire, data[i:i + 8]
            i += 8
        elif wire == 2:
            length, i = _read_varint(data, i)
            end = i + length
            if end > len(data):
                raise ValueError("truncated protobuf bytes")
            yield field_no, wire, data[i:end]
            i = end
        elif wire == 5:
            if i + 4 > len(data):
                raise ValueError("truncated protobuf fixed32")
            yield field_no, wire, data[i:i + 4]
            i += 4
        else:
            raise ValueError(f"unsupported protobuf wire type {wire}")


def _p_string(value: object) -> str | None:
    if not isinstance(value, (bytes, bytearray)):
        return None
    try:
        text = bytes(value).decode("utf-8", errors="replace").strip()
        return text or None
    except Exception:
        return None


def _parse_trip_descriptor(data: bytes) -> dict:
    out: dict[str, object] = {}
    for field, wire, value in _protobuf_fields(data):
        if wire == 2 and field == 1:
            out["trip_id"] = _p_string(value)
        elif wire == 2 and field == 2:
            out["start_time"] = _p_string(value)
        elif wire == 2 and field == 3:
            out["start_date"] = _p_string(value)
        elif wire == 2 and field == 5:
            out["route_id"] = _p_string(value)
        elif wire == 0 and field == 6:
            out["direction_id"] = int(value)
    return out


def _parse_vehicle_descriptor(data: bytes) -> dict:
    out: dict[str, object] = {}
    for field, wire, value in _protobuf_fields(data):
        if wire != 2:
            continue
        if field == 1:
            out["id"] = _p_string(value)
        elif field == 2:
            out["label"] = _p_string(value)
        elif field == 3:
            out["license_plate"] = _p_string(value)
    return out


def _parse_position(data: bytes) -> dict:
    out: dict[str, float] = {}
    for field, wire, value in _protobuf_fields(data):
        if wire == 5 and isinstance(value, (bytes, bytearray)):
            number = struct.unpack("<f", bytes(value))[0]
            if field == 1:
                out["lat"] = float(number)
            elif field == 2:
                out["lon"] = float(number)
            elif field == 3:
                out["bearing"] = float(number)
            elif field == 5:
                out["speed"] = float(number)
    return out


def _occupancy_name(value: int | None) -> str | None:
    names = {
        0: "EMPTY",
        1: "MANY_SEATS_AVAILABLE",
        2: "FEW_SEATS_AVAILABLE",
        3: "STANDING_ROOM_ONLY",
        4: "CRUSHED_STANDING_ROOM_ONLY",
        5: "FULL",
        6: "NOT_ACCEPTING_PASSENGERS",
        7: "NO_DATA_AVAILABLE",
        8: "NOT_BOARDABLE",
    }
    return names.get(value) if value is not None else None


def _parse_vehicle_position(data: bytes) -> dict:
    out: dict[str, object] = {}
    for field, wire, value in _protobuf_fields(data):
        if field == 1 and wire == 2:
            out["trip"] = _parse_trip_descriptor(bytes(value))
        elif field == 2 and wire == 2:
            out["position"] = _parse_position(bytes(value))
        elif field == 5 and wire == 0:
            out["timestamp"] = int(value)
        elif field == 7 and wire == 2:
            out["stop_id"] = _p_string(value)
        elif field == 8 and wire == 2:
            out["vehicle"] = _parse_vehicle_descriptor(bytes(value))
        elif field == 9 and wire == 0:
            out["occupancy"] = int(value)
    return out


def parse_gtfsrt_payload(payload: bytes) -> list[Vehicle]:
    """Parse BODS GTFS-RT FeedMessage vehicle positions.

    The uploaded BODS AVL OpenAPI contract documents `/api/v1/gtfsrtdatafeed/`
    as the endpoint returning all vehicles in GTFS-RT protobuf format.
    """
    latest: dict[str, Vehicle] = {}
    for field, wire, value in _protobuf_fields(payload):
        if field != 2 or wire != 2:
            continue  # FeedMessage.entity
        entity_id: str | None = None
        vehicle_pos: dict | None = None
        for ef, ew, ev in _protobuf_fields(bytes(value)):
            if ef == 1 and ew == 2:
                entity_id = _p_string(ev)
            elif ef == 4 and ew == 2:
                vehicle_pos = _parse_vehicle_position(bytes(ev))
        if not vehicle_pos:
            continue

        position = vehicle_pos.get("position") if isinstance(vehicle_pos.get("position"), dict) else {}
        lat = position.get("lat")
        lon = position.get("lon")
        if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
            continue
        if not (49.0 <= float(lat) <= 61.5 and -9.5 <= float(lon) <= 3.5):
            continue

        trip = vehicle_pos.get("trip") if isinstance(vehicle_pos.get("trip"), dict) else {}
        desc = vehicle_pos.get("vehicle") if isinstance(vehicle_pos.get("vehicle"), dict) else {}
        route = str(trip.get("route_id") or "").strip() or None
        trip_id = str(trip.get("trip_id") or "").strip() or None
        vehicle_ref = str(desc.get("id") or desc.get("label") or entity_id or trip_id or "unknown").strip()
        label = str(desc.get("label") or "").strip() or None
        stamp = vehicle_pos.get("timestamp")
        try:
            recorded_at = datetime.fromtimestamp(int(stamp), tz=timezone.utc) if stamp else datetime.now(timezone.utc)
        except (TypeError, ValueError, OSError):
            recorded_at = datetime.now(timezone.utc)

        bus_name = f"Bus {route}" if route else (label or "Bus")
        subtitle_bits = []
        if label and label != vehicle_ref:
            subtitle_bits.append(label)
        if trip_id:
            subtitle_bits.append(f"Trip {trip_id}")
        subtitle = " • ".join(subtitle_bits) or "DfT BODS GTFS-RT"

        bus = Vehicle(
            id=f"bus:{vehicle_ref}",
            type="bus",
            name=bus_name,
            subtitle=subtitle,
            lat=float(lat),
            lon=float(lon),
            route=route,
            status="Live GPS",
            speed=position.get("speed") if isinstance(position.get("speed"), (int, float)) else None,
            speed_unit="m/s" if isinstance(position.get("speed"), (int, float)) else None,
            position_method="gps",
            position_label="Live GPS from DfT BODS GTFS-RT",
            source="DfT Bus Open Data Service",
            source_id=entity_id or vehicle_ref,
            vehicle_ref=vehicle_ref,
            journey_ref=trip_id,
            bearing=position.get("bearing") if isinstance(position.get("bearing"), (int, float)) else None,
            occupancy=_occupancy_name(vehicle_pos.get("occupancy") if isinstance(vehicle_pos.get("occupancy"), int) else None),
            next_location=str(vehicle_pos.get("stop_id") or "").strip() or None,
            last_updated=recorded_at,
        )
        old = latest.get(bus.id)
        if old is None or bus.last_updated >= old.last_updated:
            latest[bus.id] = bus

    return list(latest.values())


def parse_bods_payload(payload: bytes, content_type: str = "") -> list[Vehicle]:
    """Parse BODS GTFS-RT, public bulk ZIP, or raw SIRI-VM."""
    ct = (content_type or "").lower()
    if payload[:4] == b"PK\x03\x04" or "zip" in ct:
        archive = zipfile.ZipFile(io.BytesIO(payload))
        candidates: Iterable[tuple[str, bytes]] = (
            (name, archive.read(name))
            for name in archive.namelist()
            if not name.endswith("/") and name.lower().endswith((".xml", ".siri"))
        )
        latest: dict[str, Vehicle] = {}
        for _, xml_bytes in candidates:
            try:
                for vehicle in parse_siri_vm(xml_bytes):
                    old = latest.get(vehicle.id)
                    if old is None or vehicle.last_updated >= old.last_updated:
                        latest[vehicle.id] = vehicle
            except (ET.ParseError, UnicodeError, ValueError):
                continue
        return list(latest.values())

    stripped = payload.lstrip()
    if stripped.startswith(b"<") or "xml" in ct:
        return parse_siri_vm(payload)

    # BODS GTFS-RT advertises application/bin or application/protobuf. In
    # practice proxies can return octet-stream, so treat any non-XML payload as
    # protobuf and let the parser validate it.
    return parse_gtfsrt_payload(payload)


class BODSCollector:
    def __init__(self, settings, state):
        self.settings = settings
        self.state = state
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "UKActivityRadar/0.8 (BODS consumer)"})
        self._messages_seen = 0
        self._stop_predictions: dict[str, list[dict]] = {}
        self._prediction_lock = threading.RLock()

    @property
    def url(self) -> str:
        if self.settings.bods_api_key:
            return self.settings.bods_api_url
        return self.settings.bods_bulk_url

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="bods-live", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)

    def _fetch(self) -> tuple[bytes, str, str]:
        """Fetch live buses, preferring GTFS-RT but falling back to bulk SIRI-VM.

        Older UK Activity Radar builds accidentally persisted BODS' metadata
        endpoint in .env. ``settings.bods_api_url`` repairs that automatically.
        If BODS still rejects an API key, the public bulk snapshot is attempted
        so a key/config problem does not unnecessarily remove all buses.
        """
        api_error: Exception | None = None
        if self.settings.bods_api_key:
            try:
                response = self._session.get(
                    self.settings.bods_api_url,
                    params={"api_key": self.settings.bods_api_key},
                    headers={"Accept": "application/x-protobuf, application/octet-stream, */*"},
                    timeout=(10, 60),
                )
                if response.status_code in {401, 403}:
                    raise PermissionError(
                        f"BODS GTFS-RT returned HTTP {response.status_code}; trying the free bulk SIRI-VM snapshot instead."
                    )
                response.raise_for_status()
                return response.content, response.headers.get("content-type", ""), "gtfs-rt"
            except Exception as exc:
                api_error = exc

        try:
            response = self._session.get(
                self.settings.bods_bulk_url,
                headers={"Accept": "application/zip, application/xml, */*"},
                timeout=(10, 90),
            )
            response.raise_for_status()
            return response.content, response.headers.get("content-type", ""), "bulk-siri"
        except Exception as bulk_exc:
            if api_error is not None:
                raise RuntimeError(
                    f"GTFS-RT failed ({type(api_error).__name__}: {api_error}); "
                    f"bulk fallback also failed ({type(bulk_exc).__name__}: {bulk_exc})"
                ) from bulk_exc
            raise

    def _update_predictions(self, rows: list[dict]) -> None:
        now = datetime.now(timezone.utc)
        grouped: dict[str, list[dict]] = {}
        for row in rows:
            ref = str(row.get("stop_ref") or "").strip()
            if not ref:
                continue
            grouped.setdefault(ref, []).append(row)
        for values in grouped.values():
            values.sort(key=lambda x: x.get("expected") or "")
        with self._prediction_lock:
            self._stop_predictions = grouped

    def predictions_for_stop(self, stop_ref: str, limit: int = 10) -> list[dict]:
        ref = str(stop_ref or "").strip()
        if not ref:
            return []
        now = datetime.now(timezone.utc)
        with self._prediction_lock:
            rows = [dict(x) for x in self._stop_predictions.get(ref, [])]
        out=[]
        for row in rows:
            try:
                expected=datetime.fromisoformat(str(row.get("expected")).replace("Z","+00:00"))
                if expected.tzinfo is None: expected=expected.replace(tzinfo=timezone.utc)
                if expected < now - timedelta(minutes=2): continue
                row["minutes"]=max(0,int((expected-now).total_seconds()//60))
            except Exception:
                pass
            out.append(row)
        return out[:max(1,min(50,int(limit)))]

    def _run(self) -> None:
        source_mode = "gtfs-rt" if self.settings.bods_api_key else "bulk-siri"
        source_label = "BODS GTFS-RT API" if self.settings.bods_api_key else "BODS public bulk SIRI-VM"
        self.state.set_source(SourceStatus(
            name="bods", enabled=True, connected=False, mode="connecting",
            message=f"Connecting to {source_label}…",
        ))

        while not self._stop.is_set():
            started = time.monotonic()
            try:
                payload, content_type, used_mode = self._fetch()
                vehicles = parse_bods_payload(payload, content_type)
                predictions = parse_siri_predictions_from_payload(payload, content_type)
                if predictions:
                    self._update_predictions(predictions)
                now = datetime.now(timezone.utc)
                fresh = [v for v in vehicles if (now - v.last_updated).total_seconds() <= self.settings.bods_max_age_seconds]
                for vehicle in fresh:
                    self.state.upsert(vehicle)

                self._messages_seen += len(fresh)
                stale = len(vehicles) - len(fresh)
                mb = len(payload) / 1024 / 1024
                if not vehicles:
                    self.state.set_source(SourceStatus(
                        name="bods", enabled=True, connected=True, mode="ready",
                        message=f"BODS source verified but no current bus positions were parsed • {mb:.2f} MB response",
                        last_message_at=now,
                        messages_seen=self._messages_seen,
                    ))
                else:
                    self.state.set_source(SourceStatus(
                        name="bods", enabled=True, connected=True, mode="live" if fresh else "ready",
                        message=(
                            f"Live GPS • {len(fresh):,} current buses • "
                            f"{'GTFS-RT API' if used_mode == 'gtfs-rt' else 'bulk SIRI-VM fallback'}"
                            f" • {mb:.1f} MB • poll {self.settings.bods_poll_seconds}s"
                            + (f" • {stale:,} stale ignored" if stale else "")
                        ),
                        last_message_at=now,
                        messages_seen=self._messages_seen,
                        movements_mapped=len(fresh),
                        movements_unmapped=0,
                    ))
            except Exception as exc:
                previous = self.state.source("bods")
                self.state.set_source(SourceStatus(
                    name="bods", enabled=True, connected=False, mode="error",
                    message=f"BODS error: {type(exc).__name__}: {exc}",
                    messages_seen=previous.messages_seen if previous else self._messages_seen,
                    movements_mapped=previous.movements_mapped if previous else 0,
                ))

            elapsed = time.monotonic() - started
            wait = max(5.0, self.settings.bods_poll_seconds - elapsed)
            self._stop.wait(wait)
