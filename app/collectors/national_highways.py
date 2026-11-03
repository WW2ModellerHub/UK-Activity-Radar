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


def _vms_message(record: dict) -> str | None:
    """Extract displayed text without treating IDs or instructions as copy."""
    lines: list[str] = []
    exact = {
        "messagetext", "vmstext", "textline", "legend", "displayedmessage",
        "vmsmessage", "textdisplayed", "messagecontent",
    }
    for node in _walk(record):
        for key, value in node.items():
            norm = _norm(key)
            if isinstance(value, (dict, list)):
                continue
            # DATEX places metadata such as messageSetBy, messageIndex and
            # reasonForSetting beside the actual textLine values. Only accept
            # explicit display-text fields; otherwise cards misleadingly show
            # strings such as "0 / External Center" as part of the sign.
            if norm not in exact:
                continue
            text = re.sub(r"\s+", " ", str(value)).strip(" -•")
            if not text or text.lower() in {"none", "null", "unknown", "not set"}:
                continue
            if re.fullmatch(r"\[[a-z0-9_:.\-]+\]", text, re.I):
                continue
            if text not in lines:
                lines.append(text)
    return " / ".join(lines[:4]) or None


def parse_vms_payload(payload: Any) -> list[Event]:
    """Normalise National Highways DATEX-II VMS records into honest map cards."""
    events: list[Event] = []
    now = datetime.now(timezone.utc)
    for record in _walk(payload):
        if not isinstance(record, dict):
            continue
        coords = _coordinates(record)
        message = _vms_message(record)
        if not coords or not message:
            continue
        lat, lon = coords
        road = _humanise(_first(record, "roadNumber", "roadName", "roadIdentifier", "road"), allow_generic=True)
        location = _humanise(_first(record, "locationDescription", "locationName", "descriptor"), allow_generic=True)
        direction = _humanise(_first(record, "applicableForTrafficDirection", "direction", "directionBound"))
        source_id = _first(record, "externalIdentifier", "vmsUnitReference", "vmsUnitId", "vmsReference", "situationRecordId", "id")
        if not source_id:
            source_id = hashlib.sha1(f"{message}|{lat:.5f}|{lon:.5f}".encode("utf-8")).hexdigest()[:20]
        updated = _parse_dt(_first(record, "publicationTime", "vmsMessagePublicationTime", "situationRecordVersionTime")) or now
        subtitle = " • ".join(part for part in (road, location, direction) if part)
        events.append(Event(
            id=f"nh-vms:{source_id}", type="incident", name=message[:220], subtitle=subtitle[:350],
            lat=lat, lon=lon, category="Live motorway sign", status="Displaying now",
            road=road, direction=direction, reason=message[:500], position_method="static",
            position_label="National Highways sign location", source="National Highways VMS",
            source_id=source_id, official_url="https://nationalhighways.co.uk/travel-updates/",
            interesting=True, last_updated=updated,
        ))
    return list({event.id: event for event in events}.values())


def _speed_mph(value: str | None) -> int | None:
    """Convert DATEX km/h values to the nearest displayed UK mph value."""
    if not value:
        return None
    try:
        mph = float(value) / 1.609344
    except (TypeError, ValueError):
        return None
    if mph <= 0:
        return None
    # VSS values are emitted as metric conversions (64.3736 == 40 mph).
    return int(round(mph / 5.0) * 5)


def parse_speed_managed_payload(payload: Any) -> list[Event]:
    """Normalise active National Highways temporary speed restrictions."""
    events: list[Event] = []
    now = datetime.now(timezone.utc)
    for record in _candidate_records(payload):
        speed = _speed_mph(_first(record, "temporarySpeedLimit"))
        status = (_first(record, "validityStatus", "operatorActionStatus") or "active").strip()
        status_key = _norm(status)
        if not speed or status_key in {"suspended", "inactive", "cancelled", "cleared"}:
            continue
        start = _parse_dt(_first(record, "overallStartTime", "startOfPeriod"))
        end = _parse_dt(_first(record, "overallEndTime", "endOfPeriod"))
        if start and start > now:
            continue
        if end and end < now:
            continue
        coords = _coordinates(record)
        if not coords:
            continue
        lat, lon = coords
        road = _humanise(_first(record, "roadName", "roadNumber", "roadIdentifier", "road"), allow_generic=True)
        direction = _humanise(_first(record, "directionOnLinearSection", "applicableForTrafficDirection", "direction"))
        location = _humanise(_first(record, "locationDescription", "locationName", "descriptor"), allow_generic=True)
        cause = _humanise(_first(record, "generalPublicComment", "comment", "causeType", "detailedCauseType", "constructionWorkType"), allow_generic=True)
        compliance = _humanise(_first(record, "complianceOption"), allow_generic=True)
        source_id = _first(record, "idG", "situationRecordId", "recordId", "id")
        if not source_id:
            source_id = hashlib.sha1(f"{road}|{speed}|{lat:.5f}|{lon:.5f}|{start}".encode("utf-8")).hexdigest()[:20]
        updated = _parse_dt(_first(record, "situationRecordVersionTime", "publicationTime")) or now
        subtitle_parts = [location or road, direction]
        subtitle = " • ".join(x for x in subtitle_parts if x)
        reason = cause or f"{compliance or 'Temporary'} {speed} mph restriction"
        events.append(Event(
            id=f"nh-sma:{source_id}", type="incident", name=f"{speed} mph temporary limit",
            subtitle=subtitle[:350], lat=lat, lon=lon, category="Live managed speed",
            status="Active", severity=None, road=road, direction=direction, reason=reason[:500],
            start_time=start, end_time=end, position_method="static",
            position_label="National Highways managed-speed area", source="National Highways Speed",
            source_id=source_id, official_url="https://nationalhighways.co.uk/travel-updates/",
            news_query=_dedupe_query_parts(road, location, f"{speed} mph speed restriction"),
            interesting=True, last_updated=updated,
        ))
    return list({event.id: event for event in events}.values())


