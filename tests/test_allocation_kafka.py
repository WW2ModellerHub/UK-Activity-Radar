import json
from datetime import datetime, timezone

from app.collectors.allocation_kafka import AllocationKafkaFeed, allocation_records
from app.config import Settings
from app.models import Vehicle
from app.state import LiveState


def configured():
    return Settings(raildata_allocation_kafka_group="group",
                    raildata_allocation_kafka_username="user",
                    raildata_allocation_kafka_password="secret")


def test_schema_variants_extract_exact_formation():
    payload = {"allocation": {"trainUid": "P19489", "consist": [
        {"vehicleNumber": "390 001"}, {"unit_number": "390002"},
    ]}}
    assert allocation_records(payload) == [{
        "uid": "P19489", "headcode": None, "vehicles": ["390001", "390002"],
    }]


def test_allocation_enriches_existing_train_without_moving_it():
    state = LiveState()
    reported = datetime.now(timezone.utc)
    state.upsert(Vehicle(id="nr:1", type="train", name="1A23", headcode="1A23",
                         train_uid="P19489", lat=52.5, lon=-1.2, last_updated=reported))
    feed = AllocationKafkaFeed(configured(), state)
    feed.handle(json.dumps({"trainUid": "P19489", "vehicleNumber": "390001"}).encode())
    train = state.get("nr:1")
    assert train.vehicle_ref == "390001" and train.formation == ["390001"]
    assert (train.lat, train.lon, train.last_updated) == (52.5, -1.2, reported)
    assert feed.enriched == 1 and feed.allocations == 1


def test_allocation_requires_all_credentials():
    assert configured().raildata_allocation_kafka_ready
    assert not Settings(raildata_allocation_kafka_group="group").raildata_allocation_kafka_ready
