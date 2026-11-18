import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import requests

from app.collectors.adsb_lol import ADSBLolSARCollector, parse_adsb_response, parse_nearby_aircraft_response
from app.collectors.london_fire import _parse_dt, parse_lfb_incidents
from app.collectors.met_office import parse_warning_geojson, parse_warning_rss
from app.collectors.rnli import parse_rnli_page
from app.config import Settings, _adsb_poll_seconds
from app.search import ActivitySearch
from app.state import LiveState


def test_old_bods_datafeed_url_is_repaired():
    s = Settings(_bods_api_url_raw="https://data.bus-data.dft.gov.uk/api/v1/datafeed/")
    assert s.bods_api_url.endswith("/api/v1/gtfsrtdatafeed/")


def test_rnli_official_page_fallback_parses_current_format():
    html = """
    <html><body><section>
      <a href='/find-my-nearest/lifeboat-stations/blackpool-lifeboat-station'>
      Blackpool Lifeboat Station launched 1 hour ago Launch time 5.03pm 4 October 2026
      </a>
    </section></body></html>
    """
    rows = parse_rnli_page(html, {"blackpool": (53.821, -3.056)})
    assert len(rows) == 1
    assert rows[0].type == "rescue"
    assert rows[0].source == "RNLI"
    assert rows[0].start_time.tzinfo == timezone.utc


def test_adsb_recognises_coastguard_registration_even_with_srd_callsign():
    rows, total = parse_adsb_response({"ac": [{
        "hex": "407abc", "flight": "SRD936", "r": "G-MCGA",
        "lat": 50.8, "lon": -3.2, "gs": 122, "track": 84,
        "t": "S92", "alt_baro": 1750,
    }]})
    assert total == 1
    assert len(rows) == 1
    assert rows[0].type == "sar"
    assert rows[0].operator == "HM Coastguard"
    assert rows[0].aircraft_type_code == "S92"
    assert rows[0].altitude_ft == 1750


def test_nearby_aircraft_includes_ordinary_and_special_aircraft():
    rows, total = parse_nearby_aircraft_response({"ac": [
        {"hex": "400001", "flight": "BAW123", "r": "G-ABCD", "lat": 52.1, "lon": -1.3, "t": "A320", "gs": 410, "track": 90},
        {"hex": "400002", "flight": "RRR01", "lat": 52.2, "lon": -1.2, "t": "H47", "dbFlags": 1},
    ]})
    assert total == 2
    assert {row.id for row in rows} == {"aircraft:400001", "aircraft:400002"}
    ordinary = next(row for row in rows if row.id == "aircraft:400001")
    military = next(row for row in rows if row.id == "aircraft:400002")
    assert ordinary.interesting is False
    assert military.interesting is True
    assert military.aircraft_model == "Boeing CH-47 Chinook"


def test_nearby_ryanair_b38m_is_named_immediately_without_route_guessing():
    rows, _ = parse_nearby_aircraft_response({"ac": [{
        "hex": "4cadbc", "flight": "RYR1YY", "r": "EI-IGJ",
        "lat": 52.516, "lon": -1.547, "t": "B38M", "gs": 257.5, "track": 100.7,
    }]})
    aircraft = rows[0]
    assert aircraft.aircraft_model == "Boeing 737 MAX 8"
    assert aircraft.operator == "Ryanair"
    assert aircraft.origin is None and aircraft.destination is None


