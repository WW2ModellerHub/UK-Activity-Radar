from __future__ import annotations

import json
import random
import threading
import time
from datetime import datetime, timezone
from typing import Any

from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

from ..models import SourceStatus, Vehicle

POSITION_TYPES = {
    "PositionReport",
    "StandardClassBPositionReport",
    "ExtendedClassBPositionReport",
    "StandardSearchAndRescueAircraftReport",
}

RECEIVE_TIMEOUT_SECONDS = 10
STATUS_INTERVAL_SECONDS = 1.0
STABLE_CONNECTION_SECONDS = 30.0
MAX_RETRY_SECONDS = 60.0


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
    # AIS reserves 102.3kt for "not available". Likewise 360/511 mean that
    # course/heading isn't available; don't render those sentinels as real
    # telemetry in vessel cards or direction pointers.
    if speed is not None and not (0.0 <= speed < 102.3):
        speed = None
    bearing = _num(body.get("Cog"))
    if bearing is not None and not (0.0 <= bearing < 360.0):
        bearing = None
    heading = _num(body.get("TrueHeading"))
    if heading is not None and not (0.0 <= heading < 360.0):
        heading = None
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
        identity_label=display_name if ship_name else "AIS vessel",
        identity_confidence="exact" if ship_name else "unresolved",
        identity_explanation=("Vessel name and MMSI published by the live AIS feed." if ship_name else "The live AIS position includes an MMSI but no vessel name or class."),
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
        self._unmapped = 0
        self._subscription_confirmed = False
        self._active_socket = None
        self._last_error: str | None = None

    def start(self) -> None:
        if not self.settings.aisstream_api_key:
            self.state.set_source(SourceStatus(
                name="aisstream", enabled=True, connected=False, mode="setup",
                message="Free AISStream API key required. Run configure_maritime.bat.",
            ))
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="aisstream-live", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        # recv() is otherwise allowed to wait for its timeout. Closing the
        # active connection makes application shutdown deterministic.
        ws = self._active_socket
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=6)

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

    def _set_status(
        self,
        *,
        connected: bool,
        mode: str,
        message: str,
        last_message_at: datetime | None = None,
    ) -> None:
        self.state.set_source(SourceStatus(
            name="aisstream",
            enabled=True,
            connected=connected,
            mode=mode,
            message=message,
            last_message_at=last_message_at,
            messages_seen=self._messages_seen,
            movements_mapped=self._mapped,
            movements_unmapped=self._unmapped,
        ))

    def _live_message(self) -> str:
        return (
            f"{self._messages_seen:,} AIS messages • "
            f"{self._mapped:,} live positions accepted"
            + (f" • {self._unmapped:,} ignored" if self._unmapped else "")
        )

    def _consume_connection(self, ws) -> None:
        """Subscribe and drain one connection until it closes or is stopped.

        The stream must be read continuously. Status writes are deliberately
        throttled: updating shared state for every busy-channel AIS report can
        otherwise become a significant part of backend CPU usage.
        """
        ws.send(json.dumps(self._subscription(), separators=(",", ":")))
        self._subscription_confirmed = False
        last_message_at: datetime | None = None
        last_status_at = time.monotonic()

        self._set_status(
            connected=False,
            mode="connecting",
            message="AISStream connected • authenticating subscription…",
        )

        while not self._stop.is_set():
            try:
                raw = ws.recv(timeout=RECEIVE_TIMEOUT_SECONDS)
            except TimeoutError:
                # A quiet geographic filter is normal. recv(timeout=...) is
                # safe to retry and the WebSocket library maintains pings.
                if self._subscription_confirmed:
                    self._set_status(
                        connected=True,
                        mode="live",
                        message=self._live_message() or "AISStream live • waiting for positions…",
                        last_message_at=last_message_at,
                    )
                    continue
                raise TimeoutError("AISStream did not confirm the subscription")

            if raw is None:
                continue
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", errors="replace")
            try:
                payload = json.loads(raw)
            except (json.JSONDecodeError, TypeError, UnicodeError):
                # One malformed provider frame must not discard every vessel
                # already accumulated or force an unnecessary reconnect.
                self._messages_seen += 1
                self._unmapped += 1
                continue
            if not isinstance(payload, dict):
                self._messages_seen += 1
                self._unmapped += 1
                continue

            message_type = payload.get("MessageType")
            if message_type == "SubscriptionConfirmation":
                self._subscription_confirmed = True
                self._last_error = None
                last_message_at = datetime.now(timezone.utc)
                self._set_status(
                    connected=True,
                    mode="live",
                    message="AISStream subscription confirmed • waiting for positions…",
                    last_message_at=last_message_at,
                )
                continue

            # Receiving a normal frame also proves that the subscription was
            # accepted, even if a confirmation frame was lost in transit.
            self._subscription_confirmed = True
            self._messages_seen += 1
            last_message_at = datetime.now(timezone.utc)
            vehicle = parse_ais_message(payload)
            if vehicle is not None:
                self.state.upsert(vehicle)
                self._mapped += 1
            else:
                self._unmapped += 1

            now = time.monotonic()
            if now - last_status_at >= STATUS_INTERVAL_SECONDS:
                self._set_status(
                    connected=True,
                    mode="live",
                    message=self._live_message(),
                    last_message_at=last_message_at,
                )
                last_status_at = now

    @staticmethod
    def _failure_summary(exc: Exception, subscription_confirmed: bool) -> str:
        if not subscription_confirmed and isinstance(exc, ConnectionClosed):
            return (
                "AISStream closed before confirming the subscription. "
                "The API key may be invalid/revoked, the account/IP connection limit may be reached, "
                "or the provider may be unavailable"
            )
        if not subscription_confirmed and isinstance(exc, TimeoutError):
            return "AISStream did not confirm the subscription; check the API key and account limits"
        detail = str(exc).strip().replace("\r", " ").replace("\n", " ")[:180]
        return f"AISStream connection error: {type(exc).__name__}" + (f": {detail}" if detail else "")

    def _run(self) -> None:
        delay = 1.0
        while not self._stop.is_set():
            connected_at = time.monotonic()
            self._subscription_confirmed = False
            try:
                reconnect_note = f" Last error: {self._last_error}." if self._last_error else ""
                self._set_status(
                    connected=False,
                    mode="connecting",
                    message=f"Connecting to AISStream maritime WebSocket…{reconnect_note}",
                )
                with connect(
                    self.settings.aisstream_url,
                    open_timeout=10,
                    close_timeout=3,
                    compression="deflate",
                    ping_interval=20,
                    ping_timeout=20,
                    max_queue=512,
                ) as ws:
                    self._active_socket = ws
                    self._consume_connection(ws)
            except Exception as exc:
                if self._stop.is_set():
                    break
                connection_age = time.monotonic() - connected_at
                if self._subscription_confirmed and connection_age >= STABLE_CONNECTION_SECONDS:
                    delay = 1.0
                self._last_error = self._failure_summary(exc, self._subscription_confirmed)
                retry_in = min(MAX_RETRY_SECONDS, delay * random.uniform(0.85, 1.15))
                self._set_status(
                    connected=False,
                    mode="error",
                    message=f"{self._last_error} • retrying in {retry_in:.0f}s",
                )
                self._stop.wait(retry_in)
                delay = min(delay * 2.0, MAX_RETRY_SECONDS)
            finally:
                self._active_socket = None
