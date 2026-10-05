from __future__ import annotations

from dataclasses import dataclass
from math import asin, cos, radians, sin, sqrt
from threading import RLock
from time import monotonic
from typing import Any

import requests


RADIO_BROWSER_URL = "https://de1.api.radio-browser.info/json/stations/bycountrycodeexact/GB"


def _safe_float(value):
    try:
        n = float(value)
        return n if -180 <= n <= 180 else None
    except (TypeError, ValueError):
        return None


def _distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = radians(lat1), radians(lat2)
    dp = radians(lat2 - lat1)
    dl = radians(lon2 - lon1)
    a = sin(dp / 2) ** 2 + cos(p1) * cos(p2) * sin(dl / 2) ** 2
    return 2 * r * asin(sqrt(a))


def _specialism(row: dict[str, Any]) -> str:
    text = " ".join(str(row.get(k) or "") for k in ("name", "tags", "state")).lower()
    if any(token in text for token in ("airband", "aviation", "airport", "air traffic", "atc ", " atc", "flying club")):
        return "aviation"
    if any(token in text for token in ("marine", "maritime", "harbour", "harbor", "shipping", "coast radio", "seafarer", "vhf")):
        return "marine"
    if any(token in text for token in ("railway", "railroad", "rail radio", "train radio", "heritage railway")):
        return "rail"
    return "general"


@dataclass
class _Cache:
    rows: list[dict[str, Any]]
    fetched_at: float


class RadioBrowserLookup:
    """Small on-demand helper for public UK radio streams.

    Radio Browser is a free community directory. It is deliberately kept out of
    the continuous collector loop: the left-side radio panel refreshes on map
    movement/on demand. Results are cached to be gentle with the service.
    """

    def __init__(self, url: str = RADIO_BROWSER_URL, cache_seconds: int = 1800) -> None:
        self.url = url
        self.cache_seconds = cache_seconds
        self._lock = RLock()
        self._cache = _Cache([], 0.0)

    def _fetch(self) -> list[dict[str, Any]]:
        with self._lock:
            if self._cache.rows and monotonic() - self._cache.fetched_at < self.cache_seconds:
                return self._cache.rows

        response = requests.get(
            self.url,
            params={"hidebroken": "true", "limit": 750, "order": "clickcount", "reverse": "true"},
            headers={"User-Agent": "UKActivityRadar/1.2.1 (local prototype)"},
            timeout=12,
        )
        response.raise_for_status()
        payload = response.json()
        rows = payload if isinstance(payload, list) else []
        with self._lock:
            self._cache = _Cache(rows, monotonic())
        return rows

    @staticmethod
    def _normalise(row: dict[str, Any], distance_km: float | None) -> dict[str, Any]:
        return {
            "uuid": row.get("stationuuid"),
            "name": row.get("name") or "Radio station",
            "state": row.get("state") or None,
            "tags": row.get("tags") or None,
            "specialism": _specialism(row),
            "homepage": row.get("homepage") or None,
            "favicon": row.get("favicon") or None,
            "stream_url": row.get("url_resolved") or row.get("url") or None,
            "codec": row.get("codec") or None,
            "bitrate": row.get("bitrate") or None,
            "distance_km": round(distance_km, 1) if distance_km is not None else None,
            "lat": _safe_float(row.get("geo_lat")),
            "lon": _safe_float(row.get("geo_long")),
        }

    def nearby(self, lat: float, lon: float, limit: int = 5) -> dict[str, Any]:
        rows = self._fetch()
        geocoded: list[tuple[float, dict[str, Any]]] = []
        national: list[dict[str, Any]] = []
        for row in rows:
            try:
                rlat = float(row.get("geo_lat"))
                rlon = float(row.get("geo_long"))
                if not (-90 <= rlat <= 90 and -180 <= rlon <= 180):
                    raise ValueError
                geocoded.append((_distance_km(lat, lon, rlat, rlon), row))
            except (TypeError, ValueError):
                national.append(row)

        geocoded.sort(key=lambda item: (item[0], -int(item[1].get("clickcount") or 0)))
        specialist = [(dist, row) for dist, row in geocoded if _specialism(row) != "general"]
        general = [(dist, row) for dist, row in geocoded if _specialism(row) == "general"]
        # Specialist aviation/marine/rail audio is the point of this panel. Keep it
        # ahead of ordinary broadcast radio, but never invent a stream if none exists.
        ordered = specialist[: min(5, limit)] + general[: max(0, limit - min(5, len(specialist)))]
        chosen = [self._normalise(row, dist) for dist, row in ordered[: max(1, min(limit, 8))]]
        if len(chosen) < limit:
            chosen.extend(self._normalise(row, None) for row in national[: limit - len(chosen)])
        return {
            "ok": True,
            "source": "Radio Browser",
            "stations": chosen[:limit],
            "specialist_count": sum(1 for row in chosen[:limit] if row.get("specialism") != "general"),
            "note": "Authorised/public stream directory. Specialist aviation, marine and rail streams are prioritised when listed; no radio traffic is intercepted by this app.",
        }
