"""Tap a line to see its trains; find the stretches where trains are late."""
import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

import app.main as main
from app.models import Vehicle
from app.rail_lines import RailLineLayer
from app.rail_sections import delay_hotspots, line_at
from app.state import LiveState

NOW = datetime(2026, 11, 13, 21, 0, tzinfo=timezone.utc)
STATIONS = [
    {"name": "Aston", "lat": 52.0, "lon": -1.0},
    {"name": "Bury", "lat": 52.0, "lon": -0.6},
    {"name": "Crewe", "lat": 52.3, "lon": -0.6},
]


def _layer(tmp_path: Path) -> RailLineLayer:
    # Aston -> midway -> Bury (two fragments joined end to end), and a branch
    # Bury -> Crewe that meets at Bury, making Bury a junction.
    features = [
        [[-1.0, 52.0], [-0.8, 52.0]],
        [[-0.8, 52.0], [-0.6, 52.0]],
        [[-0.6, 52.0], [-0.6, 52.3]],
        [[-0.6, 52.0], [-0.4, 52.0]],
    ]
    path = tmp_path / "lines.geojson"
    path.write_text(json.dumps({"features": [
        {"type": "Feature", "geometry": {"type": "LineString", "coordinates": line}} for line in features
    ]}), encoding="utf-8")
    return RailLineLayer(path)


def _train(train_id, lon, lat=52.0, **fields):
    return Vehicle(id=train_id, type="train", name=train_id.upper(), lat=lat, lon=lon,
                   source="Network Rail TRUST", last_updated=NOW, **fields)


def test_tapping_a_line_lists_its_trains_in_order_between_junctions(tmp_path):
    layer = _layer(tmp_path)
    trains = [_train("b", -0.7, delay_minutes=3), _train("a", -0.95), _train("off", -0.7, lat=52.2),
              _train("branch", -0.6, lat=52.2)]
    result = line_at(layer, trains, STATIONS, 52.001, -0.9, 0.5, NOW)
    assert result["found"] is True
    assert (result["from"], result["to"]) == ("Aston", "Bury")
    assert [row["id"] for row in result["trains"]] == ["a", "b"]
    assert 27 < result["length_km"] < 28
    assert line_at(layer, trains, STATIONS, 51.0, -0.9, 0.5, NOW) == {"found": False}


def test_delay_hotspots_rank_stretches_by_lateness(tmp_path):
    layer = _layer(tmp_path)
    trains = [
        _train("x", -0.9, delay_minutes=12), _train("y", -0.7, delay_minutes=20),
        _train("z", -0.6, lat=52.2, delay_minutes=6), _train("ok", -0.9, delay_minutes=2),
        _train("gone", -0.6, lat=52.1, cancelled_at=NOW),
    ]
    spots = delay_hotspots(layer, trains, STATIONS, NOW)
    assert [(s["from"], s["to"], s["late"], s["cancelled"]) for s in spots] == [
        ("Aston", "Bury", 2, 0), ("Bury", "Crewe", 1, 1),
    ]
    assert spots[0]["average_delay"] == 16 and spots[0]["worst_delay"] == 20


def test_line_endpoint_answers_for_a_tap(monkeypatch, tmp_path):
    live = LiveState()
    train = _train("nr:1", -0.9, delay_minutes=7)
    train.last_updated = datetime.now(timezone.utc)
    live.upsert(train)
    monkeypatch.setattr(main, "state", live)
    monkeypatch.setattr(main, "_delay_hotspot_cache", (0.0, []))
    monkeypatch.setattr(main, "rail_line_layer", _layer(tmp_path))
    monkeypatch.setattr(main, "_passenger_stations", lambda: STATIONS)
    client = TestClient(main.app)
    body = client.get("/api/rail/line", params={"lat": 52.0005, "lon": -0.85, "zoom": 13}).json()
    assert body["found"] is True and [row["id"] for row in body["trains"]] == ["nr:1"]
    assert client.get("/api/rail/delays").json()["hotspots"][0]["late"] == 1
