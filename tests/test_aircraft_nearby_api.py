from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app import main
from app.models import Vehicle
from app.state import LiveState


def test_nearby_aircraft_returns_retained_map_positions_when_point_feed_fails(monkeypatch):
    class BrokenPointFeed:
        def fetch_nearby(self, lat, lon, radius_nm):
            raise TimeoutError("provider did not answer")

        def retained_nearby(self, lat, lon, radius_miles):
            raise RuntimeError("rotating cache is still starting")

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


def test_nearby_aircraft_reports_the_live_provider_without_exposing_its_key(monkeypatch):
    aircraft = Vehicle(
        id="aircraft:live", type="aircraft", name="BAW123",
        lat=52.55, lon=-1.35, source="FlyItalyADSB",
        position_method="gps", position_label="Live ADS-B position",
        last_updated=datetime.now(timezone.utc),
    )

    class HealthyPointFeed:
        def fetch_nearby(self, lat, lon, radius_nm):
            return [aircraft], 1, "FlyItalyADSB"

    monkeypatch.setattr(main, "adsb_sar_collector", HealthyPointFeed())
    monkeypatch.setattr(main, "_enforce_public_budget", lambda request, budget: None)

    response = TestClient(main.app).get(
        "/api/aircraft/nearby",
        params={"lat": 52.54, "lon": -1.37, "radius_miles": 50},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["degraded"] is False
    assert payload["source"] == "FlyItalyADSB point feed"
    assert [row["id"] for row in payload["activities"]] == ["aircraft:live"]


def test_a_zero_altitude_is_kept_and_ground_or_missing_is_none():
    # 0 ft is a real reading (Birmingham's runway on a high-pressure day).
    # `alt_baro or alt_geom` dropped it and fell through to the GNSS height.
    from app.collectors.adsb_lol import parse_nearby_aircraft_response
    now = datetime(2026, 11, 22, 17, 0, tzinfo=timezone.utc)
    base = {"lat": 52.45, "lon": -1.74, "gs": 122, "flight": "EZY682G", "seen_pos": 0}
    planes, _ = parse_nearby_aircraft_response({"ac": [
        {**base, "hex": "aaa001", "alt_baro": 0, "alt_geom": 475},
        {**base, "hex": "aaa002", "alt_baro": "ground", "alt_geom": 475},
        {**base, "hex": "aaa003", "alt_geom": 475},
        {**base, "hex": "aaa004"},
    ]}, now=now)
    by_hex = {plane.source_id: plane for plane in planes}
    assert by_hex["aaa001"].altitude_ft == 0 and by_hex["aaa001"].status == "Airborne"
    assert by_hex["aaa002"].altitude_ft is None and by_hex["aaa002"].status == "On ground"
    assert by_hex["aaa003"].altitude_ft == 475
    assert by_hex["aaa004"].altitude_ft is None


def test_bus_route_lines_are_matched_cached_and_limited(monkeypatch):
    from types import SimpleNamespace
    calls = []

    class Timetables:
        def journey_for_vehicle(self, vehicle, limit=3):
            calls.append(vehicle.id)
            return {"shape": [[52.0 + i / 1000, -1.0] for i in range(400)]}

    monkeypatch.setattr(main, "smart_places", SimpleNamespace(bus_timetables=Timetables()))
    main._bus_shape_cache.clear()
    live = LiveState()
    for index in range(12):
        live.upsert(Vehicle(id=f"bus:X:{index}", type="bus", name="Bus 7", lat=52.0, lon=-1.0, route="7",
                            destination="Town", last_updated=datetime.now(timezone.utc)))
    monkeypatch.setattr(main, "state", live)
    client = TestClient(main.app)
    ids = ",".join(f"bus:X:{index}" for index in range(12))
    first = client.get(f"/api/buses/shapes?ids={ids}").json()["shapes"]
    assert len(first) == 8 and len(calls) == 8                 # at most 8 fresh matches a request
    assert all(len(line) <= 150 for line in first.values())    # thinned
    second = client.get(f"/api/buses/shapes?ids={ids}").json()["shapes"]
    assert len(second) == 12 and len(calls) == 12              # the first 8 from the cache
    assert client.get("/api/buses/shapes?ids=nope").json()["shapes"] == {}
