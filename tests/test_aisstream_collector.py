from __future__ import annotations

import json
from types import SimpleNamespace

from websockets.exceptions import ConnectionClosedError

from app.collectors.aisstream import AISStreamCollector, parse_ais_message


def _position(mmsi: int = 235000001) -> dict:
    return {
        "MessageType": "PositionReport",
        "MetaData": {
            "MMSI": mmsi,
            "ShipName": "TEST VESSEL",
            "Latitude": 51.1,
            "Longitude": 1.2,
        },
        "Message": {
            "PositionReport": {
                "UserID": mmsi,
                "Sog": 12.4,
                "Cog": 86.7,
                "TrueHeading": 87,
            }
        },
    }


class RecordingState:
    def __init__(self) -> None:
        self.statuses = []
        self.vehicles = []

    def set_source(self, status) -> None:
        self.statuses.append(status)

    def upsert(self, vehicle) -> None:
        self.vehicles.append(vehicle)


class FakeSocket:
    def __init__(self, collector: AISStreamCollector, frames: list[object]) -> None:
        self.collector = collector
        self.frames = list(frames)
        self.sent = []

    def send(self, message: str) -> None:
        self.sent.append(message)

    def recv(self, timeout: float):
        assert timeout > 0
        frame = self.frames.pop(0)
        if isinstance(frame, BaseException):
            raise frame
        if not self.frames:
            self.collector._stop.set()
        return frame


def _collector() -> tuple[AISStreamCollector, RecordingState]:
    state = RecordingState()
    settings = SimpleNamespace(
        aisstream_api_key="private-test-key",
        aisstream_url="wss://example.invalid/stream",
        aisstream_bounding_boxes=[[[61.5, -13.0], [48.5, 4.0]]],
    )
    return AISStreamCollector(settings, state), state


def test_collector_waits_for_confirmation_and_maps_binary_frame():
    collector, state = _collector()
    frames = [
        json.dumps({"MessageType": "SubscriptionConfirmation", "Message": {"CompressionEnabled": True}}),
        json.dumps(_position()).encode(),
    ]
    socket = FakeSocket(collector, frames)

    collector._consume_connection(socket)

    subscription = json.loads(socket.sent[0])
    assert subscription["APIKey"] == "private-test-key"
    assert subscription["BoundingBoxes"]
    assert collector._subscription_confirmed is True
    assert collector._messages_seen == 1
    assert collector._mapped == 1
    assert state.vehicles[0].id == "ais:235000001"
    assert any(status.mode == "live" and status.connected for status in state.statuses)


def test_quiet_timeout_and_malformed_frame_do_not_reconnect():
    collector, state = _collector()
    frames = [
        json.dumps({"MessageType": "SubscriptionConfirmation", "Message": {}}),
        TimeoutError(),
        b"not-json",
        json.dumps(_position(235000002)),
    ]

    collector._consume_connection(FakeSocket(collector, frames))

    assert collector._mapped == 1
    assert collector._unmapped == 1
    assert collector._messages_seen == 2
    assert len(state.vehicles) == 1


def test_busy_stream_does_not_write_status_for_every_position(monkeypatch):
    collector, state = _collector()
    monkeypatch.setattr("app.collectors.aisstream.time.monotonic", lambda: 100.0)
    frames = [json.dumps({"MessageType": "SubscriptionConfirmation", "Message": {}})]
    frames.extend(json.dumps(_position(235100000 + index)) for index in range(100))

    collector._consume_connection(FakeSocket(collector, frames))

    assert collector._mapped == 100
    # One authenticating status and one confirmation status; the old code did
    # another shared-state write for every position report.
    assert len(state.statuses) == 2


def test_unconfirmed_close_has_actionable_key_or_limit_message():
    summary = AISStreamCollector._failure_summary(
        ConnectionClosedError(None, None), subscription_confirmed=False
    )

    assert "before confirming" in summary
    assert "key" in summary.lower()
    assert "connection limit" in summary.lower()


def test_reserved_ais_speed_and_direction_values_are_not_displayed():
    payload = _position()
    body = payload["Message"]["PositionReport"]
    body["Sog"] = 102.3
    body["Cog"] = 360
    body["TrueHeading"] = 511

    vehicle = parse_ais_message(payload)

    assert vehicle is not None
    assert vehicle.speed is None
    assert vehicle.speed_unit is None
    assert vehicle.bearing is None
