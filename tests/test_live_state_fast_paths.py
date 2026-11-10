from datetime import datetime, timedelta, timezone

from app.models import Event, Vehicle
from app.state import LiveState


def _vehicle(identifier: str, type_name: str, name: str, *, age_minutes: int = 0) -> Vehicle:
    return Vehicle(
        id=identifier,
        type=type_name,
        name=name,
        lat=52.0,
        lon=-1.0,
        source="test",
        last_updated=datetime.now(timezone.utc) - timedelta(minutes=age_minutes),
    )


def test_summary_and_type_counts_use_live_freshness_rules():
    state = LiveState()
    state.upsert(_vehicle("bus:fresh", "bus", "Fresh bus"))
    state.upsert(_vehicle("bus:old", "bus", "Old bus", age_minutes=6))
    state.upsert(_vehicle("train:1", "train", "1A01"))
    state.upsert_event(Event(id="road:1", type="road", name="Road event", lat=52, lon=-1))

    summary = state.summary()
    counts = state.type_counts({"bus": 5})

    assert summary["visible"] == 3
    assert summary["by_type"]["bus"] == 1
    assert summary["events"] == 1
    assert counts["bus"] == 1
    assert counts["train"] == 1
    assert counts["road"] == 1


def test_global_cool_candidates_are_ranked_and_exclude_stale_rows():
    state = LiveState()
    state.upsert(_vehicle("train:steam", "train", "Steam-hauled charter"))
    state.upsert(_vehicle("train:freight", "train", "6M42 freight", age_minutes=1))
    state.upsert(_vehicle("train:stale", "train", "Steam-hauled charter", age_minutes=46))
    state.upsert(_vehicle("bus:routine", "bus", "Routine bus"))

    rows = state.cool_candidates(limit=10)

    assert [row.id for row in rows] == ["train:steam", "train:freight"]
    assert all(row.cool_candidate for row in rows)


def test_revision_changes_only_when_live_state_changes():
    state = LiveState()
    start = state.revision
    state.upsert(_vehicle("train:1", "train", "1A01"))
    assert state.revision > start
    current = state.revision
    assert state.revision == current


def test_stale_vehicle_pruning_is_source_appropriate_and_cleans_train_index():
    now = datetime.now(timezone.utc)
    state = LiveState()
    stale_bus = _vehicle("bus:old", "bus", "Old bus", age_minutes=16)
    stale_train = _vehicle("train:old", "train", "Old train", age_minutes=181)
    stale_train.train_uid = "COLD01"
    fresh_aircraft = _vehicle("aircraft:fresh", "aircraft", "Fresh aircraft", age_minutes=29)
    state.upsert(stale_bus)
    state.upsert(stale_train)
    state.upsert(fresh_aircraft)

    assert state.prune_stale(now=now) == 2
    assert state.get("bus:old") is None
    assert state.find_train_by_uid("COLD01") is None
    assert state.get("aircraft:fresh") is not None


def test_older_train_report_cannot_move_marker_backwards():
    state=LiveState()
    now=datetime.now(timezone.utc)
    newest=_vehicle('nr:train','train','1F10');newest.train_uid='RIGHT1';newest.service_date='2026-11-06';newest.last_updated=now;newest.lat=52.63
    older=_vehicle('nr:train','train','1F10');older.train_uid='RIGHT1';older.service_date='2026-11-06';older.last_updated=now-timedelta(minutes=2);older.lat=51.50
    state.upsert(newest);state.upsert(older)
    assert state.get('nr:train').lat==52.63


def test_duplicate_sources_collapse_only_within_the_same_dated_service():
    state=LiveState()
    now=datetime.now(timezone.utc)
    trust=_vehicle('nr:trust','train','1F10');trust.train_uid='SAME01';trust.service_date='2026-11-06';trust.last_updated=now
    td=_vehicle('td:berth','train','1F10');td.train_uid='SAME01';td.service_date='2026-11-06';td.last_updated=now-timedelta(seconds=5)
    tomorrow=_vehicle('nr:tomorrow','train','1F10');tomorrow.train_uid='SAME01';tomorrow.service_date='2026-11-07';tomorrow.last_updated=now-timedelta(seconds=10)
    state.upsert(td);state.upsert(trust);state.upsert(tomorrow)
    ids={item.id for item in state.all(include_buses=False,include_vessels=False)}
    assert ids=={'nr:trust','nr:tomorrow'}
