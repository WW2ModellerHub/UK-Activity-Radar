"""Saved trains alert when cancelled, late or moved to another platform."""
from datetime import datetime, timezone

from fastapi.testclient import TestClient

import app.main as main
from app.commute_alerts import late_band, normalise_preferences, saved_train_alerts, saved_train_status
from app.models import Vehicle
from app.state import LiveState


def _train(**fields):
    base = dict(id="nr:1L56", type="train", name="1L56", lat=52.48, lon=-1.9, destination="Cambridge",
                source="Network Rail TRUST", train_uid="L56", service_date="2026-11-13")
    base.update(fields)
    return Vehicle(**base)


def test_late_bands_only_step_at_meaningful_thresholds():
    assert [late_band(value) for value in (None, -2, 4, 5, 14, 15, 29, 30, 75)] == [0, 0, 0, 5, 5, 15, 15, 30, 60]


def test_alerts_for_lateness_platform_change_and_cancellation():
    late = saved_train_alerts(saved_train_status(_train(delay_minutes=16, platform="4", booked_platform="2")))
    assert [alert["title"] for alert in late] == ["1L56 to Cambridge is 16 min late", "1L56 to Cambridge now at platform 4"]
    assert [alert["key"] for alert in late] == ["saved-train:nr:1L56:late:15", "saved-train:nr:1L56:platform:4"]
    cancelled = saved_train_alerts(saved_train_status(_train(delay_minutes=16, cancelled_at=datetime.now(timezone.utc))))
    assert [alert["title"] for alert in cancelled] == ["1L56 to Cambridge is cancelled"]
    assert saved_train_alerts(saved_train_status(_train(delay_minutes=2, platform="2", booked_platform="2"))) == []
    assert saved_train_alerts(saved_train_status(None)) == []


def test_a_cancellation_known_only_from_the_timetable_note_counts():
    status = saved_train_status(_train(), {"cancelled": {"scope": "whole"}, "platforms": {}})
    assert status["cancelled"] is True


def test_push_preferences_keep_a_short_clean_list_of_saved_trains():
    prefs = normalise_preferences({"mode": "train", "lat": 52, "lon": -1, "saved_trains": ["nr:a", "nr:a", "", "x" * 81, *[f"nr:{i}" for i in range(20)]]})
    assert prefs["saved_trains"][:2] == ["nr:a", "nr:0"]
    assert len(prefs["saved_trains"]) == 12


def test_status_endpoint_reports_saved_trains_wherever_they_are(monkeypatch):
    live = LiveState()
    live.upsert(_train(delay_minutes=7))
    monkeypatch.setattr(main, "state", live)
    client = TestClient(main.app)
    payload = client.get("/api/trains/status", params={"ids": "nr:1L56,nr:gone"}).json()
    found, missing = payload["trains"]
    assert found["found"] is True and found["late_band"] == 5 and found["delay_minutes"] == 7
    assert missing == {"found": False, "id": "nr:gone", "alerts": []}
