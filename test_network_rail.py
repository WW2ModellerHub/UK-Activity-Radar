from __future__ import annotations

import json
import threading
import time

import stomp

from app.config import settings

if not settings.credentials_present:
    raise SystemExit("No Network Rail credentials found in .env")

received = threading.Event()


class Listener(stomp.ConnectionListener):
    def on_message(self, frame):
        try:
            data = json.loads(frame.body)
            count = len(data) if isinstance(data, list) else 1
        except Exception:
            count = 1
        print(f"SUCCESS: received a genuine TRUST frame ({count} message(s)).")
        received.set()

    def on_error(self, frame):
        print("STOMP ERROR:", getattr(frame, "body", frame))
        received.set()


c = stomp.Connection12([(settings.network_rail_host, settings.network_rail_port)], heartbeats=(5000, 5000), keepalive=True)
c.set_listener("test", Listener())
print("Connecting to Network Rail TRUST...")
c.connect(login=settings.network_rail_username, passcode=settings.network_rail_password, wait=True)
c.subscribe(destination=settings.trust_topic, id="ukradar-test", ack="auto")
if not received.wait(30):
    print("Connected, but no TRUST message arrived within 30 seconds. Try again during normal traffic or check My Feeds.")
try:
    c.disconnect()
except Exception:
    pass
