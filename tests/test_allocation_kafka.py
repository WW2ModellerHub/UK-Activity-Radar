import json
from datetime import datetime, timezone

from app.collectors.allocation_kafka import (
    AllocationKafkaFeed,
    allocation_records,
    allocation_xml_records,
)
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


def test_era_passenger_consist_xml_is_normalised_and_deduplicated():
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <PassengerTrainConsistMessage xmlns="http://www.era.europa.eu/schemes/TAFTSI/5.3">
      <TransportOperationalIdentifiers>
        <TransportOperationalIdentifier><OperationalTrainNumber>5u31</OperationalTrainNumber></TransportOperationalIdentifier>
      </TransportOperationalIdentifiers>
      <Allocation><Vehicle><VehicleId>421157</VehicleId></Vehicle><Vehicle><VehicleId>422157</VehicleId></Vehicle></Allocation>
      <Allocation><Vehicle><VehicleId>421157</VehicleId></Vehicle><Vehicle><VehicleId>423157</VehicleId></Vehicle></Allocation>
    </PassengerTrainConsistMessage>"""
    assert allocation_xml_records(xml) == [{
        "uid": None,
        "headcode": "5U31",
        "vehicles": ["421157", "422157", "423157"],
    }]


def test_allocation_feed_accepts_era_xml():
    feed = AllocationKafkaFeed(configured(), LiveState())
    feed.handle(b"<PassengerTrainConsistMessage><OperationalTrainNumber>1A23</OperationalTrainNumber>"
                b"<Vehicle><VehicleId>390001</VehicleId></Vehicle></PassengerTrainConsistMessage>")
    assert feed.messages == 1
    assert feed.allocations == 1
    assert feed.malformed == 0


def test_allocation_health_reports_broker_and_topic_without_credentials():
    feed = AllocationKafkaFeed(configured(), LiveState())
    report = feed.report()
    assert report["server"].endswith(":9092")
    assert report["topic"] == "prod-1033-Passenger-Train-Allocation-and-Consist-1_0"
    assert report["broker_error"] is None and report["broker_log"] == []
    assert "password" not in repr(report).lower()
