"""A copy of a train taken before it stepped on must not move it back (trains bouncing A -> B -> A)."""
from datetime import datetime, timedelta, timezone

from app.models import Vehicle
from app.state import LiveState


def _train(**changes):
    values = dict(id="td:Y4:2C00", type="train", name="2C00", lat=53.38, lon=-1.46, headcode="2C00",
                  train_uid="P1", service_date="2026-11-28", last_updated=datetime.now(timezone.utc))
    values.update(changes)
    return Vehicle(**values)


def test_a_stale_copy_written_back_does_not_move_the_train():
    state = LiveState()
    at = datetime.now(timezone.utc)
    state.upsert(_train(last_updated=at))
    slow_pass = state.get("td:Y4:2C00")                 # a maintenance pass copies the train...
    stepped = state.get("td:Y4:2C00")
    stepped.lat, stepped.lon = 53.49, -1.29              # ...a signal step moves it on (same second)
    state.upsert(stepped)
    slow_pass.estimate_path = [[53.38, -1.46], [53.40, -1.40]]
    state.upsert(slow_pass)                              # ...then the pass writes its old copy back
    assert (state.get("td:Y4:2C00").lat, state.get("td:Y4:2C00").lon) == (53.49, -1.29)
    assert state.movement_quality_report()["stale_writes_refused"] == 1


def test_a_newer_report_from_an_old_copy_still_wins_and_a_writer_may_write_twice():
    state = LiveState()
    at = datetime.now(timezone.utc)
    state.upsert(_train(last_updated=at))
    td_copy = state.get("td:Y4:2C00")
    other = state.get("td:Y4:2C00")
    other.estimate_path = [[53.38, -1.46], [53.40, -1.40]]
    state.upsert(other)
    td_copy.lat, td_copy.last_updated = 53.5, at + timedelta(seconds=20)   # a real, later report
    state.upsert(td_copy)
    assert state.get("td:Y4:2C00").lat == 53.5
    td_copy.platform = "3"                                                   # same writer, again
    state.upsert(td_copy)
    assert state.get("td:Y4:2C00").platform == "3"
    fresh = _train(lat=53.6, last_updated=at + timedelta(seconds=20))       # newly built rows always count
    state.upsert(fresh)
    assert state.get("td:Y4:2C00").lat == 53.6


def test_enrichment_changes_only_its_own_fields():
    state = LiveState()
    state.upsert(_train())
    looked_up = state.find_train_by_uid("P1")
    moved = state.get("td:Y4:2C00")
    moved.lat = 53.49
    state.upsert(moved)
    assert state.patch_train(looked_up.id, platform="2", formation=["170636"])
    train = state.get("td:Y4:2C00")
    assert (train.lat, train.platform, train.formation) == (53.49, "2", ["170636"])
    assert state.patch_train("nope", platform="1") is False
    assert "_state_write" not in train.model_dump()


def test_drawn_jumps_ignore_a_leg_rollover_and_count_a_real_jump():
    now = datetime.now(timezone.utc)
    state = LiveState()
    leg = dict(estimate_path=[[52.0, -1.0], [52.0, -0.8]], estimate_started_at=now - timedelta(minutes=10),
               estimate_due_at=now - timedelta(seconds=1), reported_lat=52.0, reported_lon=-1.0)
    state.upsert(_train(lat=52.0, lon=-1.0, last_updated=now - timedelta(minutes=10), **leg))
    # Rolls onto the next leg, starting where the last one ended: the leg start moves 14 km, the train does not.
    state.upsert(_train(lat=52.0, lon=-0.8, reported_lat=52.0, reported_lon=-0.8, last_updated=now - timedelta(minutes=10),
                        estimate_path=[[52.0, -0.8], [52.0, -0.6]], estimate_started_at=now, estimate_due_at=now + timedelta(minutes=10)))
    drawn = state.movement_quality_report()["drawn_jumps"]
    assert drawn["over_0_5km"] == 0 and state.movement_quality_report()["since_process_start"]["over_2km"] == 1
    state.upsert(_train(lat=52.3, lon=-0.8, last_updated=now))       # a report 33 km away
    assert state.movement_quality_report()["drawn_jumps"]["over_5km"] == 1
