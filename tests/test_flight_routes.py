"""Departure and arrival airports for airline flights."""
from types import SimpleNamespace

import requests

from app.flight_routes import FlightRouteLookup, airline_callsign, parse_route

ROUTE = {
    "callsign": "BAW117", "airport_codes": "EGLL-KJFK", "_airport_codes_iata": "LHR-JFK", "plausible": True,
    "_airports": [
        {"name": "London Heathrow Airport", "iata": "LHR", "icao": "EGLL", "location": "London"},
        {"name": "John F Kennedy International Airport", "iata": "JFK", "icao": "KJFK", "location": "New York"},
    ],
}


class FakeSession:
    def __init__(self, payload, fail=False):
        self.payload, self.fail, self.calls = payload, fail, []

    def post(self, url, json, timeout):
        self.calls.append((url, json))
        if self.fail:
            raise requests.ConnectionError("down")
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: self.payload)


def test_only_airline_callsigns_are_looked_up():
    assert airline_callsign(" baw117 ") == "BAW117"
    assert airline_callsign("RYR1YY") == "RYR1YY"
    assert airline_callsign("G-ABCD") is None
    assert airline_callsign("RESCUE924") is None


def test_route_names_drop_the_word_airport():
    route = parse_route(ROUTE)
    assert route["origin"] == {"name": "London Heathrow", "iata": "LHR", "icao": "EGLL"}
    assert route["destination"]["name"] == "John F Kennedy"
    assert route["via"] == []


def test_implausible_or_unknown_routes_are_not_shown():
    assert parse_route({**ROUTE, "plausible": False}) is None
    assert parse_route({"callsign": "XYZ1", "airport_codes": "unknown"}) is None
    assert parse_route({"callsign": "EZY12", "_airport_codes_iata": "LGW-GVA"})["destination"]["name"] == "GVA"


def test_lookup_caches_by_callsign_and_survives_failure():
    session = FakeSession([ROUTE])
    lookup = FlightRouteLookup("https://api.adsb.lol", session)
    assert lookup.lookup("BAW117", 51.5, -0.4)["origin"]["iata"] == "LHR"
    assert lookup.lookup("BAW117", 51.6, -0.5)["origin"]["iata"] == "LHR"
    assert len(session.calls) == 1
    assert session.calls[0] == ("https://api.adsb.lol/api/0/routeset",
                                {"planes": [{"callsign": "BAW117", "lat": 51.5, "lng": -0.4}]})
    assert FlightRouteLookup("https://api.adsb.lol", FakeSession(None, fail=True)).lookup("BAW117", 51.5, -0.4) is None
    assert lookup.lookup("G-ABCD", 51.5, -0.4) is None
    assert len(session.calls) == 1
