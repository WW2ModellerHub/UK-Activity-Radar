from __future__ import annotations

import hashlib
import json
import re
import threading
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any

import requests

from ..models import Event, SourceStatus


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower().split(":")[-1])


def _walk(obj: Any):
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from _walk(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from _walk(value)


def _scalars_for(obj: Any, names: set[str]) -> list[Any]:
    names = {_norm(n) for n in names}
    values: list[Any] = []
    for node in _walk(obj):
        for key, value in node.items():
            if _norm(key) in names and not isinstance(value, (dict, list)):
                values.append(value)
    return values


def _first(obj: Any, *names: str) -> str | None:
    for value in _scalars_for(obj, set(names)):
        text = str(value).strip()
        if text and text.lower() not in {"none", "null"}:
            return text
    return None




_GENERIC_MANAGEMENT = {
    "roadorcarriagewayorlanemanagement",
    "roadorcarriagewayorlanemanagementtype",
    "trafficmanagement",
    "trafficmanagementtype",
    "managementtype",
}

_HUMAN_ENUM = {
    "laneclosures": "lane closure",
    "lanesclosed": "lane closure",
    "carriagewayclosures": "carriageway closure",
    "carriagewayclosure": "carriageway closure",
    "roadclosure": "road closure",
    "roadclosed": "road closure",
    "intermittentshorttermclosures": "intermittent closure",
    "hardshoulderclosure": "hard shoulder closure",
    "hardshoulderclosures": "hard shoulder closure",
    "contraflow": "contraflow",
    "narrowlanes": "narrow lanes",
    "implemented": "Active",
    "active": "Active",
    "suspended": "Suspended",
    "eastbound": "eastbound",
    "westbound": "westbound",
    "northbound": "northbound",
    "southbound": "southbound",
    "bothways": "both directions",
    "bothdirections": "both directions",
}


def _humanise(value: str | None, *, allow_generic: bool = False) -> str | None:
    if not value:
        return None
    raw = str(value).strip().split(":")[-1]
    if not raw:
        return None
    norm = _norm(raw)
    if not allow_generic and norm in _GENERIC_MANAGEMENT:
        return None
    if norm in _HUMAN_ENUM:
        return _HUMAN_ENUM[norm]
    # DATEX enumerations are commonly camelCase. Split them into readable text
    # without mangling ordinary descriptions such as "J29 Lofthouse".
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", raw) and (re.search(r"[a-z][A-Z]", raw) or "_" in raw):
        raw = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", raw)
    raw = raw.replace("_", " ").strip()
    return re.sub(r"\s+", " ", raw) or None


def _dedupe_query_parts(*parts: str | None) -> str:
    out: list[str] = []
    seen: set[str] = set()
    for part in parts:
        if not part:
            continue
        text = re.sub(r"\s+", " ", str(part)).strip(" -•")
        if not text:
            continue
        key = re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()
        if not key or key in seen:
            continue
        # Do not repeat a road number when the location already starts with it.
        if out and len(out) == 1 and re.fullmatch(r"[am]\d+(?:\(m\))?", out[0], re.I) and key.startswith(out[0].lower() + " "):
            out[0] = text
            seen = {key}
            continue
        if any(key == old or key in old for old in seen):
            continue
        out.append(text)
        seen.add(key)
    return " ".join(out)


def _official_url(closure_type: str) -> str:
    if closure_type == "planned":
        return "https://nationalhighways.co.uk/roads-and-travel/live-travel-updates/road-closure-report/"
    return "https://nationalhighways.co.uk/roads-and-travel/live-travel-updates/current-incidents-disruptions-and-delays/"


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _uk_pair(a: float, b: float) -> tuple[float, float] | None:
    # Try lat/lon then lon/lat. UK bounds are intentionally generous.
    if 49.0 <= a <= 61.5 and -9.5 <= b <= 3.5:
        return a, b
    if 49.0 <= b <= 61.5 and -9.5 <= a <= 3.5:
        return b, a
    return None


def _coordinates(record: dict) -> tuple[float, float] | None:
    # Explicit latitude/longitude pairs first.
    for node in _walk(record):
        lower = {_norm(k): v for k, v in node.items()}
        lat_values = [v for k, v in lower.items() if "latitude" in k and not isinstance(v, (dict, list))]
        lon_values = [v for k, v in lower.items() if "longitude" in k and not isinstance(v, (dict, list))]
        if lat_values and lon_values:
            try:
                pair = _uk_pair(float(lat_values[0]), float(lon_values[0]))
                if pair:
                    return pair
            except Exception:
                pass

    # GeoJSON-style coordinates.
    for node in _walk(record):
        for key, value in node.items():
            if _norm(key) == "coordinates" and isinstance(value, list) and len(value) >= 2:
                first = value
                while isinstance(first, list) and first and isinstance(first[0], list):
                    first = first[0]
                if isinstance(first, list) and len(first) >= 2:
                    try:
                        pair = _uk_pair(float(first[0]), float(first[1]))
                        if pair:
                            return pair
                    except Exception:
                        pass

    # DATEX GML pos / posList. Pick a midpoint of valid WGS84 pairs.
    positions: list[tuple[float, float]] = []
    for node in _walk(record):
        for key, value in node.items():
            if _norm(key) not in {"pos", "poslist"} or isinstance(value, (dict, list)):
                continue
            try:
                nums = [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", str(value))]
            except Exception:
                continue
            for i in range(0, len(nums) - 1, 2):
                pair = _uk_pair(nums[i], nums[i + 1])
                if pair:
                    positions.append(pair)
    if positions:
        return (
            sum(p[0] for p in positions) / len(positions),
            sum(p[1] for p in positions) / len(positions),
        )
    return None




def _xml_to_obj(element: ET.Element) -> dict:
    key = element.tag.split("}")[-1]
    children = list(element)
    if not children:
        value = (element.text or "").strip()
        node: Any = value
    else:
        grouped: dict[str, Any] = {}
        for child in children:
            child_key = child.tag.split("}")[-1]
            child_value = _xml_to_obj(child)[child_key]
            if child_key in grouped:
                if not isinstance(grouped[child_key], list):
                    grouped[child_key] = [grouped[child_key]]
                grouped[child_key].append(child_value)
            else:
                grouped[child_key] = child_value
        node = grouped
    if element.attrib:
        if not isinstance(node, dict):
            node = {"value": node}
        for attr_key, attr_value in element.attrib.items():
            node[attr_key.split("}")[-1]] = attr_value
    return {key: node}

def _candidate_records(payload: Any) -> list[dict]:
    """Find DATEX situation records without depending on one exact JSON wrapping."""
    candidates: list[dict] = []
    seen: set[int] = set()
    telltale = {
        "roadorcarriagewayorlanemanagementtype", "operatoractionstatus",
        "situationrecordcreationtime", "situationrecordversiontime",
        "validity", "locationreference", "trafficmanagementtype",
    }
    for node in _walk(payload):
        keys = {_norm(k) for k in node.keys()}
        if keys & telltale:
            marker = id(node)
            if marker not in seen:
                seen.add(marker)
                candidates.append(node)

    # If the wrapper uses explicit situationRecord keys, prefer their child objects.
    explicit: list[dict] = []
    for node in _walk(payload):
        for key, value in node.items():
            if "situationrecord" in _norm(key):
                if isinstance(value, dict):
                    explicit.append(value)
                elif isinstance(value, list):
                    explicit.extend(x for x in value if isinstance(x, dict))
    return explicit or candidates


def parse_closure_payload(payload: Any, closure_type: str) -> list[Event]:
    events: list[Event] = []
    now = datetime.now(timezone.utc)
    for record in _candidate_records(payload):
        coords = _coordinates(record)
        if not coords:
            continue
        lat, lon = coords
        road = _humanise(_first(record, "roadNumber", "roadName", "roadIdentifier", "road"), allow_generic=True)
        direction = _humanise(_first(record, "applicableForTrafficDirection", "direction", "directionBound"))
        management = _humanise(_first(record, "roadOrCarriagewayOrLaneManagementType", "trafficManagementType", "managementType"))
        reason = _humanise(_first(record, "causeType", "roadMaintenanceType", "reason", "cause", "description"))
        location = _humanise(_first(record, "locationDescription", "locationName", "descriptor", "locationDescriptor"), allow_generic=True)
        status = _humanise(_first(record, "operatorActionStatus", "validityStatus"), allow_generic=True) or "Active"
        severity = _humanise(_first(record, "overallSeverity", "severity"), allow_generic=True)
        lanes = _first(record, "numberOfLanesRestricted", "numberOfLanesClosed", "numberOfLanes")
        start = _parse_dt(_first(record, "overallStartTime", "startOfPeriod", "situationRecordCreationTime"))
        end = _parse_dt(_first(record, "overallEndTime", "endOfPeriod"))
        updated = _parse_dt(_first(record, "situationRecordVersionTime", "situationRecordCreationTime")) or now
        source_id = _first(record, "situationRecordId", "situationId", "recordId", "id")
        if not source_id:
            seed = f"{closure_type}|{road}|{location}|{lat:.5f}|{lon:.5f}|{start}"
            source_id = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:20]

        # Keep the map semantics simple and non-overlapping:
        #   ROAD      = planned roadworks / closures / traffic management
        #   INCIDENT  = every unplanned National Highways disruption
        # Older builds tried to infer incidents only from keywords, which left
        # many unplanned events under Roads and made the two layer buttons look
        # almost identical.
        is_incident = closure_type == "unplanned"
        event_type = "incident" if is_incident else "road"
        category = "Unplanned road incident / disruption" if is_incident else "Planned roadworks / closure"
        title_road = road or "Strategic road"
        title_action = reason or management or ("road closure" if closure_type == "unplanned" else "planned closure")
        name = f"{title_road} — {title_action}"
        subtitle = " • ".join(x for x in [location, direction, status] if x)

        # Build a human search phrase. The raw DATEX payload can contain values
        # such as "roadOrCarriagewayOrLaneManagement" and may repeat the
        # road number inside the location description. Those were leaking into
        # Google/X searches in earlier builds.
        location_for_query = location
        road_for_query = road
        if road and location and re.match(rf"^{re.escape(road)}(?:\b|\s)", location, re.I):
            road_for_query = None
        action_for_query = reason or management or ("road closure" if closure_type == "unplanned" else "planned road closure")
        query = _dedupe_query_parts(road_for_query, location_for_query, direction, action_for_query)

        events.append(Event(
            id=f"nh:{source_id}", type=event_type, name=name[:220], subtitle=subtitle[:350],
            lat=lat, lon=lon, category=category, status=status, severity=severity,
            road=road, direction=direction, lanes_closed=lanes, reason=reason or management,
            start_time=start, end_time=end, position_method="static",
            position_label="National Highways reported closure location",
            source="National Highways", source_id=source_id,
            official_url=_official_url(closure_type), news_query=query or name,
            social_query=query or name, interesting=closure_type == "unplanned", last_updated=updated,
        ))
    # De-duplicate in case nested DATEX wrappers exposed the same record twice.
    return list({event.id: event for event in events}.values())


class NationalHighwaysCollector:
    def __init__(self, settings, state):
        self.settings = settings
        self.state = state
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="national-highways", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _get(self, closure_type: str) -> list[Event]:
        url = f"{self.settings.national_highways_base_url}/closures"
        response = requests.get(
            url,
            params={"closureType": closure_type},
            headers={
                "Ocp-Apim-Subscription-Key": self.settings.national_highways_api_key,
                "Accept": "application/json",
                "User-Agent": "UKActivityRadar/0.7.2",
            },
            timeout=25,
        )
        response.raise_for_status()
        try:
            payload = response.json()
        except Exception:
            try:
                root = ET.fromstring(response.content)
                payload = _xml_to_obj(root)
            except Exception as exc:
                raise RuntimeError(f"National Highways returned data that was neither JSON nor parseable DATEX XML ({response.headers.get('content-type')})") from exc
        return parse_closure_payload(payload, closure_type)

    def fetch_once(self) -> list[Event]:
        rows = self._get("unplanned")
        if self.settings.national_highways_include_planned:
            rows.extend(self._get("planned"))
        return list({row.id: row for row in rows}.values())

    def _run(self) -> None:
        if not self.settings.national_highways_api_key:
            self.state.set_source(SourceStatus(
                name="national_highways", enabled=True, connected=False, mode="setup",
                message="Free National Highways API key required • run configure_roads.bat",
            ))
            return
        self.state.set_source(SourceStatus(
            name="national_highways", enabled=True, connected=False, mode="connecting",
            message="Connecting to National Highways Road & Lane Closures v2.0…",
        ))
        while not self._stop.is_set():
            try:
                events = self.fetch_once()
                self.state.replace_events_for_source("National Highways", events)
                self.state.set_source(SourceStatus(
                    name="national_highways", enabled=True, connected=True, mode="live" if events else "ready",
                    message=f"{len(events):,} current road/lane closure events mapped",
                    last_message_at=datetime.now(timezone.utc), messages_seen=len(events), movements_mapped=len(events),
                ))
            except requests.HTTPError as exc:
                code = exc.response.status_code if exc.response is not None else "HTTP"
                self.state.set_source(SourceStatus(
                    name="national_highways", enabled=True, connected=False, mode="error",
                    message=f"National Highways HTTP {code}. Check the free subscription key/API subscription.",
                ))
            except Exception as exc:
                self.state.set_source(SourceStatus(
                    name="national_highways", enabled=True, connected=False, mode="error",
                    message=f"Road feed error: {type(exc).__name__}: {exc}",
                ))
            self._stop.wait(self.settings.national_highways_poll_seconds)
