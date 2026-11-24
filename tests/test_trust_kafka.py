import json
import time

import certifi

from app.collectors.trust_kafka import FRESH_S, TrustKafkaFeed
from app.config import Settings


class Trust:
    def __init__(self):
        self.got = []

    def handle_message(self, payload):
        self.got.append(payload)


def settings(**changes):
    values = {
        "raildata_trust_kafka_group": "group",
        "raildata_trust_kafka_username": "user",
        "raildata_trust_kafka_password": "secret",
    }
    values.update(changes)
    return Settings(**values)


def test_config_and_readiness_are_separate_from_td():
    configured = settings()
    config = TrustKafkaFeed(configured, None, Trust())._config()
    assert config["ssl.ca.location"] == certifi.where()
    assert config["client.id"] == "uk-activity-radar-trust"
    assert configured.raildata_trust_kafka_ready
    assert not Settings(raildata_trust_kafka_group="group").raildata_trust_kafka_ready


def test_record_reaches_existing_trust_parser_as_kafka():
    trust = Trust()
    feed = TrustKafkaFeed(settings(), None, trust)
    payload = json.dumps([{"header": {"msg_type": "0003"}, "body": {"train_id": "123"}}])
    feed.handle(payload.encode())
    assert trust.got == [("kafka", payload)]
    assert feed.live and feed.messages == 1
    feed.last_message_at = time.monotonic() - FRESH_S - 1
    assert not feed.live
