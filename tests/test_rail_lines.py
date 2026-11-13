import json

import pytest

from app.rail_lines import CELL_DEGREES, RailLineLayer, cell_index, level_for_zoom


@pytest.fixture()
def layer(tmp_path):
    path = tmp_path / "track.geojson"
    wiggle = [[-1.9 + i * 0.001, 52.48 + (0.00001 if i % 2 else 0)] for i in range(200)]
    path.write_text(json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "LineString", "coordinates": wiggle}},
        {"type": "Feature", "geometry": {"type": "MultiLineString", "coordinates": [[[-0.1, 51.5], [-0.12, 51.53]]]}},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [0, 0]}},
    ]}), encoding="utf-8")
    return RailLineLayer(path)


def test_zoom_levels():
    assert [level_for_zoom(z) for z in (5, 8.9, 9, 11.9, 12, 16)] == [0, 0, 1, 1, 2, 2]


def test_national_layer_is_simplified_lat_lon_lines(layer):
    lines = layer.national()["lines"]
    assert [line["id"] for line in lines] == [0, 1]
    first = lines[0]["points"]
    assert len(first) == 2  # a near-straight 200 point line collapses to its ends
    assert first[0] == [52.48, -1.9]


def test_cells_return_only_lines_touching_them(layer):
    birmingham = layer.cell(2, *cell_index(-1.85, 52.48))
    london = layer.cell(1, *cell_index(-0.11, 51.51))
    assert [line["id"] for line in birmingham["lines"]] == [0]
    assert [line["id"] for line in london["lines"]] == [1]
    assert layer.cell(2, *cell_index(-4.0, 56.0))["lines"] == []
    assert CELL_DEGREES == 0.5


def test_invalid_cell_requests_are_rejected(layer):
    with pytest.raises(ValueError):
        layer.cell(0, 0, 0)
    with pytest.raises(ValueError):
        layer.cell(1, 9999, 0)


def test_bundled_network_covers_great_britain():
    from pathlib import Path
    layer = RailLineLayer(Path(__file__).resolve().parents[1] / "app" / "assets" / "rail_track_geometry.geojson")
    national = layer.national()
    assert len(national["lines"]) > 1000
    size = len(json.dumps(national, separators=(",", ":")))
    assert size < 250_000
