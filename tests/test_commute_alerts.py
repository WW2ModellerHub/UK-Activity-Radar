from datetime import datetime, timezone

import pytest

from app.commute_alerts import (
    CommuteAlertStore,
    alert_for_activity,
    normalise_preferences,
    validate_subscription,
    within_alert_hours,
)
from app.models import Event, Vehicle


PREFERENCES = {
    "mode": "driving",
    "place_id": "work",
    "place_name": "Work",
    "lat": 52.0,
    "lon": -1.0,
    "radius_miles": 12,
    "start_hour": 5,
    "end_hour": 11,
}


def test_driving_alert_requires_a_relevant_nearby_problem():
    incident = Event(id="road:m1", type="incident", name="M1 lane closure", lat=52.03, lon=-1.02, reason="Two lanes closed causing delays", source="National Highways")
    far_away = incident.model_copy(update={"id": "road:far", "lat": 55.0})
    alert = alert_for_activity(incident, PREFERENCES)
    assert alert["title"] == "Driving near Work?"
    assert "Two lanes closed" in alert["body"]
    assert alert_for_activity(far_away, PREFERENCES) is None


def test_train_alert_detects_delay_and_replacement_bus_language():
    preferences = {**PREFERENCES, "mode": "train"}
    delayed = Vehicle(id="train:late", type="train", name="1A23", lat=52.01, lon=-1.01, delay_minutes=18, status="Delayed", source="Network Rail")
    replacement = Event(id="rail:replacement", type="incident", name="Rail replacement buses", lat=52.02, lon=-1.01, reason="Replacement buses between the affected stations", source="Official rail disruption feed")
    assert alert_for_activity(delayed, preferences)["mode"] == "train"
    assert alert_for_activity(replacement, preferences)["mode"] == "train"


def test_saved_activity_type_alerts_when_matching_object_enters_radius():
    preferences = normalise_preferences({**PREFERENCES, "activity_types": ["vessel", "aircraft"]})
    vessel = Vehicle(id="vessel:1", type="vessel", name="HMS Example", lat=52.01, lon=-1.01, subtitle="Naval vessel", source="AISStream")
    alert = alert_for_activity(vessel, preferences)
    assert alert["mode"] == "my-radar"
    assert "near Work" in alert["title"]


def test_subscription_store_deduplicates_deliveries_and_deactivates(tmp_path):
    store = CommuteAlertStore(tmp_path / "alerts.sqlite")
    subscription = {"endpoint": "https://fcm.googleapis.com/fcm/send/example", "keys": {"p256dh": "p" * 65, "auth": "a" * 16}}
    store.upsert("d" * 32, subscription, PREFERENCES)
    rows = store.active()
    assert len(rows) == 1
    store.mark_delivered(rows[0].endpoint_hash, "road:m1")
    assert store.already_delivered(rows[0].endpoint_hash, "road:m1") is True
    store.remove("d" * 32)
    assert store.status("d" * 32)["active"] is False
    store.close()


def test_untrusted_push_endpoint_and_bad_alert_hours_are_rejected():
    with pytest.raises(ValueError):
        validate_subscription({"endpoint": "https://example.com/collect", "keys": {"p256dh": "p" * 65, "auth": "a" * 16}})
    with pytest.raises(ValueError):
        normalise_preferences({**PREFERENCES, "start_hour": 11, "end_hour": 5})


def test_alert_hours_use_british_local_time():
    preferences = normalise_preferences(PREFERENCES)
    assert within_alert_hours(preferences, datetime(2026, 1, 1, 7, tzinfo=timezone.utc)) is True
    assert within_alert_hours(preferences, datetime(2026, 1, 1, 15, tzinfo=timezone.utc)) is False
