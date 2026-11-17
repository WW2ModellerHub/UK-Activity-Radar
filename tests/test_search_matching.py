"""Search finds what a person typed: a bus route, a town, or a named service."""
from datetime import datetime, timezone

from app.models import Vehicle
from app.search import ActivitySearch
from app.state import LiveState

NOW = datetime.now(timezone.utc)


def _bus(id_, route, lat, lon, destination, stop):
    return Vehicle(id=id_, type="bus", name=f"Bus {route}", route=route, destination=destination, subtitle=stop,
                   lat=lat, lon=lon, source="BODS", operator="AMID", last_updated=NOW)


def _searcher(*rows, place=None):
    state = LiveState()
    for row in rows:
        state.upsert(row)
    finder = ActivitySearch(state, "https://nominatim.example", enabled=False)
    finder._geocode = lambda query: place
    return finder


def test_a_route_number_finds_the_nearest_buses_on_that_route():
    near = _bus("bus:1", "5", 52.52, -1.47, "Nuneaton", "Queens Road")
    far = _bus("bus:2", "5", 53.48, -2.24, "Manchester", "Piccadilly")
    other = _bus("bus:3", "158", 52.52, -1.47, "Hinckley", "Queens Road")
    finder = _searcher(far, near, other)
    for typed in ("5", "bus 5", "Route 5"):
        result = finder.search(typed, lat=52.52, lon=-1.46)
        assert [row["id"] for row in result["matches"]] == ["bus:1", "bus:2"], typed
    assert result["matches"][0]["subtitle"].startswith("To Nuneaton")
    assert result["matches"][0]["distance_km"] < 1
    # A train headcode is not a bus route.
    assert finder.search("1Q15")["intent"] is None


def test_a_town_comes_back_as_a_place_and_only_buses_really_going_there_match():
    town = {"kind": "location", "name": "Market Bosworth, Leicestershire", "lat": 52.62, "lon": -1.40}
    going = _bus("bus:10", "153", 52.6, -1.3, "Market Bosworth", "Station Road")
    beast = _bus("bus:11", "18", 52.95, -1.15, "Arnold", "Beastmarket Hill B1")
    twice = _bus("bus:12", "153", 52.6, -1.3, "Market Bosworth", "Station Road")
    finder = _searcher(going, beast, twice, place=town)
    result = finder.search("Market Bosworth")
    assert result["location"] == town
    assert [row["id"] for row in result["matches"]] in (["bus:10"], ["bus:12"])   # once, and no Beastmarket


def test_a_postcode_is_looked_up_as_a_place():
    spot = {"kind": "location", "name": "CV11 4", "lat": 52.52, "lon": -1.46}
    assert _searcher(place=spot).search("CV11 4")["location"] == spot
