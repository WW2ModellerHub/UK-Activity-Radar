"""Railway line pages: stations in order, live trains near the line."""
from fastapi.testclient import TestClient

import app.main as main
from app.models import Vehicle
from app.named_lines import LINES, line_page, line_path, resolve, track_for, trains_on
from app.seo_pages import slug

STATIONS = [
    {"name": "Coventry", "crs": "COV", "lat": 52.4008, "lon": -1.5135, "path": "/station/coventry-cov"},
    {"name": "Bedworth", "crs": "BEH", "lat": 52.4787, "lon": -1.4672, "path": "/station/bedworth-beh"},
    {"name": "Nuneaton", "crs": "NUN", "lat": 52.5265, "lon": -1.4637, "path": "/station/nuneaton-nun"},
    {"name": "Leicester", "crs": "LEI", "lat": 52.6315, "lon": -1.1253, "path": "/station/leicester-lei"},
]
LINE = next(line for line in LINES if line["name"] == "Coventry to Nuneaton Line")


def _train(id_, lat, lon, delay=0):
    return Vehicle(id=id_, type="train", name=id_, headcode=id_, lat=lat, lon=lon, source="Network Rail",
                   delay_minutes=delay, destination="NUNEATON")


def test_stations_come_in_line_order_and_unknown_names_are_left_out():
    points = resolve(LINE, STATIONS)
    assert [p["crs"] for p in points] == ["COV", "BEH", "NUN"]   # Coventry Arena, Bermuda Park not in this list


def test_only_trains_near_the_line_count_and_in_line_order():
    points = resolve(LINE, STATIONS)
    # Two trains on the real track (one near Coventry, one near Nuneaton),
    # one 2 km off it beside the line, and one far away.
    on = trains_on(points, [_train("2N10", 52.5070, -1.4729, 7), _train("1A01", 52.4124, -1.5249),
                            _train("OFF1", 52.4787, -1.44), _train("FAR1", 52.63, -1.12)], track_for(LINE, points))
    assert [row["train"].headcode for row in on] == ["1A01", "2N10"]
    page = line_page("https://x.test", LINE, points, [row["train"] for row in on])
    assert "2 trains near the line" in page and "1 running five or more minutes late" in page
    assert '/station/bedworth-beh' in page and "FAR1" not in page and "OFF1" not in page
    # A train's panel can say which line it is on, once the line is routed.
    from app.named_lines import lines_for
    assert [line["name"] for line in lines_for(_train("X", 52.5070, -1.4729), STATIONS)] == ["Coventry to Nuneaton Line"]
    assert lines_for(_train("Y", 52.63, -1.12), STATIONS) == []


def test_line_pages_are_served_listed_and_in_the_sitemap(monkeypatch):
    monkeypatch.setattr(main, "_stations", lambda: STATIONS * 700)   # a full-size directory
    monkeypatch.setattr(main, "_live_trains", lambda: [])
    client = TestClient(main.app)
    page = client.get(line_path(LINE["name"]))
    assert page.status_code == 200 and "No trains are reporting" in page.text
    assert client.get("/railway-line/not-a-line").status_code == 404
    assert line_path(LINE["name"]) in client.get("/railway-lines").text
    assert f"/railway-line/{slug(LINES[0]['name'])}" in client.get("/sitemap.xml").text


def test_operators_are_ranked_by_official_punctuality():
    from app.seo_pages import operators_league
    page = operators_league("https://x.test")
    assert "ranked by punctuality" in page and "Most punctual" in page and "Great Britain overall" in page
    assert page.count("<tr><td>") >= 20
    assert TestClient(main.app).get("/operators/league-table").status_code == 200
