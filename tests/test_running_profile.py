"""Trains speed up from a stand and brake to a stop along a leg (Network Rail running times)."""
from datetime import datetime, timedelta

import pytest

from app import running_profile
from app.collectors.td import load_berth_steps
from app.models import Vehicle
from app.running_profile import allowances, distance_fraction, ramps
from app.state import _estimated_train_position
from app.timeutils import london_tz
from tests.test_train_continuity import _store


def test_steady_leg_is_unchanged():
    assert distance_fraction(30, 120) == pytest.approx(0.25)


def test_leaving_a_platform_starts_slowly_and_arriving_slows_to_a_stop():
    total, accel, brake = 300, 80, 60
    early = distance_fraction(20, total, accel, brake)
    assert early < 20 / total / 2                     # well behind a constant-speed train
    assert distance_fraction(total - 10, total, accel, brake) > 0.99   # creeping in at the end
    # Continuous, never goes backwards, and ends exactly at the end.
    samples = [distance_fraction(t, total, accel, brake) for t in range(0, total + 1)]
    assert samples[0] == 0 and samples[-1] == pytest.approx(1.0)
    assert all(b >= a for a, b in zip(samples, samples[1:]))
    assert max(b - a for a, b in zip(samples, samples[1:])) < 2.5 / total * 1.2


def test_ramps_follow_the_running_time_allowances_and_never_fill_a_short_leg():
    assert ramps(600, True, True, (40, 30)) == (80, 60)
    assert ramps(600, False, True, (40, 30)) == (0, 60)
    accel, brake = ramps(60, True, True, (40, 30))      # a one-minute hop between stations
    assert accel + brake <= 0.8 * 60 + 0.1 and accel > brake


def test_allowances_come_from_the_planning_data_by_train_class():
    running_profile._table.cache_clear()
    assert allowances("EMU", "390") == tuple(running_profile._table()["traction:390"])
    assert allowances("DMU", "S") == tuple(running_profile._table()["traction:150"])   # CIF letter code
    assert allowances("D", "1600") == tuple(running_profile._table()["load:1600"])     # loco + 1600 t
    assert allowances("EMU", "NOPE") == running_profile.PASSENGER


def test_legs_carry_their_shape_and_the_server_position_uses_it(tmp_path):
    now = datetime.now(london_tz()).replace(second=0, microsecond=0)
    store, schedule_id = _store(tmp_path, now)
    leg = store.estimate_leg_at(schedule_id, now.date(), now + timedelta(minutes=1))
    assert (leg["tiploc"], leg["next_tiploc"]) == ("CHFD", "CLAYX")
    assert leg["accel_s"] > 0 and leg["brake_s"] == 0     # departs Chesterfield, passes the junction
    leg = store.estimate_leg_at(schedule_id, now.date(), now + timedelta(minutes=10))
    assert leg["accel_s"] == 0 and leg["brake_s"] > 0     # passes the junction, stops at Leicester

    start = now - timedelta(seconds=20)
    train = Vehicle(id="t", type="train", name="t", lat=0, lon=0, last_updated=now,
                    estimate_path=[[0.0, 0.0], [0.0, 1.0]], estimate_started_at=start,
                    estimate_due_at=start + timedelta(seconds=300))
    steady = _estimated_train_position(train, now)[1]
    train.estimate_accel_s = 80
    assert _estimated_train_position(train, now)[1] < steady / 2


def test_newer_berth_steps_are_bundled_including_leicester():
    from app.collectors import td
    rows = load_berth_steps(td.Path(td.__file__).resolve().parents[1] / "reference" / "berth_steps.csv.gz")
    assert len(rows) > 30000
    leicester = [r for r in rows if r["TD"] == "LR" and r.get("STANME") == "LEICESTER"]
    assert leicester and all(r["STANOX"] == "59241" for r in leicester)
    assert any(r["TD"] == "TD" for r in rows)            # an area really named TD is kept


