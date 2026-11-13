from __future__ import annotations

import threading
from collections import deque

import stomp

from ..config import Settings
from ..models import SourceStatus
from ..state import LiveState


def _session_retry_seconds(attempt: int) -> int:
    """Back off long enough for Network Rail's stale-session block to expire."""
    return min(7200, 900 * (2 ** max(0, int(attempt) - 1)))


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
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)

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
                # Network Rail's documented examples negotiate STOMP 1.1,
                # send host '/', and identify a durable subscriber. Using a
                # bare 1.2 session can be accepted at TCP level then rejected
                # before a useful ERROR frame reaches the application.
                client_id = f"{self.settings.network_rail_client_id}-{self.name}"
                connection = stomp.Connection11(
                    [(self.settings.network_rail_host, self.settings.network_rail_port)],
                    # Never leave a feed thread stuck forever in a TCP/STOMP
                    # handshake. A bounded failure produces a useful health
                    # message and lets the normal retry loop recover.
                    timeout=12,
                    reconnect_attempts_max=1,
                    heartbeats=(5000, 5000),
                    keepalive=True,
                    # STOMP 1.1 requires the virtual host in its host header.
                    # Network Rail's broker expects its public feed hostname,
                    # not the generic '/' value accepted by some brokers.
                    vhost=self.settings.network_rail_host,
                )
                self._connection = connection
                connection.set_listener(self.name, _Listener(self))
                connection.connect(
                    login=self.settings.network_rail_username,
                    passcode=self.settings.network_rail_password,
                    headers={"client-id": client_id},
                    wait=True,
                )
                connection.subscribe(
                    destination=self.topic,
                    id=f"ukradar-{self.name}",
                    ack="auto",
                    headers={"activemq.subscriptionName": client_id},
                )
                self.set_status(True, f"Connected to {self.topic}")
                while connection.is_connected() and not self._stop.wait(5):
                    pass
            except Exception as exc:
                self.set_status(False, f"Connection failed: {type(exc).__name__}: {exc}. Retrying in 10s…")
            if not self._stop.is_set():
                self._stop.wait(10)

    def handle_message(self, payload: str) -> None:
        raise NotImplementedError


class _MultiplexListener(stomp.ConnectionListener):
    def __init__(self, owner: "MultiplexStompFeed") -> None:
        self.owner = owner

    def on_error(self, frame):
        body = str(getattr(frame, "body", "") or "").strip()
        message = str((getattr(frame, "headers", {}) or {}).get("message") or "").strip()
        self.owner.last_error = body or message or "Broker rejected the STOMP session"
        self.owner.set_all(False, f"STOMP error: {self.owner.last_error}")

    def on_disconnected(self):
        if self.owner.terminal_error:
            message = self.owner.terminal_error
        else:
            message = f"Disconnected: {self.owner.last_error}" if self.owner.last_error else "Disconnected; reconnecting…"
        self.owner.set_all(False, message)

    def on_message(self, frame):
        headers = getattr(frame, "headers", {}) or {}
        destination = str(headers.get("destination") or "")
        subscription = str(headers.get("subscription") or "")
        target = next((
            feed for feed in self.owner.feeds
            if feed.topic == destination
            or destination.rstrip("/").endswith(feed.topic.rstrip("/").split("/")[-1])
            or subscription == f"ukradar-{feed.name}"
        ), None)
        if target is None:
            return
        enqueue = getattr(self.owner, "enqueue", None)
        if enqueue is not None:
            # Never do timetable/database work on the socket reader thread:
            # a slow handler stops the client reading, the broker sees a slow
            # consumer and ends the session.
            enqueue(target, frame.body)
            return
        try:
            target.handle_message(frame.body)
        except Exception as exc:
            target.set_status(True, f"Message parse error: {type(exc).__name__}: {exc}")


class _FrameQueue:
    """Hand-off between the socket reader and the message worker.

    Feeds marked ``drop_stale_backlog`` (Train Describer) carry positional
    steps that newer steps supersede, so their oldest frames are discarded
    once the backlog is full. Other feeds (TRUST, VSTP) are never dropped
    short of a much larger safety bound.
    """

    def __init__(self, *, droppable_limit: int = 1500, retained_limit: int = 50000) -> None:
        self._condition = threading.Condition()
        self._retained: deque = deque()
        self._droppable: deque = deque()
        self._droppable_limit = max(1, int(droppable_limit))
        self._retained_limit = max(1, int(retained_limit))
        self.dropped = 0

    def put(self, feed, body) -> None:
        with self._condition:
            if getattr(feed, "drop_stale_backlog", False):
                queue, limit = self._droppable, self._droppable_limit
            else:
                queue, limit = self._retained, self._retained_limit
            if len(queue) >= limit:
                stale_feed, _ = queue.popleft()
                self.dropped += 1
                try:
                    stale_feed.backlog_dropped = int(getattr(stale_feed, "backlog_dropped", 0)) + 1
                except Exception:
                    pass
            queue.append((feed, body))
            self._condition.notify()

    def get(self, timeout: float):
        with self._condition:
            if not self._retained and not self._droppable:
                self._condition.wait(timeout)
            if self._retained:
                return self._retained.popleft()
            if self._droppable:
                return self._droppable.popleft()
            return None

    def __len__(self) -> int:
        with self._condition:
            return len(self._retained) + len(self._droppable)


