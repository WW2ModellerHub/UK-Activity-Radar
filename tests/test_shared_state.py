import asyncio
from datetime import datetime, timezone

from app.models import Event, SourceStatus, Vehicle
from app.shared_state import SharedStateBridge, decode_snapshot, encode_snapshot
from app.state import LiveState


class MemoryRedis:
    def __init__(self):
        self.value = None

    async def set(self, _key, value, **_kwargs):
        self.value = value

    async def get(self, _key):
        return self.value


def populated_state() -> LiveState:
    now = datetime.now(timezone.utc)
    state = LiveState()
    state.upsert(Vehicle(id="train-1", type="train", name="1A23", headcode="1A23", lat=52, lon=-1, last_updated=now))
    state.replace_events_for_source("roads", [Event(id="road-1", type="road", name="Closure", lat=52, lon=-1, source="roads", last_updated=now)])
    state.set_source(SourceStatus(name="rail", enabled=True, connected=True, mode="live"))
    return state


def test_shared_snapshot_round_trip_rebuilds_indexes():
    source = populated_state()
    payload = decode_snapshot(encode_snapshot(source.export_shared_snapshot()))
    target = LiveState()
    target.replace_shared_snapshot(payload)

    assert target.get("train-1").name == "1A23"
    assert target.get_event("road-1").name == "Closure"
    assert target.find_train_by_headcode("1A23").id == "train-1"
    assert target.source("rail").connected is True


def test_bridge_publishes_only_changed_revisions_and_consumer_keeps_last_good_state():
    async def exercise():
        redis = MemoryRedis()
        source = populated_state()
        publisher = SharedStateBridge("redis://unused", source, client=redis)
        target = LiveState()
        consumer = SharedStateBridge("redis://unused", target, client=redis)

        assert await publisher.publish_once() is True
        assert await publisher.publish_once() is False
        assert await consumer.consume_once() is True
        assert await consumer.consume_once() is False
        assert target.get("train-1") is not None

    asyncio.run(exercise())
