from __future__ import annotations

import math
import re
import threading
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from html import unescape
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from ..models import Event, SourceStatus

MET_OFFICE_WARNINGS_ITEM = "23e51204bd3348f1a37b50786b8abdea"
MET_OFFICE_WARNINGS_PAGE = "https://weather.metoffice.gov.uk/warnings-and-advice/uk-warnings"
MET_OFFICE_WARNINGS_RSS = "https://www.metoffice.gov.uk/public/data/PWSCache/WarningsRSS/Region/UK"

# RSS warnings do not carry the warning polygon used by the ArcGIS mirror.
# These centroids are only a resilient visual fallback and are labelled as such.
_RSS_REGION_CENTRES = {
    "shetland": (60.25, -1.25), "orkney": (59.00, -3.00),
    "highlands": (57.45, -4.55), "grampian": (57.15, -2.60),
    "strathclyde": (55.86, -4.25), "tayside": (56.45, -3.05),
    "fife": (56.20, -3.15), "lothian": (55.95, -3.20), "borders": (55.55, -2.70),
    "northern ireland": (54.70, -6.70), "wales": (52.30, -3.70),
    "north west england": (54.15, -2.75), "north east england": (54.90, -1.80),
    "yorkshire": (53.90, -1.50), "west midlands": (52.50, -2.00),
    "east midlands": (52.90, -1.00), "east of england": (52.20, 0.45),
    "south west england": (50.90, -3.50), "south east england": (51.25, 0.05),
    "london": (51.51, -0.12),
}


def _norm_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _get(props: dict[str, Any], *names: str) -> Any:
    wanted = {_norm_key(x) for x in names}
    for key, value in props.items():
        if _norm_key(str(key)) in wanted and value not in (None, ""):
            return value
    return None


def _dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        n = float(value)
        if n > 10_000_000_000:
            n /= 1000.0
        try:
            return datetime.fromtimestamp(n, tz=timezone.utc)
        except Exception:
            return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
        return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
    except ValueError:
        return None


def _ring_centroid(ring: list) -> tuple[float, float] | None:
    points: list[tuple[float, float]] = []
    for item in ring or []:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            try:
                lon, lat = float(item[0]), float(item[1])
            except (TypeError, ValueError):
                continue
            if -180 <= lon <= 180 and -90 <= lat <= 90:
                points.append((lon, lat))
    if not points:
        return None
    # A simple geographic mean is sufficient for a map marker; the original
    # warning polygon remains the authority and is linked via the Met Office.
    return (sum(p[1] for p in points) / len(points), sum(p[0] for p in points) / len(points))


def _geometry_centroid(geometry: dict[str, Any]) -> tuple[float, float] | None:
    kind = str(geometry.get("type") or "")
    coords = geometry.get("coordinates")
    if kind == "Point" and isinstance(coords, list) and len(coords) >= 2:
        try:
            return float(coords[1]), float(coords[0])
        except (TypeError, ValueError):
            return None
    rings: list[list] = []
    if kind == "Polygon" and isinstance(coords, list):
        rings = [r for r in coords if isinstance(r, list)]
    elif kind == "MultiPolygon" and isinstance(coords, list):
        for poly in coords:
            if isinstance(poly, list):
                rings.extend(r for r in poly if isinstance(r, list))
    centres = [c for c in (_ring_centroid(r) for r in rings) if c]
    if not centres:
        return None
    return (sum(x[0] for x in centres) / len(centres), sum(x[1] for x in centres) / len(centres))


