from types import SimpleNamespace

from starlette.requests import Request

import app.main as main
from app.models import Event, Vehicle


def _request(method: str = "GET") -> Request:
    return Request({
        "type": "http", "method": method, "path": "/", "headers": [],
        "query_string": b"", "client": ("127.0.0.1", 12345),
        "server": ("test", 80), "scheme": "http",
    })


def _settings(**overrides):
    values = {
        "public_deployment": False,
        "otp_graphql_url": "",
        "otp_timeout_seconds": 12,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_multimodal_journey_route_uses_configured_otp(monkeypatch):
    class Planner:
        configured = True

        def plan(self, *args, **kwargs):
            assert args[:4] == (52.4, -1.5, 51.5, -0.1)
            assert args[4] == "transit"
            assert kwargs["from_name"] == "Coventry"
            return {"status": "ok", "available": True, "itineraries": [{"duration_seconds": 3600}]}

    monkeypatch.setattr(main, "settings", _settings(otp_graphql_url="http://otp.test"))
    monkeypatch.setattr(main, "journey_planner", Planner())
    payload = main.plan_journey(_request("POST"), main.JourneyPlanIn(
        origin_name="Coventry", destination_name="London",
        origin_lat=52.4, origin_lon=-1.5,
        destination_lat=51.5, destination_lon=-0.1,
    ))

    assert payload["provider"] == "OpenTripPlanner"
    assert payload["itineraries"]


def test_journey_route_fails_closed_when_commercial_planner_is_absent(monkeypatch):
    class Planner:
        configured = False

    monkeypatch.setattr(main, "settings", _settings())
    monkeypatch.setattr(main, "journey_planner", Planner())
    payload = main.plan_journey(_request("POST"), main.JourneyPlanIn(
        origin_name="HNK", destination_name="BHM", mode="transit",
    ))

    assert payload["provider"] == "OpenTripPlanner"
    assert payload["available"] is False
    assert payload["itineraries"] == []


def test_road_route_attaches_authoritative_nearby_issues(monkeypatch):
    issue=Event(
        id="nh:1", type="incident", name="Lane closure", lat=52.45, lon=-1.45,
        road="M69", reason="Collision", source="National Highways",
    )

    class State:
        def all_events(self, bbox=None):
            assert bbox is not None
            return [issue]

    monkeypatch.setattr(main, "state", State())
    result=main._add_live_route_issues({"itineraries":[{"geometry":[
        {"lat":52.4,"lon":-1.5},{"lat":52.5,"lon":-1.4},
    ]}]})
    matched=result["itineraries"][0]["route_issues"]
    assert matched[0]["id"]=="nh:1"
    assert matched[0]["type"]=="incident"


def test_activity_intelligence_routes_require_matching_live_type(monkeypatch):
    train = Vehicle(id="nr:1", type="train", name="1A01", lat=52, lon=-1)
    bus = Vehicle(id="bus:1", type="bus", name="X1", lat=52, lon=-1)

    class State:
        def get(self, item_id):
            return {train.id: train, bus.id: bus}.get(item_id)

    class Places:
        def train_vehicle_detail(self, item):
            return {"available": True, "runs": [{"train": item.name}]}

        def bus_vehicle_journey(self, item, limit=12):
            return {"mode": "matched_timetable", "route": item.name, "stops": []}

        def locate_live_train(self, headcode, train_uid=None):
            return train if headcode == "1A01" or train_uid == "C12345" else None

    monkeypatch.setattr(main, "settings", _settings())
    monkeypatch.setattr(main, "state", State())
    monkeypatch.setattr(main, "smart_places", Places())

    assert main.activity_rail(_request(), "nr:1")["detail"]["runs"]
    assert main.activity_bus(_request(), "bus:1")["detail"]["mode"] == "matched_timetable"
    assert main.activity_detail(_request(), "nr:1")["id"] == "nr:1"
    located = main.locate_rail_service(_request(), train_uid="C12345", headcode="1A01")
    assert located["live"] is True
    assert located["activity"]["id"] == "nr:1"