def _feature_records(payload: Any) -> list[dict]:
    records: list[dict] = []
    for node in _walk(payload):
        for key, value in node.items():
            if _norm(key) != "limitorfeaturerecord":
                continue
            if isinstance(value, dict):
                records.append(value)
            elif isinstance(value, list):
                records.extend(item for item in value if isinstance(item, dict))
    return records


def parse_road_feature_payload(payload: Any, feature_type: str) -> list[Event]:
    """Normalise static SRN roadside features without presenting them as incidents."""
    events: list[Event] = []
    now = datetime.now(timezone.utc)
    feature_key = _norm(feature_type)
    is_bus_stop = feature_key == "busstop"
    label = "Strategic-road bus stop" if is_bus_stop else "Emergency roadside telephone"
    icon_word = "Bus stop" if is_bus_stop else "Emergency telephone"
    for record in _feature_records(payload):
        status = _first(record, "validityStatus") or "active"
        if _norm(status) not in {"active", "implemented"}:
            continue
        coords = _coordinates(record)
        if not coords:
            continue
        lat, lon = coords
        road = _humanise(_first(record, "roadName", "roadNumber", "roadIdentifier", "road"), allow_generic=True)
        location = _humanise(_first(record, "locationDescription"), allow_generic=True) or _humanise(
            _first(record, "description", "locationName", "descriptor"), allow_generic=True
        )
        direction = _humanise(_first(record, "directionAtPoint", "directionOnLinearSection", "direction", "applicableForTrafficDirection"))
        source_id = _first(record, "idG", "externalIdentifier", "featureId", "siteId", "id")
        if not source_id:
            source_id = hashlib.sha1(f"{feature_type}|{lat:.6f}|{lon:.6f}".encode("utf-8")).hexdigest()[:20]
        name = f"{icon_word}{f' • {road}' if road else ''}"
        updated = _parse_dt(_first(record, "limitOrFeatureRecordVersionTime", "publicationTime")) or now
        events.append(Event(
            id=f"nh-rlf:{feature_key}:{source_id}", type="roadfeature", name=name,
            subtitle=" • ".join(x for x in (location, direction) if x)[:350],
            lat=lat, lon=lon, category=label, status="Mapped feature", road=road,
            direction=direction, reason=label, position_method="static",
            position_label="National Highways network feature", source="National Highways Features",
            source_id=source_id, official_url="https://nationalhighways.co.uk/travel-updates/",
            interesting=False, last_updated=updated,
        ))
    return list({event.id: event for event in events}.values())


def _response_payload(response: requests.Response, service: str) -> Any:
    try:
        return response.json()
    except Exception:
        try:
            return _xml_to_obj(ET.fromstring(response.content))
        except Exception as exc:
            raise RuntimeError(f"{service} returned neither JSON nor parseable DATEX XML") from exc


class _NationalHighwaysOptionalCollector:
    source_name = "National Highways"
    status_name = "national_highways_optional"
    connecting_message = "Connecting to National Highways…"
    success_label = "records"

    def __init__(self, settings, state):
        self.settings = settings
        self.state = state
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name=self.status_name.replace("_", "-"), daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)

    def fetch_once(self) -> list[Event]:
        raise NotImplementedError

    @property
    def poll_seconds(self) -> int:
        return self.settings.national_highways_poll_seconds

    def _run(self) -> None:
        self.state.set_source(SourceStatus(name=self.status_name, enabled=True, connected=False, mode="connecting", message=self.connecting_message))
        while not self._stop.is_set():
            try:
                events = self.fetch_once()
                self.state.replace_events_for_source(self.source_name, events)
                self.state.set_source(SourceStatus(
                    name=self.status_name, enabled=True, connected=True, mode="live" if events else "ready",
                    message=f"{len(events):,} {self.success_label}", last_message_at=datetime.now(timezone.utc),
                    messages_seen=len(events), movements_mapped=len(events),
                ))
            except requests.HTTPError as exc:
                code = exc.response.status_code if exc.response is not None else "HTTP"
                self.state.set_source(SourceStatus(name=self.status_name, enabled=True, connected=False, mode="error", message=f"National Highways {self.success_label} HTTP {code}; check this API subscription"))
            except Exception as exc:
                self.state.set_source(SourceStatus(name=self.status_name, enabled=True, connected=False, mode="error", message=f"{self.success_label.capitalize()} unavailable: {type(exc).__name__}"))
            self._stop.wait(self.poll_seconds)