def parse_warning_geojson(payload: dict[str, Any]) -> list[Event]:
    now = datetime.now(timezone.utc)
    out: dict[str, Event] = {}
    for idx, feature in enumerate(payload.get("features") or []):
        if not isinstance(feature, dict):
            continue
        props = feature.get("properties") or {}
        point = _geometry_centroid(feature.get("geometry") or {})
        if not point:
            continue
        lat, lon = point
        if not (48.0 <= lat <= 61.8 and -12.5 <= lon <= 4.5):
            continue

        colour = str(_get(props, "warningLevel", "warning_level", "colour", "color", "severity") or "Warning").strip().title()
        weather = str(_get(props, "weatherType", "weather_type", "weather", "event") or "Severe weather").strip()
        headline = str(_get(props, "headline", "title", "description") or f"{colour} {weather} warning").strip()
        details = str(_get(props, "furtherDetails", "further_details", "whatToExpect", "what_to_expect", "description") or "").strip()
        start = _dt(_get(props, "validFrom", "valid_from", "onset", "start", "startTime"))
        end = _dt(_get(props, "validTo", "valid_to", "expires", "end", "endTime"))
        warning_id = str(_get(props, "id", "warningId", "warning_id", "identifier", "OBJECTID") or idx)
        updated = _dt(_get(props, "updated", "updateTime", "modified", "issued", "sent")) or now
        interesting = colour.lower() in {"amber", "red"}

        event = Event(
            id=f"metoffice:{warning_id}",
            type="weather",
            name=f"{colour} • {weather}",
            subtitle=headline,
            lat=lat,
            lon=lon,
            category=f"{colour} weather warning",
            status="Active",
            severity=colour,
            reason=details[:1500] if details else headline,
            start_time=start,
            end_time=end,
            position_method="static",
            position_label="Approximate centre of published warning polygon",
            source="Met Office NSWWS public mirror",
            source_id=warning_id,
            official_url=MET_OFFICE_WARNINGS_PAGE,
            news_query=f'"{weather}" weather warning UK',
            social_query=f'"{weather}" "{colour}" warning Met Office',
            interesting=interesting,
            last_updated=updated,
        )
        out[event.id] = event
    return list(out.values())


def _rss_text(element: ET.Element | None, tag: str) -> str:
    if element is None:
        return ""
    child = element.find(tag)
    return (child.text or "").strip() if child is not None else ""


