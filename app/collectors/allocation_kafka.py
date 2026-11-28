"""Passenger train allocations and consists from Rail Data Marketplace."""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET

import certifi

from ..models import SourceStatus
from .td_kafka import _broker_logger
from .trust_kafka import TrustKafkaFeed, _decode

_UID_KEYS = ("trainUid", "trainUID", "train_uid", "uid", "cifTrainUid", "CIF_train_uid")
_HEADCODE_KEYS = ("headcode", "signallingId", "signalling_id", "serviceCode")
_VEHICLE_KEYS = {
    "unitnumber", "unit_number", "vehiclenumber", "vehicle_number",
    "vehicleid", "vehicle_id", "rollingstocknumber", "rolling_stock_number",
}


def _walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _clean_vehicle(value) -> str | None:
    text = re.sub(r"[\s-]+", "", str(value or "").strip().upper())
    return text if re.fullmatch(r"[A-Z]{0,3}\d{4,8}", text) else None


def allocation_records(payload) -> list[dict]:
    """Normalise additive schema versions without guessing service identity."""
    records = []
    for node in _walk(payload):
        uid = next((str(node.get(key) or "").strip() for key in _UID_KEYS if node.get(key)), "")
        headcode = next((str(node.get(key) or "").strip().upper() for key in _HEADCODE_KEYS if node.get(key)), "")
        vehicles = []
        for child in _walk(node):
            for key, value in child.items():
                if str(key).replace("-", "").lower() in _VEHICLE_KEYS:
                    found = _clean_vehicle(value)
                    if found and found not in vehicles:
                        vehicles.append(found)
        if (uid or headcode) and vehicles:
            records.append({"uid": uid or None, "headcode": headcode or None, "vehicles": vehicles})
            break
    return records


def allocation_xml_records(text: str) -> list[dict]:
    """Normalise ERA TAF/TAP ``PassengerTrainConsistMessage`` XML.

    Rail Data Marketplace publishes this topic as namespace-qualified XML,
    despite some portal material describing the wider product as JSON.  A
    consist message can repeat vehicles across multiple allocation sections,
    so keep their document order while de-duplicating them.
    """
    try:
        root = ET.fromstring(text)
    except (ET.ParseError, TypeError, ValueError):
        return []

    def local(element) -> str:
        return element.tag.rsplit("}", 1)[-1]

    headcode = next(
        (str(element.text or "").strip().upper() for element in root.iter()
         if local(element) == "OperationalTrainNumber" and str(element.text or "").strip()),
        "",
    )
    vehicles: list[str] = []
    for element in root.iter():
        if local(element) != "VehicleId":
            continue
        vehicle = _clean_vehicle(element.text)
        if vehicle and vehicle not in vehicles:
            vehicles.append(vehicle)
    if not headcode or not vehicles:
        return []
    return [{"uid": None, "headcode": headcode, "vehicles": vehicles}]


class AllocationKafkaFeed(TrustKafkaFeed):
    def __init__(self, settings, state, consumer_factory=None) -> None:
        super().__init__(settings, state, trust=None, consumer_factory=consumer_factory)
        self.allocations = 0
        self.enriched = 0
        self.malformed = 0
        self.broker_error: str | None = None
        self._log, self.broker_log = _broker_logger(type(self).__name__)

    def _config(self) -> dict:
        return {
            "bootstrap.servers": self.settings.raildata_allocation_kafka_bootstrap,
            "security.protocol": "SASL_SSL", "sasl.mechanisms": "PLAIN",
            "sasl.username": self.settings.raildata_allocation_kafka_username,
            "sasl.password": self.settings.raildata_allocation_kafka_password,
            "ssl.ca.location": certifi.where(),
            "group.id": self.settings.raildata_allocation_kafka_group,
            "enable.auto.commit": False, "auto.offset.reset": "latest",
            "queued.max.messages.kbytes": 8192, "fetch.max.bytes": 4 * 1024 * 1024,
            "client.id": "uk-activity-radar-allocation",
        }

    def _consumer(self):
        if self._factory:
            return self._factory()
        from confluent_kafka import Consumer
        return Consumer({**self._config(), "error_cb": self._note_broker_error, "logger": self._log})

    def _note_broker_error(self, error) -> None:
        self.broker_error = str(error)[:300]

    def _assign(self, consumer) -> int:
        from confluent_kafka import TopicPartition
        import time
        topic = self.settings.raildata_allocation_kafka_topic
        meta = consumer.list_topics(topic, timeout=20)
        partitions = sorted((meta.topics.get(topic).partitions or {}).keys()) if meta.topics.get(topic) else []
        if not partitions:
            raise RuntimeError(f"topic {topic} has no partitions visible to this account")
        wanted = [TopicPartition(topic, part, int((time.time() - 600) * 1000)) for part in partitions]
        try:
            found = consumer.offsets_for_times(wanted, timeout=20)
            starts = [TopicPartition(topic, item.partition, item.offset if item.offset >= 0 else -1) for item in found]
        except Exception:
            starts = [TopicPartition(topic, part, -1) for part in partitions]
        consumer.assign(starts)
        return len(partitions)

    def handle(self, value) -> None:
        text = _decode(value)
        if not text:
            return
        if text.lstrip().startswith("<"):
            records = allocation_xml_records(text)
        else:
            try:
                payload = json.loads(text)
            except (TypeError, json.JSONDecodeError):
                self.malformed += 1
                return
            records = allocation_records(payload)
        if not records:
            self.malformed += 1
            return
        self.messages += 1
        import time
        self.last_message_at = time.monotonic()
        for record in records:
            self.allocations += 1
            train = None
            if record["uid"]:
                train = self.state.find_train_by_uid(record["uid"])
            if train is None and record["headcode"]:
                train = self.state.find_train_by_headcode(record["headcode"])
            if train is None:
                continue
            if self.state.patch_train(train.id, formation=record["vehicles"], vehicle_ref=record["vehicles"][0]):
                self.enriched += 1

    def _status(self, message: str) -> None:
        self.state.set_source(SourceStatus(name="allocation_kafka", enabled=True,
                                           connected=self.error is None, mode="live", message=message))

    def report(self) -> dict:
        return {"server": self.settings.raildata_allocation_kafka_bootstrap,
                "topic": self.settings.raildata_allocation_kafka_topic,
                "broker_error": self.broker_error, "broker_log": list(self.broker_log),
                "live": self.live, "messages": self.messages, "allocations": self.allocations,
                "enriched": self.enriched, "malformed": self.malformed, "error": self.error}
