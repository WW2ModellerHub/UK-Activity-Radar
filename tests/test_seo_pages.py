"""Station and operator pages: readable, live, linked and in the sitemap."""
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone

from fastapi.testclient import TestClient

import app.main as main
from app.models import Vehicle
from app.seo_pages import _shell, crs_from_path, directory, station_path, tidy_name
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
    monkeypatch.setattr(main, "_seo_build_locks", {})
    monkeypatch.setattr(main, "_seo_heavy_build_slots", threading.BoundedSemaphore(1))
    monkeypatch.setattr(main, "_seo_cache_lock", threading.Lock())
    monkeypatch.setattr(main, "settings", replace(main.settings, public_deployment=False))
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
    assert "1 of the next 4 trains are on time or within 5 minutes, 1 running late, 1 cancelled" in html
    assert "Birmingham New Street" in html and "Expected 17:24" in html and "Cancelled" in html
    assert "usually 6" in html
    assert 'href="/?place=rail-station:RUG"' in html
    assert 'href="/station/nuneaton-nun"' in html and 'href="/station/coventry-cov"' in html   # nearby, crawlable
    assert 'href="/operator/avanti-west-coast"' in html and "Severe delays" in html
    structured = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', html).group(1))
    assert {node["@type"] for node in structured["@graph"]} == {"TrainStation", "BreadcrumbList"}
    assert 'rel="canonical" href="http://testserver/station/rugby-rug"' in html
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
    assert 'href="/stations/l"' in stations and "4 stations" in stations
    assert 'href="/station/' not in stations and 'href="#' not in stations
    london = client.get("/stations/l").text
    assert 'href="/station/london-euston-eus"' in london
    assert "Rugby" not in london and "Coventry" not in london
    hub_links = set()
    for letter in ("c", "l", "n", "r"):
        hub_links.update(re.findall(r'href="(/station/[^"]+)"', client.get(f"/stations/{letter}").text))
    assert hub_links == {"/station/coventry-cov", "/station/london-euston-eus", "/station/nuneaton-nun", "/station/rugby-rug"}
    assert client.get("/stations/x").status_code == 404
    moved = client.get("/stations/L", follow_redirects=False)
    assert moved.status_code == 301 and moved.headers["location"] == "/stations/l"
    assert 'href="/operator/avanti-west-coast"' in client.get("/operators").text


def test_the_sitemap_lists_every_station_and_operator(monkeypatch):
    client = _client(monkeypatch)
    sitemap = client.get("/sitemap.xml").text
    for path in ("/stations", "/stations/l", "/operators", "/guides", "/guides/how-live-train-positions-work",
                 "/guides/how-ads-b-aircraft-tracking-works",
                 "/station/rugby-rug", "/station/london-euston-eus", "/operator/lner"):
        assert f"http://testserver{path}</loc>" in sitemap
        assert sitemap.count(f"http://testserver{path}</loc>") == 1


def test_guides_are_crawlable_useful_pages(monkeypatch):
    client = _client(monkeypatch)
    index = client.get("/guides")
    assert index.status_code == 200
    assert 'href="/guides/how-live-train-positions-work"' in index.text
    train = client.get("/guides/how-live-train-positions-work")
    assert train.status_code == 200
    assert "How live UK train positions work" in train.text
    assert "A report, not a continuous GPS dot" in train.text
    assert 'rel="canonical" href="http://testserver/guides/how-live-train-positions-work"' in train.text
    aircraft = client.get("/guides/how-ads-b-aircraft-tracking-works")
    assert aircraft.status_code == 200
    assert "How ADS-B aircraft tracking works in the UK" in aircraft.text
    assert "Why coverage changes" in aircraft.text
    for response in (train, aircraft):
        structured = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', response.text).group(1))
        assert {node["@type"] for node in structured["@graph"]} == {"Article", "BreadcrumbList"}
        assert 'name="robots" content="index,follow' in response.text
    assert client.get("/guides/not-real").status_code == 404


def test_json_ld_preserves_real_text_and_cannot_end_the_script():
    value = {"name": "A & B </script> \u2028 railway"}
    html = _shell(title="Test", description="Test", canonical="https://example.test/test", body="<h1>Test</h1>", json_ld=value)
    encoded = re.search(r'<script type="application/ld\+json">(.*?)</script>', html).group(1)
    assert "&amp;" not in encoded and "</script>" not in encoded
    assert json.loads(encoded) == value


def test_a_bare_train_still_previews_with_something_useful(monkeypatch):
    from app.main import _share_preview_text
    title, description = _share_preview_text({"type": "train", "name": "1H71", "near": "Near Rugby"})
    assert title == "1H71 · Near Rugby"
    assert description == "Live train near Rugby · follow it on the live map"


