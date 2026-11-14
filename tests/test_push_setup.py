"""Phone alerts work on the hosted site without anyone pasting keys in, and a
saved train alone is enough to get them."""
from dataclasses import replace

from fastapi.testclient import TestClient

import app.main as main
from app.commute_alerts import CommuteAlertStore, normalise_preferences
from app.push_keys import ensure

SUBSCRIPTION = {"endpoint": "https://fcm.googleapis.com/fcm/send/abc", "keys": {"p256dh": "p" * 87, "auth": "a" * 22}}
DEVICE = "d" * 32


def test_the_server_makes_its_own_keys_once_and_keeps_them_private(tmp_path):
    public, private = ensure(tmp_path)
    assert len(public) == 87 and len(private) == 43
    assert ensure(tmp_path) == (public, private)
    assert (tmp_path / "vapid_keys.json").stat().st_mode & 0o077 == 0


def test_keys_are_provisioned_only_for_an_enabled_https_site(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATA_DIR", tmp_path)
    blank = dict(vapid_public_key="", vapid_private_key="", vapid_subject="")
    monkeypatch.setattr(main, "settings", replace(main.settings, push_alerts_enabled=True, public_base_url="http://localhost:8000", **blank))
    main._provision_push_keys()
    assert not main.settings.push_alerts_ready and not (tmp_path / "vapid_keys.json").exists()
    monkeypatch.setattr(main, "settings", replace(main.settings, public_base_url="https://radar.example"))
    main._provision_push_keys()
    assert main.settings.push_alerts_ready and main.settings.vapid_subject == "https://radar.example"
    monkeypatch.setattr(main, "settings", replace(main.settings, push_alerts_enabled=False, **blank))
    main._provision_push_keys()
    assert not main.settings.push_alerts_ready and main.settings.vapid_public_key == ""


def test_alerts_for_saved_trains_alone_need_no_place():
    preferences = normalise_preferences({"mode": "trains-only", "saved_trains": ["nr:1L56"]})
    assert preferences["mode"] == "trains-only" and preferences["saved_trains"] == ["nr:1L56"]
    assert preferences["lat"] is None and preferences["activity_types"] == []


def test_a_test_alert_goes_to_this_device_and_an_expired_one_is_switched_off(tmp_path, monkeypatch):
    store = CommuteAlertStore(tmp_path / "alerts.sqlite")
    monkeypatch.setattr(main, "commute_alert_store", store)
    monkeypatch.setattr(main, "settings", replace(
        main.settings, push_alerts_enabled=True, public_base_url="https://radar.example",
        vapid_public_key="p" * 87, vapid_private_key="k" * 43, vapid_subject="https://radar.example"))
    sent = []
    monkeypatch.setattr(main, "_send_commute_push", lambda subscription, payload: sent.append((subscription["endpoint"], payload["title"])))
    client = TestClient(main.app)
    headers = {"origin": "https://radar.example", "host": "radar.example"}
    assert client.post("/api/alerts/test", json={"device_token": DEVICE}, headers=headers).status_code == 404
    store.upsert(DEVICE, SUBSCRIPTION, {"mode": "trains-only", "saved_trains": []})
    assert client.post("/api/alerts/test", json={"device_token": DEVICE}, headers=headers).status_code == 200
    assert sent == [(SUBSCRIPTION["endpoint"], "Alerts are working")]

    class Gone(Exception):
        status_code = 410

    def gone(subscription, payload):
        raise Gone()
    monkeypatch.setattr(main, "_send_commute_push", gone)
    assert client.post("/api/alerts/test", json={"device_token": DEVICE}, headers=headers).status_code == 410
    assert store.subscription_for(DEVICE) is None
    store.close()


def test_spotter_alerts_fire_for_unusual_things_nearby_in_the_daytime():
    from datetime import datetime, timezone

    from app.commute_alerts import spotter_alert
    from app.models import Vehicle

    preferences = normalise_preferences({"mode": "trains-only", "spotter": {"lat": 52.0, "lon": -1.0, "radius_miles": 10}})
    spotter = preferences["spotter"]
    assert spotter == {"lat": 52.0, "lon": -1.0, "radius_miles": 10.0, "kinds": ["air", "rail"]}
    noon = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)
    hercules = Vehicle(id="aircraft:1", type="aircraft", name="RRR4501", lat=52.05, lon=-1.0, source="ADSB.lol",
                       aircraft_model="Lockheed Martin C-130 Hercules", cool_candidate=True, cool_category="air",
                       cool_explanation="Military transport aircraft.")
    alert = spotter_alert(hercules, spotter, noon)
    assert alert["key"] == "spot:aircraft:1" and alert["title"] == "Lockheed Martin C-130 Hercules about 3 miles away"
    assert spotter_alert(hercules, spotter, noon.replace(hour=3)) is None  # night
    assert spotter_alert(hercules.model_copy(update={"lat": 53.0}), spotter, noon) is None  # too far
    assert spotter_alert(hercules.model_copy(update={"cool_candidate": False}), spotter, noon) is None  # ordinary
    assert spotter_alert(hercules.model_copy(update={"cool_category": "rescue"}), spotter, noon) is None  # never rescues
    assert spotter_alert(hercules, None, noon) is None
    assert normalise_preferences({"mode": "trains-only"})["spotter"] is None