class NationalHighwaysSpeedCollector(_NationalHighwaysOptionalCollector):
    source_name = "National Highways Speed"
    status_name = "national_highways_speed"
    connecting_message = "Connecting to live managed-speed areas…"
    success_label = "active managed-speed areas"

    @property
    def poll_seconds(self) -> int:
        return self.settings.national_highways_speed_poll_seconds

    def fetch_once(self) -> list[Event]:
        response = requests.get(self.settings.national_highways_speed_url, headers={
            "Ocp-Apim-Subscription-Key": self.settings.national_highways_api_key,
            "Accept": "application/json, application/xml", "User-Agent": "UKActivityRadar/1.5",
        }, timeout=35)
        response.raise_for_status()
        return parse_speed_managed_payload(_response_payload(response, "Managed-speed feed"))


class NationalHighwaysFeaturesCollector(_NationalHighwaysOptionalCollector):
    source_name = "National Highways Features"
    status_name = "national_highways_features"
    connecting_message = "Loading strategic-road features…"
    success_label = "roadside features"

    @property
    def poll_seconds(self) -> int:
        return self.settings.national_highways_features_poll_seconds

    def fetch_once(self) -> list[Event]:
        events: list[Event] = []
        for feature_type in self.settings.national_highways_feature_types:
            response = requests.get(self.settings.national_highways_features_url, params={"limitOrFeatureType": feature_type}, headers={
                "Ocp-Apim-Subscription-Key": self.settings.national_highways_api_key,
                "Accept": "application/json, application/xml", "User-Agent": "UKActivityRadar/1.5",
            }, timeout=40)
            response.raise_for_status()
            events.extend(parse_road_feature_payload(_response_payload(response, "Road-feature feed"), feature_type))
        return list({event.id: event for event in events}.values())


class NationalHighwaysCollector:
    def __init__(self, settings, state):
        self.settings = settings
        self.state = state
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="national-highways", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)

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


class NationalHighwaysVMSCollector:
    """Independent VMS poller so sign errors never take down road incidents."""

    def __init__(self, settings, state):
        self.settings = settings
        self.state = state
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="national-highways-vms", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)

    def fetch_once(self) -> list[Event]:
        response = requests.get(
            self.settings.national_highways_vms_url,
            headers={
                "Ocp-Apim-Subscription-Key": self.settings.national_highways_api_key,
                "Accept": "application/json, application/xml",
                "User-Agent": "UKActivityRadar/1.5",
            },
            timeout=25,
        )
        response.raise_for_status()
        try:
            payload = response.json()
        except Exception:
            payload = _xml_to_obj(ET.fromstring(response.content))
        return parse_vms_payload(payload)

    def _run(self) -> None:
        if not self.settings.national_highways_api_key or not self.settings.national_highways_vms_url:
            self.state.set_source(SourceStatus(
                name="national_highways_vms", enabled=True, connected=False, mode="setup",
                message="Subscribe to Digital VMS, then add its API URL",
            ))
            return
        while not self._stop.is_set():
            try:
                events = self.fetch_once()
                self.state.replace_events_for_source("National Highways VMS", events)
                self.state.set_source(SourceStatus(
                    name="national_highways_vms", enabled=True, connected=True,
                    mode="live" if events else "ready",
                    message=f"{len(events):,} live motorway sign messages",
                    last_message_at=datetime.now(timezone.utc), messages_seen=len(events), movements_mapped=len(events),
                ))
            except requests.HTTPError as exc:
                code = exc.response.status_code if exc.response is not None else "HTTP"
                self.state.set_source(SourceStatus(
                    name="national_highways_vms", enabled=True, connected=False, mode="error",
                    message=f"Motorway signs HTTP {code}; check the Digital VMS subscription",
                ))
            except Exception as exc:
                self.state.set_source(SourceStatus(
                    name="national_highways_vms", enabled=True, connected=False, mode="error",
                    message=f"Motorway signs unavailable: {type(exc).__name__}",
                ))
            self._stop.wait(self.settings.national_highways_poll_seconds)
