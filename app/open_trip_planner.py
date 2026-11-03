"""Optional OpenTripPlanner 2 multimodal journey-planning client.

This module is deliberately isolated from application startup.  UK Activity
Radar can therefore run normally when no OTP server has been deployed.  A
future FastAPI route can construct :class:`OpenTripPlannerClient` with an
``OTP_GRAPHQL_URL`` value and return :meth:`plan` directly.

OTP 2.8 removed the legacy REST planner.  This client uses the supported GTFS
GraphQL ``planConnection`` query exposed at ``/otp/gtfs/v1`` instead.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import math
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit, urlunsplit

import requests


_GRAPHQL_PATH = "/otp/gtfs/v1"
_ALLOWED_DIRECT_MODES = frozenset({"WALK", "BICYCLE", "CAR", "SCOOTER"})
_ALLOWED_TRANSIT_MODES = frozenset(
    {
        "AIRPLANE",
        "BUS",
        "CABLE_CAR",
        "COACH",
        "FERRY",
        "FUNICULAR",
        "GONDOLA",
        "MONORAIL",
        "RAIL",
        "SUBWAY",
        "TRAM",
        "TROLLEYBUS",
    }
)


PLAN_CONNECTION_QUERY = """
query UKActivityRadarJourney(
  $origin: PlanLabeledLocationInput!
  $destination: PlanLabeledLocationInput!
  $dateTime: PlanDateTimeInput
  $first: Int
  $modes: PlanModesInput
  $preferences: PlanPreferencesInput
  $searchWindow: Duration
) {
  planConnection(
    origin: $origin
    destination: $destination
    dateTime: $dateTime
    first: $first
    modes: $modes
    preferences: $preferences
    searchWindow: $searchWindow
  ) {
    searchDateTime
    routingErrors {
      code
      description
      inputField
    }
    edges {
      cursor
      node {
        start
        end
        duration
        numberOfTransfers
        walkDistance
        walkTime
        waitingTime
        legs {
          mode
          transitLeg
          duration
          distance
          headsign
          from { name lat lon }
          to { name lat lon }
          start {
            scheduledTime
            estimated { time delay }
          }
          end {
            scheduledTime
            estimated { time delay }
          }
          agency { name }
          route { gtfsId shortName longName mode }
          trip { gtfsId tripHeadsign }
          legGeometry { points length }
        }
      }
    }
  }
}
""".strip()


@dataclass(frozen=True)
class JourneyPoint:
    """A labelled WGS84 coordinate used as an OTP route endpoint."""

    lat: float
    lon: float
    label: str = ""

    def __post_init__(self) -> None:
        lat = _finite_float(self.lat, "latitude")
        lon = _finite_float(self.lon, "longitude")
        if not -90.0 <= lat <= 90.0:
            raise ValueError("latitude must be between -90 and 90")
        if not -180.0 <= lon <= 180.0:
            raise ValueError("longitude must be between -180 and 180")
        object.__setattr__(self, "lat", lat)
        object.__setattr__(self, "lon", lon)
        object.__setattr__(self, "label", str(self.label or "").strip()[:160])

    def graphql_value(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "location": {"coordinate": {"latitude": self.lat, "longitude": self.lon}}
        }
        if self.label:
            value["label"] = self.label
        return value


@dataclass(frozen=True)
class JourneyRequest:
    """Validated, intentionally small public routing request contract."""

    origin: JourneyPoint
    destination: JourneyPoint
    when: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    arrive_by: bool = False
    direct_modes: tuple[str, ...] = ("WALK",)
    transit_modes: tuple[str, ...] = ("BUS", "COACH", "RAIL", "SUBWAY", "TRAM", "FERRY")
    max_itineraries: int = 5
    max_transfers: int | None = 3
    wheelchair: bool = False
    search_window_minutes: int | None = 120

    def __post_init__(self) -> None:
        if not isinstance(self.origin, JourneyPoint) or not isinstance(self.destination, JourneyPoint):
            raise TypeError("origin and destination must be JourneyPoint values")
        if not isinstance(self.when, datetime) or self.when.utcoffset() is None:
            raise ValueError("when must be a timezone-aware datetime")

        direct = _normalise_modes(self.direct_modes, _ALLOWED_DIRECT_MODES, "direct")
        transit = _normalise_modes(self.transit_modes, _ALLOWED_TRANSIT_MODES, "transit")
        if not direct and not transit:
            raise ValueError("at least one direct or transit mode is required")
        object.__setattr__(self, "direct_modes", direct)
        object.__setattr__(self, "transit_modes", transit)

        if isinstance(self.max_itineraries, bool) or not 1 <= int(self.max_itineraries) <= 8:
            raise ValueError("max_itineraries must be between 1 and 8")
        object.__setattr__(self, "max_itineraries", int(self.max_itineraries))

        if self.max_transfers is not None:
            if isinstance(self.max_transfers, bool) or not 0 <= int(self.max_transfers) <= 8:
                raise ValueError("max_transfers must be between 0 and 8")
            object.__setattr__(self, "max_transfers", int(self.max_transfers))

        if self.search_window_minutes is not None:
            if (
                isinstance(self.search_window_minutes, bool)
                or not 15 <= int(self.search_window_minutes) <= 360
            ):
                raise ValueError("search_window_minutes must be between 15 and 360")
            object.__setattr__(self, "search_window_minutes", int(self.search_window_minutes))


class OpenTripPlannerClient:
    """Small synchronous client for an independently deployed OTP 2 server.

    Network and provider failures are returned as stable result dictionaries;
    they do not raise through the website request handler.  Invalid caller
    input still raises during dataclass construction so programming errors are
    not silently hidden.
    """

    def __init__(
        self,
        endpoint: str | None,
        *,
        timeout_seconds: float = 12.0,
        session: requests.Session | None = None,
    ) -> None:
        self.endpoint = _normalise_endpoint(endpoint)
        self.timeout_seconds = max(2.0, min(float(timeout_seconds), 30.0))
        self._session = session or requests.Session()
        self._session.headers.update(
            {
                "User-Agent": "UKActivityRadar/1.4 journey-planner",
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Accept-Language": "en-GB,en;q=0.8",
            }
        )

    @property
    def configured(self) -> bool:
        return bool(self.endpoint)

    def status_payload(self) -> dict[str, Any]:
        return {
            "provider": "OpenTripPlanner",
            "api": "GTFS GraphQL planConnection",
            "configured": self.configured,
            "message": (
                "OpenTripPlanner journey routing is configured"
                if self.configured
                else "Journey planning is unavailable until an OpenTripPlanner server is configured"
            ),
        }

    def plan(
        self,
        from_lat: float,
        from_lon: float,
        to_lat: float,
        to_lon: float,
        mode: str = "transit",
        *,
        from_name: str = "",
        to_name: str = "",
        when: datetime | None = None,
        arrive_by: bool = False,
        max_itineraries: int = 5,
        wheelchair: bool = False,
    ) -> dict[str, Any]:
        """Plan a journey using a small FastAPI-friendly input contract.

        ``mode`` is one of ``transit``, ``walk``, ``bike`` or ``car``.  The
        transit profile includes walking access/egress and common UK public
        transport modes.  Advanced callers can construct :class:`JourneyRequest`
        and call :meth:`plan_request` directly.
        """

        profile = str(mode or "").strip().casefold()
        if profile == "transit":
            direct_modes = ("WALK",)
            transit_modes = ("BUS", "COACH", "RAIL", "SUBWAY", "TRAM", "FERRY")
        elif profile == "walk":
            direct_modes, transit_modes = ("WALK",), ()
        elif profile in {"bike", "bicycle"}:
            direct_modes, transit_modes = ("BICYCLE",), ()
        elif profile == "car":
            direct_modes, transit_modes = ("CAR",), ()
        else:
            raise ValueError("mode must be transit, walk, bike, or car")

        route_request = JourneyRequest(
            origin=JourneyPoint(from_lat, from_lon, from_name),
            destination=JourneyPoint(to_lat, to_lon, to_name),
            when=when or datetime.now(timezone.utc),
            arrive_by=arrive_by,
            direct_modes=direct_modes,
            transit_modes=transit_modes,
            max_itineraries=max_itineraries,
            wheelchair=wheelchair,
            max_transfers=3 if profile == "transit" else None,
        )
        return self.plan_request(route_request)

    def plan_request(self, request: JourneyRequest) -> dict[str, Any]:
        """Plan a validated advanced request."""

        if not isinstance(request, JourneyRequest):
            raise TypeError("request must be a JourneyRequest")
        if not self.endpoint:
            return _result(
                request,
                status="unavailable",
                available=False,
                message="Journey planning is not configured yet.",
            )

        variables = _variables(request)
        payload = {
            "operationName": "UKActivityRadarJourney",
            "query": PLAN_CONNECTION_QUERY,
            "variables": variables,
        }
        try:
            response = self._session.post(
                self.endpoint,
                json=payload,
                headers={"OTPTimeout": str(int(self.timeout_seconds * 1000))},
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            body = response.json()
        except (requests.RequestException, ValueError, TypeError) as exc:
            return _result(
                request,
                status="unavailable",
                available=False,
                message=_network_failure_message(exc),
            )

        if not isinstance(body, Mapping):
            return _result(
                request,
                status="error",
                available=True,
                message="Journey planner returned an invalid response.",
            )

        graph_errors = _graphql_errors(body.get("errors"))
        if graph_errors:
            return _result(
                request,
                status="error",
                available=True,
                message="Journey planner could not process this request.",
                routing_errors=graph_errors,
            )

        data = body.get("data")
        plan = data.get("planConnection") if isinstance(data, Mapping) else None
        if not isinstance(plan, Mapping):
            return _result(
                request,
                status="error",
                available=True,
                message="Journey planner returned no routing result.",
            )

        routing_errors = _routing_errors(plan.get("routingErrors"))
        itineraries = _itineraries(plan.get("edges"))
        if itineraries:
            status, message = "ok", f"Found {len(itineraries)} journey option(s)."
        else:
            status = "no_route"
            message = "No journey could be found for those places and time."

        return _result(
            request,
            status=status,
            available=True,
            message=message,
            itineraries=itineraries,
            routing_errors=routing_errors,
            search_datetime=_optional_string(plan.get("searchDateTime")),
        )


def _finite_float(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a number") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} must be finite")
    return number


def _normalise_modes(values: Iterable[str], allowed: frozenset[str], label: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise ValueError(f"{label}_modes must be a sequence")
    modes = tuple(dict.fromkeys(str(value).strip().upper() for value in values if str(value).strip()))
    unknown = sorted(set(modes) - allowed)
    if unknown:
        raise ValueError(f"unsupported {label} mode(s): {', '.join(unknown)}")
    return modes


def _normalise_endpoint(endpoint: str | None) -> str | None:
    raw = str(endpoint or "").strip()
    if not raw:
        return None
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("OTP endpoint must be an absolute http(s) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("OTP endpoint must not contain credentials, a query, or a fragment")
    path = parsed.path.rstrip("/")
    if not path:
        path = _GRAPHQL_PATH
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _variables(request: JourneyRequest) -> dict[str, Any]:
    when = request.when.isoformat()
    date_time_key = "latestArrival" if request.arrive_by else "earliestDeparture"
    modes: dict[str, Any] = {"direct": list(request.direct_modes)}
    if request.transit_modes:
        modes["transit"] = {
            "access": ["WALK"],
            "egress": ["WALK"],
            "transfer": ["WALK"],
            "transit": [{"mode": mode} for mode in request.transit_modes],
        }

    preferences: dict[str, Any] = {}
    if request.wheelchair:
        preferences["accessibility"] = {"wheelchair": {"enabled": True}}
    if request.max_transfers is not None:
        preferences.setdefault("transit", {})["transfer"] = {
            "maximumTransfers": request.max_transfers
        }

    return {
        "origin": request.origin.graphql_value(),
        "destination": request.destination.graphql_value(),
        "dateTime": {date_time_key: when},
        "first": request.max_itineraries,
        "modes": modes,
        "preferences": preferences or None,
        "searchWindow": (
            f"PT{request.search_window_minutes}M"
            if request.search_window_minutes is not None
            else None
        ),
    }


def _graphql_errors(value: Any) -> list[dict[str, str | None]]:
    if not isinstance(value, list):
        return []
    errors: list[dict[str, str | None]] = []
    for row in value[:8]:
        if not isinstance(row, Mapping):
            continue
        # GraphQL validation errors are operational/developer details and can
        # contain internal hostnames or stack context.  Keep the public shape
        # useful without reflecting the upstream message into the browser.
        errors.append(
            {
                "code": "GRAPHQL_ERROR",
                "description": "The journey planner rejected the routing request.",
                "input_field": None,
            }
        )
    return errors


def _network_failure_message(exc: BaseException) -> str:
    """Return actionable provider state without exposing the private OTP URL."""

    if isinstance(exc, requests.Timeout):
        detail = "The routing server timed out."
    elif isinstance(exc, requests.exceptions.ProxyError):
        detail = "The configured outbound proxy could not be reached."
    elif isinstance(exc, requests.HTTPError):
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
        detail = f"The routing server returned HTTP {status}." if status else "The routing server returned an error."
    elif isinstance(exc, requests.ConnectionError):
        detail = "The routing server could not be reached."
    else:
        detail = "The routing server returned an invalid response."
    return f"Journey planner is temporarily unavailable. {detail}"


def _routing_errors(value: Any) -> list[dict[str, str | None]]:
    if not isinstance(value, list):
        return []
    errors: list[dict[str, str | None]] = []
    for row in value[:20]:
        if not isinstance(row, Mapping):
            continue
        errors.append(
            {
                "code": _optional_string(row.get("code")) or "ROUTING_ERROR",
                "description": (_optional_string(row.get("description")) or "Routing failed")[:240],
                "input_field": _optional_string(row.get("inputField")),
            }
        )
    return errors


def _itineraries(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    output: list[dict[str, Any]] = []
    for edge in value[:8]:
        if not isinstance(edge, Mapping) or not isinstance(edge.get("node"), Mapping):
            continue
        node = edge["node"]
        legs = [_normalise_leg(leg) for leg in (node.get("legs") or []) if isinstance(leg, Mapping)]
        output.append(
            {
                "cursor": _optional_string(edge.get("cursor")),
                "depart_at": _optional_string(node.get("start")),
                "arrive_at": _optional_string(node.get("end")),
                "duration_seconds": _optional_number(node.get("duration")),
                "transfers": _optional_int(node.get("numberOfTransfers")),
                "walk_distance_m": _optional_number(node.get("walkDistance")),
                "walk_time_seconds": _optional_int(node.get("walkTime")),
                "waiting_time_seconds": _optional_int(node.get("waitingTime")),
                "legs": legs,
            }
        )
    return output


def _normalise_leg(leg: Mapping[str, Any]) -> dict[str, Any]:
    start = leg.get("start") if isinstance(leg.get("start"), Mapping) else {}
    end = leg.get("end") if isinstance(leg.get("end"), Mapping) else {}
    start_estimate = start.get("estimated") if isinstance(start.get("estimated"), Mapping) else {}
    end_estimate = end.get("estimated") if isinstance(end.get("estimated"), Mapping) else {}
    route = leg.get("route") if isinstance(leg.get("route"), Mapping) else {}
    trip = leg.get("trip") if isinstance(leg.get("trip"), Mapping) else {}
    agency = leg.get("agency") if isinstance(leg.get("agency"), Mapping) else {}
    geometry = leg.get("legGeometry") if isinstance(leg.get("legGeometry"), Mapping) else {}
    return {
        "mode": _optional_string(leg.get("mode")),
        "transit": bool(leg.get("transitLeg")),
        "duration_seconds": _optional_number(leg.get("duration")),
        "distance_m": _optional_number(leg.get("distance")),
        "headsign": _optional_string(leg.get("headsign")),
        "from": _place(leg.get("from")),
        "to": _place(leg.get("to")),
        "depart_at": _optional_string(start_estimate.get("time") or start.get("scheduledTime")),
        "scheduled_departure": _optional_string(start.get("scheduledTime")),
        "departure_delay": _optional_string(start_estimate.get("delay")),
        "arrive_at": _optional_string(end_estimate.get("time") or end.get("scheduledTime")),
        "scheduled_arrival": _optional_string(end.get("scheduledTime")),
        "arrival_delay": _optional_string(end_estimate.get("delay")),
        "agency": _optional_string(agency.get("name")),
        "route": {
            "id": _optional_string(route.get("gtfsId")),
            "short_name": _optional_string(route.get("shortName")),
            "long_name": _optional_string(route.get("longName")),
            "mode": _optional_string(route.get("mode")),
        }
        if route
        else None,
        "trip": {
            "id": _optional_string(trip.get("gtfsId")),
            "headsign": _optional_string(trip.get("tripHeadsign")),
        }
        if trip
        else None,
        "geometry": {
            "encoded_polyline": _optional_string(geometry.get("points")),
            "point_count": _optional_int(geometry.get("length")),
        }
        if geometry
        else None,
    }


def _place(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    return {
        "name": _optional_string(value.get("name")),
        "lat": _optional_number(value.get("lat")),
        "lon": _optional_number(value.get("lon")),
    }


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_number(value: Any) -> float | int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return int(number) if number.is_integer() else number


def _optional_int(value: Any) -> int | None:
    number = _optional_number(value)
    return int(number) if number is not None else None


def _result(
    request: JourneyRequest,
    *,
    status: str,
    available: bool,
    message: str,
    itineraries: list[dict[str, Any]] | None = None,
    routing_errors: list[dict[str, str | None]] | None = None,
    search_datetime: str | None = None,
) -> dict[str, Any]:
    return {
        "provider": "OpenTripPlanner",
        "api": "GTFS GraphQL planConnection",
        "status": status,
        "available": available,
        "message": message,
        "search_datetime": search_datetime,
        "request": {
            "origin": {
                "name": request.origin.label or None,
                "lat": request.origin.lat,
                "lon": request.origin.lon,
            },
            "destination": {
                "name": request.destination.label or None,
                "lat": request.destination.lat,
                "lon": request.destination.lon,
            },
            "arrive_by": request.arrive_by,
        },
        "itineraries": itineraries or [],
        "routing_errors": routing_errors or [],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