def test_flyitaly_aircraft_details_are_preserved_for_the_detail_panel():
    rows, _ = parse_nearby_aircraft_response({"ac": [{
        "hex": "4cadbc", "flight": "RYR1YY", "r": "EI-IGJ",
        "lat": 52.516, "lon": -1.547, "t": "B38M", "ownOp": "Ryanair DAC",
        "gs": 257.5, "ias": 238, "tas": 260, "mach": 0.42,
        "track": 100.7, "true_heading": 98.2, "baro_rate": 1472,
        "alt_baro": 11875, "nav_altitude_mcp": 18000,
        "nav_heading": 102, "nav_modes": ["autopilot", "vnav", "tcas"],
        "wd": 230, "ws": 54, "oat": -12, "roll": -8.5,
        "squawk": "7700", "category": "A3", "type": "adsb_icao",
        "dbFlags": 3,
        "route": {"from": "EGSS", "from_iata": "STN", "to": "EIDW", "to_iata": "DUB", "count": 12, "last_day": "2026-11-17"},
    }]}, source_name="FlyItalyADSB")

    aircraft = rows[0]
    assert aircraft.origin == "STN" and aircraft.destination == "DUB"
    assert aircraft.operator == "Ryanair DAC"
    assert aircraft.aircraft_category == "Large aircraft"
    assert aircraft.squawk == "7700"
    assert aircraft.emergency == "General emergency alert"
    assert aircraft.status == "General emergency alert"
    assert aircraft.vertical_rate_fpm == 1472
    assert aircraft.indicated_airspeed_kt == 238
    assert aircraft.true_airspeed_kt == 260
    assert aircraft.mach == 0.42
    assert aircraft.heading == 98.2
    assert aircraft.selected_altitude_ft == 18000
    assert aircraft.selected_heading == 102
    assert aircraft.navigation_modes == ["autopilot", "vnav", "tcas"]
    assert aircraft.wind_direction == 230
    assert aircraft.wind_speed_kt == 54
    assert aircraft.outside_air_temp_c == -12
    assert aircraft.roll_deg == -8.5
    assert aircraft.aircraft_route_count == 12
    assert aircraft.aircraft_route_last_day == "2026-11-17"
    assert aircraft.position_source == "ADS-B"
    assert aircraft.aircraft_flags == ["Military", "Notable"]


def test_nearby_aircraft_uses_recent_point_cache_when_refresh_fails(monkeypatch):
    collector = ADSBLolSARCollector(Settings(), LiveState())

    class GoodResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"ac": [{
                "hex": "400001", "flight": "BAW123", "lat": 52.54,
                "lon": -1.37, "t": "A320", "gs": 410, "track": 90,
            }]}

    monkeypatch.setattr(collector._session, "get", lambda *args, **kwargs: GoodResponse())
    first, checked, source = collector.fetch_nearby(52.54, -1.37, 43)
    assert checked == 1 and [row.id for row in first] == ["aircraft:400001"]
    assert source == "ADSB.lol"

    key = (52.54, -1.37, 43)
    fetched_at, rows, checked, source = collector._nearby_cache[key]
    collector._nearby_cache[key] = (fetched_at - timedelta(seconds=30), rows, checked, source)

    def timeout(*args, **kwargs):
        raise requests.Timeout("temporary point-feed timeout")

    monkeypatch.setattr(collector._session, "get", timeout)
    retained, retained_checked, retained_source = collector.fetch_nearby(52.54, -1.37, 43)
    assert retained_checked == 1
    assert [row.id for row in retained] == ["aircraft:400001"]
    assert retained_source == "ADSB.lol"


def test_nearby_aircraft_prefers_keyed_commercial_provider_and_sends_key(monkeypatch):
    settings = Settings(
        flyitaly_adsb_api_key="server-side-test-key",
        flyitaly_adsb_base_url="https://aircraft.example/v2",
    )
    collector = ADSBLolSARCollector(settings, LiveState())
    calls = []

    class GoodResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"ac": [{
                "hex": "400001", "flight": "BAW123", "lat": 52.54,
                "lon": -1.37, "t": "A320", "gs": 410, "track": 90,
            }]}

    def get(url, **kwargs):
        calls.append((url, kwargs))
        return GoodResponse()

    monkeypatch.setattr(collector._session, "get", get)
    rows, checked, source = collector.fetch_nearby(52.54, -1.37, 43)

    assert checked == 1 and len(rows) == 1
    assert source == "FlyItalyADSB"
    assert calls[0][0] == "https://aircraft.example/v2/lat/52.54/lon/-1.37/dist/80?with=route"
    assert calls[0][1]["headers"] == {"X-Api-Key": "server-side-test-key"}
    assert rows[0].source == "FlyItalyADSB"


