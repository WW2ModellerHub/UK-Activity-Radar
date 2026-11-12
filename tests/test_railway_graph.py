from app.railway_graph import RailwayRoutingGraph
from app.schedule_store import ScheduleStore
from app.track_graph import PhysicalRailwayGraph
import json


def test_railway_graph_routes_over_published_intermediate_points():
    coordinates = {
        "START": (52.50, -1.50),
        "JUNCTION": (52.52, -1.45),
        "END": (52.55, -1.40),
        "DETOUR": (53.10, -2.10),
    }
    graph = RailwayRoutingGraph(
        [("START", "JUNCTION", 50), ("JUNCTION", "END", 50), ("START", "DETOUR", 1), ("DETOUR", "END", 1)],
        coordinates,
    )
    assert graph.route("START", "END") == ["START", "JUNCTION", "END"]


def test_railway_graph_returns_empty_for_unconnected_locations():
    graph = RailwayRoutingGraph([("A", "B", 1)], {"A": (52, -1), "B": (52.1, -1), "C": (53, -1)})
    assert graph.route("A", "C") == []


def test_railway_graph_can_ignore_a_long_direct_chord():
    coordinates = {
        "START": (52.50, -1.50), "JUNCTION": (52.53, -1.44), "END": (52.56, -1.38),
    }
    graph = RailwayRoutingGraph(
        [("START", "END", 20), ("START", "JUNCTION", 50), ("JUNCTION", "END", 50)],
        coordinates,
    )
    assert graph.route("START", "END", avoid_direct_edge=True) == ["START", "JUNCTION", "END"]


def test_schedule_route_expands_a_long_chord_through_the_railway_graph(tmp_path):
    store = ScheduleStore(tmp_path / "schedule.sqlite")
    with store._connect() as connection:
        connection.executemany(
            "INSERT INTO tiplocs(tiploc,description,lat,lon) VALUES(?,?,?,?)",
            [("START", "Start", 52.50, -1.50), ("JUNCTION", "Junction", 52.55, -1.44), ("END", "End", 52.56, -1.38)],
        )
        schedule_id = connection.execute(
            "INSERT INTO schedules(train_uid,locations_json) VALUES(?,?)",
            ("TEST01", json.dumps([{"tiploc_code": "START"}, {"tiploc_code": "END"}])),
        ).lastrowid
        connection.executemany(
            "INSERT INTO rail_edges(from_tiploc,to_tiploc,service_count) VALUES(?,?,?)",
            [("START", "JUNCTION", 100), ("JUNCTION", "END", 100)],
        )
    assert store.route_points(schedule_id, "START", "END") == [
        [52.50, -1.50], [52.55, -1.44], [52.56, -1.38]
    ]


def test_schedule_route_rejects_an_unproven_long_straight_chord(tmp_path):
    store = ScheduleStore(tmp_path / "schedule.sqlite")
    store._physical_routing_graph = PhysicalRailwayGraph(tmp_path / "missing-track-geometry.geojson")
    with store._connect() as connection:
        connection.executemany(
            "INSERT INTO tiplocs(tiploc,description,lat,lon) VALUES(?,?,?,?)",
            [("NUNEATON", "Nuneaton", 52.526, -1.464), ("COLESHIL", "Coleshill", 52.516, -1.706)],
        )
        schedule_id = connection.execute(
            "INSERT INTO schedules(train_uid,locations_json) VALUES(?,?)",
            ("TEST02", json.dumps([{"tiploc_code": "NUNEATON"}, {"tiploc_code": "COLESHIL"}])),
        ).lastrowid
        connection.execute(
            "INSERT INTO rail_edges(from_tiploc,to_tiploc,service_count) VALUES(?,?,?)",
            ("NUNEATON", "COLESHIL", 100),
        )
    assert store.route_points(schedule_id, "NUNEATON", "COLESHIL") == []
