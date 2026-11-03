import os
import sqlite3
import time
from datetime import datetime
from types import SimpleNamespace

from app.bus_timetable import BODS_CACHE_MAX_AGE_SECONDS, BusTimetableService
from app.timeutils import london_tz


class _State:
    def set_source(self, _source):
        pass


def _ready_region(service: BusTimetableService, region: str) -> None:
    path = service._db(region)
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE stop_times(stop_id TEXT)")
        connection.execute("INSERT INTO stop_times VALUES('stop-1')")


def test_regional_bus_timetable_cache_expires_without_becoming_unusable(tmp_path):
    service = BusTimetableService(tmp_path, _State())
    _ready_region(service, "london")
    path = service._db("london")

    assert service.ready("london") is True
    assert service.needs_refresh("london") is False

    stale = time.time() - BODS_CACHE_MAX_AGE_SECONDS - 60
    os.utime(path, (stale, stale))

    assert service.ready("london") is True
    assert service.needs_refresh("london") is True


def test_live_vehicle_trip_is_joined_to_official_stop_sequence(tmp_path):
    service = BusTimetableService(tmp_path, _State())
    path = service._db("london")
    now = datetime.now(tz=london_tz())
    minutes = now.hour * 60 + now.minute + 10
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE stops(stop_id TEXT PRIMARY KEY,stop_code TEXT,name TEXT,lat REAL,lon REAL);
            CREATE TABLE agencies(agency_id TEXT PRIMARY KEY,name TEXT);
            CREATE TABLE routes(route_id TEXT PRIMARY KEY,agency_id TEXT,short_name TEXT,long_name TEXT);
            CREATE TABLE trips(trip_id TEXT PRIMARY KEY,route_id TEXT,service_id TEXT,headsign TEXT,direction_id TEXT);
            CREATE TABLE calendar(service_id TEXT PRIMARY KEY,monday INTEGER,tuesday INTEGER,wednesday INTEGER,thursday INTEGER,friday INTEGER,saturday INTEGER,sunday INTEGER,start_date TEXT,end_date TEXT);
            CREATE TABLE calendar_dates(service_id TEXT,date TEXT,exception_type INTEGER);
            CREATE TABLE stop_times(stop_id TEXT,trip_id TEXT,arrival_time TEXT,departure_time TEXT,stop_sequence INTEGER);
            INSERT INTO agencies VALUES('agency','Example Buses');
            INSERT INTO routes VALUES('route','agency','7','Town - Centre');
            INSERT INTO trips VALUES('trip-7','route','daily','City Centre','0');
            INSERT INTO calendar VALUES('daily',1,1,1,1,1,1,1,'20200101','20991231');
            INSERT INTO stops VALUES('stop-a','A','Market Street',51.50,-0.10);
            INSERT INTO stops VALUES('stop-b','B','Central Station',51.51,-0.11);
        """)
        first, second = divmod(minutes, 60)
        connection.execute("INSERT INTO stop_times VALUES('stop-a','trip-7',?,?,1)", (f"{first:02d}:{second:02d}:00", f"{first:02d}:{second:02d}:00"))
        first, second = divmod(minutes + 8, 60)
        connection.execute("INSERT INTO stop_times VALUES('stop-b','trip-7',?,?,2)", (f"{first:02d}:{second:02d}:00", f"{first:02d}:{second:02d}:00"))
    vehicle = SimpleNamespace(lat=51.50, lon=-0.10, journey_ref="trip-7", next_location="stop-a")

    result = service.journey_for_vehicle(vehicle)

    assert result["mode"] == "matched_timetable"
    assert result["route"] == "7"
    assert result["operator"] == "Example Buses"
    assert result["next_stop_id"] == "stop-a"
    assert result["stops"][0]["state"] == "next"


def test_live_vehicle_with_operator_journey_ref_matches_official_route_time_and_position(tmp_path):
    service = BusTimetableService(tmp_path, _State())
    path = service._db("london")
    now = datetime.now(tz=london_tz())
    minutes = now.hour * 60 + now.minute + 4
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE stops(stop_id TEXT PRIMARY KEY,stop_code TEXT,name TEXT,lat REAL,lon REAL);
            CREATE TABLE agencies(agency_id TEXT PRIMARY KEY,name TEXT);
            CREATE TABLE routes(route_id TEXT PRIMARY KEY,agency_id TEXT,short_name TEXT,long_name TEXT);
            CREATE TABLE trips(trip_id TEXT PRIMARY KEY,route_id TEXT,service_id TEXT,headsign TEXT,direction_id TEXT);
            CREATE TABLE calendar(service_id TEXT PRIMARY KEY,monday INTEGER,tuesday INTEGER,wednesday INTEGER,thursday INTEGER,friday INTEGER,saturday INTEGER,sunday INTEGER,start_date TEXT,end_date TEXT);
            CREATE TABLE calendar_dates(service_id TEXT,date TEXT,exception_type INTEGER);
            CREATE TABLE stop_times(stop_id TEXT,trip_id TEXT,arrival_time TEXT,departure_time TEXT,stop_sequence INTEGER);
            INSERT INTO agencies VALUES('agency','Example Buses');
            INSERT INTO routes VALUES('route','agency','30','City - Wollaton');
            INSERT INTO trips VALUES('gtfs-hash','route','daily','Wollaton Vale','0');
            INSERT INTO calendar VALUES('daily',1,1,1,1,1,1,1,'20200101','20991231');
            INSERT INTO stops VALUES('stop-a','A','Market Street',51.50,-0.10);
            INSERT INTO stops VALUES('stop-b','B','Wollaton Vale',51.51,-0.11);
        """)
        hour, minute = divmod(minutes, 60)
        connection.execute("INSERT INTO stop_times VALUES('stop-a','gtfs-hash',?,?,1)", (f"{hour:02d}:{minute:02d}:00", f"{hour:02d}:{minute:02d}:00"))
        hour, minute = divmod(minutes + 8, 60)
        connection.execute("INSERT INTO stop_times VALUES('stop-b','gtfs-hash',?,?,2)", (f"{hour:02d}:{minute:02d}:00", f"{hour:02d}:{minute:02d}:00"))
    vehicle = SimpleNamespace(
        lat=51.50, lon=-0.10, route="30", destination="Wollaton Vale",
        journey_ref="operator-reference-that-is-not-the-gtfs-id", next_location=None,
    )

    result = service.journey_for_vehicle(vehicle)

    assert result["mode"] == "matched_timetable"
    assert result["match_method"] == "route_time_position"
    assert result["trip_id"] == "gtfs-hash"
    assert result["next_stop_id"] == "stop-a"
