"""Busiest days at Network Rail's managed stations, from daily concourse footfall."""
from app.seo_pages import station_footfall_html, station_usage_html
from app.train_identity import operator_name


def test_a_managed_station_says_its_busiest_and_quietest_days():
    html = station_footfall_html("LDS", "Leeds")
    assert "Saturdays are busiest and Sundays quietest" in html
    assert html.count("<li>") == 7 and "Network Rail daily concourse footfall" in html
    assert "Busiest days at Leeds" in station_usage_html("LDS", "Leeds")


def test_other_stations_have_no_footfall_section():
    assert station_footfall_html("NUN", "Nuneaton") == ""


def test_lumo_is_named():
    assert operator_name("LD") == "Lumo"
