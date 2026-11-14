"""Where a flight is going: departure and arrival airports by callsign.

The aircraft positions come from adsb.lol, which also publishes a route
database keyed by airline callsign (``POST /api/0/routeset``). A route is
only shown when adsb.lol marks it plausible for the aircraft's position, so
a callsign reused for a different leg is not mislabelled.
"""
from __future__ import annotations

import re
import threading
import time

import requests

AIRLINE_CALLSIGN = re.compile(r"^[A-Z]{3}[0-9][A-Z0-9]{0,4}$")
FOUND_SECONDS = 30 * 60
MISSING_SECONDS = 10 * 60


def airline_callsign(value: str | None) -> str | None:
    text = re.sub(r"\s+", "", str(value or "")).upper()
    return text if AIRLINE_CALLSIGN.match(text) else None


def _airport_name(airport: dict) -> str | None:
    name = str(airport.get("name") or "").strip()
    for suffix in (" International Airport", " Airport", " Airfield"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    return name or str(airport.get("location") or "").strip() or None


def _airport(raw: dict | None, code: str | None = None) -> dict | None:
    raw = raw if isinstance(raw, dict) else {}
    iata = str(raw.get("iata") or "").strip().upper() or None
    icao = str(raw.get("icao") or "").strip().upper() or None
    name = _airport_name(raw)
    code = str(code or "").strip().upper() or None
    if not (name or iata or icao or code):
        return None
    return {"name": name or code or iata or icao, "iata": iata, "icao": icao or (code if code and len(code) == 4 else None)}


def parse_route(item: dict | None) -> dict | None:
    """One adsb.lol routeset entry -> {origin, destination, via}, or None."""
    if not isinstance(item, dict):
        return None
    if str(item.get("plausible", True)).strip().lower() in {"false", "0", "no"}:
        return None
    airports = item.get("_airports")
    stops: list[dict] = []
    if isinstance(airports, list) and len(airports) >= 2:
        stops = [stop for stop in (_airport(row) for row in airports) if stop]
    else:
        codes = str(item.get("_airport_codes_iata") or item.get("airport_codes") or "").strip()
        if not codes or codes.lower() == "unknown":
            return None
        stops = [stop for stop in (_airport(None, code) for code in codes.split("-")) if stop]
    if len(stops) < 2:
        return None
    return {"origin": stops[0], "destination": stops[-1], "via": stops[1:-1]}


def _km(a_lat, a_lon, b_lat, b_lon) -> float:
    import math
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp, dl = p2 - p1, math.radians(b_lon - a_lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


def parse_adsbdb(payload, lat: float, lon: float) -> dict | None:
    """adsbdb.com ``/v0/callsign/{cs}`` -> route, if it fits the aircraft.

    Plausible means the aircraft is not far off the line between the two
    airports: the detour via its position is at most 30% plus 100 km.
    """
    route = ((payload or {}).get("response") or {}) if isinstance(payload, dict) else {}
    route = route.get("flightroute") if isinstance(route, dict) else None
    if not isinstance(route, dict):
        return None
    def airport(raw):
        if not isinstance(raw, dict):
            return None
        return _airport({"name": raw.get("name"), "iata": raw.get("iata_code"), "icao": raw.get("icao_code"),
                         "location": raw.get("municipality")})
    origin, destination = airport(route.get("origin")), airport(route.get("destination"))
    if not origin or not destination:
        return None
    try:
        a = (float(route["origin"]["latitude"]), float(route["origin"]["longitude"]))
        b = (float(route["destination"]["latitude"]), float(route["destination"]["longitude"]))
        direct = _km(*a, *b)
        if _km(*a, lat, lon) + _km(lat, lon, *b) > direct * 1.3 + 100:
            return None
    except (KeyError, TypeError, ValueError):
        pass
    via = route.get("midpoint")
    return {"origin": origin, "destination": destination, "via": [x for x in [airport(via)] if x]}


class FlightRouteLookup:
    """adsb.lol route set first; adsbdb.com when that is unavailable or unknown."""

    def __init__(self, base_url: str, session: requests.Session | None = None,
                 fallback_url: str = "https://api.adsbdb.com") -> None:
        self.base_url = base_url.rstrip("/")
        self.fallback_url = fallback_url.rstrip("/") if fallback_url else ""
        self._session = session or requests.Session()
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[float, dict | None]] = {}
        self.last_error: str | None = None
        self.last_reason: str | None = None

    def _adsb_lol(self, code: str, lat: float, lon: float) -> dict | None:
        response = self._session.post(
            f"{self.base_url}/api/0/routeset",
            json={"planes": [{"callsign": code, "lat": float(lat), "lng": float(lon)}]},
            timeout=(4, 8),
        )
        response.raise_for_status()
        payload = response.json()
        rows = payload if isinstance(payload, list) else []
        row = next((item for item in rows if str((item or {}).get("callsign") or "").upper() == code), rows[0] if rows else None)
        return parse_route(row)

    def _adsbdb(self, code: str, lat: float, lon: float) -> dict | None:
        response = self._session.get(f"{self.fallback_url}/v0/callsign/{code}", timeout=(4, 8))
        if getattr(response, "status_code", 200) == 404:
            return None
        response.raise_for_status()
        return parse_adsbdb(response.json(), lat, lon)

    def lookup(self, callsign: str | None, lat: float, lon: float) -> dict | None:
        code = airline_callsign(callsign)
        if code is None:
            self.last_reason = "not_an_airline_callsign"
            return None
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(code)
            if cached and cached[0] > now:
                self.last_reason = "cached"
                return cached[1]
        route, errors = None, []
        sources = [("adsb.lol", self._adsb_lol)]
        if self.fallback_url:
            sources.append(("adsbdb", self._adsbdb))
        for name, fetch in sources:
            try:
                route = fetch(code, lat, lon)
            except (requests.RequestException, ValueError, TypeError, AttributeError) as exc:
                errors.append(f"{name}: {type(exc).__name__}: {str(exc)[:120]}")
                continue
            if route:
                break
        failed = route is None and len(errors) == len(sources)
        self.last_error = "; ".join(errors) or None
        self.last_reason = "found" if route else ("lookup_failed" if failed else "no_published_route")
        with self._lock:
            if len(self._cache) > 5000:
                self._cache.clear()
            ttl = 60 if failed else (FOUND_SECONDS if route else MISSING_SECONDS)
            self._cache[code] = (now + ttl, route)
        return route
