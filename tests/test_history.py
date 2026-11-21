"""The radar's own day-by-day record: train lateness and unusual finds."""
from datetime import date, timedelta

from fastapi.testclient import TestClient

import app.main as main
from app.history import HistoryStore
from app.models import Vehicle


def _train(delay, *, uid="C12345", cancelled=False, cool=False):
    return Vehicle(id="nr:1", type="train", name="1A23", headcode="1A23", train_uid=uid, operator="CrossCountry",
                   origin="BHM", destination="LEI", lat=52.5, lon=-1.4, source="Network Rail", delay_minutes=delay,
                   cancelled_at="2026-11-20T10:00:00Z" if cancelled else None, cool_candidate=cool,
                   cool_category="rail" if cool else None, cool_badge="STEAM" if cool else None,
                   cool_score=90 if cool else 0, position_label="Confirmed report at NUNEATON")


def test_a_train_is_usually_on_time_late_or_cancelled_from_earlier_days():
    store = HistoryStore(":memory:")
    days = [(date.today() - timedelta(days=n)).isoformat() for n in range(1, 6)]
    for day, (delay, cancelled) in zip(days, [(0, False), (2, False), (12, False), (None, True), (1, False)]):
        store.record([_train(delay, cancelled=cancelled)], day=day)
        store.record([_train(delay, cancelled=cancelled)], day=day)      # sampled twice: still one day
    found = store.usually(_train(0))
    assert found == {"days": 5, "late": 1, "cancelled": 1, "average": 3.8}
    assert store.usually(_train(0, uid="OTHER")) is None                 # never seen: no claim
    by_day = store.operator_days("CrossCountry")
    assert len(by_day) == 5 and sum(d["on_time"] for d in by_day) == 3


def test_unusual_finds_become_daily_archive_pages(monkeypatch):
    store = HistoryStore(":memory:")
    store.record([_train(0, cool=True)], day="2026-11-20")
    store.record([_train(0, cool=True)], day="2026-11-21")
    monkeypatch.setattr(main, "history_store", store)
    client = TestClient(main.app)
    page = client.get("/discover/2026-11-20")
    assert page.status_code == 200 and "Friday 20 November 2026" in page.text and "1A23" in page.text
    assert "Near Nuneaton." in page.text and "/discover/2026-11-21" in page.text
    assert client.get("/discover/2026-01-01").status_code == 404
    assert "/discover/2026-11-21" in client.get("/discover-archive").text
