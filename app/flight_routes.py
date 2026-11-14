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


class FlightRouteLookup:
    def __init__(self, base_url: str, session: requests.Session | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self._session = session or requests.Session()
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[float, dict | None]] = {}

    def lookup(self, callsign: str | None, lat: float, lon: float) -> dict | None:
        code = airline_callsign(callsign)
        if code is None:
            return None
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(code)
            if cached and cached[0] > now:
                return cached[1]
        route = None
        failed = False
        try:
            response = self._session.post(
                f"{self.base_url}/api/0/routeset",
                json={"planes": [{"callsign": code, "lat": float(lat), "lng": float(lon)}]},
                timeout=(4, 8),
            )
            response.raise_for_status()
            payload = response.json()
            rows = payload if isinstance(payload, list) else []
            route = parse_route(next((row for row in rows if str((row or {}).get("callsign") or "").upper() == code), rows[0] if rows else None))
        except (requests.RequestException, ValueError, TypeError):
            failed = True
        with self._lock:
            if len(self._cache) > 5000:
                self._cache.clear()
            ttl = 60 if failed else (FOUND_SECONDS if route else MISSING_SECONDS)
            self._cache[code] = (now + ttl, route)
        return route
