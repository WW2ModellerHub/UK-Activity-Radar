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


def _closure(event_id, lat=52.03, lon=-1.02, **impact):
    facts = {"title": "Road closed · A5", "what": "Road closed", "where": "A5 Watling Street, Hinckley to Nuneaton",
             "impact": None, "when": "Until 18:00", "active": True, "rank": 4}
    facts.update(impact)
    return Event(id=event_id, type="incident", name="Closed", reason="Closed", road="A5", lat=lat, lon=lon,
                 source="TomTom Traffic", impact=facts)


def test_driving_alert_says_what_and_where_in_plain_words():
    alert = alert_for_activity(_closure("tomtom:1"), PREFERENCES)
    assert alert["title"] == "Road closed near Work"
    assert alert["body"] == "A5 Watling Street, Hinckley to Nuneaton · Until 18:00"
    assert "Closed: Closed" not in alert["body"]
    assert alert_for_activity(_closure("tomtom:far", lat=55.0), PREFERENCES) is None
    # Plain roadworks, and things that have finished, are not worth a notification.
    assert alert_for_activity(_closure("tomtom:2", what="Roadworks", rank=1), PREFERENCES) is None
    assert alert_for_activity(_closure("tomtom:3", active=False), PREFERENCES) is None


def test_the_same_closure_alerts_once_even_when_the_feed_renames_or_splits_it():
    first = alert_for_activity(_closure("tomtom:TTR1"), PREFERENCES)
    reissued = alert_for_activity(_closure("tomtom:TTR1-new-version"), PREFERENCES)
    other_direction = alert_for_activity(_closure("tomtom:TTR2", lat=52.031, lon=-1.021), PREFERENCES)
    assert first["key"] == reissued["key"] == other_direction["key"]
    elsewhere = alert_for_activity(_closure("tomtom:TTR3", lat=52.08), PREFERENCES)
    assert elsewhere["key"] != first["key"]


def test_tomtom_ids_drop_the_part_that_changes_every_few_minutes():
    from app.traffic_incidents import parse_incidents
    payload = lambda version: {"incidents": [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [-1.37, 52.54]},
        "properties": {"id": f"TTI-{version}-TTR33205440276014001", "iconCategory": 8, "magnitudeOfDelay": 4,
                       "events": [{"description": "Closed"}], "from": "A", "to": "B", "timeValidity": "present"}}]}
    first = parse_incidents(payload("70ff94db-7aba-46f4-9464-94224a9926dd"))[0]
    later = parse_incidents(payload("12345678-aaaa-bbbb-cccc-1234567890ab"))[0]
    assert first.source_id == later.source_id == "TTR33205440276014001"


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


def test_nearby_road_alerts_stop_after_three_a_day(tmp_path):
    from app.commute_alerts import MAX_PLACE_ROAD_ALERTS_PER_DAY, road_key_prefix
    store = CommuteAlertStore(tmp_path / "alerts.sqlite")
    prefix = road_key_prefix()
    for index in range(MAX_PLACE_ROAD_ALERTS_PER_DAY):
        store.mark_delivered("phone", f"{prefix}Road closed:A5:52.0{index},-1.0")
    store.mark_delivered("phone", "road:2020-01-01:Road closed:A5:52.0,-1.0")  # another day
    store.mark_delivered("phone", "saved-train:nr:1:late:5")                 # not a road alert
    assert store.delivered_count("phone", prefix) == MAX_PLACE_ROAD_ALERTS_PER_DAY
    assert store.delivered_count("other-phone", prefix) == 0
    store.close()
