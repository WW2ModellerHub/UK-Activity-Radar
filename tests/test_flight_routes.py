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


class FallbackSession(FakeSession):
    def __init__(self, routeset_payload, adsbdb_payload, routeset_fail=False):
        super().__init__(routeset_payload, fail=routeset_fail)
        self.adsbdb_payload = adsbdb_payload

    def get(self, url, timeout):
        self.calls.append((url, None))
        return SimpleNamespace(status_code=200, raise_for_status=lambda: None, json=lambda: self.adsbdb_payload)


ADSBDB = {"response": {"flightroute": {
    "callsign": "VIR3N",
    "origin": {"name": "London Heathrow Airport", "iata_code": "LHR", "icao_code": "EGLL", "latitude": 51.47, "longitude": -0.46, "municipality": "London"},
    "destination": {"name": "John F Kennedy International Airport", "iata_code": "JFK", "icao_code": "KJFK", "latitude": 40.64, "longitude": -73.78, "municipality": "New York"},
}}}


def test_adsbdb_is_used_when_adsb_lol_fails_or_has_no_route():
    failing = FlightRouteLookup("https://api.adsb.lol", FallbackSession(None, ADSBDB, routeset_fail=True))
    route = failing.lookup("VIR3N", 51.6, -1.0)
    assert (route["origin"]["iata"], route["destination"]["iata"]) == ("LHR", "JFK")
    unknown = FlightRouteLookup("https://api.adsb.lol", FallbackSession([{"callsign": "VIR3N", "airport_codes": "unknown"}], ADSBDB))
    assert unknown.lookup("VIR3N", 51.6, -1.0)["destination"]["name"] == "John F Kennedy"


def test_adsbdb_route_far_from_the_aircraft_is_ignored_and_reasons_are_reported():
    lookup = FlightRouteLookup("https://api.adsb.lol", FallbackSession([{"callsign": "VIR3N", "airport_codes": "unknown"}], ADSBDB))
    assert lookup.lookup("VIR3N", 35.0, 139.0) is None  # over Tokyo
    assert lookup.last_reason == "no_published_route"
    broken = FlightRouteLookup("https://api.adsb.lol", FakeSession(None, fail=True), fallback_url="")
    assert broken.lookup("VIR3N", 51.6, -1.0) is None
    assert broken.last_reason == "lookup_failed" and "ConnectionError" in broken.last_error