def test_newer_berth_steps_replace_old_smart_rows_in_the_areas_they_cover(tmp_path, monkeypatch):
    import gzip
    import json as _json
    from app.collectors import td
    from app.collectors.td import TDCollector
    from app.config import Settings
    from app.state import LiveState
    newer = tmp_path / "steps.csv.gz"
    with gzip.open(newer, "wt") as handle:
        handle.write("LR,B,0426,0412,59241,LEICESTER,A,1,,50,,,28/06/2026\n")
    monkeypatch.setattr(td, "BERTH_STEPS_FILE", newer)
    (tmp_path / "stations.json").write_text("{}")
    (tmp_path / "smart.json").write_text(_json.dumps([
        {"TD": "LR", "FROMBERTH": "0999", "TOBERTH": "0998", "STANOX": "59241"},   # deleted since
        {"TD": "ZZ", "FROMBERTH": "0001", "TOBERTH": "0002", "STANOX": "12345"},   # area not covered
    ]))
    collector = TDCollector(Settings(), LiveState(), tmp_path / "stations.json", tmp_path / "smart.json")
    assert ("LR", "0426", "0412") in collector.exact
    assert ("LR", "0999", "0998") not in collector.exact
    assert ("ZZ", "0001", "0002") in collector.exact


def test_a_train_is_not_drawn_at_an_unconfirmed_stop_and_waits_for_a_late_report():
    from datetime import timedelta as td_
    from types import SimpleNamespace
    from app.collectors import td as td_module
    start = datetime(2026, 11, 25, 7, 20, tzinfo=london_tz())
    train = Vehicle(id="t", type="train", name="1K03", lat=0, lon=0, last_updated=start,
                    estimate_path=[[52.53, -1.50], [52.535, -1.372]], estimate_started_at=start,
                    estimate_due_at=start + td_(minutes=7), estimate_brake_s=60)
    lat, lon = _estimated_train_position(train, start + td_(minutes=8))       # a minute after its booked arrival
    assert abs(lon - (-1.372)) > 0.002                                          # still short of the platform
    train.rail_event_type = "Arrival"                                           # an arrival step knows when it arrives
    assert abs(_estimated_train_position(train, start + td_(minutes=8))[1] - (-1.372)) < 1e-6
    # The rollover past the stop waits ARRIVAL_GRACE_S for a report.
    collector = SimpleNamespace(_schedule_departure_after_arrival=lambda *a: None, _schedule_leg=lambda *a, **k: {"due_at": start + td_(minutes=20)},
                                _apply_estimate_leg=lambda *a: None)
    late = Vehicle(id="u", type="train", name="1K03", lat=0, lon=0, last_updated=start, next_tiploc="HINCKLY",
                   estimate_due_at=start + td_(minutes=7), estimate_brake_s=60, schedule_id=1, service_date="2026-11-25")
    advance = td_module.TDCollector._advance_schedule_estimate
    assert advance(collector, late, start + td_(minutes=8)) is False
    assert advance(collector, late, start + td_(minutes=9)) is True


def test_a_train_that_only_passes_a_station_is_rolled_on_past_it():
    """Seen at Market Harborough: 1F56 (no stop there) stayed parked at the end of its leg."""
    from datetime import timedelta as td_
    from types import SimpleNamespace
    from app.collectors import td as td_module
    start = datetime(2026, 11, 25, 17, 21, tzinfo=london_tz())
    applied = []
    collector = SimpleNamespace(
        _schedule_departure_after_arrival=lambda *a: None,      # no dwell: it passes MRKTHRB
        _schedule_leg=lambda *a, **k: {"due_at": start + td_(minutes=14)},
        _apply_estimate_leg=lambda vehicle, leg: (applied.append(leg), setattr(vehicle, "estimate_due_at", leg["due_at"])),
    )
    train = Vehicle(id="f", type="train", name="1F56", lat=0, lon=0, last_updated=start, next_tiploc="MRKTHRB",
                    rail_event_type="Arrival", estimate_due_at=start + td_(minutes=6), schedule_id=1, service_date="2026-11-25")
    assert td_module.TDCollector._advance_schedule_estimate(collector, train, start + td_(minutes=12)) is True
    assert applied and train.estimate_due_at == start + td_(minutes=14)
