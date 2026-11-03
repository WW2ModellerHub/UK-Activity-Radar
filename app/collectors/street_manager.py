"""Street Manager V6 Reporting API client and permit parser.

This module deliberately targets the documented Reporting API rather than an
invented public polling endpoint.  Reporting results are scoped by the roles,
organisation and workstreams in the supplied Street Manager JWT; they are *not*
the nationwide Street Manager open-data feed.

The production service documents the following contract::

    GET https://api.manage-roadworks.service.gov.uk/v6/reporting/permits
    token: <Street Manager JWT>

Responses contain ``rows`` plus ``pagination.has_next_page`` and return at
most 25 rows per page by default.  The client is intentionally one-shot and
stateless so application wiring can decide its own conservative poll interval.
"""

from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from typing import Any, Mapping

import requests

from ..models import Event


PRODUCTION_REPORTING_URL = "https://api.manage-roadworks.service.gov.uk/v6/reporting"
STREET_MANAGER_GUIDANCE_URL = "https://www.gov.uk/guidance/find-and-use-roadworks-data"


def _parse_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _humanise(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip().replace("_", " ")
    return re.sub(r"\s+", " ", text).strip().capitalize() or None


def bng_to_wgs84(easting: float, northing: float) -> tuple[float, float]:
    """Convert EPSG:27700 British National Grid to WGS84 latitude/longitude.

    Street Manager geometries use BNG.  This compact implementation performs
    the inverse OSGB projection followed by the published seven-parameter
    OSGB36-to-WGS84 Helmert transform, avoiding a heavyweight GIS dependency
    for a single point conversion.
    """

    easting = float(easting)
    northing = float(northing)
    if not (0 <= easting <= 700_000 and 0 <= northing <= 1_300_000):
        raise ValueError("coordinate is outside the British National Grid")

    # Inverse Transverse Mercator on the Airy 1830 ellipsoid.
    a = 6_377_563.396
    b = 6_356_256.909
    f0 = 0.999_601_271_7
    lat0 = math.radians(49)
    lon0 = math.radians(-2)
    n0 = -100_000.0
    e0 = 400_000.0
    e2 = 1 - (b * b) / (a * a)
    n = (a - b) / (a + b)

    lat = lat0
    meridional = 0.0
    while True:
        lat = (northing - n0 - meridional) / (a * f0) + lat
        dlat = lat - lat0
        slat = lat + lat0
        meridional = b * f0 * (
            (1 + n + 1.25 * n**2 + 1.25 * n**3) * dlat
            - (3 * n + 3 * n**2 + 2.625 * n**3) * math.sin(dlat) * math.cos(slat)
            + (1.875 * n**2 + 1.875 * n**3) * math.sin(2 * dlat) * math.cos(2 * slat)
            - (35 / 24) * n**3 * math.sin(3 * dlat) * math.cos(3 * slat)
        )
        if abs(northing - n0 - meridional) < 0.00001:
            break

    sin_lat = math.sin(lat)
    cos_lat = math.cos(lat)
    tan_lat = math.tan(lat)
    nu = a * f0 / math.sqrt(1 - e2 * sin_lat**2)
    rho = a * f0 * (1 - e2) / (1 - e2 * sin_lat**2) ** 1.5
    eta2 = nu / rho - 1
    de = easting - e0

    vii = tan_lat / (2 * rho * nu)
    viii = tan_lat / (24 * rho * nu**3) * (5 + 3 * tan_lat**2 + eta2 - 9 * tan_lat**2 * eta2)
    ix = tan_lat / (720 * rho * nu**5) * (61 + 90 * tan_lat**2 + 45 * tan_lat**4)
    x = 1 / (cos_lat * nu)
    xi = 1 / (cos_lat * 6 * nu**3) * (nu / rho + 2 * tan_lat**2)
    xii = 1 / (cos_lat * 120 * nu**5) * (5 + 28 * tan_lat**2 + 24 * tan_lat**4)
    xiia = 1 / (cos_lat * 5040 * nu**7) * (
        61 + 662 * tan_lat**2 + 1320 * tan_lat**4 + 720 * tan_lat**6
    )

    airy_lat = lat - vii * de**2 + viii * de**4 - ix * de**6
    airy_lon = lon0 + x * de - xi * de**3 + xii * de**5 - xiia * de**7

    # Airy/OSGB36 geodetic -> Cartesian.
    airy_nu = a / math.sqrt(1 - e2 * math.sin(airy_lat) ** 2)
    x1 = airy_nu * math.cos(airy_lat) * math.cos(airy_lon)
    y1 = airy_nu * math.cos(airy_lat) * math.sin(airy_lon)
    z1 = airy_nu * (1 - e2) * math.sin(airy_lat)

    # OSGB36 -> WGS84 Helmert transform (translations in metres, rotations in
    # arc-seconds and scale in parts per million).
    tx, ty, tz = 446.448, -125.157, 542.060
    rx = math.radians(0.1502 / 3600)
    ry = math.radians(0.2470 / 3600)
    rz = math.radians(0.8421 / 3600)
    scale = 1 - 20.4894e-6
    x2 = tx + scale * x1 - rz * y1 + ry * z1
    y2 = ty + rz * x1 + scale * y1 - rx * z1
    z2 = tz - ry * x1 + rx * y1 + scale * z1

    # Cartesian -> WGS84 geodetic.
    wa = 6_378_137.0
    wb = 6_356_752.314_245
    we2 = 1 - (wb * wb) / (wa * wa)
    lon = math.atan2(y2, x2)
    p = math.hypot(x2, y2)
    wlat = math.atan2(z2, p * (1 - we2))
    for _ in range(12):
        wnu = wa / math.sqrt(1 - we2 * math.sin(wlat) ** 2)
        next_lat = math.atan2(z2 + we2 * wnu * math.sin(wlat), p)
        if abs(next_lat - wlat) < 1e-12:
            wlat = next_lat
            break
        wlat = next_lat
    return math.degrees(wlat), math.degrees(lon)


def _flatten_pairs(value: Any) -> list[tuple[float, float]]:
    if isinstance(value, Mapping):
        lower = {str(key).lower(): item for key, item in value.items()}
        if "easting" in lower and "northing" in lower:
            return [(float(lower["easting"]), float(lower["northing"]))]
        if "latitude" in lower and "longitude" in lower:
            return [(float(lower["latitude"]), float(lower["longitude"]))]
        if "coordinates" in lower:
            return _flatten_pairs(lower["coordinates"])
        pairs: list[tuple[float, float]] = []
        for item in value.values():
            pairs.extend(_flatten_pairs(item))
        return pairs
    if isinstance(value, (list, tuple)):
        if len(value) >= 2 and all(isinstance(item, (int, float, str)) for item in value[:2]):
            try:
                return [(float(value[0]), float(value[1]))]
            except (TypeError, ValueError):
                return []
        pairs = []
        for item in value:
            pairs.extend(_flatten_pairs(item))
        return pairs
    return []


def street_manager_position(geometry: Any) -> tuple[float, float] | None:
    """Return a representative WGS84 point for a Street Manager geometry.

    The Reporting API leaves the coordinate schema open; live responses have
    historically used BNG WKT while additive releases may expose a GeoJSON-like
    object.  Both are accepted.  A line/polygon is represented by the mean of
    its vertices, which is adequate for a map marker without pretending that it
    is the full work extent.
    """

    if geometry in (None, ""):
        return None
    pairs: list[tuple[float, float]] = []
    if isinstance(geometry, str):
        text = geometry.strip()
        if ";" in text and text.upper().startswith("SRID="):
            text = text.split(";", 1)[1]
        if text.startswith(("{", "[")):
            try:
                pairs = _flatten_pairs(json.loads(text))
            except (json.JSONDecodeError, TypeError, ValueError):
                pairs = []
        if not pairs:
            numbers = [float(item) for item in re.findall(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?", text)]
            pairs = list(zip(numbers[0::2], numbers[1::2]))
    else:
        pairs = _flatten_pairs(geometry)
    if not pairs:
        return None

    a = sum(item[0] for item in pairs) / len(pairs)
    b = sum(item[1] for item in pairs) / len(pairs)
    # Accept already-normalised UK coordinates in either lat/lon or GeoJSON
    # lon/lat order, useful for future additive response formats.
    if 49 <= a <= 61.5 and -9.5 <= b <= 3.5:
        return a, b
    if -9.5 <= a <= 3.5 and 49 <= b <= 61.5:
        return b, a
    try:
        return bng_to_wgs84(a, b)
    except (TypeError, ValueError):
        return None


def _payload_rows(payload: Any) -> list[dict[str, Any]]:
    rows = payload.get("rows") if isinstance(payload, Mapping) else payload
    if not isinstance(rows, list):
        return []
    return [dict(row) for row in rows if isinstance(row, Mapping)]


def parse_permit_payload(payload: Any, *, active_only: bool = True) -> list[Event]:
    """Map a documented ``GET /permits`` response to Radar road events."""

    events: list[Event] = []
    for row in _payload_rows(payload):
        work_status = str(row.get("work_status") or "").strip().lower()
        permit_status = str(row.get("status") or "").strip().lower()
        if active_only and (
            row.get("is_active_permit") is False
            or work_status in {"completed", "cancelled", "historical", "unattributable"}
            or permit_status in {"cancelled", "revoked", "progressed"}
        ):
            continue

        position = street_manager_position(row.get("works_coordinates"))
        if not position:
            continue
        lat, lon = position
        source_id = str(row.get("permit_reference_number") or row.get("work_reference_number") or "").strip()
        if not source_id:
            continue

        street = str(row.get("street") or row.get("location_description") or "Local road").strip()
        town = str(row.get("town") or row.get("area") or "").strip()
        description = str(row.get("description_of_work") or "").strip()
        management = _humanise(row.get("traffic_management_type_string") or row.get("traffic_management_type"))
        category = _humanise(row.get("work_category_string") or row.get("work_category")) or "Roadworks"
        reason = description or management or category
        status = _humanise(row.get("work_status_string") or work_status or permit_status) or "Planned"
        subtitle = " • ".join(part for part in (town, management, status) if part)
        query = " ".join(part for part in (street, town, reason, "roadworks") if part)
        urgency = str(row.get("work_category") or "").lower()
        severity = "High" if urgency in {"major", "immediate_urgent", "immediate_emergency"} else None
        updated = (
            _parse_datetime(row.get("status_changed_date"))
            or _parse_datetime(row.get("date_created"))
            or datetime.now(timezone.utc)
        )

        events.append(Event(
            id=f"street-manager:{source_id}",
            type="road",
            name=f"{street} — {reason}"[:220],
            subtitle=subtitle[:350],
            lat=lat,
            lon=lon,
            category=f"Street Manager • {category}",
            status=status,
            severity=severity,
            road=street,
            reason=reason,
            start_time=_parse_datetime(row.get("actual_start_date") or row.get("proposed_start_date")),
            end_time=_parse_datetime(row.get("actual_end_date") or row.get("proposed_end_date")),
            position_method="static",
            position_label="DfT Street Manager reported work location (BNG geometry)",
            source="DfT Street Manager",
            source_id=source_id,
            official_url=STREET_MANAGER_GUIDANCE_URL,
            news_query=query,
            social_query=query,
            interesting=severity == "High" or management in {"Road closure", "Contra flow", "Lane closure"},
            last_updated=updated,
        ))
    return list({event.id: event for event in events}.values())


class StreetManagerReportingClient:
    """Minimal authenticated client for Street Manager's V6 Reporting API."""

    def __init__(
        self,
        jwt_token: str,
        *,
        base_url: str = PRODUCTION_REPORTING_URL,
        timeout: float = 25,
        session: requests.Session | None = None,
    ) -> None:
        token = str(jwt_token or "").strip()
        if not token:
            raise ValueError("a Street Manager API-user JWT is required")
        self._token = token
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = session or requests.Session()

    def _get(self, resource: str, params: Mapping[str, Any]) -> dict[str, Any]:
        response = self.session.get(
            f"{self.base_url}/{resource.lstrip('/')}",
            params=dict(params),
            headers={
                "token": self._token,
                "Accept": "application/json",
                "User-Agent": "UKActivityRadar/StreetManager-V6",
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("rows"), list):
            raise ValueError("Street Manager Reporting API returned an unexpected response")
        return payload

    def fetch_permit_rows(
        self,
        *,
        filters: Mapping[str, Any] | None = None,
        max_pages: int = 20,
    ) -> list[dict[str, Any]]:
        """Fetch paginated organisation-scoped permit summaries.

        ``max_pages`` is a hard safety ceiling because the API caps
        ``total_rows`` metadata at 501 and protects its environments with
        unpublished rate controls.
        """

        if max_pages < 1:
            raise ValueError("max_pages must be at least one")
        base_params = dict(filters or {})
        base_params.setdefault("active_permit_only", True)
        base_params.setdefault("include_total_count", True)
        rows: list[dict[str, Any]] = []
        offset = 0
        for _ in range(max_pages):
            params = {**base_params, "offset": offset}
            payload = self._get("permits", params)
            page_rows = _payload_rows(payload)
            rows.extend(page_rows)
            pagination = payload.get("pagination")
            has_next = bool(pagination.get("has_next_page")) if isinstance(pagination, Mapping) else False
            if not has_next or not page_rows:
                break
            offset += len(page_rows)
        return rows

    def fetch_events(
        self,
        *,
        filters: Mapping[str, Any] | None = None,
        max_pages: int = 20,
    ) -> list[Event]:
        return parse_permit_payload(
            {"rows": self.fetch_permit_rows(filters=filters, max_pages=max_pages)},
            active_only=True,
        )
