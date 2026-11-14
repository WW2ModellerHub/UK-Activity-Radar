"""The four everyday jobs: planes overhead, trains passing, next bus, my journey,
plus why a train is late."""
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

import app.main as main
from app.models import Vehicle
from app.smart_places import delay_explanation
from app.state import LiveState


def _train(train_id, lat, lon, **fields):
    return Vehicle(id=train_id, type="train", name=train_id.split(":")[-1], lat=lat, lon=lon,
                   source="Network Rail TRUST", last_updated=datetime.now(timezone.utc), **fields)


def test_trains_near_me_are_listed_nearest_first(monkeypatch):
    live = LiveState()
    live.upsert(_train("nr:far", 52.60, -1.90))
    live.upsert(_train("nr:near", 52.481, -1.901, destination="Rugby", delay_minutes=4))
    live.upsert(_train("nr:mid", 52.50, -1.90))
    monkeypatch.setattr(main, "state", live)
    body = TestClient(main.app).get("/api/trains/near", params={"lat": 52.48, "lon": -1.90, "km": 5}).json()
    assert [row["id"] for row in body["trains"]] == ["nr:near", "nr:mid"]
    assert body["trains"][0]["destination"] == "Rugby" and body["trains"][0]["delay_minutes"] == 4


def test_lateness_readings_are_kept_across_reports():
    live = LiveState()
    now = datetime.now(timezone.utc)
    for minutes, tiploc, delay in ((0, "RUGBY", 2), (5, "RUGBY", 2), (12, "NUNEATN", 9), (20, "TMWTHLL", 10)):
        live.upsert(_train("nr:1A01", 52.4, -1.3, rail_tiploc=tiploc, delay_minutes=delay).model_copy(
            update={"last_updated": now + timedelta(minutes=minutes)}))
    history = live.get("nr:1A01").delay_history
    assert [(row["tiploc"], row["delay"]) for row in history] == [("RUGBY", 2), ("NUNEATN", 9), ("TMWTHLL", 10)]


def test_delay_explanation_names_where_time_was_lost():
    history = [{"tiploc": "RUGBY", "delay": 2}, {"tiploc": "NUNEATN", "delay": 9}, {"tiploc": "TMWTHLL", "delay": 10}]
    names = {"RUGBY": "Rugby", "NUNEATN": "Nuneaton", "TMWTHLL": "Tamworth"}
    out = delay_explanation(10, history, names, others_late=3)
    assert out == {"late": 10, "grew_by": 7, "between": ["Rugby", "Nuneaton"], "others_late": 3}
    assert delay_explanation(2, history, names) is None
    steady = [{"tiploc": "RUGBY", "delay": 8}, {"tiploc": "NUNEATN", "delay": 8}]
    assert delay_explanation(8, steady, names) == {"late": 8, "already_late": True}