def test_identical_nearby_requests_are_coalesced(monkeypatch):
    collector = ADSBLolSARCollector(Settings(), LiveState())
    started = threading.Event()
    release = threading.Event()
    call_count = 0

    class GoodResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"ac": [{"hex": "400001", "lat": 52.54, "lon": -1.37}]}

    def get(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        started.set()
        assert release.wait(timeout=2)
        return GoodResponse()

    monkeypatch.setattr(collector._session, "get", get)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(collector.fetch_nearby, 52.54, -1.37, 43)
        assert started.wait(timeout=2)
        second = pool.submit(collector.fetch_nearby, 52.54, -1.37, 43)
        release.set()
        results = [first.result(timeout=3), second.result(timeout=3)]

    assert call_count == 1
    assert all(result[1:] == (1, "ADSB.lol") for result in results)


def test_aircraft_background_poll_defaults_are_quota_safe(monkeypatch):
    monkeypatch.delenv("ADSB_SAR_POLL_SECONDS", raising=False)
    monkeypatch.delenv("ADSB_LOL_API_KEY", raising=False)
    monkeypatch.delenv("FLYITALYADSB_API_KEY", raising=False)
    assert _adsb_poll_seconds() == 360

    monkeypatch.setenv("FLYITALYADSB_API_KEY", "test-key")
    assert _adsb_poll_seconds() == 60

    monkeypatch.setenv("ADSB_SAR_POLL_SECONDS", "not-a-number")
    assert _adsb_poll_seconds() == 60


def test_nearby_aircraft_falls_back_to_adsb_lol_when_primary_is_limited(monkeypatch):
    settings = Settings(
        flyitaly_adsb_api_key="server-side-test-key",
        flyitaly_adsb_base_url="https://aircraft.example/api/aircraft/v2",
        adsb_lol_base_url="https://lol.example/v2",
        adsb_lol_api_key="lol-test-key",
    )
    collector = ADSBLolSARCollector(settings, LiveState())
    calls = []

    class RateLimitedResponse:
        status_code = 429
        headers = {"Retry-After": "30"}

        def raise_for_status(self):
            raise requests.HTTPError("limited", response=self)

    class GoodResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"ac": [{"hex": "400002", "lat": 52.5, "lon": -1.3}]}

    def get(url, **kwargs):
        calls.append((url, kwargs))
        return RateLimitedResponse() if "aircraft.example" in url else GoodResponse()

    monkeypatch.setattr(collector._session, "get", get)
    rows, checked, source = collector.fetch_nearby(52.54, -1.37, 43)
    second_rows, second_checked, second_source = collector.fetch_nearby(52.60, -1.30, 43)

    assert checked == 1 and [row.id for row in rows] == ["aircraft:400002"]
    assert source == "ADSB.lol"
    assert second_checked == 1 and [row.id for row in second_rows] == ["aircraft:400002"]
    assert second_source == "ADSB.lol"
    assert len(calls) == 3
    assert sum("aircraft.example" in url for url, _kwargs in calls) == 1
    assert calls[1][1]["headers"] == {"x-api-key": "lol-test-key"}


def test_interactive_nearby_timeout_returns_without_waiting_for_a_second_provider(monkeypatch):
    settings = Settings(
        flyitaly_adsb_api_key="server-side-test-key",
        flyitaly_adsb_base_url="https://aircraft.example/api/aircraft/v2",
        adsb_lol_base_url="https://lol.example/v2",
    )
    collector = ADSBLolSARCollector(settings, LiveState())
    calls = []

    def timeout(url, **kwargs):
        calls.append((url, kwargs))
        raise requests.Timeout("primary provider timed out")

    monkeypatch.setattr(collector._session, "get", timeout)

    try:
        collector.fetch_nearby(52.54, -1.37, 43)
    except requests.Timeout:
        pass
    else:
        raise AssertionError("interactive timeout should reach the retained fallback")

    assert len(calls) == 1
    assert calls[0][1]["timeout"] == (2, 3.5)


def test_rotating_region_cache_retains_ordinary_aircraft_for_nearby_fallback():
    collector = ADSBLolSARCollector(Settings(), LiveState())
    nearby, _ = parse_nearby_aircraft_response({"ac": [
        {"hex": "400001", "flight": "BAW123", "lat": 52.54, "lon": -1.37},
        {"hex": "400002", "flight": "DISTANT", "lat": 55.0, "lon": -4.0},
    ]}, source_name="FlyItalyADSB")
    collector._region_cache[0] = (
        nearby, [], [], 2, datetime.now(timezone.utc), "FlyItalyADSB"
    )

    rows, source = collector.retained_nearby(52.54, -1.37, 50)

    assert [row.id for row in rows] == ["aircraft:400001"]
    assert source == "FlyItalyADSB"


