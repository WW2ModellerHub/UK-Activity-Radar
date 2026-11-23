"""Which runway an airport is using now, and its wind.

From live aircraft: planes low over the airport, climbing or descending, and
lined up with a runway, show which way it is working ("landing 27L,
departing 27R"). With no traffic to go on, aircraft land and take off into
the wind, so the airport's latest weather report (METAR) says which runway
to expect. The wind itself comes from that report.
"""
from __future__ import annotations

import re
import threading
import time
from math import asin, atan2, cos, degrees, radians, sin, sqrt

_DESIGNATOR = re.compile(r"\b(\d{2})([LRC]?)\b")
_WIND = re.compile(r"\b(\d{3}|VRB)(\d{2,3})(?:G(\d{2,3}))?KT\b")
METAR_URL = "https://aviationweather.gov/api/data/metar"
METAR_TTL_S = 600
LANE_KM = 8.0           # planes this close to the airport's middle count
ALIGN_DEG = 25.0        # this close to a runway's heading counts as lined up


def runway_ends(airport: dict) -> list[dict]:
    """Every runway end: '09L/27R and 09R/27L' -> 09L, 27R, 09R, 27L."""
    ends, seen = [], set()
    for number, side in _DESIGNATOR.findall(str(airport.get("runways") or "")):
        name = number + side
        if name in seen or not 1 <= int(number) <= 36:
            continue
        seen.add(name)
        ends.append({"name": name, "heading": int(number) * 10 % 360, "side": side})
    return ends


def parse_wind(raw: str | None) -> dict | None:
    """'EGBB 231650Z 25012G22KT ...' -> direction 250, 12 kt, gusting 22."""
    match = _WIND.search(str(raw or ""))
    if not match:
        return None
    direction, speed, gust = match.groups()
    return {"direction": None if direction == "VRB" else int(direction) % 360,
            "speed_kt": int(speed), "gust_kt": int(gust) if gust else None}


def wind_text(wind: dict | None) -> str | None:
    if not wind:
        return None
    if wind["speed_kt"] == 0:
        return "Calm"
    where = "variable" if wind["direction"] is None else f"from {wind['direction']:03d}°"
    gust = f", gusting {wind['gust_kt']} kt" if wind.get("gust_kt") else ""
    return f"Wind {where} at {wind['speed_kt']} kt{gust}"


def _angle(a: float, b: float) -> float:
    return abs((a - b + 180) % 360 - 180)


