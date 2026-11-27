from types import SimpleNamespace

import pytest
from fastapi import HTTPException
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


def test_journey_route_uses_configured_tomtom_for_driving(monkeypatch):
    class Planner:
        configured = True

        def plan(self, *args, **kwargs):
            assert args[:4] == (52.4, -1.5, 51.5, -0.1)
            assert args[4] == "car"
            assert kwargs["from_name"] == "Coventry"
            return {"provider": "TomTom Routing", "status": "ok", "available": True, "itineraries": [{"duration_seconds": 3600}]}

    monkeypatch.setattr(main, "road_journey_planner", Planner())
    payload = main.plan_journey(_request("POST"), main.JourneyPlanIn(
        origin_name="Coventry", destination_name="London",
        origin_lat=52.4, origin_lon=-1.5,
        destination_lat=51.5, destination_lon=-0.1,
    ))

    assert payload["provider"] == "TomTom Routing"
    assert payload["itineraries"]


def test_free_discover_preview_is_balanced_and_reports_full_availability(monkeypatch):
    candidates = [
        Vehicle(id=f"air:{i}", type="aircraft", name=f"Jet {i}", lat=52, lon=-1, cool_category="air", cool_score=100 - i)
        for i in range(12)
    ] + [
        Vehicle(id="rail:1", type="train", name="Railtour", lat=54, lon=-3, cool_category="rail", cool_score=70),
        Event(id="rescue:1", type="rescue", name="Rescue", lat=51, lon=-1, cool_category="rescue", cool_score=60),
    ]

    class State:
        def cool_candidates(self, limit=20, category=None):
            rows = [row for row in candidates if not category or row.cool_category == category]
            return rows[:limit]

    monkeypatch.setattr(main, "state", State())
    monkeypatch.setattr(main, "_billing_context", lambda request: main.AccessContext())

    # While Radar+ cannot be bought, everyone sees everything.
    monkeypatch.setattr(main, "billing_service", None)
    payload = main.cool_activities(_request(), limit=40)
    assert payload["count"] == 14 and payload["full_access"] is True

    # Once it can, free sees ten: one of each kind first, then by score.
    monkeypatch.setattr(main, "billing_service", object())
    payload = main.cool_activities(_request(), limit=40)
    assert payload["count"] == main.FREE_DISCOVER_FINDS == 10
    assert [row["cool_category"] for row in payload["activities"][:3]] == ["air", "rail", "rescue"]
    assert payload["available_count"] == 14
    assert payload["available_counts"] == {"air": 12, "rail": 1, "rescue": 1}
    assert payload["full_access"] is False


def test_journey_route_rejects_public_transport_instead_of_showing_a_broken_option(monkeypatch):
    class Planner:
        configured = False

    monkeypatch.setattr(main, "road_journey_planner", Planner())
    with pytest.raises(HTTPException, match="driving routes only") as exc:
        main.plan_journey(_request("POST"), main.JourneyPlanIn(
            origin_name="HNK", destination_name="BHM", mode="transit",
        ))
    assert exc.value.status_code == 400


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

        def locate_live_train(self, headcode, train_uid=None, service_date=None):
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


def test_launch_preview_grants_plus_without_stripe(monkeypatch):
    code = "launch-preview-code-that-is-long-enough"
    monkeypatch.setattr(main, "settings", _settings(radar_plus_preview_key=code))

    response = main.billing_preview(main.RadarPlusPreviewIn(code=code), _request("POST"))
    cookie = response.headers["set-cookie"]

    assert response.status_code == 200
    assert main.PREVIEW_COOKIE in cookie
    assert "HttpOnly" in cookie
    assert main._preview_context(main._preview_cookie_value()).plus is True


def test_the_server_reports_how_busy_it_is_even_without_a_free_worker_thread():
    import inspect

    from fastapi.testclient import TestClient

    import app.main as main
    assert inspect.iscoroutinefunction(main.health_runtime)   # no worker thread needed
    report = TestClient(main.app).get("/api/health/runtime").json()
    for key in ("uptime_seconds", "memory_mb", "worker_threads_busy", "worker_threads_total", "loop_lag_ms"):
        assert key in report
    detailed = TestClient(main.app).get("/api/health/runtime", params={"detail": "true"}).json()
    assert "over_10km" in detailed["train_movement_quality"]["since_process_start"]
    assert "learned_berth_correction" in detailed["train_movement_quality"]["classification_counts"]
