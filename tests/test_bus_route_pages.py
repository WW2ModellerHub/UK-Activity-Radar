"""Bus route pages from the official timetable plus live buses."""
import sqlite3
from datetime import datetime, timezone

from fastapi.testclient import TestClient

import app.main as main
from app.bus_timetable import BusTimetableService, _ROUTE_INDEX, route_index, route_profile
from app.models import Vehicle
from app.state import LiveState
from app.timeutils import london_tz

STOPS = [("S1", "Nuneaton Bus Station", 52.523, -1.468), ("S2", "Attleborough", 52.515, -1.455),
         ("S3", "Hinckley Town Centre", 52.540, -1.373)]


def _service(tmp_path):
    _ROUTE_INDEX.clear()
    service = BusTimetableService(tmp_path, LiveState())
    con = sqlite3.connect(service._db("west_midlands"))
    con.executescript("""
        CREATE TABLE stops(stop_id TEXT PRIMARY KEY,stop_code TEXT,name TEXT,lat REAL,lon REAL);
        CREATE TABLE agencies(agency_id TEXT PRIMARY KEY,name TEXT);
        CREATE TABLE routes(route_id TEXT PRIMARY KEY,agency_id TEXT,short_name TEXT,long_name TEXT);
        CREATE TABLE trips(trip_id TEXT PRIMARY KEY,route_id TEXT,service_id TEXT,headsign TEXT,direction_id TEXT,shape_id TEXT);
        CREATE TABLE calendar(service_id TEXT PRIMARY KEY,monday INTEGER,tuesday INTEGER,wednesday INTEGER,thursday INTEGER,friday INTEGER,saturday INTEGER,sunday INTEGER,start_date TEXT,end_date TEXT);
        CREATE TABLE calendar_dates(service_id TEXT,date TEXT,exception_type INTEGER);
        CREATE TABLE stop_times(stop_id TEXT,trip_id TEXT,arrival_time TEXT,departure_time TEXT,stop_sequence INTEGER);
        CREATE TABLE shapes(shape_id TEXT,lat REAL,lon REAL,sequence INTEGER);""")
    con.executemany("INSERT INTO stops VALUES (?,?,?,?,?)", [(i, i, n, la, lo) for i, n, la, lo in STOPS])
    con.execute("INSERT INTO agencies VALUES ('A1','Arriva Midlands')")
    con.execute("INSERT INTO routes VALUES ('R1','A1','158','Nuneaton - Hinckley')")
    con.execute("INSERT INTO calendar VALUES ('EVERY',1,1,1,1,1,1,1,'20200101','20991231')")
    for n, start in enumerate(["07:00", "10:00", "10:30", "11:00", "18:00"]):
        hh, mm = map(int, start.split(":"))
        con.execute("INSERT INTO trips VALUES (?,?,?,?,?,NULL)", (f"T{n}", "R1", "EVERY", "Hinckley", "0"))
        for seq, (stop, *_rest) in enumerate(STOPS):
            t = f"{hh:02d}:{mm + seq * 10:02d}:00" if mm + seq * 10 < 60 else f"{hh + 1:02d}:{mm + seq * 10 - 60:02d}:00"
            con.execute("INSERT INTO stop_times VALUES (?,?,?,?,?)", (stop, f"T{n}", t, t, seq))
    con.execute("INSERT INTO trips VALUES ('B1','R1','EVERY','Nuneaton','1',NULL)")
    for seq, (stop, *_rest) in enumerate(reversed(STOPS)):
        con.execute("INSERT INTO stop_times VALUES (?,?,?,?,?)", (stop, "B1", f"12:{seq * 10:02d}:00", f"12:{seq * 10:02d}:00", seq))
    con.commit(); con.close()
    return service


def test_a_route_profile_says_stops_buses_today_frequency_and_journey_time(tmp_path):
    service = _service(tmp_path)
    assert [(r["operator"], r["route"]) for r in route_index(service)] == [("Arriva Midlands", "158")]
    profile = route_profile(service, "west_midlands", "Arriva Midlands", "158")
    out, back = profile["directions"]
    assert out["towards"] == "Hinckley" and [s["name"] for s in out["stops"]][0] == "Nuneaton Bus Station"
    assert out["today"] == 5 and out["first"] == "07:00" and out["last"] == "18:00"
    assert out["every"] == 30 and out["journey_minutes"] == 20
    assert back["towards"] == "Nuneaton" and back["today"] == 1


def test_route_pages_are_served_with_live_buses(tmp_path, monkeypatch):
    service = _service(tmp_path)

    class Places:
        bus_timetables = service
    state = LiveState()
    now = datetime.now(timezone.utc)
    state.upsert(Vehicle(id="bus:AMID:1", type="bus", name="Bus 158", route="158", destination="HINCKLEY",
                         lat=52.516, lon=-1.456, source="BODS", last_updated=now))
    state.upsert(Vehicle(id="bus:X:2", type="bus", name="Bus 158", route="158", lat=53.48, lon=-2.24,
                         source="BODS", last_updated=now))                       # another 158, in Manchester
    monkeypatch.setattr(main, "smart_places", Places())
    monkeypatch.setattr(main, "state", state)
    client = TestClient(main.app)
    page = client.get("/bus/arriva-midlands/158")
    assert page.status_code == 200
    assert "Bus 158: Hinckley – Nuneaton" in page.text and "1 bus on route 158" in page.text
    assert "near Attleborough" in page.text and "5 buses today" in page.text
    assert "/bus/arriva-midlands/158" in client.get("/bus/arriva-midlands").text
    assert "/bus/arriva-midlands" in client.get("/buses").text
    assert client.get("/bus/arriva-midlands/999").status_code == 404
