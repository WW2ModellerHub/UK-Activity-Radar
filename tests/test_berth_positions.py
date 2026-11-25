"""Berth positions learned from passing trains, then used to place later ones (Signalbox-style)."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.berth_positions import BerthPositions, point_at, project
from app.collectors.td import TDCollector
from app.models import Vehicle

LEICESTER, WIGSTON = [52.6314, -1.1253], [52.5880, -1.1300]
T0 = datetime(2026, 11, 25, 18, 0, tzinfo=timezone.utc)


def test_a_berth_is_trusted_only_once_several_trains_agree(tmp_path):
    berths = BerthPositions(str(tmp_path / "b.json"))
    key = berths.key("LR", "0396")
    berths.observe(key, 52.61, -1.127)
    berths.observe(key, 52.6102, -1.1271)
    assert berths.position(key) is None                       # two is not enough
    berths.observe(key, 52.70, -1.30)                         # a stray (wrong headcode match)
    assert berths.position(key) is None                       # still only two agree
    berths.observe(key, 52.6101, -1.1270)
    lat, lon = berths.position(key)
    assert abs(lat - 52.6101) < 0.001 and abs(lon + 1.127) < 0.001
    berths.save(force=True)
    assert BerthPositions(str(tmp_path / "b.json")).position(key) is not None   # survives a restart


def test_path_helpers():
    path = [LEICESTER, WIGSTON]
    mid = point_at(path, 0.5)
    fraction, off = project(path, *mid)
    assert abs(fraction - 0.5) < 0.01 and off < 0.01


def _collector(berths):
    collector = SimpleNamespace(berths=berths, _trail={}, _last_place={},
                                schedules=SimpleNamespace(route_points=lambda sid, a, b: [LEICESTER, WIGSTON]))
    for name in ("_learn_and_anchor", "_learn", "_place_on_leg"):
        setattr(collector, name, getattr(TDCollector, name).__get__(collector))
    return collector


def test_unplaced_steps_between_two_reports_are_learned_then_place_the_next_train(tmp_path):
    berths = BerthPositions(None)
    collector = _collector(berths)
    key = berths.key("LR", "0396")
    for n in range(3):   # three trains run Leicester -> Wigston in 4 min, stepping into 0396 two minutes in
        train = Vehicle(id=f"t{n}", type="train", name="1N41", lat=0, lon=0, last_updated=T0, schedule_id=1)
        collector._learn_and_anchor(train, {"tiploc": "LESTER", "lat": LEICESTER[0], "lon": LEICESTER[1]}, T0, None)
        collector._trail[train.id]["steps"].append((key, T0 + timedelta(minutes=2)))
        collector._learn_and_anchor(train, {"tiploc": "WGSTNNJ", "lat": WIGSTON[0], "lon": WIGSTON[1]}, T0 + timedelta(minutes=4), None)
    learned = berths.position(key)
    assert learned is not None
    assert abs(project([LEICESTER, WIGSTON], *learned)[0] - 0.5) < 0.05           # half way, at half time
    # A fourth train, drawn only a quarter of the way along at that moment, is moved to the berth.
    late = Vehicle(id="t4", type="train", name="1N43", lat=0, lon=0, last_updated=T0,
                   estimate_path=[LEICESTER, WIGSTON], estimate_started_at=T0, estimate_due_at=T0 + timedelta(minutes=8))
    assert collector._place_on_leg(late, learned, T0 + timedelta(minutes=2), "LR", "0396")
    assert late.estimate_started_at == T0 - timedelta(minutes=2)                  # now half way, same pace
    assert "learned berth" in late.position_label and berths.placed_steps == 1
    # Off its track by more than 400 m: not used.
    assert not collector._place_on_leg(late, (52.70, -1.30), T0, "LR", "0396")


def test_steps_are_followed_after_trust_reports_the_train_in_between(tmp_path):
    """TRUST can take a train over (source, and a clock minutes ahead); its next TD steps are still its own."""
    import json as _json
    from app.collectors.td import TDCollector
    from app.state import LiveState
    (tmp_path / "stations.json").write_text("{}")
    (tmp_path / "smart.json").write_text("[]")
    state = LiveState()
    collector = TDCollector(SimpleNamespace(td_topic="/topic/TD"), state, tmp_path / "stations.json", tmp_path / "smart.json")
    collector.berths = BerthPositions(None)
    train = Vehicle(id="nr:1", type="train", name="1N41", headcode="1N41", lat=LEICESTER[0], lon=LEICESTER[1], source="Network Rail TRUST",
                    last_updated=T0 + timedelta(minutes=6))                     # TRUST, stamped ahead
    state.upsert(train)
    collector._trail["nr:1"] = {"tiploc": "LESTER", "t": T0, "kind": None, "steps": [], "area": "LR",
                                "lat": LEICESTER[0], "lon": LEICESTER[1]}
    collector.handle_message(_json.dumps([{"CA_MSG": {"area_id": "LR", "from": "0404", "to": "0396", "descr": "1N41",
                                                      "time": str(int((T0 + timedelta(minutes=2)).timestamp() * 1000))}}]))
    assert collector._trail["nr:1"]["steps"] == [("LR:0396", T0 + timedelta(minutes=2))]
    assert state.get("nr:1").last_updated == T0 + timedelta(minutes=6)        # never moved backwards


def test_a_train_slower_than_its_timetable_is_paced_by_its_own_steps():
    berths = BerthPositions(None)
    collector = _collector(berths)
    path = [LEICESTER, WIGSTON]                       # ~4.8 km, booked 4 minutes
    train = Vehicle(id="slow", type="train", name="1N41", lat=0, lon=0, last_updated=T0,
                    estimate_path=path, estimate_started_at=T0, estimate_due_at=T0 + timedelta(minutes=4))
    quarter, half = point_at(path, 0.25), point_at(path, 0.5)
    assert collector._place_on_leg(train, quarter, T0 + timedelta(minutes=1), "LR", "1")
    assert train.estimate_due_at == T0 + timedelta(minutes=4)               # one step: timetable pace
    # It takes 2 minutes to cover the next quarter (booked: 1): half speed.
    assert collector._place_on_leg(train, half, T0 + timedelta(minutes=3), "LR", "2")
    assert abs((train.estimate_due_at - (T0 + timedelta(minutes=7))).total_seconds()) < 2   # the rest at its own pace
    report = berths.report()["correction_km"]
    assert report["n"] == 2 and report["median"] > 0.5                       # the second step was a big correction


def test_a_td_only_copy_is_hidden_once_the_matched_train_reports_elsewhere():
    from app.state import LiveState
    state = LiveState()
    now = datetime.now(timezone.utc)
    state.upsert(Vehicle(id="td:LR:1C98", type="train", name="1C98", headcode="1C98", lat=52.62, lon=-1.13,
                         source="Network Rail TD", last_updated=now - timedelta(minutes=7)))
    state.upsert(Vehicle(id="nr:1C98", type="train", name="1C98", headcode="1C98", lat=52.48, lon=-0.92, train_uid="C12345",
                         source="Network Rail TRUST", last_updated=now - timedelta(minutes=1)))
    state.upsert(Vehicle(id="td:G1:1C98", type="train", name="1C98", headcode="1C98", lat=55.86, lon=-4.25,
                         source="Network Rail TD", last_updated=now - timedelta(minutes=7)))      # another 1C98 in Scotland
    ids = {v.id for v in state.all(include_buses=False, include_vessels=False)}
    assert "nr:1C98" in ids and "td:LR:1C98" not in ids and "td:G1:1C98" in ids


def test_a_train_is_not_drawn_past_the_next_signal_berth_until_it_steps_in():
    from app.collectors.td import CAP_SECONDS, cap_share
    from app.state import _estimated_train_position
    berths = BerthPositions(None)
    collector = _collector(berths)
    path = [LEICESTER, WIGSTON]
    a, b = berths.key("LR", "A"), berths.key("LR", "B")
    for spot, key in ((0.25, a), (0.5, b)):
        for _ in range(3):
            berths.observe(key, *point_at(path, spot))
    berths.note_next(a, b); berths.note_next(a, b)                     # trains step A -> B
    assert berths.next_berth(a) == b
    train = Vehicle(id="t", type="train", name="1N41", lat=0, lon=0, last_updated=T0,
                    estimate_path=path, estimate_started_at=T0, estimate_due_at=T0 + timedelta(minutes=4))
    assert collector._place_on_leg(train, berths.position(a), T0 + timedelta(minutes=1), "LR", "A")
    assert abs(cap_share(train, T0 + timedelta(minutes=2)) - 0.5) < 0.02
    # At its pace it would be 3/4 of the way at +3 min; held at berth B (half way) instead.
    at = T0 + timedelta(minutes=3)
    lat, lon = _estimated_train_position(train.model_copy(update={"estimate_path": path}), at)
    assert abs(project(path, lat, lon)[0] - 0.5) < 0.02
    # The cap lapses (a missed step must not freeze a train) and belongs to this leg only.
    assert cap_share(train, T0 + timedelta(minutes=1, seconds=CAP_SECONDS + 1)) is None
    other = train.model_copy(update={"estimate_path": [WIGSTON, LEICESTER]})
    assert cap_share(other, T0 + timedelta(minutes=2)) is None
    # Junction with no settled next berth: no cap.
    berths.note_next(b, berths.key("LR", "C")); berths.note_next(b, berths.key("LR", "D"))
    assert berths.next_berth(b) is None


def test_next_berths_survive_a_restart(tmp_path):
    berths = BerthPositions(str(tmp_path / "b.json"))
    berths.observe("LR:A", 52.6, -1.1)
    berths.note_next("LR:A", "LR:B"); berths.note_next("LR:A", "LR:B")
    berths.save(force=True)
    again = BerthPositions(str(tmp_path / "b.json"))
    assert again.next_berth("LR:A") == "LR:B" and again.report()["berths_seen"] == 1
