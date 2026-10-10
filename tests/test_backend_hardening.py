import json
import time
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import app.collectors.bods as bods_module
from app.collectors.bods import BODSCollector
from app.collectors.stomp_client import StompFeed
from app.reference_data import merge_station_file


def test_station_file_merge_preserves_existing_rows_and_cleans_temp_file(tmp_path: Path):
    station_file = tmp_path / "stations.json"
    merge_station_file(station_file, {"00001": {"name": "One"}})
    count = merge_station_file(station_file, {"00002": {"name": "Two"}})

    assert count == 2
    assert set(json.loads(station_file.read_text(encoding="utf-8"))) == {"00001", "00002"}
    assert not station_file.with_suffix(".json.tmp").exists()


def test_stomp_stop_waits_for_worker_shutdown():
    class ControlledFeed(StompFeed):
        def __init__(self):
            super().__init__(SimpleNamespace(), state=None, name="test", topic="test")
            self.started = Event()
            self.finished = Event()

        def _run(self):
            self.started.set()
            self._stop.wait()
            time.sleep(0.05)
            self.finished.set()

    feed = ControlledFeed()
    feed.start()
    assert feed.started.wait(1)
    feed.stop()

    assert feed.finished.is_set()
    assert not feed._thread.is_alive()


def test_successful_bods_snapshot_clears_predictions_removed_upstream(monkeypatch):
    settings = SimpleNamespace(
        bods_api_key="", bods_max_age_seconds=300, bods_poll_seconds=10,
    )

    class State:
        def source(self, name):
            return None

        def set_source(self, status):
            if status.mode == "ready":
                collector._stop.set()

        def upsert(self, vehicle):
            raise AssertionError("empty snapshot should not upsert")

    collector = BODSCollector(settings, State())
    collector._update_predictions([{
        "stop_ref": "STOP1", "expected": "2099-01-01T00:00:00+00:00",
    }])
    collector._fetch = lambda: (b"snapshot", "application/test", "bulk-siri")
    monkeypatch.setattr(bods_module, "parse_bods_payload", lambda payload, content_type: [])
    monkeypatch.setattr(bods_module, "parse_siri_predictions_from_payload", lambda payload, content_type: [])

    collector._run()

    assert collector._stop_predictions == {}
