from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from typing import Any

from websockets.sync.client import connect

from ..models import SourceStatus, Vehicle

POSITION_TYPES = {
    "PositionReport",
    "StandardClassBPositionReport",
    "ExtendedClassBPositionReport",
    "StandardSearchAndRescueAircraftReport",
}


def _num(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_ais_message(payload: dict[str, Any]) -> Vehicle | None:
    """Convert one AISStream envelope into a common Vehicle object.

    AISStream normalises MMSI/name/last known position in MetaData. Position
    message fields are used for speed/course where present. The browser only
    receives this normalised object; the AIS API key remains server-side.
    """
    message_type = str(payload.get("MessageType") or "")
    if message_type not in POSITION_TYPES:
        return None

    metadata = payload.get("MetaData") or {}
    if not isinstance(metadata, dict):
        metadata = {}
    message = payload.get("Message") or {}
    if not isinstance(message, dict):
        message = {}
    body = message.get(message_type) or {}
    if not isinstance(body, dict):
        body = {}

    lat = _num(metadata.get("Latitude"))
    lon = _num(metadata.get("Longitude"))
    if lat is None:
        lat = _num(body.get("Latitude"))
    if lon is None:
        lon = _num(body.get("Longitude"))
    if lat is None or lon is None or not (48.0 <= lat <= 62.5 and -13.5 <= lon <= 5.0):
        return None

    mmsi_raw = metadata.get("MMSI") or body.get("UserID") or body.get("UserId")
    if mmsi_raw in (None, ""):
        return None
    mmsi = str(mmsi_raw).strip()

    ship_name = str(metadata.get("ShipName") or "").strip().strip("@")
    is_sar = message_type == "StandardSearchAndRescueAircraftReport"
    activity_type = "sar" if is_sar else "vessel"
    if is_sar:
        display_name = ship_name or f"SAR aircraft {mmsi}"
    else:
        display_name = ship_name or f"Vessel {mmsi}"

    speed = _num(body.get("Sog"))
    bearing = _num(body.get("Cog"))
    heading = _num(body.get("TrueHeading"))
    if bearing is None:
        bearing = heading

    upper_name = display_name.upper()
    interesting = is_sar or any(token in upper_name for token in ("RNLI", "LIFEBOAT", "COASTGUARD", "RESCUE"))

    return Vehicle(
        id=f"ais:{mmsi}",
        type=activity_type,
        name=display_name,
        subtitle=("AIS search-and-rescue aircraft report" if is_sar else f"MMSI {mmsi}"),
        lat=lat,
        lon=lon,
        operator=None,
        route=None,
        status="Live AIS",
        speed=speed,
        speed_unit="kt" if speed is not None else None,
        bearing=bearing,
        position_method="gps",
        position_label=("Live AIS SAR position" if is_sar else "Live AIS vessel position"),
        source="AISStream",
        source_id=mmsi,
        vehicle_ref=mmsi,
        interesting=interesting,
        last_updated=datetime.now(timezone.utc),
    )


class AISStreamCollector:
    """Free AISStream WebSocket collector for UK/nearby waters.

    AISStream requires a free API key. The connection is always server-side and
    reconnects with bounded exponential backoff. Only position messages are
    requested to keep bandwidth and browser load under control.
    """

    def __init__(self, settings, state):
        self.settings = settings
        self.state = state
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._messages_seen = 0
        self._mapped = 0

    def start(self) -> None:
        if not self.settings.aisstream_api_key:
            self.state.set_source(SourceStatus(
                name="aisstream", enabled=True, connected=False, mode="setup",
                message="Free AISStream API key required. Run configure_maritime.bat.",
            ))
            return
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="aisstream-live", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=4)

    def _subscription(self) -> dict[str, Any]:
        return {
            "APIKey": self.settings.aisstream_api_key,
            "BoundingBoxes": self.settings.aisstream_bounding_boxes,
            "FilterMessageTypes": [
                "PositionReport",
                "StandardClassBPositionReport",
                "ExtendedClassBPositionReport",
                "StandardSearchAndRescueAircraftReport",
            ],
        }

    def _run(self) -> None:
        delay = 1.0
        while not self._stop.is_set():
            try:
                self.state.set_source(SourceStatus(
                    name="aisstream", enabled=True, connected=False, mode="connecting",
                    message="Connecting to free AISStream maritime WebSocket…",
                    messages_seen=self._messages_seen,
                    movements_mapped=self._mapped,
                ))
                with connect(
                    self.settings.aisstream_url,
                    open_timeout=10,
                    close_timeout=3,
                    compression="deflate",
                ) as ws:
                    ws.send(json.dumps(self._subscription(), separators=(",", ":")))
                    delay = 1.0
                    self.state.set_source(SourceStatus(
                        name="aisstream", enabled=True, connected=True, mode="live",
                        message="Connected • waiting for AIS position reports…",
                        last_message_at=datetime.now(timezone.utc),
                        messages_seen=self._messages_seen,
                        movements_mapped=self._mapped,
                    ))
                    while not self._stop.is_set():
                        raw = ws.recv(timeout=30)
                        if raw is None:
                            continue
                        if isinstance(raw, bytes):
                            raw = raw.decode("utf-8", errors="replace")
                        payload = json.loads(raw)
                        if payload.get("MessageType") == "SubscriptionConfirmation":
                            continue
                        self._messages_seen += 1
                        vehicle = parse_ais_message(payload)
                        if vehicle is not None:
                            self.state.upsert(vehicle)
                            self._mapped += 1
                        if self._messages_seen % 100 == 0 or vehicle is not None:
                            self.state.set_source(SourceStatus(
                                name="aisstream", enabled=True, connected=True, mode="live",
                                message=f"{self._messages_seen:,} AIS messages • {self._mapped:,} live positions accepted",
                                last_message_at=datetime.now(timezone.utc),
                                messages_seen=self._messages_seen,
                                movements_mapped=self._mapped,
                            ))
            except Exception as exc:
                self.state.set_source(SourceStatus(
                    name="aisstream", enabled=True, connected=False, mode="error",
                    message=f"AISStream connection error: {type(exc).__name__}: {exc} • retrying",
                    messages_seen=self._messages_seen,
                    movements_mapped=self._mapped,
                ))
                self._stop.wait(delay)
                delay = min(delay * 2.0, 30.0)