def _plain_html(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", unescape(value or ""))).strip()


def _rss_region(text: str) -> tuple[str, float, float]:
    low = text.lower()
    # Prefer the longest phrase so e.g. north west England beats England-like fragments.
    for name in sorted(_RSS_REGION_CENTRES, key=len, reverse=True):
        if name in low:
            lat, lon = _RSS_REGION_CENTRES[name]
            return name.title(), lat, lon
    return "United Kingdom", 54.50, -2.50


def _rss_weather_type(text: str) -> str:
    low = text.lower()
    for label, words in (
        ("Thunderstorms", ("thunder", "lightning")),
        ("Snow & ice", ("snow", "ice", "wintry")),
        ("Wind", ("wind", "gale")),
        ("Rain", ("rain", "flooding")),
        ("Fog", ("fog",)),
        ("Extreme heat", ("heat", "hot weather")),
    ):
        if any(word in low for word in words):
            return label
    return "Severe weather"


def parse_warning_rss(content: bytes | str) -> list[Event]:
    """Parse the official Met Office UK warning RSS as a resilient fallback.

    The RSS contains textual warning areas rather than polygons, so the markers
    use explicit approximate region centroids and never pretend to be precise.
    """
    root = ET.fromstring(content)
    now = datetime.now(timezone.utc)
    out: dict[str, Event] = {}
    for idx, item in enumerate(root.findall(".//item")):
        title = _plain_html(_rss_text(item, "title"))
        description = _plain_html(_rss_text(item, "description"))
        link = _rss_text(item, "link") or MET_OFFICE_WARNINGS_PAGE
        guid = _rss_text(item, "guid") or link or f"rss-{idx}"
        published = _rss_text(item, "pubDate")
        try:
            updated = parsedate_to_datetime(published).astimezone(timezone.utc) if published else now
        except Exception:
            updated = now
        combined = " ".join(x for x in (title, description) if x)
        if not combined:
            continue
        low = combined.lower()
        severity = "Red" if "red warning" in low or low.startswith("red ") else ("Amber" if "amber warning" in low or low.startswith("amber ") else ("Yellow" if "yellow warning" in low or low.startswith("yellow ") else "Warning"))
        weather = _rss_weather_type(combined)
        region, lat, lon = _rss_region(combined)
        source_id = re.sub(r"[^a-zA-Z0-9_-]+", "-", guid)[-120:] or f"rss-{idx}"
        event = Event(
            id=f"metoffice-rss:{source_id}", type="weather", name=f"{severity} • {weather}",
            subtitle=title or description[:240], lat=lat, lon=lon, category=f"{severity} weather warning",
            status="Active", severity=severity, reason=description[:1500] or title,
            position_method="static", position_label=f"Approximate {region} marker from official warning RSS",
            source="Met Office warning RSS", source_id=guid[:240], official_url=link,
            news_query=f'{weather} weather warning {region} Met Office',
            social_query=f'{weather} {severity} warning {region} Met Office',
            interesting=severity.lower() in {"amber", "red"}, last_updated=updated,
        )
        out[event.id] = event
    return list(out.values())


class MetOfficeWarningCollector:
    """No-key warning layer using the publicly accessible NSWWS ArcGIS mirror.

    The public layer is intentionally labelled as a mirror and is only a
    convenience layer for the free-only development build. Users are linked to
    the official Met Office warning page for the authoritative warning.
    """

    def __init__(self, settings, state, data_dir: Path | None = None):
        self.settings = settings
        self.state = state
        self.data_dir = data_dir
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "UKActivityRadar/1.2.1 (weather warnings consumer)"})
        self._layer_url: str | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="met-office-warnings", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)

    def _resolve_layer(self) -> str:
        if self._layer_url:
            return self._layer_url
        meta = self._session.get(
            f"https://www.arcgis.com/sharing/rest/content/items/{self.settings.met_office_arcgis_item}",
            params={"f": "json"}, timeout=20,
        )
        meta.raise_for_status()
        url = str(meta.json().get("url") or "").strip().rstrip("/")
        if not url:
            raise RuntimeError("public NSWWS mirror did not return a FeatureServer URL")
        if not re.search(r"/\d+$", url):
            url += "/0"
        self._layer_url = url
        return url

    def fetch_once(self) -> list[Event]:
        layer = self._resolve_layer()
        response = self._session.get(
            layer + "/query",
            params={
                "where": "1=1",
                "outFields": "*",
                "returnGeometry": "true",
                "outSR": "4326",
                "f": "geojson",
            },
            timeout=30,
        )
        response.raise_for_status()
        return parse_warning_geojson(response.json())

    def fetch_rss_once(self) -> list[Event]:
        response = self._session.get(MET_OFFICE_WARNINGS_RSS, timeout=25)
        response.raise_for_status()
        return parse_warning_rss(response.content)

    def _run(self) -> None:
        self.state.set_source(SourceStatus(
            name="met_office", enabled=True, connected=False, mode="connecting",
            message="Loading free public Met Office NSWWS warning mirror…",
        ))
        while not self._stop.is_set():
            try:
                events = self.fetch_once()
                self.state.replace_events_for_source("Met Office warning RSS", [])
                self.state.replace_events_for_source("Met Office NSWWS public mirror", events)
                mode = "live" if events else "ready"
                self.state.set_source(SourceStatus(
                    name="met_office", enabled=True, connected=True, mode=mode,
                    message=(
                        f"{len(events):,} current weather warnings mapped • warning polygons via public NSWWS mirror"
                        if events else "Met Office warning feed verified • no current UK warnings"
                    ),
                    last_message_at=datetime.now(timezone.utc),
                    messages_seen=len(events), movements_mapped=len(events),
                ))
            except Exception as mirror_exc:
                # The public mirror metadata can move. Fall back to the official
                # Met Office UK RSS so a mirror outage does not make weather look broken.
                self._layer_url = None
                try:
                    events = self.fetch_rss_once()
                    self.state.replace_events_for_source("Met Office NSWWS public mirror", [])
                    self.state.replace_events_for_source("Met Office warning RSS", events)
                    self.state.set_source(SourceStatus(
                        name="met_office", enabled=True, connected=True, mode="live" if events else "ready",
                        message=(
                            f"{len(events):,} current warnings • official Met Office RSS fallback • regional marker positions"
                            if events else "Official Met Office warning RSS verified • no current UK warnings"
                        ),
                        last_message_at=datetime.now(timezone.utc),
                        messages_seen=len(events), movements_mapped=len(events),
                    ))
                except Exception as rss_exc:
                    self.state.set_source(SourceStatus(
                        name="met_office", enabled=True, connected=False, mode="error",
                        message=(
                            f"Weather feeds unavailable: mirror {type(mirror_exc).__name__}; "
                            f"official RSS {type(rss_exc).__name__}: {rss_exc}"
                        ),
                    ))
            self._stop.wait(self.settings.met_office_poll_seconds)
