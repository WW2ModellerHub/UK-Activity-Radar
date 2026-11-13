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
    rows={item.id:item for item in state.all(include_buses=False,include_vessels=False)}
    # The first identity seen for a dated service is kept (so the browser
    # moves one marker) while it carries the newest report's data.
    assert set(rows)=={'td:berth','nr:tomorrow'}
    assert rows['td:berth'].last_updated==now
    assert state.get('nr:trust') is None


def test_dated_service_keeps_one_identity_when_sources_alternate():
    state=LiveState()
    now=datetime.now(timezone.utc)
    seen=[]
    for step in range(6):
        source_id='td:WG:1F10' if step%2==0 else 'nr:1F10X'
        train=_vehicle(source_id,'train','1F10');train.train_uid='ALT001';train.service_date='2026-11-06'
        train.last_updated=now-timedelta(seconds=60-step*10)
        state.upsert(train)
        seen.append(tuple(item.id for item in state.all(include_buses=False,include_vessels=False)))
    assert set(seen)=={('td:WG:1F10',)}


def test_late_duplicate_report_does_not_replace_newer_service_position():
    state=LiveState()
    now=datetime.now(timezone.utc)
    td=_vehicle('td:WG:1F10','train','1F10');td.train_uid='LATE01';td.service_date='2026-11-06';td.last_updated=now;td.lat=52.5
    trust=_vehicle('nr:1F10X','train','1F10');trust.train_uid='LATE01';trust.service_date='2026-11-06';trust.last_updated=now-timedelta(minutes=2);trust.lat=52.0
    state.upsert(td);state.upsert(trust)
    rows=state.all(include_buses=False,include_vessels=False)
    assert [(row.id,row.lat) for row in rows]==[('td:WG:1F10',52.5)]


def test_train_stream_cap_keeps_already_streamed_trains_stable():
    state=LiveState()
    now=datetime.now(timezone.utc)
    for index in range(6):
        train=_vehicle(f'td:XX:{index}','train',f'1A{index:02d}')
        train.last_updated=now-timedelta(seconds=60-index)
        state.upsert(train)
    first={row.id for row in state.all(include_buses=False,include_vessels=False,train_limit=3)}
    assert first=={'td:XX:3','td:XX:4','td:XX:5'}
    # Older trains report again; without priority they would evict the
    # trains already on the viewer's map.
    for index in range(3):
        train=_vehicle(f'td:XX:{index}','train',f'1A{index:02d}')
        train.last_updated=now+timedelta(seconds=index+1)
        state.upsert(train)
    stable={row.id for row in state.all(include_buses=False,include_vessels=False,train_limit=3,train_priority_ids=first)}
    assert stable==first
    reshuffled={row.id for row in state.all(include_buses=False,include_vessels=False,train_limit=3)}
    assert reshuffled=={'td:XX:0','td:XX:1','td:XX:2'}


def test_train_remains_streamed_when_estimate_is_visible_but_report_point_is_not():
    state = LiveState()
    now = datetime.now(timezone.utc)
    train = _vehicle("td:WG:1P04", "train", "1P04")
    train.lat = 52.535
    train.lon = -1.371  # confirmed point is west of this narrow viewport
    train.reported_lat = 52.535
    train.reported_lon = -1.371
    train.next_lat = 52.571
    train.next_lon = -1.203
    train.estimate_path = [[52.535, -1.371], [52.571, -1.203]]
    train.estimate_started_at = now - timedelta(minutes=8)
    train.estimate_due_at = now + timedelta(minutes=2)
    train.last_updated = now
    state.upsert(train)

    rows = state.all(
        bbox=(-1.25, 52.54, -1.20, 52.59),
        train_bbox=(-1.25, 52.54, -1.20, 52.59),
        include_buses=False,
        include_vessels=False,
    )

    assert [row.id for row in rows] == ["td:WG:1P04"]


def test_track_shaped_train_projection_survives_short_feed_outage():
    state = LiveState()
    now = datetime.now(timezone.utc)
    train = _vehicle("nr:projected", "train", "1C17", age_minutes=61)
    train.train_uid = "C1C17"
    train.service_date = now.date().isoformat()
    train.schedule_id = 17
    train.reported_lat = train.lat
    train.reported_lon = train.lon
    train.next_lat = 52.10
    train.next_lon = -0.90
    train.estimate_path = [[52.0, -1.0], [52.04, -0.96], [52.10, -0.90]]
    train.estimate_started_at = now - timedelta(minutes=3)
    train.estimate_due_at = now + timedelta(minutes=7)
    train.position_method = "estimated"
    state.upsert(train)

    rows = state.all(include_buses=False, include_vessels=False)

    assert [row.id for row in rows] == ["nr:projected"]
    assert state.find_train_by_uid("C1C17").id == "nr:projected"
    assert state.count_type("train", max_age_minutes=45) == 1
    assert state.type_counts({"train": 45})["train"] == 1
    assert state.summary()["by_type"]["train"] == 1


def test_old_or_finished_train_projection_cannot_live_forever():
    state = LiveState()
    now = datetime.now(timezone.utc)
    train = _vehicle("nr:expired", "train", "1C17", age_minutes=91)
    train.train_uid = "C1C17"
    train.service_date = now.date().isoformat()
    train.schedule_id = 17
    train.estimate_path = [[52.0, -1.0], [52.10, -0.90]]
    train.estimate_started_at = now - timedelta(minutes=20)
    train.estimate_due_at = now - timedelta(minutes=11)
    state.upsert(train)

    assert state.all(include_buses=False, include_vessels=False) == []
    assert state.find_train_by_uid("C1C17") is None
    assert state.count_type("train", max_age_minutes=45) == 0
