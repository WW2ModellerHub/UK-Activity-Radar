from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app import main
from app.models import Vehicle
from app.state import LiveState


def test_nearby_aircraft_returns_retained_map_positions_when_point_feed_fails(monkeypatch):
    class BrokenPointFeed:
        def fetch_nearby(self, lat, lon, radius_nm):
            raise TimeoutError("provider did not answer")

    retained_state = LiveState()
    retained_state.upsert(Vehicle(
        id="aircraft:retained", type="aircraft", name="RRR01",
        lat=52.55, lon=-1.35, source="ADSB.lol",
        position_method="gps", position_label="Retained ADS-B position",
        interesting=True, last_updated=datetime.now(timezone.utc),
    ))
    # Outside the requested radius and therefore never leaked into the result.
    retained_state.upsert(Vehicle(
        id="aircraft:distant", type="aircraft", name="DISTANT",
        lat=55.0, lon=-4.0, source="ADSB.lol",
        position_method="gps", last_updated=datetime.now(timezone.utc),
    ))

    monkeypatch.setattr(main, "state", retained_state)
    monkeypatch.setattr(main, "adsb_sar_collector", BrokenPointFeed())
    monkeypatch.setattr(main, "_enforce_public_budget", lambda request, budget: None)

    response = TestClient(main.app).get(
        "/api/aircraft/nearby",
        params={"lat": 52.54, "lon": -1.37, "radius_miles": 50},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["degraded"] is True
    assert payload["source"] == "Retained UK aircraft feed"
    assert "retrying" in payload["warning"]
    assert [row["id"] for row in payload["activities"]] == ["aircraft:retained"]
