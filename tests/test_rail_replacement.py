"""Where rail replacement buses stop (Rail Data Marketplace)."""
import json

import pytest

from app.rail_replacement import API_HEADERS, RailReplacementStops, parse_stops
from app.seo_pages import engineering_html


def _point(atco, crs, text, lat=52.63, lon=-1.12, street=None):
    return {"AtcoCode": atco, "Descriptor": {"CommonName": "Leicester", "Street": street, "Description": text},
            "Place": {"Location": {"Translation": {"Latitude": lat, "Longitude": lon}}},
            "StopClassification": {"StopType": "RRL", "RailReplacement": {"CrsRef": crs}}}


FILE = {"RailReplacementLocation": {"StopPoints": [
    _point("9110LEI0", "LEI", "Planned rail replacement buses will depart from the station car park."),
    _point("9110LEI1", "LEI", "Planned rail replacement buses will depart from the station car park.", street="London Road"),
    _point("9110NUN0", "NUN", "Rail replacement buses/coaches depart from the front of the station.", 52.526, -1.464),
    _point("9110BAD0", "XX", "no station code"),
]}}


def test_stops_are_read_by_station():
    stops = parse_stops(FILE)
    assert sorted(stops) == ["LEI", "NUN"]
    assert stops["LEI"][1]["street"] == "London Road" and stops["NUN"][0]["lat"] == 52.526


class _Response:
    def __init__(self, payload): self._payload = payload
    def raise_for_status(self): pass
    def json(self): return self._payload


class _Session:
    def __init__(self, file): self.file, self.calls = file, []
    def get(self, url, headers=None, timeout=None):
        self.calls.append((url, headers))
        return _Response({"downloadUrl": "https://storage.example/911Stops.json"} if "raildata" in url else self.file)


def test_refresh_downloads_saves_and_keeps_the_last_good_copy(tmp_path):
    session = _Session(FILE)
    service = RailReplacementStops(tmp_path, "secret", session)
    assert service.due() and service.refresh() == 4 - 1
    assert session.calls[0][1] == {**API_HEADERS, "x-apikey": "secret"}
    assert session.calls[1][1] is None   # key only to the API
    assert service.at("lei")[0]["atco"] == "9110LEI0" and not service.due()
    # A bad file later keeps what we had; a fresh process reads the saved copy.
    with pytest.raises(ValueError):
        RailReplacementStops(tmp_path, "secret", _Session({"RailReplacementLocation": {"StopPoints": []}})).refresh()
    assert len(RailReplacementStops(tmp_path, "secret").at("LEI")) == 2
    assert RailReplacementStops(tmp_path, "").configured is False


def test_station_page_says_where_buses_leave_from_each_place_once():
    stops = parse_stops(FILE)["LEI"]
    html = engineering_html("Leicester", [], stops)
    assert "Rail replacement buses at Leicester" in html
    assert html.count("station car park") == 1 and "zoom=18" in html
    with_works = engineering_html("Leicester", [{"day": "2026-11-28", "buses": 4}], stops)
    assert "Engineering works at Leicester" in with_works and "station car park" in with_works
    assert engineering_html("Leicester", [], []) == ""
