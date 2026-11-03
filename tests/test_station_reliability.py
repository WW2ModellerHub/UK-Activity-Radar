import ast
import json
from pathlib import Path

import pytest

import app.reference_data as reference_data
from app.schedule_store import ScheduleStore
from app.smart_places import SmartPlacesService


class FakeState:
    def set_source(self, status):
        self.status = status

    def find_train_by_headcode(self, headcode):
        return None

    def all(self, **kwargs):
        return []


def test_station_merge_keeps_last_known_good_when_windows_replace_is_denied(tmp_path: Path, monkeypatch):
    station_file = tmp_path / "stations.json"
    original = {"00001": {"name": "Existing station", "lat": 52.5, "lon": -1.3}}
    station_file.write_text(json.dumps(original), encoding="utf-8")

    def denied(*args, **kwargs):
        raise PermissionError("destination is locked")

    monkeypatch.setattr(reference_data, "_write_json_atomically", denied)
    count = reference_data.merge_station_file(
        station_file,
        {"00002": {"name": "New station", "lat": 52.6, "lon": -1.4}},
    )

    assert count == 1
    assert json.loads(station_file.read_text(encoding="utf-8")) == original


def test_station_merge_does_not_hide_write_failure_without_a_fallback(tmp_path: Path, monkeypatch):
    def denied(*args, **kwargs):
        raise PermissionError("destination is locked")

    monkeypatch.setattr(reference_data, "_write_json_atomically", denied)
    with pytest.raises(PermissionError):
        reference_data.merge_station_file(
            tmp_path / "stations.json",
            {"00001": {"name": "Only copy", "lat": 52.5, "lon": -1.3}},
        )


def test_smart_places_uses_existing_station_cache_before_naptan_is_ready(tmp_path: Path):
    (tmp_path / "stations.json").write_text(json.dumps({
        "12345": {
            "name": "Hinckley",
            "lat": 52.535,
            "lon": -1.371,
            "crs": "HNK",
            "tiploc": "HINCKLY",
            "source": "Public rail geography fallback",
        },
        # A non-passenger rail location without a CRS must not become a station.
        "54321": {
            "name": "Freight loop",
            "lat": 52.54,
            "lon": -1.38,
            "tiploc": "FREIGHT",
        },
    }), encoding="utf-8")

    service = SmartPlacesService(tmp_path, ScheduleStore(tmp_path / "schedule.sqlite"), FakeState())
    nearby = service.nearest_rail_station(52.55, -1.38)

    assert nearby is not None
    assert nearby["id"] == "rail-station:HNK"
    assert nearby["source"] == "Cached public rail geography"
    assert service.get_place("rail-station:HNK")["name"] == "Hinckley"


def test_demo_collector_is_only_started_behind_demo_mode_gate():
    """Keep browser fixtures/demo vehicles out of the real server path."""
    tree = ast.parse(Path("app/main.py").read_text(encoding="utf-8"))
    run_demo_calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "run_demo"
    ]
    assert len(run_demo_calls) == 1

    guarded_calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        is_demo_gate = (
            isinstance(test, ast.Attribute)
            and isinstance(test.value, ast.Name)
            and test.value.id == "settings"
            and test.attr == "demo_mode"
        )
        if is_demo_gate:
            guarded_calls.extend(
                call for child in node.body for call in ast.walk(child)
                if isinstance(call, ast.Call)
                and isinstance(call.func, ast.Name)
                and call.func.id == "run_demo"
            )
    assert run_demo_calls == guarded_calls