class MultiplexStompFeed:
    """One Network Rail broker session carrying TRUST, TD and VSTP.

    Network Rail explicitly recommends one connection with multiple topic
    subscriptions. This also avoids three simultaneous authentication races
    and reduces reconnect load on the public broker.
    """

    def __init__(self, settings: Settings, feeds: list[StompFeed]) -> None:
        self.settings = settings
        self.feeds = feeds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._connection = None
        self.last_error: str | None = None
        self.terminal_error: str | None = None
        self._frames = _FrameQueue()
        self._worker: threading.Thread | None = None

    def enqueue(self, feed, body) -> None:
        self._frames.put(feed, body)

    def backlog(self) -> int:
        return len(self._frames)

    def _work(self) -> None:
        while not self._stop.is_set():
            item = self._frames.get(0.5)
            if item is None:
                continue
            feed, body = item
            try:
                feed.handle_message(body)
            except Exception as exc:
                try:
                    feed.set_status(True, f"Message parse error: {type(exc).__name__}: {exc}")
                except Exception:
                    pass

    def set_all(self, connected: bool, message: str) -> None:
        for feed in self.feeds:
            feed.set_status(connected, message)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="network-rail-multiplex", daemon=True)
        self._thread.start()
        self._worker = threading.Thread(target=self._work, name="network-rail-worker", daemon=True)
        self._worker.start()

    def stop(self) -> None:
        self._stop.set()
        try:
            if self._connection and self._connection.is_connected():
                self._connection.disconnect()
        except Exception:
            pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)
        if self._worker and self._worker.is_alive():
            self._worker.join(timeout=3)

    def _run(self) -> None:
        if not self.settings.credentials_present:
            self.set_all(False, "Network Rail credentials are missing")
            return
        delay = 10.0
        rejected_sessions = 0
        while not self._stop.is_set():
            self.last_error = None
            self.terminal_error = None
            connection = None
            self.set_all(False, "Connecting to Network Rail live feeds…")
            try:
                connection = stomp.Connection11(
                    [(self.settings.network_rail_host, self.settings.network_rail_port)],
                    timeout=12, reconnect_attempts_max=1, heartbeats=(5000, 5000),
                    keepalive=True, vhost=self.settings.network_rail_host,
                )
                self._connection = connection
                connection.set_listener("network-rail-multiplex", _MultiplexListener(self))
                # These are deliberately non-durable subscriptions. A STOMP
                # client-id is the durable-session identity in ActiveMQ and
                # can make overlapping deploy processes contend for a session
                # even when subscriptionName is absent.
                connection.connect(
                    login=self.settings.network_rail_username,
                    passcode=self.settings.network_rail_password, wait=True,
                )
                for feed in self.feeds:
                    connection.subscribe(
                        destination=feed.topic, id=f"ukradar-{feed.name}", ack="auto",
                    )
                    feed.set_status(True, f"Connected to {feed.topic}")
                # Once a session has been established, give the broker time
                # to release it before reconnecting: an immediate retry is
                # answered with AMQ339009 and costs a 15 minute cool-down.
                delay = 30.0
                rejected_sessions = 0
                while connection.is_connected() and not self._stop.wait(5):
                    pass
            except Exception as exc:
                reason = self.last_error or f"{type(exc).__name__}: {exc}".strip(": ")
                fatal = reason.casefold()
                if "amq339009" in fatal:
                    # Session acquisition failures are commonly a deployment
                    # overlap or a broker-side stale session, not bad account
                    # details. Network Rail can firewall accounts for hours if
                    # they reconnect while the old STOMP session still exists,
                    # so a short retry loop actively prolongs the outage.
                    rejected_sessions += 1
                    cooldown = _session_retry_seconds(rejected_sessions)
                    self.terminal_error = (
                        f"Network Rail broker session was busy: {reason}. "
                        f"Retrying safely in {cooldown} seconds."
                    )
                    self.set_all(False, self.terminal_error)
                    if self._stop.wait(cooldown):
                        return
                    continue
                if any(marker in fatal for marker in (
                    "bad credentials", "authentication", "not authorized", "unauthorized",
                )):
                    # Repeated rejected sessions can cause a temporary source-IP
                    # block. Keep static schedules available, but stop reconnecting
                    # until the feed account/session has been corrected.
                    rejected_sessions += 1
                    cooldown = min(3600, 960 * rejected_sessions)
                    self.terminal_error = (
                        f"Network Rail rejected the live-feed session: {reason}. "
                        f"Cooling down for {cooldown // 60} minutes before one safe retry."
                    )
                    self.set_all(False, self.terminal_error)
                    if self._stop.wait(cooldown):
                        return
                    continue
                self.set_all(False, f"Network Rail connection failed: {reason}. Retrying in {int(delay)}s…")
            finally:
                try:
                    if connection is not None and connection.is_connected():
                        connection.disconnect()
                except Exception:
                    pass
                self._connection = None
            if not self._stop.is_set():
                self._stop.wait(delay)
                delay = min(300.0, delay * 2)
