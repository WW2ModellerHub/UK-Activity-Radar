from __future__ import annotations

import re
from math import asin, cos, radians, sin, sqrt
import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import requests

from .models import Event, Vehicle




def _distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = radians(lat1), radians(lat2)
    dp = radians(lat2 - lat1)
    dl = radians(lon2 - lon1)
    a = sin(dp / 2) ** 2 + cos(p1) * cos(p2) * sin(dl / 2) ** 2
    return 2 * r * asin(sqrt(a))


_INTENT_TYPES = {
    "train": {"train", "trains", "rail", "railway"},
    "bus": {"bus", "buses"},
    "vessel": {"ship", "ships", "vessel", "vessels", "boat", "boats", "maritime"},
    "sar": {"sar", "coastguard", "hems", "air ambulance", "helicopter", "helicopters"},
    "rescue": {"rnli", "lifeboat", "lifeboats", "rescue"},
    "road": {"road", "roads", "traffic", "motorway", "motorways"},
    "incident": {"incident", "incidents", "accident", "accidents", "collision", "collisions"},
    "flood": {"flood", "floods", "flooding"},
    "weather": {"weather", "warning", "warnings"},
}

@dataclass
class _CachedLocation:
    expires: float
    value: dict[str, Any] | None


class ActivitySearch:
    """Search live activity first, then use a throttled free OSM geocoder.

    Nominatim is deliberately a fallback for one-off human searches, not a data
    feed. Results are cached and outbound requests are kept to <= 1 per second.
    """

    def __init__(self, state, nominatim_url: str, enabled: bool = True, *, tomtom_api_key: str = "", tomtom_base_url: str = "https://api.tomtom.com") -> None:
        self.state = state
        self.nominatim_url = nominatim_url
        self.enabled = enabled
        self.tomtom_api_key = (tomtom_api_key or "").strip()
        self.tomtom_base_url = (tomtom_base_url or "https://api.tomtom.com").rstrip("/")
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "UKActivityRadar/0.8 search (OpenStreetMap Nominatim)"})
        self._lock = threading.RLock()
        self._last_request = 0.0
        self._cache: dict[str, _CachedLocation] = {}

    @staticmethod
    def _haystack(a: Vehicle | Event) -> str:
        values = a.model_dump().values()
        return " ".join(str(v) for v in values if v not in (None, "")).lower()

    @staticmethod
    def _normalise_road_query(query: str) -> str:
        q = re.sub(r"\s+", " ", query.strip())
        # Nominatim understands "junction" much better than the enthusiast
        # shorthand J20 / J 20.
        q = re.sub(r"\b([AM]\d+(?:\(M\))?)\s+J(?:UNCTION)?\s*(\d+[A-Z]?)\b", r"\1 junction \2", q, flags=re.I)
        if re.search(r"\b(?:M\d+|A\d+)\b", q, re.I) and "united kingdom" not in q.lower():
            q += ", United Kingdom"
        return q

    @staticmethod
    def _parse_intent(query: str) -> tuple[str | None, str | None]:
        raw = re.sub(r"\s+", " ", query.strip())
        lowered = raw.casefold()
        for activity_type, words in _INTENT_TYPES.items():
            for word in sorted(words, key=len, reverse=True):
                m = re.match(rf"^{re.escape(word)}\s+(?:near|around|by|in)\s+(.+)$", lowered, flags=re.I)
                if m:
                    # Slice from the original string so place capitalization is preserved.
                    prefix = re.match(rf"^{re.escape(word)}\s+(?:near|around|by|in)\s+", raw, flags=re.I)
                    place = raw[prefix.end():].strip() if prefix else m.group(1).strip()
                    return activity_type, place
        return None, None

    def _activity_matches(self, query: str, activity_type: str | None = None) -> list[dict[str, Any]]:
        needle = query.casefold().strip()
        tokens = [t for t in re.split(r"\s+", needle) if t]
        if not tokens:
            return []
        rows = self.state.all_activities(include_buses=True, bus_limit=5000, include_vessels=True, vessel_limit=3000)
        ranked: list[tuple[int, Vehicle | Event]] = []
        for row in rows:
            if activity_type and row.type != activity_type:
                continue
            hay = self._haystack(row)
            hits = sum(1 for token in tokens if token in hay)
            if not hits:
                continue
            score = hits * 10
            if isinstance(row, Event) and row.road and row.road.casefold() in needle:
                score += 12
            if row.name.casefold().startswith(needle):
                score += 20
            if needle in row.name.casefold():
                score += 10
            ranked.append((score, row))
        ranked.sort(key=lambda x: (x[0], x[1].last_updated), reverse=True)
        return [
            {
                "kind": "activity",
                "id": row.id,
                "type": row.type,
                "name": row.name,
                "subtitle": row.subtitle,
                "lat": row.lat,
                "lon": row.lon,
                "source": row.source,
                "score": score,
            }
            for score, row in ranked[:10]
        ]

    def _tomtom_geocode(self, query: str) -> dict[str, Any] | None:
        if not self.tomtom_api_key:
            return None
        normal = self._normalise_road_query(query).removesuffix(", United Kingdom")
        url = f"{self.tomtom_base_url}/search/2/search/{quote(normal, safe='')}.json"
        response = self._session.get(
            url,
            params={
                "key": self.tomtom_api_key, "countrySet": "GB", "limit": 1,
                "idxSet": "POI,PAD,Str,Xstr,Geo,Addr,EPP", "language": "en-GB",
            },
            timeout=8,
        )
        response.raise_for_status()
        rows = response.json().get("results") or []
        if not rows:
            return None
        row = rows[0]
        pos = row.get("position") or {}
        try:
            lat, lon = float(pos["lat"]), float(pos["lon"])
        except (KeyError, TypeError, ValueError):
            return None
        address = row.get("address") or {}
        poi = row.get("poi") or {}
        viewport = row.get("viewport") or {}
        top_left, bottom_right = viewport.get("topLeftPoint") or {}, viewport.get("btmRightPoint") or {}
        bbox = None
        try:
            # Keep the same [south,north,west,east] order used by the OSM fallback.
            bbox = [float(bottom_right["lat"]), float(top_left["lat"]), float(top_left["lon"]), float(bottom_right["lon"])]
        except (KeyError, TypeError, ValueError):
            bbox = None
        name = poi.get("name") or address.get("freeformAddress") or row.get("id") or normal
        return {
            "kind": "location", "name": name, "lat": lat, "lon": lon, "bbox": bbox,
            "source": "TomTom Search", "query": normal,
        }

    def _geocode(self, query: str) -> dict[str, Any] | None:
        normal = self._normalise_road_query(query)
        key = normal.casefold()
        with self._lock:
            cached = self._cache.get(key)
            if cached and cached.expires > time.time():
                return cached.value
        # Prefer TomTom when the user has configured the existing traffic/search key.
        # It is particularly good for UK postcodes, roads, junctions and POIs.
        if self.tomtom_api_key:
            try:
                result = self._tomtom_geocode(query)
                if result:
                    with self._lock:
                        self._cache[key] = _CachedLocation(time.time() + 24 * 3600, result)
                    return result
            except Exception:
                pass
        if not self.enabled:
            return None
        with self._lock:
            wait = 1.05 - (time.time() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.time()
        response = self._session.get(
            self.nominatim_url,
            params={"q": normal, "format": "jsonv2", "limit": 1, "countrycodes": "gb", "addressdetails": 1},
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json()
        result = None
        if isinstance(payload, list) and payload:
            row = payload[0]
            try:
                lat, lon = float(row["lat"]), float(row["lon"])
                bbox = [float(x) for x in row.get("boundingbox", [])[:4]] if row.get("boundingbox") else None
                result = {
                    "kind": "location",
                    "name": row.get("display_name") or normal,
                    "lat": lat,
                    "lon": lon,
                    "bbox": bbox,
                    "source": "OpenStreetMap Nominatim",
                    "query": normal,
                }
            except (KeyError, TypeError, ValueError):
                result = None
        with self._lock:
            self._cache[key] = _CachedLocation(time.time() + 24 * 3600, result)
        return result

    def search(self, query: str) -> dict[str, Any]:
        query = re.sub(r"\s+", " ", (query or "").strip())[:160]
        if len(query) < 2:
            return {"query": query, "matches": [], "location": None, "intent": None}

        activity_type, place = self._parse_intent(query)
        search_text = place or query
        matches = self._activity_matches(query, activity_type=activity_type)
        location = None

        # Category-near-place searches are resolved around the place, then live
        # objects are ranked by distance. This makes queries such as
        # "ships near Portsmouth" and "trains near Hinckley" genuinely useful.
        if place:
            try:
                location = self._geocode(place)
            except Exception:
                location = None
            if location:
                rows = self.state.all_activities(include_buses=True, bus_limit=5000, include_vessels=True, vessel_limit=3000)
                nearby = []
                for row in rows:
                    if activity_type and row.type != activity_type:
                        continue
                    km = _distance_km(location["lat"], location["lon"], row.lat, row.lon)
                    if km <= 80:
                        nearby.append((km, row))
                nearby.sort(key=lambda x: (x[0], -x[1].last_updated.timestamp()))
                matches = [
                    {
                        "kind": "activity", "id": row.id, "type": row.type, "name": row.name,
                        "subtitle": row.subtitle, "lat": row.lat, "lon": row.lon, "source": row.source,
                        "score": max(1, int(100 - km)), "distance_km": round(km, 1),
                    }
                    for km, row in nearby[:12]
                ]
        elif not matches or re.search(r"\b(?:M\d+|A\d+|junction|\bJ\s*\d+)\b", query, re.I):
            try:
                location = self._geocode(search_text)
            except Exception:
                location = None

        return {
            "query": query,
            "matches": matches,
            "location": location,
            "intent": {"type": activity_type, "place": place} if activity_type and place else None,
        }
