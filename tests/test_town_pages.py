"""Town pages: trains, buses, roads and lines around one place, now."""
from datetime import datetime, timezone

from fastapi.testclient import TestClient

import app.main as main
from app.models import Event, Vehicle
from app.state import LiveState
from app.town_pages import TOWNS, town_path

STATIONS = [
    {"name": "Hinckley", "crs": "HNK", "lat": 52.5383, "lon": -1.3720, "path": "/station/hinckley-hnk"},
    {"name": "Nuneaton", "crs": "NUN", "lat": 52.5265, "lon": -1.4637, "path": "/station/nuneaton-nun"},
    {"name": "Narborough", "crs": "NBR", "lat": 52.5714, "lon": -1.2030, "path": "/station/narborough-nbr"},
]
NOW = datetime.now(timezone.utc)


def test_a_town_page_brings_together_trains_buses_roads_lines_and_the_airport(monkeypatch):
    state = LiveState()
    state.upsert(Vehicle(id="bus:AMID:1", type="bus", name="Bus 158", route="158", lat=52.540, lon=-1.375,
                         source="BODS", last_updated=NOW))
    state.upsert(Vehicle(id="bus:AMID:2", type="bus", name="Bus 48", route="48", lat=52.70, lon=-1.10,
                         source="BODS", last_updated=NOW))                       # far away: not counted
    state.upsert_event(Event(id="road:1", type="road", name="A5 lane closure", lat=52.55, lon=-1.40, source="National Highways",
                       last_updated=NOW))

    class Places:
        def rail_board(self, crs, limit):
            return {"departures": [{"scheduled": "15:30", "destination": "LEICESTER", "status": "On time", "minutes": 10,
                                    "operator_name": "CrossCountry"}]}
    monkeypatch.setattr(main, "state", state)
    monkeypatch.setattr(main, "smart_places", Places())
    monkeypatch.setattr(main, "_stations", lambda: STATIONS * 900)
    page = TestClient(main.app).get(town_path("Hinckley"))
    assert page.status_code == 200
    text = page.text
    assert "Hinckley live transport today" in text and "Leicester" in text and "CrossCountry" in text
    assert "1 bus moving" in text and "routes 158" in text and "A5 lane closure" in text
    assert "Birmingham to Leicester Line" in text and "Birmingham Airport" in text and "1 train due" in text
    assert "/station/nuneaton-nun" in text and text.count('href="/station/hinckley-hnk"') == 2
    assert TestClient(main.app).get("/town/atlantis").status_code == 404


def test_every_town_is_listed_and_in_the_sitemap(monkeypatch):
    monkeypatch.setattr(main, "_stations", lambda: STATIONS * 900)
    client = TestClient(main.app)
    index = client.get("/towns").text
    assert all(town_path(name) in index for name, _, _ in TOWNS)
    assert town_path("Hinckley") in client.get("/sitemap.xml").text
