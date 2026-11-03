from __future__ import annotations

import threading
import time
from collections.abc import Callable


class FixedWindowBudget:
    """Small single-process request budget for quota-bearing public routes."""

    def __init__(
        self,
        *,
        per_client: int,
        global_limit: int,
        window_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.per_client = max(1, int(per_client))
        self.global_limit = max(self.per_client, int(global_limit))
        self.window_seconds = max(1.0, float(window_seconds))
        self._clock = clock
        self._lock = threading.Lock()
        self._window_started = self._clock()
        self._global_count = 0
        self._clients: dict[str, int] = {}
        self._rejected = 0

    def consume(self, client: str) -> tuple[bool, int]:
        now = self._clock()
        key = (client or "unknown")[:128]
        with self._lock:
            elapsed = now - self._window_started
            if elapsed >= self.window_seconds or elapsed < 0:
                self._window_started = now
                self._global_count = 0
                self._clients.clear()
                self._rejected = 0
                elapsed = 0
            retry_after = max(1, int(self.window_seconds - elapsed + 0.999))
            used = self._clients.get(key, 0)
            if used >= self.per_client or self._global_count >= self.global_limit:
                self._rejected += 1
                return False, retry_after
            self._clients[key] = used + 1
            self._global_count += 1
            return True, retry_after

    def status(self) -> dict[str, int]:
        """Return aggregate, non-identifying counters for operations monitoring."""
        now = self._clock()
        with self._lock:
            elapsed = now - self._window_started
            if elapsed >= self.window_seconds or elapsed < 0:
                used = 0
                rejected = 0
                seconds_remaining = int(self.window_seconds)
            else:
                used = self._global_count
                rejected = self._rejected
                seconds_remaining = max(1, int(self.window_seconds - elapsed + 0.999))
            return {
                "limit": self.global_limit,
                "used": used,
                "remaining": max(0, self.global_limit - used),
                "rejected": rejected,
                "seconds_remaining": seconds_remaining,
            }
