from datetime import datetime, timedelta, timezone

from app.collectors.adsb_lol import parse_adsb_response, parse_nearby_aircraft_response
from app.collectors.london_fire import _parse_dt, parse_lfb_incidents
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
