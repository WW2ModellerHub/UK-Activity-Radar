import json
import sys
import time

from websockets.sync.client import connect

from app.collectors.aisstream import parse_ais_message
from app.config import settings

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
started = time.monotonic()
with connect(settings.aisstream_url, open_timeout=10, close_timeout=3, compression="deflate") as ws:
    ws.send(json.dumps(subscription, separators=(",", ":")))
    while time.monotonic() - started < 30:
        raw = ws.recv(timeout=10)
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        payload = json.loads(raw)
        if payload.get("MessageType") == "SubscriptionConfirmation":
            print("Subscription confirmed.")
            continue
        seen += 1
        vehicle = parse_ais_message(payload)
        if vehicle:
            accepted += 1
            print(f"{vehicle.type.upper():7} {vehicle.name[:34]:34} {vehicle.lat:8.4f}, {vehicle.lon:9.4f}  {vehicle.speed or 0:g} kt")
            if accepted >= 10:
                break

if not accepted:
    print(f"Connected and saw {seen} AIS messages, but none mapped in the configured UK box during the test window.")
    sys.exit(2)
print(f"SUCCESS: {accepted} live AIS positions parsed.")