def test_a_busy_server_serves_a_light_station_page_instead_of_piling_up(monkeypatch):
    client = _client(monkeypatch)
    station_lock = main._seo_lock_for("station:RUG")
    assert station_lock.acquire()      # the same page is already being built
    try:
        page = client.get("/station/rugby-rug")
        assert page.status_code == 200
        assert "Rugby departures" in page.text
        assert "are on the live map" in page.text and "Birmingham New Street" not in page.text
        assert page.headers["cache-control"] == "public, max-age=60"
    finally:
        station_lock.release()
    full = client.get("/station/rugby-rug")
    assert "Birmingham New Street" in full.text and full.headers["cache-control"] == "public, max-age=600"
    # Once built, it is served from the copy even while another build runs.
    assert station_lock.acquire()
    try:
        assert "Birmingham New Street" in client.get("/station/rugby-rug").text
    finally:
        station_lock.release()


def test_one_slow_seo_page_does_not_block_an_unrelated_page(monkeypatch):
    client = _client(monkeypatch)
    station_lock = main._seo_lock_for("station:RUG")
    assert station_lock.acquire()
    try:
        operators = client.get("/operators")
        assert operators.status_code == 200
        assert "UK train operators today" in operators.text
    finally:
        station_lock.release()


def test_waiting_for_the_same_page_does_not_build_it_twice(monkeypatch):
    _client(monkeypatch)
    count = 0
    count_lock = threading.Lock()

    def build():
        nonlocal count
        with count_lock:
            count += 1
        time.sleep(0.05)
        return "<h1>Built once</h1>"

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: main._cached_page("same", 60, build), range(2)))
    assert count == 1
    assert all(response.body == b"<h1>Built once</h1>" for response in responses)


def test_distinct_heavy_pages_are_bounded_and_fall_back_to_light(monkeypatch):
    _client(monkeypatch)
    active = 0
    maximum = 0
    active_lock = threading.Lock()
    started = threading.Event()

    def build():
        nonlocal active, maximum
        with active_lock:
            active += 1
            maximum = max(maximum, active)
            started.set()
        time.sleep(0.08)
        with active_lock:
            active -= 1
        return "<h1>Full page</h1>"

    def render(key):
        started.wait(0.2) if key != "heavy:0" else None
        return main._cached_page(key, 60, build, lambda: "<h1>Light page</h1>", heavy=True)

    with ThreadPoolExecutor(max_workers=5) as pool:
        responses = list(pool.map(render, [f"heavy:{index}" for index in range(5)]))
    assert maximum == 1
    assert all(response.status_code == 200 for response in responses)
    assert sum(response.body == b"<h1>Full page</h1>" for response in responses) == 1


def test_concurrent_cache_eviction_is_bounded(monkeypatch):
    _client(monkeypatch)
    now = time.monotonic()
    main._seo_page_cache.update({f"old:{index}": (now - 100, "old") for index in range(2995)})
    errors = []

    def render(index):
        try:
            return main._cached_page(f"new:{index}", 60, lambda: f"page {index}")
        except Exception as error:  # pragma: no cover - assertion records thread failures
            errors.append(error)
            return None

    with ThreadPoolExecutor(max_workers=12) as pool:
        list(pool.map(render, range(30)))
    assert errors == []
    assert len(main._seo_page_cache) <= 3000


def test_sitemap_never_publishes_a_partial_station_directory(monkeypatch):
    client = _client(monkeypatch)
    monkeypatch.setattr(main, "settings", replace(main.settings, public_deployment=True))
    monkeypatch.setattr(main, "_stations", lambda: [{"name": "Only One", "crs": "ONE", "path": "/station/only-one-one"}])
    response = client.get("/sitemap.xml")
    assert response.status_code == 503
    assert response.headers["retry-after"] == "60"
    assert response.headers["cache-control"] == "no-store"
    assert "<urlset" not in response.text


def test_partial_station_refresh_keeps_the_last_complete_snapshot(monkeypatch):
    complete = [{"name": f"Station {index}", "crs": "AAA", "path": f"/station/{index}"}
                for index in range(main._MIN_PUBLIC_STATION_DIRECTORY)]

    class PartialPlaces:
        _rail = [{"name": "Only One", "crs": "ONE", "lat": 52.0, "lon": -1.0}]

        def rail_places(self, bbox, limit):
            return []

    monkeypatch.setattr(main, "smart_places", PartialPlaces())
    monkeypatch.setattr(main, "_station_directory", (-1e9, complete))
    assert main._stations() is complete
    assert main._station_directory[1] is complete
