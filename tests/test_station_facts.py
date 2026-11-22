"""Station facts: direct destinations from the timetable, bus stops, chips."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from app.schedule_store import ScheduleStore
from app.seo_pages import station_facts_html
from app.smart_places import SmartPlacesService
from app.timeutils import london_tz


class FakeState:
    def all(self, **kwargs):
        return []


def _store(tmp_path: Path) -> ScheduleStore:
    store = ScheduleStore(tmp_path / "schedule.sqlite")
    today = datetime.now(london_tz()).date().isoformat()
    trains = [
        # Hinckley to Leicester and Nuneaton calls, passing a junction.
        ("H00001", [{"tiploc_code": "NUNEATN", "public_departure": "0800"},
                    {"tiploc_code": "HINCKLY", "public_arrival": "0807", "public_departure": "0808"},
                    {"tiploc_code": "NARBJCN", "pass": "0815"},
                    {"tiploc_code": "LESTER", "public_arrival": "0828"}]),
        ("H00002", [{"tiploc_code": "HINCKLY", "public_departure": "0908"},
                    {"tiploc_code": "LESTER", "public_arrival": "0928"}]),
        # Terminates at Hinckley: no departure from it.
        ("H00003", [{"tiploc_code": "LESTER", "public_departure": "1000"},
                    {"tiploc_code": "HINCKLY", "public_arrival": "1020"}]),
        # Freight passing through: not a passenger departure.
        ("F00001", [{"tiploc_code": "NUNEATN", "departure": "1100"},
                    {"tiploc_code": "HINCKLY", "pass": "1108"},
                    {"tiploc_code": "LESTER", "arrival": "1130"}]),
    ]
    with store._connect() as conn:
        conn.executemany("INSERT INTO tiplocs(tiploc,crs,description) VALUES(?,?,?)", [
            ("HINCKLY", "HNK", "HINCKLEY"), ("LESTER", "LEI", "LEICESTER"), ("NUNEATN", "NUN", "NUNEATON")])
        for uid, locations in trains:
            conn.execute(
                """INSERT INTO schedules(train_uid,start_date,end_date,days_runs,stp_indicator,signalling_id,locations_json)
                   VALUES(?,?,?,?,?,?,?)""", (uid, today, today, "1111111", "P", "1L00", json.dumps(locations)))
    store.ensure_call_index()
    store.ready = lambda: True
    return store


def test_direct_destinations_count_passenger_departures_and_where_they_go(tmp_path):
    svc = SmartPlacesService(tmp_path, _store(tmp_path), FakeState())

    direct = svc.direct_destinations("HNK")

    assert direct["trains"] == 2
    assert direct["destinations"] == [{"crs": "LEI", "name": "LEICESTER", "trains": 2}]


def test_bus_stops_near_counts_only_stops_within_reach(tmp_path):
    svc = SmartPlacesService(tmp_path, ScheduleStore(tmp_path / "schedule.sqlite"), FakeState())
    with sqlite3.connect(svc.stop_db) as con:
        con.executescript("CREATE TABLE stops(atco_code TEXT PRIMARY KEY,naptan_code TEXT,name TEXT,indicator TEXT,stop_type TEXT,lat REAL,lon REAL);")
        con.executemany("INSERT INTO stops VALUES(?,?,?,?,?,?,?)", [
            ("A", "a", "Station Road", "", "BCT", 52.5362, -1.3720),
            ("B", "b", "Rugby Road", "", "BCT", 52.5340, -1.3700),
            ("C", "c", "Far Away", "", "BCT", 52.5500, -1.3500),
        ])
    assert svc.bus_stops_near(52.5350, -1.3710) == 2


def test_station_facts_render_chips_and_link_direct_destinations():
    chips, section = station_facts_html("HNK", "Hinckley", {
        "direct": {"trains": 44, "destinations": [{"crs": "LEI", "name": "LEICESTER", "trains": 22},
                                                   {"crs": "XYZ", "name": "SOMEWHERE", "trains": 1}]},
        "bus_stops": 9,
    }, {"LEI": "/station/leicester-lei"})

    assert "2 direct destinations" in chips
    assert "44 departures today" in chips
    assert "9 bus stops within 400 m" in chips
    assert 'href="/station/leicester-lei"' in section and "22 trains today" in section
    assert "1 train today" in section


def test_station_facts_say_nothing_without_data():
    assert station_facts_html("ZZZ", "Nowhere", {}, {}) == ("", "")


def test_direct_trains_between_two_stations_with_journey_times(tmp_path):
    svc = SmartPlacesService(tmp_path, _store(tmp_path), FakeState())

    route = svc.direct_trains("HNK", "LEI")

    assert [(t["depart"], t["arrive"], t["minutes"], t["stops_between"]) for t in route["trains"]] == [
        ("08:08", "08:28", 20, 0), ("09:08", "09:28", 20, 0)]
    assert svc.direct_trains("HNK", "NUN")["trains"] == []      # only arrives from Nuneaton


STATIONS = [
    {"name": "Hinckley", "crs": "HNK", "lat": 52.535, "lon": -1.372, "path": "/station/hinckley-hnk"},
    {"name": "Leicester", "crs": "LEI", "lat": 52.632, "lon": -1.125, "path": "/station/leicester-lei"},
    {"name": "Stratford-upon-Avon", "crs": "SAV", "lat": 52.194, "lon": -1.716, "path": "/station/stratford-upon-avon-sav"},
]


def test_route_paths_resolve_both_ways_even_with_hyphenated_names():
    from app.route_pages import resolve, route_path
    assert route_path(STATIONS[0], STATIONS[1]) == "/trains/hinckley-to-leicester"
    assert resolve("hinckley-to-leicester", STATIONS) == (STATIONS[0], STATIONS[1])
    assert resolve("stratford-upon-avon-to-hinckley", STATIONS) == (STATIONS[2], STATIONS[0])
    assert resolve("hinckley-to-hinckley", STATIONS) is None
    assert resolve("hinckley-to-nowhere", STATIONS) is None


def test_route_page_reads_well(monkeypatch):
    from datetime import datetime
    from app.route_pages import route_page
    data = {"trains": [
        {"depart": "08:08", "arrive": "08:28", "minutes": 20, "stops_between": 1, "operator": "CrossCountry"},
        {"depart": "09:08", "arrive": "09:26", "minutes": 18, "stops_between": 0, "operator": "CrossCountry"}]}
    html = route_page("https://x.test", STATIONS[0], STATIONS[1], data,
                      now=datetime(2026, 11, 21, 8, 30, tzinfo=london_tz()))
    assert "Hinckley to Leicester trains" in html
    assert "2 direct trains today" in html and "typically 19 min, fastest 18 min" in html
    assert "first 08:08, last 09:08" in html
    assert "next direct train leaves Hinckley at <strong>09:08</strong>" in html
    assert 'class="gone"' in html                        # 08:08 has gone
    assert 'href="/trains/leicester-to-hinckley"' in html
    assert "Trainline" not in html                       # no affiliate link set
    empty = route_page("https://x.test", STATIONS[0], STATIONS[1], {"trains": []})
    assert "no direct trains" in empty


def test_route_endpoint_redirects_to_one_address_and_404s_unknown(monkeypatch):
    from fastapi.testclient import TestClient
    import app.main as main
    monkeypatch.setattr(main, "_stations", lambda: STATIONS * 700)
    monkeypatch.setattr(main, "smart_places", None)
    client = TestClient(main.app)
    assert client.get("/trains/hinckley-to-leicester").status_code == 200
    assert client.get("/trains/nowhere-to-leicester").status_code == 404


def test_replacement_buses_are_indexed_by_station_and_day(tmp_path):
    from datetime import date, timedelta
    store = ScheduleStore(tmp_path / "schedule.sqlite")
    today = date(2026, 11, 22)                       # a Sunday
    with store._connect() as conn:
        conn.executemany("INSERT INTO tiplocs(tiploc,crs,description) VALUES(?,?,?)", [
            ("HINCKLY", "HNK", "HINCKLEY"), ("LESTER", "LEI", "LEICESTER")])
        bus = [{"tiploc_code": "HINCKLY", "public_departure": "0910"}, {"tiploc_code": "LESTER", "public_arrival": "0950"}]
        for uid, days, start, end in (("B00001", "0000001", today, today + timedelta(days=7)),     # Sundays only
                                      ("B00002", "0000001", today, today)):
            conn.execute("""INSERT INTO schedules(train_uid,start_date,end_date,days_runs,stp_indicator,signalling_id,
                              locations_json,train_category,origin_name,destination_name) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                         (uid, start.isoformat(), end.isoformat(), days, "N", "0B00", json.dumps(bus), "BR", "HINCKLEY", "LEICESTER"))
        # An ordinary train is not engineering work.
        conn.execute("""INSERT INTO schedules(train_uid,start_date,end_date,days_runs,stp_indicator,signalling_id,locations_json,train_category)
                        VALUES('T00001',?,?,'1111111','P','1L00',?,'OO')""", (today.isoformat(), today.isoformat(), json.dumps(bus)))
        ScheduleStore._rebuild_replacement_index_on(conn, today.isoformat())

    days = store.replacements_at("HNK", today=today)
    assert [d["day"] for d in days] == ["2026-11-22", "2026-11-29"]
    assert days[0]["buses"] == 2 and days[0]["first"] == "09:10"
    assert days[0]["between"] == ["HINCKLEY", "LEICESTER"]
    assert store.replacements_at("XYZ", today=today) == []


def test_engineering_works_read_well_on_station_and_route_pages():
    from datetime import datetime
    from app.route_pages import route_page
    from app.seo_pages import engineering_html
    html = engineering_html("Hinckley", [{"day": "2026-11-22", "buses": 2, "first": "09:10", "last": "21:10",
                                          "between": ["HINCKLEY", "LEICESTER"]}])
    assert "Engineering works at Hinckley" in html
    assert "Sunday 22 November" in html and "2 replacement buses, 09:10 to 21:10" in html
    page = route_page("https://x.test", STATIONS[0], STATIONS[1], {"trains": []}, engineering_days=["2026-11-22"],
                      now=datetime(2026, 11, 21, 8, 0, tzinfo=london_tz()))
    assert "Replacement buses run between Hinckley and Leicester on Sunday 22 November" in page
