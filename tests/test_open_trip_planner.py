from __future__ import annotations

from datetime import datetime, timezone

import pytest
import requests

from app.open_trip_planner import JourneyPoint, JourneyRequest, OpenTripPlannerClient


WHEN = datetime(2026, 10, 25, 12, 30, tzinfo=timezone.utc)


class StubResponse:
    def __init__(self, body, *, status_code=200):
        self._body = body
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} upstream failure")

    def json(self):
        return self._body


class StubSession:
    def __init__(self, response=None, error=None):
        self.headers = {}
        self.response = response
        self.error = error
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error:
            raise self.error
        return self.response


def request(**overrides):
    values = {
        "origin": JourneyPoint(52.9548, -1.1581, "Nottingham"),
        "destination": JourneyPoint(52.4778, -1.8986, "Birmingham"),
        "when": WHEN,
    }
    values.update(overrides)
    return JourneyRequest(**values)


def test_unconfigured_client_is_a_non_fatal_optional_service():
    session = StubSession()
    client = OpenTripPlannerClient("", session=session)

    result = client.plan_request(request())

    assert result["status"] == "unavailable"
    assert result["available"] is False
    assert result["itineraries"] == []
    assert "not configured" in result["message"]
    assert session.calls == []
    assert client.status_payload()["configured"] is False


def test_endpoint_validation_and_default_graphql_path():
    assert OpenTripPlannerClient("http://otp:8080").endpoint == "http://otp:8080/otp/gtfs/v1"
    assert (
        OpenTripPlannerClient("https://planner.example/otp/gtfs/v1/").endpoint
        == "https://planner.example/otp/gtfs/v1"
    )
    with pytest.raises(ValueError):
        OpenTripPlannerClient("file:///tmp/otp")
    with pytest.raises(ValueError):
        OpenTripPlannerClient("https://user:secret@planner.example/")
    with pytest.raises(ValueError):
        OpenTripPlannerClient("https://planner.example/?token=secret")


@pytest.mark.parametrize(
    ("point", "message"),
    [
        ((91, 0), "latitude"),
        ((0, -181), "longitude"),
        ((float("nan"), 0), "finite"),
    ],
)
def test_journey_point_rejects_invalid_coordinates(point, message):
    with pytest.raises(ValueError, match=message):
        JourneyPoint(*point)


def test_request_rejects_naive_datetimes_and_unknown_modes():
    with pytest.raises(ValueError, match="timezone-aware"):
        request(when=datetime(2026, 10, 25, 12, 30))
    with pytest.raises(ValueError, match="unsupported transit"):
        request(transit_modes=("HOVERCRAFT",))


def test_plan_uses_supported_plan_connection_graphql_contract():
    body = {"data": {"planConnection": {"searchDateTime": WHEN.isoformat(), "routingErrors": [], "edges": []}}}
    session = StubSession(StubResponse(body))
    client = OpenTripPlannerClient("http://otp:8080", timeout_seconds=7, session=session)

    result = client.plan_request(request(wheelchair=True, max_transfers=2))

    assert result["status"] == "no_route"
    url, kwargs = session.calls[0]
    assert url == "http://otp:8080/otp/gtfs/v1"
    assert "planConnection(" in kwargs["json"]["query"]
    assert "/plan" not in url
    variables = kwargs["json"]["variables"]
    assert variables["origin"]["location"]["coordinate"] == {
        "latitude": 52.9548,
        "longitude": -1.1581,
    }
    assert variables["dateTime"] == {"earliestDeparture": "2026-10-25T12:30:00+00:00"}
    assert {row["mode"] for row in variables["modes"]["transit"]["transit"]} >= {
        "BUS",
        "RAIL",
        "FERRY",
    }
    assert variables["preferences"]["accessibility"]["wheelchair"]["enabled"] is True
    assert variables["preferences"]["transit"]["transfer"]["maximumTransfers"] == 2
    assert variables["searchWindow"] == "PT120M"
    assert kwargs["headers"]["OTPTimeout"] == "7000"
    assert kwargs["timeout"] == 7


def test_arrive_by_uses_latest_arrival():
    body = {"data": {"planConnection": {"routingErrors": [], "edges": []}}}
    session = StubSession(StubResponse(body))
    OpenTripPlannerClient("http://otp:8080", session=session).plan_request(request(arrive_by=True))

    variables = session.calls[0][1]["json"]["variables"]
    assert variables["dateTime"] == {"latestArrival": "2026-10-25T12:30:00+00:00"}


