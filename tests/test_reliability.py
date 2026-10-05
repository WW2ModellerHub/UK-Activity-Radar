from datetime import timezone

from app.collectors.adsb_lol import parse_adsb_response
from app.collectors.london_fire import _parse_dt
from app.collectors.met_office import parse_warning_geojson, parse_warning_rss
from app.collectors.rnli import parse_rnli_page
from app.config import Settings
from app.search import ActivitySearch


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


def test_london_fire_time_parser_has_timezone_on_windows_safe_path():
    dt = _parse_dt("04/10/2026 8:15 PM")
    assert dt is not None
    assert dt.tzinfo == timezone.utc


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
