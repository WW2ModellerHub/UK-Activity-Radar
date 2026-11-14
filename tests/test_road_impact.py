"""Incidents and roadworks say what they mean for a driver, and only show
while they matter."""
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

import app.main as main
from app.collectors.national_highways import parse_closure_payload
from app.engagement import build_today_payload
from app.models import Event, Vehicle
from app.road_impact import describe
from app.state import AIRCRAFT_VISIBLE_MINUTES, LiveState

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)


def _record(**fields):
    record = {
        "id": "REC1",
        "situationRecordCreationTime": "2026-11-09T11:25:55Z",
        "situationRecordVersionTime": "2026-11-09T11:25:55Z",
        "locationReference": {"locationForDisplay": {"latitude": 52.64, "longitude": -1.22}, "roadNumber": "M1",
                              "locationDescription": "M1 southbound between J22 and J21A"},
    }
    record.update(fields)
    return {"payloadPublication": {"situation": [{"situationRecord": [record]}]}}


def test_works_times_come_from_the_validity_not_the_record_creation():
    payload = _record(
        roadOrCarriagewayOrLaneManagementType="laneClosures", numberOfLanesRestricted="2",
        validity={"validityTimeSpecification": {
            "overallStartTime": "2026-11-14T20:00:00Z", "overallEndTime": "2026-11-15T06:00:00Z",
            "validPeriod": [{"startOfPeriod": "2026-11-14T20:00:00Z", "endOfPeriod": "2026-11-15T06:00:00Z"}],
        }},
        cause={"causeType": "roadMaintenance"},
    )
    event = parse_closure_payload(payload, "planned")[0]
    assert event.start_time == datetime(2026, 11, 14, 20, 0, tzinfo=timezone.utc)
    facts = describe(event, NOW)
    assert facts["what"] == "Lane closure" and facts["title"] == "Lane closure · M1"
    assert facts["where"] == "M1 southbound between J22 and J21A"
    assert facts["impact"] == "2 lanes closed"
    assert facts["active"] is False and facts["when"] == "From 20:00 to tomorrow 06:00"
    assert facts["worth_showing"] is True  # starts within hours
    assert describe(event, NOW + timedelta(hours=9))["active"] is True


def test_an_incident_reports_delay_queue_cause_and_the_public_comment():
    payload = _record(
        cause={"causeType": "accident"}, impact={"delays": {"delayTimeValue": "1200"}},
        queueLength="2500", numberOfLanesRestricted="1",
        generalPublicComment=[{"comment": {"values": {"value": [{"lang": "en", "value": "Two vehicles involved. Recovery on scene."}]}}}],
        validity={"validityTimeSpecification": {"overallStartTime": "2026-11-14T11:35:00Z"}},
    )
    event = parse_closure_payload(payload, "unplanned")[0]
    facts = describe(event, NOW)
    assert facts["what"] == "Crash"
    assert facts["impact"] == "1 lane closed · Delays of about 20 min · Queue of 2.5 km"
    assert facts["when"] == "Started 25 min ago"
    assert "Recovery on scene" in facts["note"]
    assert facts["active"] and facts["rank"] >= 3


def test_long_running_works_that_restrict_nothing_are_not_shown():
    payload = _record(cause={"causeType": "roadMaintenance"}, numberOfLanesRestricted="0", operatorActionStatus="implemented",
                      validity={"validityTimeSpecification": {"overallStartTime": "2026-11-09T11:00:00Z", "overallEndTime": "2027-11-30T09:00:00Z"}})
    event = parse_closure_payload(payload, "planned")[0]
    assert event.lanes_closed is None or event.lanes_closed == "0"
    assert describe(event, NOW)["worth_showing"] is False


def test_finished_and_far_future_works_are_not_shown():
    far = Event(id="f", type="road", name="x", lat=52, lon=-1, source="Street Manager", road="High St",
                start_time=NOW + timedelta(days=30), end_time=NOW + timedelta(days=31),
                details={"management": "Road closure", "planned": True})
    done = Event(id="d", type="road", name="x", lat=52, lon=-1, source="Street Manager", road="High St",
                 start_time=NOW - timedelta(days=3), end_time=NOW - timedelta(hours=1),
                 details={"management": "Road closure", "planned": True})
    assert describe(far, NOW)["worth_showing"] is False
    assert describe(done, NOW)["worth_showing"] is False


def test_local_works_say_who_is_doing_them():
    works = Event(id="s", type="road", name="x", lat=52, lon=-1, source="DfT Street Manager Open Data", road="Station Road",
                  start_time=NOW - timedelta(hours=2), end_time=NOW + timedelta(days=2),
                  details={"management": "Multi way signals", "cause": "Gas", "promoter": "Cadent Gas", "planned": True})
    facts = describe(works, NOW)
    assert facts["what"] == "Temporary traffic lights"
    assert facts["why"] == "Gas works by Cadent Gas"
    assert facts["when"].startswith("Until ")


def test_right_now_prefers_a_serious_local_incident_and_never_a_bus_stop_closure():
    state = LiveState()
    crash = Event(id="c", type="incident", name="raw", lat=52.48, lon=-1.90, source="National Highways", road="M6",
                  start_time=datetime.now(timezone.utc) - timedelta(minutes=10),
                  details={"cause": "Accident", "lanes_closed": 2})
    far_closure = Event(id="far", type="incident", name="raw", lat=55.9, lon=-3.2, source="National Highways", road="M8",
                        start_time=datetime.now(timezone.utc) - timedelta(minutes=10), details={"management": "Road closed"})
    stop = Event(id="stop", type="bus", name="Smallbrook Road - Bus Stop Closure", lat=52.49, lon=-1.89,
                 source="DfT BODS Disruptions", reason="Stop will be closed", details={"bus_disruption": True, "stop": "Wrens Nest"})
    for event in (crash, far_closure, stop):
        state.upsert_event(event)
    near = build_today_payload(state.all_activities(), lat=52.48, lon=-1.9)
    assert [row["activity_id"] for row in near["disruptions"]] == ["c"]
    assert near["disruptions"][0]["name"] == "Crash · M6"
    national = build_today_payload(state.all_activities())
    assert "stop" not in [row["activity_id"] for row in national["disruptions"]]


def test_stale_aircraft_leave_the_map():
    state = LiveState()
    now = datetime.now(timezone.utc)
    state.upsert(Vehicle(id="aircraft:fresh", type="aircraft", name="RRR1", lat=52, lon=-1, source="ADSB.lol", last_updated=now))
    state.upsert(Vehicle(id="aircraft:old", type="aircraft", name="RRR2", lat=52, lon=-1, source="ADSB.lol",
                         last_updated=now - timedelta(minutes=AIRCRAFT_VISIBLE_MINUTES + 5)))
    assert {row.id for row in state.all(include_buses=False, include_vessels=False)} == {"aircraft:fresh"}


def test_flight_route_works_for_a_plane_the_server_does_not_keep(monkeypatch):
    calls = []
    monkeypatch.setattr(main.flight_route_lookup, "lookup", lambda callsign, lat, lon: calls.append((callsign, lat, lon)) or None)
    monkeypatch.setattr(main, "state", LiveState())
    client = TestClient(main.app)
    response = client.get("/api/activity/aircraft:abc/flight-route", params={"callsign": "EZY12", "lat": 52.1, "lon": -1.2})
    assert response.status_code == 200 and calls == [("EZY12", 52.1, -1.2)]
    assert client.get("/api/activity/aircraft:abc/flight-route").status_code == 404