def _km(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = radians(lat1), radians(lat2)
    h = sin((p2 - p1) / 2) ** 2 + cos(p1) * cos(p2) * sin(radians(lon2 - lon1) / 2) ** 2
    return 2 * 6371.0 * asin(sqrt(h))


def _bearing_from(lat1, lon1, lat2, lon2) -> float:
    p1, p2, dl = radians(lat1), radians(lat2), radians(lon2 - lon1)
    return (degrees(atan2(sin(dl) * cos(p2), cos(p1) * sin(p2) - sin(p1) * cos(p2) * cos(dl))) + 360) % 360


def _end_for(ends: list[dict], heading: float, airport: dict, lat: float, lon: float) -> dict | None:
    """The runway end a plane on this heading is using; on parallels, the side it is on."""
    lined = [end for end in ends if _angle(end["heading"], heading) <= ALIGN_DEG]
    if not lined:
        return None
    if len(lined) == 1:
        return lined[0]
    # Parallel runways: left of the direction of travel is the L runway.
    offset = _angle(_bearing_from(airport["lat"], airport["lon"], lat, lon), (lined[0]["heading"] - 90) % 360)
    side = "L" if offset < 90 else "R"
    return next((end for end in lined if end["side"] == side), lined[0])


def _phase(plane, airport: dict) -> str | None:
    """'landing' or 'departing' for a plane low over the airport, else None."""
    lat, lon = getattr(plane, "lat", None), getattr(plane, "lon", None)
    if lat is None or lon is None or _km(airport["lat"], airport["lon"], lat, lon) > LANE_KM:
        return None
    speed = getattr(plane, "speed", None)
    if speed is None or not 60 <= speed <= 220:
        return None
    altitude = getattr(plane, "altitude_ft", None)
    if altitude is None or altitude - airport.get("elevation_ft", 0) > 2500:
        return None
    rate = getattr(plane, "vertical_rate_fpm", None) or 0
    if rate <= -300:
        return "landing"
    if rate >= 300:
        return "departing"
    return None


def _pick(counts: dict[str, int]) -> str | None:
    return max(counts, key=lambda name: (counts[name], name)) if counts else None


def runways_in_use(airport: dict, planes: list, wind: dict | None = None) -> dict:
    """{'landing': '27L', 'departing': '27R', 'basis': 'traffic'|'wind'|None, ...}."""
    ends = runway_ends(airport)
    counts: dict[str, dict[str, int]] = {"landing": {}, "departing": {}}
    for plane in planes or []:
        if getattr(plane, "type", None) not in {"aircraft", "sar"}:
            continue
        phase = _phase(plane, airport)
        heading = getattr(plane, "bearing", None)
        if phase is None or heading is None:
            continue
        end = _end_for(ends, float(heading), airport, plane.lat, plane.lon)
        if end:
            counts[phase][end["name"]] = counts[phase].get(end["name"], 0) + 1
    result = {"landing": _pick(counts["landing"]), "departing": _pick(counts["departing"]),
              "basis": None, "wind": wind, "wind_text": wind_text(wind)}
    if result["landing"] or result["departing"]:
        result["basis"] = "traffic"
        # One seen, the other not: in the same direction on a single runway.
        seen = result["landing"] or result["departing"]
        heading = next(end["heading"] for end in ends if end["name"] == seen)
        same_way = [end["name"] for end in ends if _angle(end["heading"], heading) <= ALIGN_DEG]
        if len(same_way) == 1:
            result["landing"] = result["landing"] or seen
            result["departing"] = result["departing"] or seen
    elif wind and wind.get("direction") is not None and wind["speed_kt"] >= 5 and ends:
        # Into the wind: the runway whose heading is closest to where it blows from.
        best = min(ends, key=lambda end: (_angle(end["heading"], wind["direction"]), end["name"]))
        heading = best["heading"]
        names = sorted(end["name"] for end in ends if end["heading"] == heading)
        result.update(landing="/".join(names), departing="/".join(names), basis="wind")
    return result


def summary(state: dict) -> str | None:
    """One line: 'Landing 27L, departing 27R · Wind from 250° at 12 kt'."""
    parts = []
    landing, departing = state.get("landing"), state.get("departing")
    if state.get("basis") == "traffic":
        if landing and landing == departing:
            parts.append(f"Using runway {landing}")
        else:
            words = [x for x in (f"landing {landing}" if landing else None,
                                 f"departing {departing}" if departing else None) if x]
            line = ", ".join(words)
            parts.append(line[:1].upper() + line[1:])
    elif state.get("basis") == "wind":
        parts.append(f"Expect runway {landing} (into the wind)")
    if state.get("wind_text"):
        parts.append(state["wind_text"])
    return " · ".join(parts) or None


class MetarFeed:
    """Latest METAR per airport, fetched for all airports at once, kept 10 minutes."""

    def __init__(self, session=None, url: str = METAR_URL):
        self._session = session
        self._url = url
        self._lock = threading.Lock()
        self._fetched_at = float("-inf")   # never fetched: fetch on first use
        self._reports: dict[str, dict] = {}

    def _get(self, icaos: list[str]) -> list[dict]:
        import requests
        session = self._session or requests
        response = session.get(self._url, params={"ids": ",".join(icaos), "format": "json"}, timeout=8)
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, list) else []

    def report(self, icao: str, all_icaos: list[str]) -> dict | None:
        """{'raw': ..., 'wind': {...}, 'time': ...} or None when unavailable."""
        with self._lock:
            stale = time.monotonic() - self._fetched_at > METAR_TTL_S
            if stale:
                self._fetched_at = time.monotonic()   # one attempt per period, even if it fails
        if stale:
            try:
                fresh = {}
                for row in self._get(all_icaos):
                    raw = str(row.get("rawOb") or "")
                    code = str(row.get("icaoId") or raw[:4]).upper()
                    if code:
                        fresh[code] = {"raw": raw, "wind": parse_wind(raw), "time": row.get("reportTime")}
                if fresh:
                    with self._lock:
                        self._reports = fresh
            except Exception:
                pass   # keep the last good reports
        with self._lock:
            return self._reports.get(icao.upper())
