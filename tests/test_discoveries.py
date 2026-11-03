from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app.discoveries import (
    DiscoveryClaimError,
    catalogue_payload,
    claim_activity,
    missions_payload,
)
from app.models import Event, Vehicle


NOW = datetime(2026, 10, 20, 12, 30, tzinfo=timezone.utc)


def _vehicle(**updates) -> Vehicle:
    values = {
        "id": "live:43c001",
        "type": "aircraft",
        "name": "RAF Eurofighter Typhoon RAZOR11",
        "aircraft_model": "Eurofighter Typhoon FGR4",
        "lat": 52.4,
        "lon": -1.5,
        "source": "ADSB.lol military filter",
        "source_id": "43c001",
        "last_updated": NOW - timedelta(seconds=30),
    }
    values.update(updates)
    return Vehicle(**values)


def _error(activity, code: str) -> DiscoveryClaimError:
    with pytest.raises(DiscoveryClaimError) as raised:
        claim_activity(activity, now=NOW)
    assert raised.value.code == code
    return raised.value


def test_catalogue_is_stable_and_contains_only_safe_classified_subtypes():
    payload = catalogue_payload()
    rows = payload["collectibles"]

    assert payload["version"] == 1
    assert payload["count"] == 20
    assert len({row["id"] for row in rows}) == len(rows)
    assert all(row["id"] == row["key"] == f"{row['category']}:{row['subtype']}" for row in rows)
    assert {row["category"] for row in rows} == {"air", "rail", "sea"}
    assert all(row["category"] != "rescue" for row in rows)
    assert "air:military-unresolved" not in {row["id"] for row in rows}
    assert "air:military-trainer" not in {row["id"] for row in rows}
    assert "sea:tug" not in {row["id"] for row in rows}
    assert "rail:test-measurement" in {row["id"] for row in rows}
    assert all(row["safety_note"] for row in rows)


def test_weekly_missions_are_stable_map_discovery_definitions():
    first = missions_payload(now=NOW)
    second = missions_payload(now=NOW + timedelta(hours=8))
    catalogue_ids = {row["id"] for row in catalogue_payload()["collectibles"]}

    assert first == second
    assert first["week"] == "2026-W43"
    assert first["starts_at"] == "2026-10-19T00:00:00+00:00"
    assert first["ends_at"] == "2026-10-26T00:00:00+00:00"
    assert len(first["missions"]) == 7
    for mission in first["missions"]:
        assert set(mission["targets"]) <= catalogue_ids
        assert set(mission["categories"]) <= {"air", "rail", "sea"}
        assert mission["total"] >= 2
        assert mission["ends_at"] == first["ends_at"]
        assert "Map discovery only" in mission["safety_note"]


def test_claim_returns_a_safe_stateless_collectible_record():
    activity = _vehicle(vehicle_ref="ZJ939")
    discovery = claim_activity(activity, now=NOW)

    assert discovery["id"] == discovery["key"] == "air:combat-aircraft"
    assert discovery["activity_id"] == activity.id
    assert discovery["category"] == "air"
    assert discovery["subtype"] == "combat-aircraft"
    assert discovery["title"] == "Fast Jet"
    assert discovery["confidence"] == "high"
    assert discovery["freshness"] == "live"
    assert discovery["age_seconds"] == 30
    assert discovery["fresh_for_seconds"] == 600
    assert discovery["broad_region"] == "Central UK"
    assert discovery["identity_level"] == "exact"
    assert discovery["source"] == "ADSB.lol military filter"
    assert "lat" not in discovery and "lon" not in discovery


def test_special_passenger_headcode_is_not_mislabelled_as_railtour():
    railtour = _vehicle(
        id="nr:551Z802X20",
        type="train",
        name="1Z80",
        subtitle="Departure • CHESTERFIELD • P1",
        aircraft_model=None,
        headcode="1Z80",
        vehicle_ref=None,
        source="Network Rail TRUST",
        source_id="551Z802X20",
        last_updated=NOW - timedelta(minutes=4),
    )

    discovery = claim_activity(railtour, now=NOW)

    assert discovery["key"] == "rail:special-passenger"
    assert discovery["identity_level"] == "type"
    assert "does not confirm its locomotive or formation" in discovery["identity_note"]
    assert discovery["fresh_for_seconds"] == 2700


def test_real_measurement_train_is_not_rejected_for_the_word_test():
    measurement = _vehicle(
        id="nr:live-measurement",
        type="train",
        name="New Measurement Train",
        headcode="1Q23",
        source="Network Rail TRUST",
        source_id="551Q23XX20",
        aircraft_model=None,
        last_updated=NOW - timedelta(minutes=5),
    )

    discovery = claim_activity(measurement, now=NOW)
    assert discovery["key"] == "rail:test-measurement"


@pytest.mark.parametrize(
    "activity",
    [
        _vehicle(id="demo:typhoon", position_method="demo", source="demo"),
        _vehicle(id="fixture:typhoon", source="fixture"),
        _vehicle(id="replay:typhoon", source="Recorded replay"),
        _vehicle(id="test:typhoon", source="test"),
    ],
)
def test_demo_fixture_replay_and_test_provenance_cannot_be_claimed(activity):
    error = _error(activity, "activity_not_live")
    assert error.status_code == 422


def test_missing_stale_and_non_collectible_activity_are_rejected():
    assert _error(None, "activity_not_found").status_code == 404
    assert _error(
        _vehicle(last_updated=NOW - timedelta(minutes=11)),
        "activity_stale",
    ).status_code == 409
    assert _error(
        _vehicle(type="bus", name="Route 158", source="BODS", source_id="bus-1"),
        "not_collectible",
    ).status_code == 422


def test_force_excluded_cool_subtypes_cannot_be_claimed():
    trainer = _vehicle(
        id="live:trainer",
        name="RAF Hawk trainer VYT51",
        aircraft_model="BAE Systems Hawk T2 trainer",
    )
    tug = _vehicle(
        id="ais:235009999",
        type="vessel",
        name="Working tug under tow",
        aircraft_model=None,
        source="AISStream",
        source_id="235009999",
    )

    assert _error(trainer, "not_collectible").status_code == 422
    assert _error(tug, "not_collectible").status_code == 422


def test_live_rescue_and_emergency_records_are_never_collectibles():
    lifeboat = _vehicle(
        id="ais:235001234",
        type="vessel",
        name="RNLI Lifeboat",
        source="AISStream",
        source_id="235001234",
        aircraft_model=None,
    )
    emergency = Event(
        id="rnli:launch:123",
        type="rescue",
        name="RNLI launch",
        lat=50.2,
        lon=-5.5,
        source="RNLI",
        last_updated=NOW,
    )

    assert _error(lifeboat, "unsafe_activity").status_code == 403
    assert _error(emergency, "unsafe_activity").status_code == 403


def test_http_routes_expose_contract_and_structured_claim_errors(monkeypatch):
    from app import main

    live = _vehicle(last_updated=datetime.now(timezone.utc), vehicle_ref="ZJ939")
    monkeypatch.setattr(main.state, "get", lambda activity_id: live if activity_id == live.id else None)

    assert main.discovery_catalogue()["count"] == 20
    assert main.discovery_missions()["missions"]
    claimed = main.discovery_claim(main.DiscoveryClaimIn(activity_id=live.id))
    assert claimed["ok"] is True
    assert claimed["discovery"]["key"] == "air:combat-aircraft"

    with pytest.raises(HTTPException) as raised:
        main.discovery_claim(main.DiscoveryClaimIn(activity_id="missing"))
    assert raised.value.status_code == 404
    assert raised.value.detail["code"] == "activity_not_found"
