from __future__ import annotations

import math
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import requests
from requests.adapters import HTTPAdapter

from .error_safety import public_error
from .models import SourceStatus
from .state import LiveState

_ALLOWED_STYLES = {"relative0", "relative0-dark", "relative-delay"}
_ALLOWED_MAP_STYLES = {"street-light", "street-dark"}


def _tile_for(lat: float, lon: float, zoom: int) -> tuple[int, int]:
    lat = max(-85.05112878, min(85.05112878, lat))
    n = 2 ** zoom
    x = int((lon + 180.0) / 360.0 * n)
    lat_rad = math.radians(lat)
    y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return max(0, min(n - 1, x)), max(0, min(n - 1, y))


@dataclass
class TileResult:
    content: bytes
    content_type: str
    fetched_at: float
    cache_max_age: int | None = None

    @property
    def cache_control(self) -> str:
        if self.cache_max_age is None:
            return "no-store"
        if self.cache_max_age <= 0:
            return "no-cache"
        return f"public, max-age={self.cache_max_age}"


def _provider_cache_max_age(response: requests.Response, fallback: int) -> int:
    """Use the provider's freshness instruction without extending it.

    Map providers can change tile freshness independently of this application.
    A missing directive uses our conservative configured fallback; explicit
    no-cache/no-store must always win.
    """
    control = str(response.headers.get("Cache-Control") or "").lower()
    if "no-store" in control or "no-cache" in control:
        return 0
    match = re.search(r"(?:^|,)\s*(?:s-maxage|max-age)\s*=\s*\"?(\d+)", control)
    if match:
        return max(0, int(match.group(1)))
    return max(0, int(fallback))


