"""Runways in use from live planes, or from the wind."""
from types import SimpleNamespace

from fastapi.testclient import TestClient

import app.main as main
from app.airport_pages import AIRPORTS
from app.airport_runways import MetarFeed, parse_wind, runway_ends, runways_in_use, summary

LHR = next(a for a in AIRPORTS if a["iata"] == "LHR")
BHX = next(a for a in AIRPORTS if a["iata"] == "BHX")


def plane(lat, lon, heading, rate, altitude=1000, speed=140):
    return SimpleNamespace(type="aircraft", lat=lat, lon=lon, bearing=heading, vertical_rate_fpm=rate,
                           altitude_ft=altitude, speed=speed)


def test_runway_ends_and_wind_are_read_from_plain_text():
    assert [e["name"] for e in runway_ends(LHR)] == ["09L", "27R", "09R", "27L"]
    assert [e["heading"] for e in runway_ends(BHX)] == [150, 330]
    assert parse_wind("EGLL 231650Z 25012G22KT 9999") == {"direction": 250, "speed_kt": 12, "gust_kt": 22}
    assert parse_wind("EGBB 231650Z VRB03KT CAVOK")["direction"] is None
    assert parse_wind("no wind group") is None


def test_heathrow_westerly_lands_on_the_south_runway_and_departs_the_north():
    planes = [plane(51.4648, -0.38, 270, -700), plane(51.4648, -0.40, 269, -700),     # south of centre, inbound
              plane(51.4775, -0.50, 271, 2500, altitude=900, speed=160)]               # north of centre, climbing
    state = runways_in_use(LHR, planes, parse_wind("EGLL 25012KT"))
    assert (state["landing"], state["departing"], state["basis"]) == ("27L", "27R", "traffic")
    assert summary(state) == "Landing 27L, departing 27R · Wind from 250° at 12 kt"


def test_a_single_runway_seen_one_way_is_used_both_ways():
    state = runways_in_use(BHX, [plane(52.47, -1.77, 150, -700)])
    assert summary(state) == "Using runway 15"


def test_cruising_and_far_away_planes_do_not_count():
    high = plane(52.46, -1.75, 150, -700, altitude=12000, speed=300)
    far = plane(53.2, -1.75, 150, -700)
    assert runways_in_use(BHX, [high, far])["basis"] is None


def test_with_no_traffic_the_wind_says_which_runway_to_expect():
    state = runways_in_use(BHX, [], parse_wind("EGBB 14008KT"))
    assert summary(state) == "Expect runway 15 (into the wind) · Wind from 140° at 8 kt"
    calm = runways_in_use(BHX, [], parse_wind("EGBB 00000KT"))
    assert summary(calm) == "Calm"


def test_metar_feed_keeps_the_last_good_report_when_a_fetch_fails():
    class Feed(MetarFeed):
        replies = [[{"icaoId": "EGBB", "rawOb": "EGBB 231650Z 33006KT 9999", "reportTime": "2026-11-23T16:50:00Z"}]]
        def _get(self, icaos):
            if not self.replies:
                raise OSError("offline")
            return self.replies.pop(0)
    feed = Feed()
    assert feed.report("EGBB", ["EGBB"])["wind"]["direction"] == 330
    feed._fetched_at = float("-inf")
    assert feed.report("EGBB", ["EGBB"])["wind"]["direction"] == 330   # failure keeps the last report


def test_runways_api_and_airport_page_show_the_runways(monkeypatch):
    class Collector:
        def fetch_nearby(self, lat, lon, radius):
            return [SimpleNamespace(type="aircraft", lat=52.47, lon=-1.77, bearing=150, vertical_rate_fpm=-700,
                                    altitude_ft=1200, speed=140, name="EZY1", status="Airborne",
                                    aircraft_type_code="A320", aircraft_model=None, origin=None, destination=None,
                                    operator=None)], 1, "test"
    monkeypatch.setattr(main, "adsb_sar_collector", Collector())
    monkeypatch.setattr(main, "_metar_feed", None)
    client = TestClient(main.app)
    state = client.get("/api/airports/bhx/runways").json()
    assert state["summary"] == "Using runway 15" and state["basis"] == "traffic"
    assert client.get("/api/airports/xxx/runways").status_code == 404
    main._seo_page_cache.clear() if hasattr(main, "_seo_page_cache") else None
    page = client.get("/airport/birmingham-bhx").text
    assert "Runways now:" in page and "Using runway 15" in page
