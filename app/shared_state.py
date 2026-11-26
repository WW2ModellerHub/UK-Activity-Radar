from __future__ import annotations

import asyncio
import json
import zlib
from dataclasses import dataclass

from .state import LiveState


SNAPSHOT_KEY = "uk-activity-radar:live-state:v1"


def encode_snapshot(payload: dict) -> bytes:
    """Compact a snapshot before it crosses the private network."""
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return zlib.compress(raw, level=3)


def decode_snapshot(blob: bytes) -> dict:
    value = json.loads(zlib.decompress(blob).decode("utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("revision"), int):
        raise ValueError("shared-state snapshot has no integer revision")
    return value


@dataclass
class SharedStateStatus:
    mode: str = "off"
    connected: bool = False
    revision: int = 0
    payload_bytes: int = 0
    error: str | None = None


class SharedStateBridge:
    """Redis-compatible handoff between the ingest and web processes.

    The bridge intentionally starts with atomic compressed snapshots. That is
    easier to verify and recover than a lossy event journal. After the split is
    soaked in production, changed-ID hashes can replace this transport without
    changing LiveState or the HTTP/WebSocket layer.
    """

    def __init__(self, url: str, state: LiveState, *, interval: float = 2.0, client=None) -> None:
        self.url = url
        self.state = state
        self.interval = max(0.5, float(interval))
        self.client = client
        self.status = SharedStateStatus()
        self._task: asyncio.Task | None = None

    async def _client(self):
        if self.client is None:
            from redis.asyncio import Redis
            self.client = Redis.from_url(self.url, decode_responses=False, socket_timeout=3)
        return self.client

    async def start_publisher(self) -> None:
        self.status.mode = "publisher"
        self._task = asyncio.create_task(self._publish_loop())

    async def start_consumer(self) -> None:
        self.status.mode = "consumer"
        self._task = asyncio.create_task(self._consume_loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self.client is not None and hasattr(self.client, "aclose"):
            await self.client.aclose()

    async def publish_once(self) -> bool:
        payload = await asyncio.to_thread(self.state.export_shared_snapshot)
        revision = int(payload["revision"])
        if revision == self.status.revision:
            return False
        blob = await asyncio.to_thread(encode_snapshot, payload)
        client = await self._client()
        await client.set(SNAPSHOT_KEY, blob, ex=30)
        self.status.connected = True
        self.status.revision = revision
        self.status.payload_bytes = len(blob)
        self.status.error = None
        return True

    async def consume_once(self) -> bool:
        client = await self._client()
        blob = await client.get(SNAPSHOT_KEY)
        if not blob:
            return False
        payload = await asyncio.to_thread(decode_snapshot, blob)
        revision = int(payload["revision"])
        if revision <= self.status.revision:
            return False
        await asyncio.to_thread(self.state.replace_shared_snapshot, payload)
        self.status.connected = True
        self.status.revision = revision
        self.status.payload_bytes = len(blob)
        self.status.error = None
        return True

    async def _publish_loop(self) -> None:
        while True:
            try:
                await self.publish_once()
            except Exception as exc:  # the live collectors must keep running
                self.status.connected = False
                self.status.error = type(exc).__name__
            await asyncio.sleep(self.interval)

    async def _consume_loop(self) -> None:
        while True:
            try:
                await self.consume_once()
            except Exception as exc:  # the web worker keeps its last good state
                self.status.connected = False
                self.status.error = type(exc).__name__
            await asyncio.sleep(min(self.interval, 1.0))
