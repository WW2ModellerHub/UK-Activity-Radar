import json

from app.track_graph import PhysicalRailwayGraph


def test_physical_graph_follows_track_curve_instead_of_endpoint_chord(tmp_path):
    geometry = tmp_path / "track.geojson"
    geometry.write_text(json.dumps({
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature", "properties": {},
                "geometry": {"type": "MultiLineString", "coordinates": [[
                    [-1.50, 52.50], [-1.46, 52.55], [-1.42, 52.50],
                ]]},
            },
            {
                "type": "Feature", "properties": {},
                "geometry": {"type": "MultiLineString", "coordinates": [[
                    [-1.42, 52.50], [-1.38, 52.46], [-1.34, 52.50],
                ]]},
            },
        ],
    }), encoding="utf-8")
    graph = PhysicalRailwayGraph(geometry)

    path = graph.route([[52.50, -1.50], [52.50, -1.34]])

    assert path[0] == [52.50, -1.50]
    assert path[-1] == [52.50, -1.34]
    assert [52.55, -1.46] in path
    assert [52.46, -1.38] in path


def test_parallel_platform_line_does_not_disconnect_main_route(tmp_path):
    geometry = tmp_path / "track.geojson"
    geometry.write_text(json.dumps({
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature", "properties": {},
                "geometry": {"type": "MultiLineString", "coordinates": [[
                    [-1.50, 52.50], [-1.40, 52.50], [-1.30, 52.50],
                ]]},
            },
            {
                "type": "Feature", "properties": {},
                "geometry": {"type": "MultiLineString", "coordinates": [[
                    [-1.405, 52.5001], [-1.395, 52.5001],
                ]]},
            },
        ],
    }), encoding="utf-8")
    graph = PhysicalRailwayGraph(geometry)

    path = graph.route([[52.5001, -1.40], [52.50, -1.30]])

    assert path
    assert path[-1] == [52.50, -1.30]
