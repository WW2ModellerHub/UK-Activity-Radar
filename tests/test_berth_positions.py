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
    collector = SimpleNamespace(berths=berths, _trail={},
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