def test_rotating_region_cache_does_not_present_old_aircraft_as_overhead():
    collector = ADSBLolSARCollector(Settings(), LiveState())
    nearby, _ = parse_nearby_aircraft_response({"ac": [
        {"hex": "400001", "flight": "BAW123", "lat": 52.54, "lon": -1.37},
    ]}, source_name="FlyItalyADSB")
    collector._region_cache[0] = (
        nearby,
        [],
        [],
        1,
        datetime.now(timezone.utc) - timedelta(minutes=3),
        "FlyItalyADSB",
    )

    rows, source = collector.retained_nearby(52.54, -1.37, 50)

    assert rows == []
    assert source is None


def test_common_military_training_and_tanker_types_are_named():
    rows, _ = parse_nearby_aircraft_response({"ac": [
        {"hex": "43c8d0", "flight": "VYT61", "r": "ZM329", "lat": 52.0, "lon": -1.0, "t": "TEX2", "dbFlags": 1},
        {"hex": "43c8d8", "flight": "CWL45", "r": "ZM337", "lat": 52.1, "lon": -1.1, "t": "E50P", "dbFlags": 1},
        {"hex": "ae026d", "flight": "QID12", "r": "62-3576", "lat": 52.2, "lon": -1.2, "t": "K35R", "dbFlags": 1},
    ]})

    assert [item.aircraft_model for item in rows] == [
        "Beechcraft T-6 Texan II",
        "Embraer Phenom 100",
        "Boeing KC-135R Stratotanker",
    ]


def test_london_fire_time_parser_has_timezone_on_windows_safe_path():
    dt = _parse_dt("04/10/2026 8:15 PM")
    assert dt is not None
    assert dt.tzinfo == timezone.utc


def test_london_fire_map_excludes_three_day_old_archive_cards():
    old = (datetime.now(timezone.utc) - timedelta(days=3)).astimezone()
    current = (datetime.now(timezone.utc) - timedelta(minutes=20)).astimezone()
    stamp = lambda value: value.strftime("%d/%m/%Y %I:%M %p")
    html = f"""<main>
      <article><a href='/incidents/old-fire'>House fire in Camden</a><p>Camden {stamp(old)}</p></article>
      <article><a href='/incidents/current-fire'>Shed fire in Camden</a><p>Camden {stamp(current)}</p></article>
    </main>"""
    rows = parse_lfb_incidents(html)
    assert [row.name for row in rows] == ["Shed fire in Camden"]
    assert rows[0].status == "Recent public report"


def test_road_shorthand_normalisation():
    assert ActivitySearch._normalise_road_query("M1 J20") == "M1 junction 20, United Kingdom"
    assert ActivitySearch._normalise_road_query("A5 Hinckley").endswith(", United Kingdom")


def test_weather_warning_geojson_parses_polygon():
    payload = {"features": [{
        "type": "Feature",
        "properties": {
            "OBJECTID": 9,
            "warningLevel": "Amber",
            "weatherType": "Wind",
            "headline": "Strong winds are expected",
            "validFrom": "2026-10-05T08:00:00Z",
            "validTo": "2026-10-05T18:00:00Z",
        },
        "geometry": {"type": "Polygon", "coordinates": [[[-2.0, 52.0], [-1.0, 52.0], [-1.0, 53.0], [-2.0, 53.0], [-2.0, 52.0]]]},
    }]}
    rows = parse_warning_geojson(payload)
    assert len(rows) == 1
    assert rows[0].type == "weather"
    assert rows[0].severity == "Amber"
    assert 52.0 <= rows[0].lat <= 53.0
    assert -2.0 <= rows[0].lon <= -1.0


def test_weather_warning_official_rss_fallback():
    rss = b"""<?xml version='1.0'?><rss><channel><item>
      <title>Amber warning of wind for West Midlands</title>
      <description>Strong winds may cause disruption across the West Midlands.</description>
      <link>https://weather.metoffice.gov.uk/warnings-and-advice/uk-warnings</link>
      <guid>warning-123</guid><pubDate>Mon, 05 Oct 2026 12:00:00 GMT</pubDate>
    </item></channel></rss>"""
    rows = parse_warning_rss(rss)
    assert len(rows) == 1
    assert rows[0].type == "weather"
    assert rows[0].severity == "Amber"
    assert rows[0].source == "Met Office warning RSS"
    assert "West Midlands" in rows[0].position_label
    assert rows[0].official_url.startswith("https://weather.metoffice.gov.uk/")
