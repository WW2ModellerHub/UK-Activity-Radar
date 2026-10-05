from __future__ import annotations

import threading
import time
from typing import Callable

import stomp

from ..config import Settings
from ..models import SourceStatus
from ..state import LiveState


class _Listener(stomp.ConnectionListener):
    def __init__(self, owner: "StompFeed") -> None:
        self.owner = owner

    def on_error(self, frame):
        body = getattr(frame, "body", "")
        self.owner.set_status(False, f"STOMP error: {str(body)[:260]}")

    def on_disconnected(self):
        self.owner.set_status(False, "Disconnected; reconnecting…")

    def on_message(self, frame):
        try:
            self.owner.handle_message(frame.body)
        except Exception as exc:
            self.owner.set_status(True, f"Message parse error: {type(exc).__name__}: {exc}")


class StompFeed:
    def __init__(self, settings: Settings, state: LiveState, *, name: str, topic: str) -> None:
        self.settings = settings
        self.state = state
        self.name = name
        self.topic = topic
        self._thread: threading.Thread | None = None
        self._connection = None
        self._stop = threading.Event()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=f"feed-{self.name}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        try:
            if self._connection and self._connection.is_connected():
                self._connection.disconnect()
        except Exception:
            pass

    def set_status(self, connected: bool, message: str, *, mode: str = "live") -> SourceStatus:
        old = self.state.source(self.name)
        status = old or SourceStatus(name=self.name, enabled=True, mode=mode)
        status.enabled = True
        status.connected = connected
        status.mode = mode
        status.message = message
        self.state.set_source(status)
        return status

    def _run(self) -> None:
        if not self.settings.credentials_present:
            self.state.set_source(SourceStatus(
                name=self.name, enabled=True, connected=False, mode="setup",
                message="Network Rail credentials are missing. Run configure_network_rail.bat, then restart run.bat.",
            ))
            return

        while not self._stop.is_set():
            self.set_status(False, f"Connecting to {self.topic}…")
            try:
                connection = stomp.Connection12(
                    [(self.settings.network_rail_host, self.settings.network_rail_port)],
                    heartbeats=(5000, 5000),
                    keepalive=True,
                )
                self._connection = connection
                connection.set_listener(self.name, _Listener(self))
                connection.connect(
                    login=self.settings.network_rail_username,
                    passcode=self.settings.network_rail_password,
                    wait=True,
                )
                connection.subscribe(destination=self.topic, id=f"ukradar-{self.name}", ack="auto")
                self.set_status(True, f"Connected to {self.topic}")
                while connection.is_connected() and not self._stop.wait(5):
                    pass
            except Exception as exc:
                self.set_status(False, f"Connection failed: {type(exc).__name__}: {exc}. Retrying in 10s…")
            if not self._stop.is_set():
                time.sleep(10)

    def handle_message(self, payload: str) -> None:
        raise NotImplementedError
