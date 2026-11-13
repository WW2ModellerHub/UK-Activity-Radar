import dataclasses
import time
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

import app.main as main
from app.models import Vehicle
from app.state import LiveState


def _train(identifier: str, seconds_ago: int) -> Vehicle:
    return Vehicle(
        id=identifier, type="train", name=identifier[-4:], lat=52.5, lon=-1.5,
        source="test", last_updated=datetime.now(timezone.utc) - timedelta(seconds=seconds_ago),
    )


def test_websocket_payload_carries_server_clock_and_keeps_capped_trains_stable(monkeypatch):
    live = LiveState()
    monkeypatch.setattr(main, "state", live)
    monkeypatch.setattr(main, "settings", dataclasses.replace(main.settings, train_max_stream=100))
    # 120 trains at national zoom with a 100 train cap.
    for index in range(120):
        train = _train(f"td:XX:{index:04d}", 300 - index)
        train.cool_candidate = True
        live._vehicles[train.id] = train
    client = TestClient(main.app)
    with client.websocket_connect("/ws", headers={"origin": "http://testserver"}) as socket:
        socket.send_json({"action": "viewport", "bbox": [-3, 51, 0, 54], "zoom": 8, "types": ["train"]})
        first = socket.receive_json()
        while "activities" not in first:
            first = socket.receive_json()
        assert abs(first["server_time_ms"] - time.time() * 1000) < 5000
        shown = {row["id"] for row in first["activities"]}
        assert len(shown) == 100
        # The oldest (currently hidden) trains report again. Without sticky
        # selection they would evict 20 trains already on this viewer's map.
        for index in range(20):
            train = _train(f"td:XX:{index:04d}", 0)
            train.last_updated = datetime.now(timezone.utc) + timedelta(seconds=index + 1)
            live.upsert(train)
        update = socket.receive_json()
        delta = update.get("activity_delta") or {}
        assert delta.get("remove", []) == []
        assert all(row["id"] in shown for row in delta.get("upsert", []))
