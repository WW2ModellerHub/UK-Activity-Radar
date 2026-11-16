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
    assert spotter == {"lat": 52.0, "lon": -1.0, "radius_miles": 10.0, "kinds": ["loco", "military", "steam", "test"]}
    noon = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)
    hercules = Vehicle(id="aircraft:1", type="aircraft", name="RRR4501", lat=52.05, lon=-1.0, source="ADSB.lol",
                       aircraft_model="Lockheed Martin C-130 Hercules", cool_candidate=True, cool_category="air",
                       cool_subtype="military-transport",
                       cool_explanation="Military transport aircraft.")
    alert = spotter_alert(hercules, spotter, noon)
    assert alert["key"] == "spot:aircraft:1" and alert["title"] == "Lockheed Martin C-130 Hercules about 3 miles away"
    assert spotter_alert(hercules, spotter, noon.replace(hour=3)) is None  # night
    assert spotter_alert(hercules.model_copy(update={"lat": 53.0}), spotter, noon) is None  # too far
    assert spotter_alert(hercules.model_copy(update={"cool_candidate": False}), spotter, noon) is None  # ordinary
    assert spotter_alert(hercules.model_copy(update={"cool_category": "rescue"}), spotter, noon) is None  # never rescues
    assert spotter_alert(hercules, None, noon) is None
    assert normalise_preferences({"mode": "trains-only"})["spotter"] is None


def test_journey_watch_warns_only_before_leaving_and_only_for_serious_things_on_the_route():
    from datetime import datetime, timezone

    from app.commute_alerts import journey_alerts, journey_bboxes, journey_due
    from app.models import Event

    preferences = normalise_preferences({"mode": "trains-only", "journey": {
        "name": "your drive to Leeds", "points": [[53.70, -1.60], [53.75, -1.55], [53.80, -1.55]], "leave_minutes": 7 * 60 + 30}})
    journey = preferences["journey"]
    assert journey["days"] == [0, 1, 2, 3, 4] and len(journey_bboxes(journey)) == 1
    monday_7 = datetime(2026, 11, 16, 7, 0, tzinfo=timezone.utc)  # GMT, a Monday
    assert journey_due(journey, monday_7)
    assert not journey_due(journey, monday_7.replace(hour=6, minute=30))   # too early
    assert not journey_due(journey, monday_7.replace(hour=8))              # already left
    assert not journey_due(journey, datetime(2026, 11, 14, 7, 0, tzinfo=timezone.utc))  # Saturday

    def event(event_id, lat, lon, rank, active=True):
        return Event(id=event_id, type="incident", name="raw", lat=lat, lon=lon, source="TomTom Traffic",
                     impact={"title": "Crash · M621", "where": "M621 eastbound", "impact": "Delays of about 20 min",
                             "when": "Started 10 min ago", "active": active, "rank": rank})
    events = [event("on-route", 53.752, -1.55, 4), event("minor", 53.76, -1.55, 1),
              event("far", 53.75, -1.40, 4), event("finished", 53.77, -1.55, 4, active=False)]
    alerts = journey_alerts(events, journey, monday_7)
    assert [a["activity_id"] for a in alerts] == ["on-route"]
    assert alerts[0]["title"] == "Crash · M621 on your drive to Leeds"
    assert alerts[0]["key"].startswith("journey:road:2026-11-16:")
    assert journey_alerts(events, journey, monday_7.replace(hour=12)) == []
    assert normalise_preferences({"mode": "trains-only", "journey": {"points": [[1, 1]], "leave_minutes": 10}})["journey"] is None