def test_success_is_normalised_for_the_frontend_and_prefers_realtime_estimates():
    body = {
        "data": {
            "planConnection": {
                "searchDateTime": "2026-10-25T12:30:00Z",
                "routingErrors": [],
                "edges": [
                    {
                        "cursor": "next-1",
                        "node": {
                            "start": "2026-10-25T12:35:00Z",
                            "end": "2026-10-25T13:35:00Z",
                            "duration": 3600,
                            "numberOfTransfers": 0,
                            "walkDistance": 450.5,
                            "walkTime": 330,
                            "waitingTime": 120,
                            "legs": [
                                {
                                    "mode": "RAIL",
                                    "transitLeg": True,
                                    "duration": 3000,
                                    "distance": 79000,
                                    "headsign": "Birmingham New Street",
                                    "from": {"name": "Nottingham", "lat": 52.947, "lon": -1.146},
                                    "to": {"name": "Birmingham New Street", "lat": 52.477, "lon": -1.9},
                                    "start": {
                                        "scheduledTime": "2026-10-25T12:34:00Z",
                                        "estimated": {"time": "2026-10-25T12:35:00Z", "delay": "PT1M"},
                                    },
                                    "end": {
                                        "scheduledTime": "2026-10-25T13:30:00Z",
                                        "estimated": {"time": "2026-10-25T13:35:00Z", "delay": "PT5M"},
                                    },
                                    "agency": {"name": "Example Rail"},
                                    "route": {
                                        "gtfsId": "uk:rail-1",
                                        "shortName": "XC",
                                        "longName": "Nottingham - Birmingham",
                                        "mode": "RAIL",
                                    },
                                    "trip": {"gtfsId": "uk:trip-1", "tripHeadsign": "Birmingham"},
                                    "legGeometry": {"points": "encoded-polyline", "length": 42},
                                }
                            ],
                        },
                    }
                ],
            }
        }
    }
    result = OpenTripPlannerClient(
        "http://otp:8080", session=StubSession(StubResponse(body))
    ).plan_request(request())

    assert result["status"] == "ok"
    assert result["available"] is True
    itinerary = result["itineraries"][0]
    assert itinerary["duration_seconds"] == 3600
    assert itinerary["transfers"] == 0
    leg = itinerary["legs"][0]
    assert leg["depart_at"] == "2026-10-25T12:35:00Z"
    assert leg["scheduled_departure"] == "2026-10-25T12:34:00Z"
    assert leg["departure_delay"] == "PT1M"
    assert leg["route"]["short_name"] == "XC"
    assert leg["geometry"] == {"encoded_polyline": "encoded-polyline", "point_count": 42}


def test_provider_connection_failure_is_returned_not_raised():
    session = StubSession(error=requests.ConnectionError("connection refused at http://otp.internal:8080"))
    result = OpenTripPlannerClient("http://otp:8080", session=session).plan_request(request())

    assert result["status"] == "unavailable"
    assert result["available"] is False
    assert result["itineraries"] == []
    assert "temporarily unavailable" in result["message"]
    assert "otp.internal" not in result["message"]


def test_graphql_and_routing_errors_have_stable_public_shapes():
    graph_result = OpenTripPlannerClient(
        "http://otp:8080",
        session=StubSession(StubResponse({"errors": [{"message": "Invalid mode"}]})),
    ).plan_request(request())
    assert graph_result["status"] == "error"
    assert graph_result["routing_errors"] == [
        {
            "code": "GRAPHQL_ERROR",
            "description": "The journey planner rejected the routing request.",
            "input_field": None,
        }
    ]

    no_route = OpenTripPlannerClient(
        "http://otp:8080",
        session=StubSession(
            StubResponse(
                {
                    "data": {
                        "planConnection": {
                            "routingErrors": [
                                {
                                    "code": "LOCATION_NOT_FOUND",
                                    "description": "Origin was outside the graph",
                                    "inputField": "ORIGIN",
                                }
                            ],
                            "edges": [],
                        }
                    }
                }
            )
        ),
    ).plan_request(request())
    assert no_route["status"] == "no_route"
    assert no_route["routing_errors"][0]["code"] == "LOCATION_NOT_FOUND"
    assert no_route["routing_errors"][0]["input_field"] == "ORIGIN"


def test_simple_plan_api_builds_frontend_friendly_mode_profiles():
    body = {"data": {"planConnection": {"routingErrors": [], "edges": []}}}
    session = StubSession(StubResponse(body))
    result = OpenTripPlannerClient("http://otp:8080", session=session).plan(
        52.9548,
        -1.1581,
        52.4778,
        -1.8986,
        "bike",
        from_name="Nottingham",
        to_name="Birmingham",
        when=WHEN,
    )

    assert result["status"] == "no_route"
    variables = session.calls[0][1]["json"]["variables"]
    assert variables["modes"] == {"direct": ["BICYCLE"]}
    assert variables["preferences"] is None

    with pytest.raises(ValueError, match="mode must be"):
        OpenTripPlannerClient("").plan(52, -1, 53, -2, "hovercraft", when=WHEN)
