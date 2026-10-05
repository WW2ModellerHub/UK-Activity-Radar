from __future__ import annotations

import hashlib
import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin
from xml.etree import ElementTree as ET

import requests
from bs4 import BeautifulSoup

from ..models import Event, SourceStatus
from ..timeutils import london_tz


RNLI_LAUNCH_PAGE = "https://rnli.org/what-we-do/lifeboats-and-stations/latest-lifeboat-launches"
RNLI_STATIONS_ITEM = "7dad2e58254345c08dfde737ec348166"


def _norm_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _station_key(value: str) -> str:
    text = re.sub(r"\blifeboat\s+station\b", "", value, flags=re.I)
    text = re.sub(r"\brnli\b", "", text, flags=re.I)
    return _norm_key(text)


def _walk_dicts(obj: Any):
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from _walk_dicts(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from _walk_dicts(item)


def _find_value(row: dict[str, Any], aliases: tuple[str, ...]) -> Any:
    wanted = {_norm_key(x) for x in aliases}
    for key, value in row.items():
        if _norm_key(str(key)) in wanted and value not in (None, "") and not isinstance(value, (dict, list)):
            return value
    for value in row.values():
        if isinstance(value, dict):
            found = _find_value(value, aliases)
            if found not in (None, ""):
                return found
    return None


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        if number > 10_000_000_000:
            number /= 1000.0
        try:
            return datetime.fromtimestamp(number, tz=timezone.utc)
        except Exception:
            return None
    text = str(value).strip()
    match = re.search(r"/Date\((\d+)\)/", text)
    if match:
        return _dt(int(match.group(1)))
    clean = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(clean)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        pass
    london = london_tz()
    for fmt in ("%d/%m/%Y %H:%M", "%d/%m/%Y %H.%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=london).astimezone(timezone.utc)
        except ValueError:
            continue
    return None


def _decode_payload(raw: str) -> Any:
    text = raw.strip()
    if text.startswith("<"):
        root = ET.fromstring(text)

        def element_to_obj(node: ET.Element) -> Any:
            children = list(node)
            if not children:
                return (node.text or "").strip()
            out: dict[str, Any] = {}
            for child in children:
                key = child.tag.rsplit("}", 1)[-1]
                value = element_to_obj(child)
                if key in out:
                    old = out[key]
                    out[key] = old + [value] if isinstance(old, list) else [old, value]
                else:
                    out[key] = value
            return out
        return element_to_obj(root)

    m = re.match(r"^[\w$.]+\((.*)\)\s*;?\s*$", text, re.S)
    if m:
        text = m.group(1)
    return json.loads(text)


def _coords_for_station(name: str, station_coords: dict[str, tuple[float, float]]) -> tuple[float, float] | None:
    key = _station_key(name)
    if key in station_coords:
        return station_coords[key]
    # A few page/API names contain suffixes/prefixes that differ from the open station dataset.
    for other, point in station_coords.items():
        if key and other and (key in other or other in key):
            return point
    return None


def parse_rnli_launches(raw: str | bytes, station_coords: dict[str, tuple[float, float]] | None = None) -> list[Event]:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    payload = _decode_payload(raw)
    station_coords = station_coords or {}
    events: dict[str, Event] = {}
    now = datetime.now(timezone.utc)

    station_aliases = (
        "stationName", "lifeboatStation", "lifeboatStationName", "lifeboatStationNameProper",
        "station", "name", "title",
    )
    lat_aliases = ("latitude", "lat", "stationLatitude")
    lon_aliases = ("longitude", "long", "lng", "lon", "stationLongitude")
    time_aliases = (
        "launchDateTime", "launchTime", "dateTime", "launchDate", "timestamp", "date",
        "shoutDate", "shoutDateTime", "launchdatetimeutc",
    )
    id_aliases = ("id", "launchId", "shoutId", "incidentId", "shoutNumber")
    url_aliases = ("url", "link", "stationUrl", "stationLink")

    for row in _walk_dicts(payload):
        station = _find_value(row, station_aliases)
        if isinstance(station, dict):
            station = _find_value(station, ("stationName", "name", "title"))
        launched = _dt(_find_value(row, time_aliases))
        if not station or launched is None:
            continue

        station_text = str(station).strip()
        lat = _float(_find_value(row, lat_aliases))
        lon = _float(_find_value(row, lon_aliases))
        if lat is None or lon is None:
            point = _coords_for_station(station_text, station_coords)
            if point:
                lat, lon = point
        if lat is None or lon is None or not (48.0 <= lat <= 62.5 and -13.5 <= lon <= 5.0):
            continue

        source_id = _find_value(row, id_aliases)
        if source_id in (None, ""):
            source_id = hashlib.sha1(f"{station_text}|{launched.isoformat()}".encode("utf-8")).hexdigest()[:16]
        url = _find_value(row, url_aliases)
        official = RNLI_LAUNCH_PAGE
        if isinstance(url, str) and url.strip():
            official = urljoin("https://rnli.org/", url.strip())

        event = Event(
            id=f"rnli:{source_id}", type="rescue", name=f"{station_text} launch",
            subtitle="RNLI lifeboat launch", lat=float(lat), lon=float(lon),
            category="RNLI lifeboat launch", status="Launched", severity=None,
            reason="Launch reported by the RNLI crew pager/API system.", start_time=launched,
            position_method="static", position_label="RNLI lifeboat station / launch location",
            source="RNLI", source_id=str(source_id), official_url=official,
            news_query=f'"{station_text}" RNLI lifeboat', social_query=f'"{station_text}" RNLI',
            interesting=True, last_updated=launched or now,
        )
        events[event.id] = event

    return list(events.values())


def parse_rnli_page(html: str, station_coords: dict[str, tuple[float, float]]) -> list[Event]:
    """Fallback parser for the official RNLI latest-launches page.

    This is deliberately a fallback: the API remains primary, but the official
    page itself is a useful guard against an API schema change returning zero.
    """
    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    pattern = re.compile(
        r"(?P<station>[A-Z][A-Za-zÀ-ÿ0-9&’'().,\- /]+? Lifeboat Station)"
        r"(?:\s+launched\s+.*?ago)?\s+Launch time\s+"
        r"(?P<time>\d{1,2}\.\d{2}(?:am|pm))\s+"
        r"(?P<date>\d{1,2}\s+[A-Za-z]+\s+\d{4})",
        re.I,
    )
    london = london_tz()
    out: dict[str, Event] = {}
    for m in pattern.finditer(text):
        station = m.group("station").strip()
        point = _coords_for_station(station, station_coords)
        if not point:
            continue
        try:
            launched = datetime.strptime(
                f"{m.group('time').upper()} {m.group('date')}", "%I.%M%p %d %B %Y"
            ).replace(tzinfo=london).astimezone(timezone.utc)
        except ValueError:
            continue
        source_id = hashlib.sha1(f"{station}|{launched.isoformat()}".encode()).hexdigest()[:16]
        lat, lon = point
        out[source_id] = Event(
            id=f"rnli:{source_id}", type="rescue", name=f"{station} launch",
            subtitle="RNLI lifeboat launch", lat=lat, lon=lon,
            category="RNLI lifeboat launch", status="Launched",
            reason="Launch listed on the official RNLI latest-launches page.", start_time=launched,
            position_method="static", position_label="RNLI lifeboat station / launch location",
            source="RNLI", source_id=source_id, official_url=RNLI_LAUNCH_PAGE,
            news_query=f'"{station}" RNLI lifeboat', social_query=f'"{station}" RNLI',
            interesting=True, last_updated=launched,
        )
    return list(out.values())


class RNLILaunchCollector:
    """Official RNLI latest-launch collector with official open-station geography fallback."""

    def __init__(self, settings, state, data_dir: Path | None = None):
        self.settings = settings
        self.state = state
        self.data_dir = data_dir
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "UKActivityRadar/0.8 (RNLI consumer)"})
        self._station_coords: dict[str, tuple[float, float]] = {}

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="rnli-launches", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)

    def _station_cache_path(self) -> Path | None:
        return self.data_dir / "rnli_stations.json" if self.data_dir else None

    def _load_station_coords(self) -> dict[str, tuple[float, float]]:
        if self._station_coords:
            return self._station_coords
        cache = self._station_cache_path()
        if cache and cache.exists():
            try:
                raw = json.loads(cache.read_text(encoding="utf-8"))
                self._station_coords = {k: (float(v[0]), float(v[1])) for k, v in raw.items()}
                if self._station_coords:
                    return self._station_coords
            except Exception:
                pass

        meta_url = f"https://www.arcgis.com/sharing/rest/content/items/{RNLI_STATIONS_ITEM}"
        meta = self._session.get(meta_url, params={"f": "json"}, timeout=20)
        meta.raise_for_status()
        service_url = str(meta.json().get("url") or "").strip()
        if not service_url:
            raise RuntimeError("RNLI open station dataset did not return a FeatureServer URL")
        layer_url = service_url.rstrip("/")
        if not re.search(r"/\d+$", layer_url):
            layer_url += "/0"
        query = self._session.get(
            layer_url + "/query",
            params={"where": "1=1", "outFields": "*", "returnGeometry": "true", "outSR": "4326", "f": "geojson"},
            timeout=30,
        )
        query.raise_for_status()
        coords: dict[str, tuple[float, float]] = {}
        for feature in query.json().get("features", []):
            props = feature.get("properties") or {}
            geom = feature.get("geometry") or {}
            xy = geom.get("coordinates") or []
            if len(xy) < 2:
                continue
            name = None
            for key in ("Station", "station", "Name", "name", "Lifeboat_Station", "LifeboatStation"):
                if props.get(key):
                    name = str(props[key]).strip()
                    break
            if not name:
                continue
            try:
                lon, lat = float(xy[0]), float(xy[1])
            except (TypeError, ValueError):
                continue
            coords[_station_key(name)] = (lat, lon)
        if not coords:
            raise RuntimeError("RNLI open station dataset returned zero usable stations")
        self._station_coords = coords
        if cache:
            try:
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_text(json.dumps({k: [v[0], v[1]] for k, v in coords.items()}), encoding="utf-8")
            except Exception:
                pass
        return coords

    def fetch_once(self) -> list[Event]:
        coords = self._load_station_coords()
        response = self._session.get(
            self.settings.rnli_launches_url,
            params={"numberOfShouts": 50, "callback": "ukActivityRadar"},
            timeout=20,
        )
        response.raise_for_status()
        events = parse_rnli_launches(response.text, coords)
        if events:
            return events

        # If the API schema changes, use the official RNLI page instead of
        # incorrectly reporting zero launches while the page visibly has shouts.
        page = self._session.get(RNLI_LAUNCH_PAGE, timeout=20)
        page.raise_for_status()
        return parse_rnli_page(page.text, coords)

    def _run(self) -> None:
        self.state.set_source(SourceStatus(
            name="rnli", enabled=True, connected=False, mode="connecting",
            message="Loading official RNLI stations and latest launches…",
        ))
        while not self._stop.is_set():
            try:
                events = self.fetch_once()
                self.state.replace_events_for_source("RNLI", events)
                if events:
                    self.state.set_source(SourceStatus(
                        name="rnli", enabled=True, connected=True, mode="live",
                        message=f"{len(events):,} recent lifeboat launches mapped • official RNLI data",
                        last_message_at=datetime.now(timezone.utc), messages_seen=len(events), movements_mapped=len(events),
                    ))
                else:
                    self.state.set_source(SourceStatus(
                        name="rnli", enabled=True, connected=True, mode="ready",
                        message="RNLI sources verified • 0 recent launches parsed. LIVE appears only when launch records are present.",
                        last_message_at=datetime.now(timezone.utc),
                    ))
            except Exception as exc:
                self.state.set_source(SourceStatus(
                    name="rnli", enabled=True, connected=False, mode="error",
                    message=f"RNLI launch feed error: {type(exc).__name__}: {exc}",
                ))
            self._stop.wait(self.settings.rnli_poll_seconds)
