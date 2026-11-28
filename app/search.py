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




def _display_name(row) -> str:
    """Roads and incidents read as "Lane closure · M6", not the feed title."""
    impact = getattr(row, "impact", None)
    return (impact or {}).get("title") or row.name


def _display_subtitle(row) -> str:
    impact = getattr(row, "impact", None)
    if impact:
        return " · ".join(x for x in (impact.get("where"), impact.get("impact"), impact.get("when")) if x)
    return row.subtitle


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

    # What a person would search a live thing by. Dumping every field of
    # 20,000 vehicles on each search was slow and matched noise.
    _SEARCH_FIELDS = ("name", "route", "headcode", "destination", "origin", "operator", "vehicle_ref",
                      "subtitle", "aircraft_model", "road", "category", "train_uid")

    @classmethod
    def _haystack(cls, a: Vehicle | Event) -> str:
        values = (getattr(a, field, None) for field in cls._SEARCH_FIELDS)
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
        # Every word must start a word in the record: "market bosworth" finds
        # buses to Market Bosworth, not ones at Beastmarket Hill.
        patterns = [re.compile(rf"(?<![a-z0-9]){re.escape(token)}") for token in tokens]
        rows = self.state.all_activities(include_buses=True, bus_limit=5000, include_vessels=True, vessel_limit=3000)
        ranked: list[tuple[int, Vehicle | Event]] = []
        for row in rows:
            if activity_type and row.type != activity_type:
                continue
            hay = self._haystack(row)
            if not all(pattern.search(hay) for pattern in patterns):
                continue
            score = len(tokens) * 10
            if isinstance(row, Event) and row.road and row.road.casefold() in needle:
                score += 12
            if row.name.casefold().startswith(needle):
                score += 20
            if needle in row.name.casefold():
                score += 10
            ranked.append((score, row))
        ranked.sort(key=lambda x: (x[0], x[1].last_updated), reverse=True)
        seen: set[tuple[str, str]] = set()
        unique = []
        for score, row in ranked:   # the same bus reported twice reads as one
            label = (_display_name(row), _display_subtitle(row) or "")
            if label in seen:
                continue
            seen.add(label)
            unique.append((score, row))
        ranked = unique
        return [
            {
                "kind": "activity",
                "id": row.id,
                "type": row.type,
                "name": _display_name(row),
                "subtitle": _display_subtitle(row),
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
            timeout=(1.5, 3.5),
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

    _FULL_POSTCODE = re.compile(r"^([A-Z]{1,2}\d[A-Z\d]?)\s*(\d[A-Z]{2})$", re.I)
    _OUTCODE = re.compile(r"^([A-Z]{1,2}\d[A-Z\d]?)(?:\s*\d[A-Z]{0,1})?$", re.I)

    def _postcode(self, query: str) -> dict[str, Any] | None:
        """UK postcodes from postcodes.io (open government data), not a shop
        search: "CV11 4" is the CV11 district, not a vape shop."""
        text = query.strip().upper()
        full = self._FULL_POSTCODE.match(text)
        part = None if full else self._OUTCODE.match(text)
        if not (full or part):
            return None
        if full:
            code = f"{full.group(1)} {full.group(2)}"
            url = f"https://api.postcodes.io/postcodes/{quote(code)}"
        else:
            code = part.group(1)
            url = f"https://api.postcodes.io/outcodes/{quote(code)}"
        key = f"postcode|{code}"
        with self._lock:
            cached = self._cache.get(key)
            if cached and cached.expires > time.time():
                return cached.value
        response = self._session.get(url, timeout=(1.5, 3.0))
        result = None
        if response.status_code == 200:
            row = (response.json() or {}).get("result") or {}
            lat, lon = row.get("latitude"), row.get("longitude")
            if lat is not None and lon is not None:
                town = row.get("admin_district")
                town = town[0] if isinstance(town, list) and town else town
                result = {"kind": "location", "name": ", ".join(x for x in (code, town) if x), "lat": float(lat),
                          "lon": float(lon), "bbox": None, "source": "postcodes.io (ONS)", "query": code}
        with self._lock:
            self._cache[key] = _CachedLocation(time.time() + 7 * 24 * 3600, result)
        return result

    def _geocode(self, query: str) -> dict[str, Any] | None:
        try:
            postcode = self._postcode(query)
        except Exception:
            postcode = None
        if postcode:
            return postcode
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
            timeout=(1.5, 4.0),
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

    # "5", "158", "X5", "U1A", "bus 18A": a bus route. Not a train headcode
    # (1Q15: digit, letter, two digits).
    _ROUTE = re.compile(r"^(?:bus\s+|route\s+)?([a-z]{0,3}\d{1,4}[a-z]{0,2})$", re.I)
    _HEADCODE = re.compile(r"^\d[a-z]\d{2}$", re.I)

    def _route_matches(self, route: str, lat: float | None, lon: float | None) -> list[dict[str, Any]]:
        wanted = route.casefold()
        rows = [row for row in self.state.all_activities(include_buses=True, bus_limit=8000)
                if row.type == "bus" and str(getattr(row, "route", "") or "").casefold() == wanted]
        if lat is not None and lon is not None:
            rows.sort(key=lambda row: _distance_km(lat, lon, row.lat, row.lon))
        else:
            rows.sort(key=lambda row: row.last_updated, reverse=True)
        out = []
        for row in rows[:12]:
            item = {
                "kind": "activity", "id": row.id, "type": row.type, "name": _display_name(row),
                "subtitle": self._bus_journey_label(row),
                "lat": row.lat, "lon": row.lon, "source": row.source, "score": 90,
            }
            if lat is not None and lon is not None:
                item["distance_km"] = round(_distance_km(lat, lon, row.lat, row.lon), 1)
            out.append(item)
        return out

    # Set by the app once the bus timetables exist: vehicle -> {"origin", "destination"} or None.
    bus_trip_ends = None

    def _bus_journey_label(self, row) -> str:
        """'Lutterworth → St Margaret's Bus Station, Leicester · Arriva': where it runs, not a trip id."""
        origin, destination = row.origin, row.destination
        if self.bus_trip_ends is not None:
            try:
                ends = self.bus_trip_ends(row)
            except Exception:
                ends = None
            if ends:
                origin = ends.get("origin") or origin
                destination = ends.get("destination") or destination
        route = (f"{origin} → {destination}" if origin and destination
                 else f"To {destination}" if destination else None)
        operator = str(row.operator or "").strip()
        return " · ".join(x for x in (route or row.subtitle, operator if operator and operator not in str(row.subtitle) else None) if x)

    @staticmethod
    def _looks_like_a_place(query: str) -> bool:
        """Words (a town, street, landmark) or a postcode, worth a map lookup."""
        if len(query) < 3:
            return False
        if re.fullmatch(r"[A-Za-z]{1,2}\d[A-Za-z\d]?\s*\d?[A-Za-z]{0,2}", query.strip()):
            return True   # postcode or the start of one: CV11 4, LE10
        return bool(re.search(r"[A-Za-z]{3,}", query)) and not re.search(r"\d", query)

    # "8 lutterworth", "bus 8 to Lutterworth", "Lutterworth 8": a route and a place on it.
    _ROUTE_PLACE = re.compile(r"^(?:bus\s+|route\s+)?([a-z]{0,3}\d{1,4}[a-z]{0,2})\s+(?:to\s+|from\s+|in\s+|via\s+)?([a-z][a-z' .&-]{2,60})$", re.I)
    _PLACE_ROUTE = re.compile(r"^([a-z][a-z' .&-]{2,60}?)\s+(?:bus\s+|route\s+)?([a-z]{0,3}\d{1,4}[a-z]{0,2})$", re.I)
    # Set by the app: (route, place, (lat, lon) | None) -> timetabled routes serving that place.
    bus_routes_serving = None

    def _route_place(self, query: str) -> tuple[str, str] | None:
        found = self._ROUTE_PLACE.match(query)
        if found and not self._HEADCODE.match(found.group(1)):
            return found.group(1), found.group(2).strip()
        found = self._PLACE_ROUTE.match(query)
        if found and not self._HEADCODE.match(found.group(2)) and not re.fullmatch(r"(?i)[a-z]{1,2}\d", query[-3:].strip()):
            place = found.group(1).strip()
            # "LE17 4": a postcode, not a route.
            if not re.fullmatch(r"(?i)[a-z]{1,2}", place):
                return found.group(2), place
        return None

    def _route_place_matches(self, route: str, place: str, lat: float | None, lon: float | None) -> dict[str, Any]:
        """Every timetabled route with this number calling there, then its buses on the road now."""
        try:
            location = self._geocode(place)
        except Exception:
            location = None
        near = (location["lat"], location["lon"]) if location else None
        rows: list[dict[str, Any]] = []
        if self.bus_routes_serving is not None:
            try:
                served = self.bus_routes_serving(route, place, near)
            except Exception:
                served = []
            for row in served:
                from .seo_pages import slug
                rows.append({
                    "kind": "route", "id": f"route:{row['operator']}:{row['route']}", "type": "bus",
                    "name": f"Bus {row['route']} · {row['operator']}", "subtitle": row["ends"],
                    "url": f"/bus/{slug(row['operator'])}/{slug(row['route'])}", "score": 95,
                })
        place_key = place.casefold()
        centre = near or ((lat, lon) if lat is not None and lon is not None else None)
        for bus in self._route_matches(route, *(centre or (None, None))):
            label = str(bus.get("subtitle") or "").casefold()
            close = bus.get("distance_km") is not None and near is not None and bus["distance_km"] <= 25
            if place_key in label or close:
                rows.append(bus)
        return {"query": f"{route} {place}", "matches": rows[:14], "location": None,
                "intent": {"type": "bus", "route": route.upper(), "place": place}}

    def search(self, query: str, *, lat: float | None = None, lon: float | None = None) -> dict[str, Any]:
        query = re.sub(r"\s+", " ", (query or "").strip())[:160]
        route_place = self._route_place(query)
        if route_place:
            found = self._route_place_matches(*route_place, lat, lon)
            # No such route there: "10 Downing Street" or "A5 Hinckley" is an address or a road.
            if found["matches"]:
                return found
        route = self._ROUTE.match(query)
        if route and not self._HEADCODE.match(route.group(1)):
            matches = self._route_matches(route.group(1), lat, lon)
            if matches or len(query) < 2:
                return {"query": query, "matches": matches, "location": None, "intent": {"type": "bus", "route": route.group(1).upper()}}
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
                        "kind": "activity", "id": row.id, "type": row.type, "name": _display_name(row),
                        "subtitle": _display_subtitle(row), "lat": row.lat, "lon": row.lon, "source": row.source,
                        "score": max(1, int(100 - km)), "distance_km": round(km, 1),
                    }
                    for km, row in nearby[:12]
                ]
        elif not matches or self._looks_like_a_place(query) or re.search(r"\b(?:M\d+|A\d+|junction|\bJ\s*\d+)\b", query, re.I):
            # A town or street is found as a place even when buses mention it.
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
