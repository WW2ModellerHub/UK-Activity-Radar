"""How late a bus is, and its expected times at the stops ahead."""
from datetime import datetime, timedelta

from app.smart_places import _with_bus_lateness
from app.timeutils import london_tz

NOW = datetime(2026, 11, 14, 9, 30, tzinfo=london_tz())


def _stops():
    return [
        {"name": "Market", "scheduled": (NOW - timedelta(minutes=12)).isoformat(), "state": "passed"},
        {"name": "Church", "scheduled": (NOW - timedelta(minutes=4)).isoformat(), "state": "next"},
        {"name": "Station", "scheduled": (NOW + timedelta(minutes=6)).isoformat(), "state": "upcoming"},
    ]


def test_operator_prediction_sets_lateness_and_expected_times():
    payload = {"stops": _stops(), "live_prediction": {"delay_minutes": 3}}
    out = _with_bus_lateness(payload, NOW)
    assert (out["delay_minutes"], out["delay_source"]) == (3, "operator")
    assert [row.get("expected_clock") for row in out["stops"]] == [None, "09:29", "09:39"]
    assert "expected_clock" not in payload["stops"][1]  # the cached payload is not changed


def test_bus_still_heading_for_a_stop_it_was_due_at_is_about_that_late():
    out = _with_bus_lateness({"stops": _stops(), "next_from_report": True}, NOW)
    assert (out["delay_minutes"], out["delay_source"]) == (4, "estimated")


def test_no_lateness_is_guessed_when_the_next_stop_came_from_the_clock():
    out = _with_bus_lateness({"stops": _stops(), "next_from_report": False}, NOW)
    assert "delay_minutes" not in out


def test_a_bus_not_yet_due_at_its_next_stop_has_no_estimate():
    stops = _stops()
    stops[1]["scheduled"] = (NOW + timedelta(minutes=1)).isoformat()
    assert "delay_minutes" not in _with_bus_lateness({"stops": stops, "next_from_report": True}, NOW)