class TrafficFlowService:
    """On-demand live traffic-flow tile proxy.

    The browser never sees the TomTom API key. Tiles are cached briefly because
    traffic updates roughly once per minute; this also helps stay well inside
    the free development allowance.
    """

    def __init__(self, settings, state: LiveState):
        self.settings = settings
        self.state = state
        self._cache: dict[tuple[int, int, int, str], TileResult] = {}
        self._map_cache: dict[tuple[int, int, int, str], TileResult] = {}
        self._lock = threading.RLock()
        self._session = requests.Session()
        # A single map view requests many independent tiles at once. Requests'
        # default pool only keeps ten connections, which made cold map pans
        # queue behind discarded connections.
        adapter = HTTPAdapter(pool_connections=32, pool_maxsize=64, max_retries=0)
        self._session.mount("https://", adapter)
        self._session.mount("http://", adapter)
        self._messages = 0
        self._map_cache_hits = 0
        self._map_cache_misses = 0
        # Coalesce identical cold requests after a production restart.
        self._map_inflight: dict[tuple[int, int, int, str], threading.Event] = {}

    @staticmethod
    def _evict_oldest(cache: dict, limit: int) -> None:
        if len(cache) <= limit:
            return
        oldest = sorted(cache.items(), key=lambda item: item[1].fetched_at)[:max(1, len(cache) // 5)]
        for old_key, _ in oldest:
            cache.pop(old_key, None)

    @property
    def enabled(self) -> bool:
        return bool(self.settings.traffic_flow_enabled)

    @property
    def ready(self) -> bool:
        return self.enabled and bool(self.settings.tomtom_traffic_api_key)

    @property
    def map_ready(self) -> bool:
        """TomTom map display needs the same server-side platform key."""
        return bool(self.settings.tomtom_traffic_api_key)

    def set_initial_status(self) -> None:
        if not self.enabled:
            self.state.set_source(SourceStatus(
                name="traffic_flow", enabled=False, connected=False, mode="off",
                message="Live traffic-flow layer disabled in .env",
            ))
            return
        if not self.settings.tomtom_traffic_api_key:
            self.state.set_source(SourceStatus(
                name="traffic_flow", enabled=True, connected=False, mode="setup",
                message="Live traffic flow needs a free TomTom API key. Run configure_traffic.bat.",
            ))
            return
        self.state.set_source(SourceStatus(
            name="traffic_flow", enabled=True, connected=False, mode="connecting",
            message="Testing live traffic-flow tiles…",
        ))

    def self_test(self) -> bool:
        if not self.ready:
            self.set_initial_status()
            return False
        try:
            x, y = _tile_for(52.5, -1.5, 7)
            self.get_tile(7, x, y, "relative0", force=True)
            return True
        except Exception as exc:
            self.state.set_source(SourceStatus(
                name="traffic_flow", enabled=True, connected=False, mode="error",
                message=f"Traffic-flow test failed: {public_error(exc, self.settings.tomtom_traffic_api_key)}",
            ))
            return False

    @staticmethod
    def _orbis_style(style: str) -> str:
        return "dark" if style == "relative0-dark" else "light"

    def _orbis_flow_url(self, z: int, x: int, y: int) -> str:
        return (
            f"{self.settings.tomtom_traffic_base_url}/maps/orbis/traffic/flow/"
            f"raster/tile/{z}/{x}/{y}"
        )

    def _legacy_flow_url(self, z: int, x: int, y: int, style: str) -> str:
        style = style if style in _ALLOWED_STYLES else "relative0"
        return (
            f"{self.settings.tomtom_traffic_base_url}/traffic/map/4/tile/flow/"
            f"{style}/{z}/{x}/{y}.png"
        )

    def _tile_request(self, primary_url: str, primary_params: dict, *, fallback_url: str | None = None,
                      fallback_params: dict | None = None) -> requests.Response:
        headers = {
            "User-Agent": "UKActivityRadar/1.5.0",
            "Accept": "image/png",
            "TomTom-Api-Key": self.settings.tomtom_traffic_api_key,
            "TomTom-Api-Version": "2",
        }
        response = self._session.get(
            primary_url,
            params=primary_params,
            headers=headers,
            timeout=self.settings.traffic_flow_timeout_seconds,
        )
        if response.status_code == 200:
            return response
        if response.status_code == 429:
            raise RuntimeError("TomTom traffic allowance/rate limit reached (429)")
        if fallback_url:
            legacy = self._session.get(
                fallback_url,
                params=fallback_params or {"key": self.settings.tomtom_traffic_api_key, "tileSize": 256},
                headers={"User-Agent": "UKActivityRadar/1.5.0", "Accept": "image/png"},
                timeout=self.settings.traffic_flow_timeout_seconds,
            )
            if legacy.status_code == 200:
                return legacy
            if legacy.status_code == 429:
                raise RuntimeError("TomTom traffic allowance/rate limit reached (429)")
            if response.status_code == 403 and legacy.status_code == 403:
                raise RuntimeError("TomTom rejected the traffic API key/product entitlement (403)")
            legacy.raise_for_status()
        if response.status_code == 403:
            raise RuntimeError("TomTom rejected the traffic API key/product entitlement (403)")
        response.raise_for_status()
        return response

    def get_tile(self, z: int, x: int, y: int, style: str, *, force: bool = False) -> TileResult:
        if not self.ready:
            raise RuntimeError("Traffic flow is not configured")
        if style not in _ALLOWED_STYLES:
            style = "relative0"
        if not (0 <= z <= 22):
            raise ValueError("Invalid zoom")
        n = 2 ** z
        if not (0 <= x < n and 0 <= y < n):
            raise ValueError("Invalid tile coordinates")

        key = (z, x, y, style)
        now = time.time()
        with self._lock:
            cached = self._cache.get(key)
            if cached and not force and now - cached.fetched_at <= self.settings.traffic_flow_cache_seconds:
                return cached

        response = self._tile_request(
            self._orbis_flow_url(z, x, y),
            {"apiVersion": 2, "style": self._orbis_style(style), "tileSize": 256},
            fallback_url=self._legacy_flow_url(z, x, y, style),
            fallback_params={"key": self.settings.tomtom_traffic_api_key, "tileSize": 256},
        )
        content_type = response.headers.get("Content-Type", "image/png").split(";")[0]
        if not response.content:
            raise RuntimeError("Traffic provider returned an empty tile")

        result = TileResult(response.content, content_type, now)
        with self._lock:
            self._cache[key] = result
            # Bound RAM use while keeping the most recently used region hot.
            if len(self._cache) > self.settings.traffic_flow_cache_max_tiles:
                oldest = sorted(self._cache.items(), key=lambda kv: kv[1].fetched_at)[: max(1, len(self._cache) // 5)]
                for old_key, _ in oldest:
                    self._cache.pop(old_key, None)
            self._messages += 1
            messages = self._messages

        self.state.set_source(SourceStatus(
            name="traffic_flow", enabled=True, connected=True, mode="live",
            message=(
                f"TomTom Orbis v2 traffic live • {messages:,} tile requests served • "
                f"{self.settings.traffic_flow_cache_seconds}s server cache"
            ),
            last_message_at=datetime.now(timezone.utc),
            messages_seen=messages,
        ))
        return result

    def get_map_tile(self, z: int, x: int, y: int, style: str = "street-light", *, force: bool = False) -> TileResult:
        """Return a commercially supported TomTom Orbis base-map tile.

        The API key stays on the server, replacing community tile servers that
        provide no production SLA.  Base-map tiles receive a much longer cache
        lifetime than live traffic overlays.
        """
        if not self.map_ready:
            raise RuntimeError("Base map is not configured")
        if style not in _ALLOWED_MAP_STYLES:
            style = "street-light"
        if not (0 <= z <= 22):
            raise ValueError("Invalid zoom")
        n = 2 ** z
        if not (0 <= x < n and 0 <= y < n):
            raise ValueError("Invalid tile coordinates")

        cache_style = f"map-{style}"
        key = (z, x, y, cache_style)
        owner_event: threading.Event | None = None
        registered_inflight = False
        while owner_event is None:
            now = time.time()
            with self._lock:
                cached = self._map_cache.get(key)
                if cached and not force and now - cached.fetched_at <= int(cached.cache_max_age or 0):
                    self._map_cache_hits += 1
                    return cached
                if force:
                    owner_event = threading.Event()
                    self._map_cache_misses += 1
                    break
                waiting_for = None if force else self._map_inflight.get(key)
                if waiting_for is None:
                    owner_event = threading.Event()
                    self._map_inflight[key] = owner_event
                    registered_inflight = True
                    self._map_cache_misses += 1
                    break
            # Never hold the service lock while another request is doing I/O.
            # If that request fails, the loop elects one waiter to retry.
            waiting_for.wait(timeout=max(1.0, float(self.settings.traffic_flow_timeout_seconds) + 1.0))

        url = (
            f"{self.settings.tomtom_traffic_base_url}/maps/orbis/display/"
            f"raster/tile/{z}/{x}/{y}"
        )
        try:
            response = self._tile_request(
                url,
                {"apiVersion": 2, "style": style},
            )
            content_type = response.headers.get("Content-Type", "image/png").split(";")[0]
            if not response.content:
                raise RuntimeError("Map provider returned an empty tile")
            result = TileResult(
                response.content,
                content_type,
                time.time(),
                cache_max_age=_provider_cache_max_age(response, self.settings.map_tile_cache_seconds),
            )
            with self._lock:
                if result.cache_max_age:
                    self._map_cache[key] = result
                    self._evict_oldest(self._map_cache, self.settings.map_tile_cache_max_tiles)
            return result
        finally:
            if registered_inflight:
                with self._lock:
                    event = self._map_inflight.pop(key, None)
                    if event is not None:
                        event.set()


    def get_incident_tile(self, z: int, x: int, y: int, *, force: bool = False) -> TileResult:
        """Return TomTom's live traffic-incident raster tile.

        This deliberately uses the same server-side proxy/cache pattern as flow
        tiles so the user's TomTom key is never exposed to browser JavaScript.
        """
        if not self.ready:
            raise RuntimeError("Traffic incidents are not configured")
        if not (0 <= z <= 22):
            raise ValueError("Invalid zoom")
        n = 2 ** z
        if not (0 <= x < n and 0 <= y < n):
            raise ValueError("Invalid tile coordinates")

        cache_style = "incidents-s3"
        key = (z, x, y, cache_style)
        now = time.time()
        with self._lock:
            cached = self._cache.get(key)
            if cached and not force and now - cached.fetched_at <= self.settings.traffic_flow_cache_seconds:
                return cached

        orbis_url = (
            f"{self.settings.tomtom_traffic_base_url}/maps/orbis/traffic/incidents/"
            f"raster/tile/{z}/{x}/{y}"
        )
        legacy_url = (
            f"{self.settings.tomtom_traffic_base_url}/traffic/map/4/tile/incidents/"
            f"s3/{z}/{x}/{y}.png"
        )
        response = self._tile_request(
            orbis_url,
            {"apiVersion": 2, "style": "light", "tileSize": 256},
            fallback_url=legacy_url,
            fallback_params={"key": self.settings.tomtom_traffic_api_key, "tileSize": 256},
        )
        content_type = response.headers.get("Content-Type", "image/png").split(";")[0]
        if not response.content:
            raise RuntimeError("Traffic provider returned an empty incident tile")

        result = TileResult(response.content, content_type, now)
        with self._lock:
            self._cache[key] = result
            if len(self._cache) > self.settings.traffic_flow_cache_max_tiles:
                oldest = sorted(self._cache.items(), key=lambda kv: kv[1].fetched_at)[: max(1, len(self._cache) // 5)]
                for old_key, _ in oldest:
                    self._cache.pop(old_key, None)
        return result


    def get_segment(self, lat: float, lon: float, zoom: int = 10) -> dict:
        """Return live road-speed detail at a clicked map point.

        Complements the colour tile with current/free-flow speeds, travel time,
        delay and road-closure status. The provider key remains server-side.
        """
        if not self.ready:
            raise RuntimeError("Traffic flow is not configured")
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError("Invalid coordinates")
        zoom = max(0, min(22, int(zoom)))
        url = f"{self.settings.tomtom_traffic_base_url}/traffic/services/4/flowSegmentData/absolute/{zoom}/json"
        response = self._session.get(
            url,
            params={"key": self.settings.tomtom_traffic_api_key, "point": f"{lat},{lon}", "unit": "mph"},
            headers={"User-Agent": "UKActivityRadar/1.5.0"},
            timeout=self.settings.traffic_flow_timeout_seconds,
        )
        if response.status_code == 403:
            raise RuntimeError("TomTom rejected the API key (403)")
        if response.status_code == 429:
            raise RuntimeError("TomTom free traffic allowance/rate limit reached (429)")
        response.raise_for_status()
        p = response.json().get("flowSegmentData") or {}
        current = float(p.get("currentSpeed")) if p.get("currentSpeed") is not None else None
        free = float(p.get("freeFlowSpeed")) if p.get("freeFlowSpeed") is not None else None
        current_tt = float(p.get("currentTravelTime")) if p.get("currentTravelTime") is not None else None
        free_tt = float(p.get("freeFlowTravelTime")) if p.get("freeFlowTravelTime") is not None else None
        delay = max(0.0, current_tt - free_tt) if current_tt is not None and free_tt is not None else None
        ratio = (current / free) if current is not None and free and free > 0 else None
        if p.get("roadClosure"):
            level = "closed"
        elif ratio is None:
            level = "unknown"
        elif ratio < .2:
            level = "severe"
        elif ratio < .45:
            level = "heavy"
        elif ratio < .7:
            level = "slow"
        else:
            level = "normal"
        coords = (((p.get("coordinates") or {}).get("coordinate")) or [])
        return {
            "current_speed_mph": current, "free_flow_speed_mph": free,
            "current_travel_time_seconds": current_tt, "free_flow_travel_time_seconds": free_tt,
            "delay_seconds": delay, "speed_ratio": ratio, "confidence": p.get("confidence"),
            "road_closure": bool(p.get("roadClosure")), "congestion": level,
            "frc": p.get("frc"), "coordinates": coords, "provider": "TomTom Orbis Traffic",
        }

    def status_payload(self) -> dict:
        status = self.state.source("traffic_flow")
        with self._lock:
            map_cache = {
                "entries": len(self._map_cache),
                "hits": self._map_cache_hits,
                "misses": self._map_cache_misses,
                "limit": self.settings.map_tile_cache_max_tiles,
            }
        return {
            "enabled": self.enabled,
            "configured": self.ready,
            "provider": "TomTom Orbis Traffic",
            "free_tier": True,
            "source_status": status.model_dump(mode="json") if status else None,
            "styles": ["relative0", "relative-delay", "relative0-dark"],
            "base_map": {
                "configured": self.map_ready,
                "provider": "TomTom Orbis Map Display",
                "cache": map_cache,
            },
        }
