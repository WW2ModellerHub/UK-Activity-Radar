from fastapi.testclient import TestClient

from app import main
from app.models import Event


def test_bus_disruptions_returns_only_official_bus_events_inside_view():
    source = "Bus disruptions API fixture"
    bus_event = Event(
        id="bods-sx:test-stop",
        type="incident",
        name="Queens Road stop closed",
        lat=52.04,
        lon=-0.76,
        source=source,
        details={"bus_disruption": True},
        impact={"title": "Bus stop closed", "where": "Queens Road"},
    )
    road_event = Event(
        id="road:test",
        type="incident",
        name="Unrelated road incident",
        lat=52.04,
        lon=-0.76,
        source=source,
    )
    outside = bus_event.model_copy(update={"id": "bods-sx:outside", "lat": 55.0})
    main.state.replace_events_for_source(source, [bus_event, road_event, outside])
    try:
        response = TestClient(main.app).get(
            "/api/buses/disruptions",
            params={"west": -1.0, "south": 51.8, "east": -0.5, "north": 52.2},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["count"] == 1
        assert [row["id"] for row in body["disruptions"]] == ["bods-sx:test-stop"]
    finally:
        main.state.replace_events_for_source(source, [])


def test_bus_disruptions_rejects_invalid_bounds():
    response = TestClient(main.app).get(
        "/api/buses/disruptions",
        params={"west": 1, "south": 52, "east": -1, "north": 53},
    )
    assert response.status_code == 400
