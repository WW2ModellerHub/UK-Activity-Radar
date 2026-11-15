"""Station and operator pages: readable, live, linked and in the sitemap."""
from datetime import datetime, timezone

from fastapi.testclient import TestClient

import app.main as main
from app.models import Vehicle
from app.seo_pages import crs_from_path, directory, station_path, tidy_name
from app.state import LiveState


class FakePlaces:
    _rail = [
        {"name": "Rugby Rail Station", "crs": "RUG", "lat": 52.379, "lon": -1.250},
        {"name": "Nuneaton Rail Station", "crs": "NUN", "lat": 52.526, "lon": -1.464},
        {"name": "Coventry Rail Station", "crs": "COV", "lat": 52.401, "lon": -1.513},
    ]

    def rail_places(self, bbox, limit):
        return [{"name": "LONDON EUSTON", "crs": "EUS", "lat": 51.528, "lon": -0.134},
                {"name": "RUGBY", "crs": "RUG", "lat": 52.379, "lon": -1.250}]

    def rail_board(self, crs, limit):
        return {"departures": [
            {"scheduled": "17:15", "destination": "BIRMINGHAM NEW STREET", "platform": "1", "operator_name": "Avanti West Coast", "delay_minutes": 0, "minutes": 3},
            {"scheduled": "17:16", "expected": "2026-11-15T17:24:00+00:00", "destination": "LONDON EUSTON", "platform": "5", "operator_name": "West Midlands Trains", "delay_minutes": 8, "minutes": 4},
            {"scheduled": "17:30", "destination": "CREWE", "operator_name": "West Midlands Trains", "status": "Cancelled", "cancelled": True, "minutes": 18},
            {"scheduled": "17:40", "destination": "NORTHAMPTON", "operator_name": "West Midlands Trains", "minutes": 28, "usual_platform": {"platform": "6", "seen": 5, "of": 6}},
        ]}


def _client(monkeypatch):
    state = LiveState()
    now = datetime.now(timezone.utc)
    for i in range(6):
        state.upsert(Vehicle(id=f"nr:{i}", type="train", name=f"1A{i:02d}", lat=52, lon=-1, operator="Avanti West Coast",
                             delay_minutes=12 if i < 4 else 0, destination="MANCHESTER PICCADILLY", source="Network Rail TRUST", last_updated=now))
    monkeypatch.setattr(main, "state", state)
    monkeypatch.setattr(main, "smart_places", FakePlaces())
    monkeypatch.setattr(main, "_station_directory", (-1e9, []))
    monkeypatch.setattr(main, "_operator_status_cache", (-1e9, []))
    monkeypatch.setattr(main, "_seo_page_cache", {})
    return TestClient(main.app)


def test_names_paths_and_codes():
    assert tidy_name("Rugby Rail Station") == "Rugby"
    assert tidy_name("LONDON EUSTON") == "London Euston"
    assert tidy_name("STRATFORD-UPON-AVON") == "Stratford-upon-Avon"
    assert tidy_name("NEWTON-LE-WILLOWS") == "Newton-le-Willows"
    assert station_path("London Euston", "EUS") == "/station/london-euston-eus"
    assert crs_from_path("london-euston-eus") == "EUS" and crs_from_path("eus") == "EUS" and crs_from_path("x") is None
    rows = directory(FakePlaces._rail + FakePlaces().rail_places(None, 0))
    assert [row["name"] for row in rows] == ["Coventry", "London Euston", "Nuneaton", "Rugby"]


def test_a_station_page_reads_well_with_live_departures(monkeypatch):
    client = _client(monkeypatch)
    page = client.get("/station/rugby-rug")
    assert page.status_code == 200
    html = page.text
    assert "<title>Rugby live departures and delays | UK Activity Radar</title>" in html
    assert "<h1>Rugby departures</h1>" in html
    assert "1 of the next 4 trains are on time, 1 running late, 1 cancelled" in html
    assert "Birmingham New Street" in html and "Expected 17:24" in html and "Cancelled" in html
    assert "usually 6" in html
    assert 'href="/?place=rail-station:RUG"' in html
    assert 'href="/station/nuneaton-nun"' in html and 'href="/station/coventry-cov"' in html   # nearby, crawlable
    assert 'href="/operator/avanti-west-coast"' in html and "Severe delays" in html
    assert '"@type": "TrainStation"' in html and 'rel="canonical" href="http://testserver/station/rugby-rug"' in html
    # Other spellings send people to the one address.
    moved = client.get("/station/rug", follow_redirects=False)
    assert moved.status_code == 301 and moved.headers["location"] == "/station/rugby-rug"
    assert client.get("/station/nowhere-zzz").status_code == 404


def test_operator_pages_and_indexes(monkeypatch):
    client = _client(monkeypatch)
    page = client.get("/operator/avanti-west-coast").text
    assert "<h1>Avanti West Coast today</h1>" in page and "Severe delays" in page
    assert "4 of 6 trains running are 5 or more minutes late" in page and "12 min late" in page
    quiet = client.get("/operator/merseyrail").text
    assert "Merseyrail today" in quiet and "No trains reporting" in quiet
    assert client.get("/operator/not-a-railway").status_code == 404
    stations = client.get("/stations").text
    assert 'href="/station/london-euston-eus"' in stations and "4 stations" in stations
    assert 'href="/operator/avanti-west-coast"' in client.get("/operators").text


def test_the_sitemap_lists_every_station_and_operator(monkeypatch):
    client = _client(monkeypatch)
    sitemap = client.get("/sitemap.xml").text
    for path in ("/stations", "/operators", "/station/rugby-rug", "/station/london-euston-eus", "/operator/lner"):
        assert f"http://testserver{path}</loc>" in sitemap
