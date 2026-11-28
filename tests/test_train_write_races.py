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