def test_leave_now_times_the_alert_to_the_train_and_moves_with_its_delay():
    from datetime import datetime, timedelta, timezone

    from app.commute_alerts import leave_alert

    # Always tomorrow at 07:42 UK time, so the plan is never already in the past.
    from zoneinfo import ZoneInfo
    london = ZoneInfo("Europe/London")
    depart = (datetime.now(london) + timedelta(days=1)).replace(hour=7, minute=42, second=0, microsecond=0).astimezone(timezone.utc)
    now = depart - timedelta(minutes=60)
    plans = normalise_preferences({"mode": "trains-only", "leave_plans": [
        {"train_id": "nr:1L56", "train_name": "1L56", "station": "Rugby", "minutes": 15, "depart": depart.isoformat()},
        {"train_id": "nr:old", "station": "Rugby", "minutes": 15, "depart": (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()},
        {"train_id": "nr:bad", "station": "Rugby", "minutes": "soon", "depart": depart.isoformat()},
    ]})["leave_plans"]
    assert [plan["train_id"] for plan in plans] == ["nr:1L56"]
    plan = plans[0]
    on_time = {"found": True, "delay_minutes": 0, "platform": "4"}
    # Leave at 07:25 (15 min + 2 spare). Nothing an hour before; alert from 07:20.
    assert leave_alert(plan, on_time, now) is None
    alert = leave_alert(plan, on_time, depart - timedelta(minutes=21))
    assert alert["title"] == "Leave in 4 min for the 07:42 from Rugby"
    assert alert["body"] == "On time. Platform 4. Allowing 15 min to get there."
    assert leave_alert(plan, on_time, depart - timedelta(minutes=17))["title"] == "Leave now for the 07:42 from Rugby"
    # Ten minutes late: the same moment is now too early, and the wording says so later.
    late = {"found": True, "delay_minutes": 10, "platform": None}
    assert leave_alert(plan, late, depart - timedelta(minutes=21)) is None
    assert "Running 10 min late, now expected 07:52" in leave_alert(plan, late, depart - timedelta(minutes=10))["body"]
    assert "from the timetable" in leave_alert(plan, {"found": False}, depart - timedelta(minutes=20))["body"]
    assert leave_alert(plan, {"found": True, "cancelled": True}, depart - timedelta(minutes=20)) is None
    assert leave_alert(plan, on_time, depart + timedelta(minutes=1)) is None


def test_the_worker_is_served_from_the_top_of_the_site_so_it_covers_the_app_page():
    client = TestClient(main.app)
    response = client.get("/service-worker.js")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/javascript")
    assert response.headers["cache-control"] == "no-cache"
    assert "addEventListener('push'" in response.text
    script = (main.STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert "register('/service-worker.js',{scope:'/'})" in script
    assert "register('/static/service-worker.js')" not in script


def test_without_radar_plus_only_one_saved_train_alerts_once_payments_are_on(monkeypatch):
    from types import SimpleNamespace

    from app.billing import AccessContext, PLUS_FEATURES

    wanted = {"mode": "train", "place_id": "x", "lat": 52.0, "lon": -1.0, "saved_trains": ["nr:1", "nr:2", "nr:3"],
              "spotter": {"lat": 52.0, "lon": -1.0}, "journey": {"points": []}, "leave_plans": [{"train_id": "nr:1"}]}
    request = SimpleNamespace(cookies={})
    # Payments off: nobody can buy Radar+, so nothing is limited.
    monkeypatch.setattr(main, "billing_service", None)
    assert main._free_alert_allowance(wanted, request) == wanted
    # Payments on, free: one saved train, nothing else.
    monkeypatch.setattr(main, "billing_service", object())
    monkeypatch.setattr(main, "_billing_context", lambda request: AccessContext())
    free = main._free_alert_allowance(wanted, request)
    assert free["saved_trains"] == ["nr:1"] and free["mode"] == "trains-only"
    assert free["spotter"] is None and free["journey"] is None and free["leave_plans"] == []
    # Radar+: everything.
    monkeypatch.setattr(main, "_billing_context", lambda request: AccessContext(
        token_valid=True, customer_id="cus_1", plan="plus", subscription_status="active", features=PLUS_FEATURES))
    assert main._free_alert_allowance(wanted, request) == wanted


def test_spotter_tick_boxes_choose_what_alerts():
    from datetime import datetime, timezone

    from app.commute_alerts import spotter_alert
    from app.models import Vehicle

    noon = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)
    def thing(subtype, category="rail", kind_type="train"):
        return Vehicle(id=f"x:{subtype}", type=kind_type, name="1Z99", lat=52.01, lon=-1.0, source="test",
                       cool_candidate=True, cool_category=category, cool_subtype=subtype)
    steam_only = normalise_preferences({"mode": "trains-only", "spotter": {"lat": 52.0, "lon": -1.0, "kinds": ["steam"]}})["spotter"]
    assert steam_only["kinds"] == ["steam"]
    assert spotter_alert(thing("steam"), steam_only, noon) is not None
    assert spotter_alert(thing("test-measurement"), steam_only, noon) is None
    assert spotter_alert(thing("military-helicopter", "air", "aircraft"), steam_only, noon) is None
    everything = normalise_preferences({"mode": "trains-only", "spotter": {"lat": 52.0, "lon": -1.0}})["spotter"]
    assert spotter_alert(thing("freight"), everything, noon) is None          # ordinary freight never alerts
    assert spotter_alert(thing("sar-aircraft", "rescue", "aircraft"), everything, noon) is None   # nor rescues
    # Saved before the tick boxes ("rail", "air") still works.
    assert normalise_preferences({"mode": "trains-only", "spotter": {"lat": 52.0, "lon": -1.0, "kinds": ["rail"]}})["spotter"]["kinds"] == ["loco", "steam", "test"]
    old = {"lat": 52.0, "lon": -1.0, "radius_miles": 10.0, "kinds": ["air", "rail"]}
    assert spotter_alert(thing("military-transport", "air", "aircraft"), old, noon) is not None
    assert spotter_alert(thing("locomotive-move"), old, noon) is not None
