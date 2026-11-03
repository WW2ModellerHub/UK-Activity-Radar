import json
import sys
import time

from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

from app.collectors.aisstream import parse_ais_message
from app.config import settings

validate_only = "--validate-only" in sys.argv[1:]

if not settings.aisstream_api_key:
    raise SystemExit("No AISSTREAM_API_KEY found. Run configure_maritime.bat first.")

subscription = {
    "APIKey": settings.aisstream_api_key,
    "BoundingBoxes": settings.aisstream_bounding_boxes,
    "FilterMessageTypes": [
        "PositionReport",
        "StandardClassBPositionReport",
        "ExtendedClassBPositionReport",
        "StandardSearchAndRescueAircraftReport",
    ],
}

print("Connecting to AISStream…")
accepted = 0
seen = 0
confirmed = False
started = time.monotonic()
try:
    with connect(
        settings.aisstream_url,
        open_timeout=10,
        close_timeout=3,
        compression="deflate",
        ping_interval=20,
        ping_timeout=20,
        max_queue=512,
    ) as ws:
        ws.send(json.dumps(subscription, separators=(",", ":")))
        while time.monotonic() - started < 30:
            try:
                raw = ws.recv(timeout=10)
            except TimeoutError:
                if confirmed:
                    continue
                raise SystemExit("FAILED: AISStream did not confirm the subscription. Check the API key and account limits.")
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", errors="replace")
            try:
                payload = json.loads(raw)
            except (json.JSONDecodeError, TypeError, UnicodeError):
                continue
            if not isinstance(payload, dict):
                continue
            if payload.get("MessageType") == "SubscriptionConfirmation":
                confirmed = True
                print("Subscription confirmed.")
                if validate_only:
                    raise SystemExit(0)
                continue
            confirmed = True
            seen += 1
            vehicle = parse_ais_message(payload)
            if vehicle:
                accepted += 1
                print(f"{vehicle.type.upper():7} {vehicle.name[:34]:34} {vehicle.lat:8.4f}, {vehicle.lon:9.4f}  {vehicle.speed or 0:g} kt")
                if accepted >= 10:
                    break
except ConnectionClosed:
    if not confirmed:
        raise SystemExit(
            "FAILED: AISStream closed before confirming the subscription. "
            "The API key may be invalid/revoked, or the provider may be unavailable. "
            "Generate a current key at https://aisstream.io/account and retry."
        ) from None
    raise SystemExit("FAILED: AISStream disconnected during the live feed test; try again shortly.") from None

if not accepted:
    print(f"Connected and saw {seen} AIS messages, but none mapped in the configured UK box during the test window.")
    sys.exit(2)
print(f"SUCCESS: {accepted} live AIS positions parsed.")
