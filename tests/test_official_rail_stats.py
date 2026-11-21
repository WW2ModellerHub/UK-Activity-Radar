"""Official ORR figures on station and operator pages."""
from app.line_status import PASSENGER_OPERATORS
from app.seo_pages import operator_page, operator_performance, station_page, station_usage


def test_station_pages_show_official_passenger_numbers():
    usage = station_usage("HNK")
    assert usage["region"] == "East Midlands" and usage["rank"] > 0 and len(usage["usage"]) >= 5
    page = station_page("https://x.test", {"name": "Hinckley", "crs": "HNK", "lat": 52.53, "lon": -1.37,
                                           "path": "/station/hinckley-hnk"}, None, [], [])
    assert "How busy is Hinckley?" in page and "busiest of" in page and "Office of Rail and Road" in page
    assert station_usage("ZZZ") is None


def test_operator_pages_show_official_punctuality_for_every_main_operator():
    missing = [op for op in sorted(set(PASSENGER_OPERATORS.values())) if operator_performance(op) is None]
    assert set(missing) <= {"Eurostar", "Island Line", "London Underground"}
    orr, periods, britain = operator_performance("Southern")
    assert orr == "Greater Thameslink Railway" and periods and britain
    page = operator_page("https://x.test", {"operator": "CrossCountry", "status": "good", "label": "Good service",
                                            "running": 10, "late": 1, "cancelled": 0, "average_delay": 6, "worst": []})
    assert "Official punctuality" in page and "on time" in page and "Great Britain overall" in page
