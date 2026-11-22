"""Airport pages from live aircraft tracking."""
from fastapi.testclient import TestClient

import app.main as main
from app.airport_pages import AIRPORTS, airport_page, airport_path, classify
from app.models import Vehicle

EMA = next(a for a in AIRPORTS if a["iata"] == "EMA")


def _plane(id_, lat, lon, alt, rate=0, origin=None, dest=None, status="Airborne", code="B738"):
    return Vehicle(id=id_, type="aircraft", name=id_, lat=lat, lon=lon, altitude_ft=alt, vertical_rate_fpm=rate,
                   origin=origin, destination=dest, status=status, aircraft_type_code=code, source="FlyItalyADSB")


def test_planes_are_sorted_into_arriving_leaving_ground_and_overhead():
    planes = [
        _plane("RYR1", 52.80, -1.20, 3000, -700, "DUB", "EMA"),      # route says EMA
        _plane("TOM2", 52.84, -1.45, 2500, 1500, "EMA", "PMI"),
        _plane("LOW3", 52.86, -1.40, 2200, -800),                     # no route: low and descending nearby
        _plane("GND4", 52.831, -1.330, 0, 0, status="On ground"),
        _plane("ICE5", 52.91, -1.55, 38000, 0, "CDG", "KEF"),
        _plane("TWR6", 52.83, -1.33, None, code="TWR"),               # airport transmitter, not a plane
    ]
    groups = classify(EMA, planes)
    assert [r["plane"].name for r in groups["arriving"]] == ["LOW3", "RYR1"] or \
        sorted(r["plane"].name for r in groups["arriving"]) == ["LOW3", "RYR1"]
    assert [r["plane"].name for r in groups["departing"]] == ["TOM2"]
    assert [r["plane"].name for r in groups["ground"]] == ["GND4"]
    assert [r["plane"].name for r in groups["overhead"]] == ["ICE5"]
    page = airport_page("https://x.test", EMA, planes)
    assert "2 arriving, 1 departing, 1 on the ground and 1 passing" in page
    assert "not an official arrivals board" in page and "DUB → EMA" in page and "TWR6" not in page


def test_airport_pages_are_served_and_listed(monkeypatch):
    class Feed:
        def fetch_nearby(self, lat, lon, radius):
            return [_plane("RYR1", lat + 0.03, lon, 3000, -700, "DUB", "EMA")], 1, "FlyItalyADSB"
    monkeypatch.setattr(main, "adsb_sar_collector", Feed())
    client = TestClient(main.app)
    page = client.get(airport_path(EMA))
    assert page.status_code == 200 and "East Midlands Airport live aircraft" in page.text and "RYR1" in page.text
    assert client.get("/airport/nowhere-xxx").status_code == 404
    assert airport_path(EMA) in client.get("/airports").text


def test_the_map_airport_list_carries_each_airports_height():
    # A plane on Birmingham's runway reports about 341 ft: the map subtracts
    # the airport's height so it reads "On runway", not "flying at 341 ft".
    rows = TestClient(main.app).get("/api/airports").json()
    assert len(rows) == len(AIRPORTS)
    bhx = next(row for row in rows if row["iata"] == "BHX")
    assert bhx["elevation_ft"] == 341
    assert all(isinstance(row["elevation_ft"], int) and 0 < row["elevation_ft"] < 1000 for row in rows)
