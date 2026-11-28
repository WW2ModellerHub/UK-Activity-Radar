"""Buses say where they run: "Bus 4 · Lutterworth → St Margaret's Bus Station, Leicester"."""
import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace

from app.bus_timetable import BusTimetableService, stop_with_town
from app.collectors.bods import BODSCollector
from app.models import Vehicle
from app.search import ActivitySearch
from app.state import LiveState
from app.timeutils import london_tz


class _State:
    def set_source(self, _source):
        pass


def _service_with_route_4(tmp_path, stops=40):
    service = BusTimetableService(tmp_path, _State())
    now = datetime.now(tz=london_tz())
    start = max(0, now.hour * 60 + now.minute - 10)
    with sqlite3.connect(service._db("east_midlands")) as con:
        con.executescript("""
            CREATE TABLE stops(stop_id TEXT PRIMARY KEY,stop_code TEXT,name TEXT,lat REAL,lon REAL);
            CREATE TABLE agencies(agency_id TEXT PRIMARY KEY,name TEXT);
            CREATE TABLE routes(route_id TEXT PRIMARY KEY,agency_id TEXT,short_name TEXT,long_name TEXT);
            CREATE TABLE trips(trip_id TEXT PRIMARY KEY,route_id TEXT,service_id TEXT,headsign TEXT,direction_id TEXT);
            CREATE TABLE calendar(service_id TEXT PRIMARY KEY,monday INTEGER,tuesday INTEGER,wednesday INTEGER,thursday INTEGER,friday INTEGER,saturday INTEGER,sunday INTEGER,start_date TEXT,end_date TEXT);
            CREATE TABLE calendar_dates(service_id TEXT,date TEXT,exception_type INTEGER);
            CREATE TABLE stop_times(stop_id TEXT,trip_id TEXT,arrival_time TEXT,departure_time TEXT,stop_sequence INTEGER);
            INSERT INTO agencies VALUES('a','Arriva Midlands');
            INSERT INTO routes VALUES('r','a','4','Lutterworth - Leicester');
            INSERT INTO trips VALUES('trip-4','r','daily','Leicester','0');
            INSERT INTO calendar VALUES('daily',1,1,1,1,1,1,1,'20200101','20991231');
        """)
        for n in range(stops):
            name = "Lutterworth Bus Station" if n == 0 else "St Margaret's Bus Station" if n == stops - 1 else f"Stop {n}"
            con.execute("INSERT INTO stops VALUES(?,?,?,?,?)", (f"s{n}", f"c{n}", name, 52.45 + n * 0.004, -1.2 + n * 0.002))
            hour, minute = divmod(start + n * 2, 60)
            clock = f"{hour:02d}:{minute:02d}:00"
            con.execute("INSERT INTO stop_times VALUES(?,?,?,?,?)", (f"s{n}", "trip-4", clock, clock, n + 1))
    return service


def test_the_last_stop_gets_its_town_from_the_headsign():
    assert stop_with_town("St Margaret's Bus Station", "Leicester") == "St Margaret's Bus Station, Leicester"
    assert stop_with_town("Leicester Haymarket", "Leicester") == "Leicester Haymarket"
    assert stop_with_town("Coventry", "Coventry") == "Coventry"
    assert stop_with_town("Pool Meadow", None) == "Pool Meadow"


def test_a_long_route_always_ends_with_its_final_stop_and_says_where_it_runs(tmp_path):
    service = _service_with_route_4(tmp_path)
    bus = SimpleNamespace(lat=52.47, lon=-1.19, journey_ref="trip-4", next_location="s5", route="4", destination="Leicester")
    journey = service.journey_for_vehicle(bus)
    assert journey["origin"] == "Lutterworth Bus Station"
    assert journey["final_stop"] == "St Margaret's Bus Station, Leicester"
    last = journey["stops"][-1]
    assert last["name"] == "St Margaret's Bus Station" and last["after_gap"] is True
    assert len(journey["stops"]) < 40
    assert service.trip_ends(bus) == {"origin": "Lutterworth Bus Station", "destination": "St Margaret's Bus Station, Leicester"}


def test_searching_a_route_number_lists_each_bus_with_where_it_runs(tmp_path):
    service = _service_with_route_4(tmp_path)
    state = LiveState()
    state.upsert(Vehicle(id="bus:ARBB:1", type="bus", name="Bus 4", subtitle="Trip trip-4", lat=52.47, lon=-1.19,
                         route="4", operator="Arriva Midlands", journey_ref="trip-4", last_updated=datetime.now(timezone.utc)))
    state.upsert(Vehicle(id="bus:X:2", type="bus", name="Bus 4", subtitle="Hinckley → Coventry", lat=52.5, lon=-1.4,
                         route="4", origin="Hinckley", destination="Coventry", operator="Stagecoach",
                         last_updated=datetime.now(timezone.utc)))
    search = ActivitySearch(state, "", enabled=False)
    search.bus_trip_ends = service.trip_ends
    rows = {row["id"]: row for row in search.search("4", lat=52.47, lon=-1.19)["matches"]}
    assert rows["bus:ARBB:1"]["name"] == "Bus 4"
    assert rows["bus:ARBB:1"]["subtitle"] == "Lutterworth Bus Station → St Margaret's Bus Station, Leicester · Arriva Midlands"
    assert rows["bus:X:2"]["subtitle"].startswith("Hinckley → Coventry")


def test_the_bus_feed_backs_off_while_bods_refuses_it():
    collector = BODSCollector(SimpleNamespace(), None)
    assert collector.retry_after(1) == 30
    assert collector.retry_after(3) == 120
    assert collector.retry_after(10) == 900
    assert collector.retry_after(1, "BODS error: HTTP 403") == 300


def test_searching_a_route_and_a_town_finds_the_timetabled_route_even_with_no_buses_live(tmp_path):
    from app.bus_timetable import routes_serving
    service = _service_with_route_4(tmp_path)
    assert routes_serving(service, "4", "lutterworth") == [{
        "operator": "Arriva Midlands", "route": "4", "region": "east_midlands",
        "ends": "Lutterworth Bus Station → St Margaret's Bus Station, Leicester"}]
    assert routes_serving(service, "4", "Hinckley") == []          # route 4 does not call there
    assert routes_serving(service, "8", "Lutterworth") == []
    search = ActivitySearch(LiveState(), "", enabled=False)
    search.bus_routes_serving = lambda route, place, near: routes_serving(service, route, place, near)
    for query in ("4 lutterworth", "bus 4 to Lutterworth", "Lutterworth 4"):
        found = search.search(query)
        assert found["location"] is None                           # not "4 Lutterworth Road, Newcastle"
        assert found["matches"][0]["name"] == "Bus 4 · Arriva Midlands"
        assert found["matches"][0]["subtitle"] == "Lutterworth Bus Station → St Margaret's Bus Station, Leicester"
        assert found["matches"][0]["url"] == "/bus/arriva-midlands/4"
    assert search.search("4 hinckley").get("intent") is None       # no route 4 there: an ordinary search
    assert search._route_place("A5 Hinckley") == ("A5", "Hinckley")
    assert search.search("A5 Hinckley").get("intent") != {"type": "bus", "route": "A5", "place": "Hinckley"}   # a road
    assert search._route_place("LE17 4") is None and search._route_place("1Q15 leicester") is None
