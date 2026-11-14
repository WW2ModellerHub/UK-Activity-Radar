"""Each operator's service in one word, from the live trains."""
from datetime import datetime, timezone

from fastapi.testclient import TestClient

import app.main as main
from app.line_status import operator_status
from app.models import Vehicle
from app.state import LiveState


def _train(index, operator, delay=0, **fields):
    return Vehicle(id=f"nr:{operator[:2]}{index}", type="train", name=f"1A{index:02d}", lat=52, lon=-1, source="Network Rail TRUST",
                   operator=operator, delay_minutes=delay, destination="London", **fields)


def test_operators_are_graded_and_listed_worst_first():
    now = datetime.now(timezone.utc)
    trains = (
        [_train(i, "Northern", 25) for i in range(5)] + [_train(i + 5, "Northern", 0) for i in range(5)]
        + [_train(i, "LNER", 7) for i in range(2)] + [_train(i + 2, "LNER", 0) for i in range(8)]
        + [_train(i, "Southern", 0) for i in range(9)] + [_train(9, "Southern", 3)]
        + [_train(i, "Merseyrail", 30) for i in range(2)]
        + [_train(1, "Some Freight Co", 90)]
        + [_train(i, "ScotRail", 0) for i in range(8)] + [_train(8, "ScotRail", None, cancelled_at=now), _train(9, "ScotRail", None, cancelled_at=now)]
    )
    rows = operator_status(trains)
    assert [(row["operator"], row["status"]) for row in rows] == [
        ("Northern", "severe"), ("LNER", "minor"), ("ScotRail", "minor"), ("Southern", "good"), ("Merseyrail", "unknown")]
    northern = rows[0]
    assert (northern["late"], northern["running"], northern["average_delay"]) == (5, 10, 25)
    assert northern["worst"][0]["delay_minutes"] == 25 and len(northern["worst"]) == 5
    assert rows[2]["cancelled"] == 2 and rows[2]["worst"][0]["cancelled"] is True


def test_the_status_endpoint_serves_the_board(monkeypatch):
    state = LiveState()
    for i in range(6):
        state.upsert(_train(i, "Avanti West Coast", 12, last_updated=datetime.now(timezone.utc)))
    monkeypatch.setattr(main, "state", state)
    monkeypatch.setattr(main, "_operator_status_cache", (0.0, []))
    rows = TestClient(main.app).get("/api/rail/status").json()["operators"]
    assert rows[0]["operator"] == "Avanti West Coast" and rows[0]["status"] == "severe"
