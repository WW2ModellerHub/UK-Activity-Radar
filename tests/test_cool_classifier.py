from datetime import datetime, timedelta, timezone

from app.collectors.adsb_lol import parse_interesting_aircraft_response
from app.cool_classifier import classify_activity
from app.models import Event, Vehicle
from app.state import LiveState


NOW = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)


def _aircraft(name: str, **extra) -> Vehicle:
    return Vehicle(
        id=f"air:{name}",
        type="aircraft",
        name=name,
        lat=52.0,
        lon=-1.0,
        source="ADSB.lol military filter",
        last_updated=NOW,
        **extra,
    )


def test_combat_aircraft_outranks_trainer_and_unresolved_military():
    typhoon = classify_activity(_aircraft("RAF Eurofighter Typhoon RAZOR11"), now=NOW)
    trainer = classify_activity(_aircraft("RAF Hawk trainer VYT51"), now=NOW)
    unresolved = classify_activity(_aircraft("VYT51", interesting=True), now=NOW)

    assert typhoon.subtype == "combat-aircraft"
    assert typhoon.interest_score == 100
    assert typhoon.candidate is True
    assert trainer.subtype == "military-trainer"
    assert trainer.interest_score == 18
    assert trainer.candidate is False
    assert unresolved.interest_score < 45
    assert unresolved.candidate is False


def test_network_rail_operator_does_not_turn_every_train_into_a_test_train():
    ordinary = Vehicle(
        id="train:ordinary",
        type="train",
        name="1A23 London Express",
        operator="Network Rail",
        headcode="1A23",
        lat=52.0,
        lon=-1.0,
        source="Network Rail TRUST",
        last_updated=NOW,
    )
    result = classify_activity(ordinary, now=NOW)
    assert result.interest_score == 0
    assert result.subtype is None
    assert result.candidate is False


def test_rail_subtypes_are_centralised_and_explainable():
    steam = Vehicle(id="train:steam", type="train", name="Steam-hauled charter", lat=52, lon=-1, last_updated=NOW)
    measurement = Vehicle(id="train:nmt", type="train", name="New Measurement Train", headcode="3Q01", lat=52, lon=-1, last_updated=NOW)
    freight = Vehicle(id="train:freight", type="train", name="6M42", headcode="6M42", lat=52, lon=-1, last_updated=NOW)
    assert classify_activity(steam, now=NOW).subtype == "steam"
    assert classify_activity(measurement, now=NOW).subtype == "test-measurement"
    assert classify_activity(freight, now=NOW).subtype == "freight"


def test_measurement_headcode_and_real_adsb_description_expand_cool_identity():
    measurement = Vehicle(id="train:1q", type="train", name="1Q23", headcode="1Q23", lat=52, lon=-1, last_updated=NOW)
    assert classify_activity(measurement, now=NOW).subtype == "test-measurement"

    rows, total = parse_interesting_aircraft_response({"ac": [{
        "hex": "43c001",
        "flight": "TYPHOON1",
        "lat": 52.5,
        "lon": -1.2,
        "dbFlags": 1,
        # ``type`` is an ADS-B address/source type, not an airframe code.
        "type": "adsb_icao",
        "desc": "EUROFIGHTER TYPHOON FGR4",
        "ownOp": "Royal Air Force",
    }]}, now=NOW)

    assert total == 1
    assert len(rows) == 1
    assert rows[0].aircraft_type_code is None
    assert rows[0].aircraft_model == "EUROFIGHTER TYPHOON FGR4"
    assert rows[0].operator == "Royal Air Force"
    result = classify_activity(rows[0], now=NOW)
    assert result.subtype == "combat-aircraft"
    assert result.candidate is True


def test_adsb_designators_name_apache_and_chinook_in_plain_english():
    rows, total = parse_interesting_aircraft_response({"ac": [
        {"hex":"43c101","flight":"ARMY01","lat":52.2,"lon":-1.1,"dbFlags":1,"t":"AH64","ownOp":"British Army"},
        {"hex":"43c102","flight":"VORTEX2","lat":52.3,"lon":-1.2,"dbFlags":1,"t":"H47","ownOp":"Royal Air Force"},
    ]}, now=NOW)
    assert total == 2
    assert {row.aircraft_model for row in rows} == {"Boeing AH-64 Apache", "Boeing CH-47 Chinook"}
    assert all(classify_activity(row, now=NOW).candidate for row in rows)


def test_lynx_is_classified_as_a_military_helicopter():
    lynx = _aircraft("NAVY LYNX", aircraft_model="Westland Lynx AH.7")
    result = classify_activity(lynx, now=NOW)
    assert result.subtype == "military-helicopter"
    assert result.candidate is True


def test_old_rnli_record_is_not_a_best_spot_now_candidate():
    event = Event(
        id="rnli:old",
        type="rescue",
        name="St Ives RNLI launch",
        lat=50.2,
        lon=-5.5,
        source="RNLI",
        last_updated=NOW - timedelta(hours=8),
    )
    result = classify_activity(event, now=NOW)
    assert result.subtype == "rnli-launch"
    assert result.interest_score < 45
    assert result.candidate is False
    assert "Historical" in result.explanation


def test_live_state_serialises_server_owned_cool_fields():
    state = LiveState()
    aircraft = _aircraft("RAF Eurofighter Typhoon RAZOR11")
    aircraft.last_updated = datetime.now(timezone.utc)
    state.upsert(aircraft)
    stored = state.get("air:RAF Eurofighter Typhoon RAZOR11")
    assert stored is not None
    assert stored.cool_category == "air"
    assert stored.cool_subtype == "combat-aircraft"
    assert stored.cool_score == 100
    assert stored.cool_candidate is True
