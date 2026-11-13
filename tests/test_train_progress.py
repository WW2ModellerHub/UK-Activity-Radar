"""Where a live train is within its calling pattern, and how late it is."""
from datetime import datetime, timedelta
from types import SimpleNamespace

from app.smart_places import _train_progress
from app.timeutils import london_tz

BASE = datetime(2026, 11, 13, 19, 30, tzinfo=london_tz())


def _route():
    # Leicester 19:50, a passing junction, Nuneaton 20:09, Coleshill 20:25, New Street 20:38.
    locations = [
        {"tiploc_code": "LESTER", "_departure_at": BASE + timedelta(minutes=20)},
        {"tiploc_code": "WIGSTJN", "_pass_at": BASE + timedelta(minutes=24)},
        {"tiploc_code": "NUNEATN", "_arrival_at": BASE + timedelta(minutes=38), "_departure_at": BASE + timedelta(minutes=39)},
        {"tiploc_code": "COLESHL", "_departure_at": BASE + timedelta(minutes=55)},
        {"tiploc_code": "BHAMNWS", "_arrival_at": BASE + timedelta(minutes=68)},
    ]
    codes = [row["tiploc_code"] for row in locations]
    calls = [
        {"name": name, "_index": index, "_at": locations[index].get("_departure_at") or locations[index].get("_arrival_at")}
        for name, index in (("Leicester", 0), ("Nuneaton", 2), ("Coleshill Parkway", 3), ("Birmingham New Street", 4))
    ]
    return locations, codes, calls


def test_signalling_train_between_stops_is_placed_and_its_lateness_derived():
    locations, codes, calls = _route()
    vehicle = SimpleNamespace(
        rail_tiploc="WIGSTJN", rail_event_type="Pass", source="Network Rail TD", delay_minutes=None,
        last_updated=BASE + timedelta(minutes=28),  # booked to pass at +24
    )
    progress = _train_progress(vehicle, locations, codes, calls)
    assert progress == {"delay_minutes": 4, "delay_source": "signalling"}
    assert [call["state"] for call in calls] == ["passed", "next", "upcoming", "upcoming"]
    assert "expected" not in calls[0]
    assert [call.get("expected") for call in calls[1:]] == ["20:13", "20:29", "20:42"]


def test_trust_lateness_is_used_when_trust_reported_last_and_early_trains_show_no_expected_time():
    locations, codes, calls = _route()
    vehicle = SimpleNamespace(
        rail_tiploc="LESTER", rail_event_type="Departure", source="Network Rail TRUST", delay_minutes=-2,
        last_updated=BASE + timedelta(minutes=18),
    )
    progress = _train_progress(vehicle, locations, codes, calls)
    assert progress == {"delay_minutes": -2, "delay_source": "trust"}
    assert [call["state"] for call in calls] == ["passed", "next", "upcoming", "upcoming"]
    assert all("expected" not in call for call in calls)


def test_a_train_standing_at_a_station_is_current_there():
    locations, codes, calls = _route()
    vehicle = SimpleNamespace(
        rail_tiploc="NUNEATN", rail_event_type="Arrival", source="Network Rail TD", delay_minutes=None,
        last_updated=BASE + timedelta(minutes=41),
    )
    _train_progress(vehicle, locations, codes, calls)
    assert [call["state"] for call in calls] == ["passed", "current", "upcoming", "upcoming"]
    assert calls[1]["expected"] == "20:12"


def test_trust_variation_sign_follows_variation_status(tmp_path):
    from tests.test_trust_collector import make_collector, movement
    collector, state, now = make_collector(tmp_path)
    movement(collector, now, event_type="DEPARTURE", timetable_variation="3", variation_status="EARLY")
    assert next(iter(state.all(include_buses=False, include_vessels=False))).delay_minutes == -3
    movement(collector, now + timedelta(minutes=1), event_type="DEPARTURE", timetable_variation="5", variation_status="LATE")
    assert next(iter(state.all(include_buses=False, include_vessels=False))).delay_minutes == 5
