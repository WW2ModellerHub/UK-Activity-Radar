"""The Rail Data Marketplace Kafka copy of the Train Describer feed."""
import json
import time
from types import SimpleNamespace

import certifi

from app.collectors import td_kafka
from app.collectors.stomp_client import MultiplexStompFeed
from app.collectors.td_kafka import TDKafkaFeed
from app.config import Settings


class _TD:
    def __init__(self):
        self.got = []
        self.kafka = None

    def stomp_superseded(self):
        return bool(self.kafka is not None and self.kafka.live)

    def handle_message(self, payload):
        self.got.append(payload)


STEP = json.dumps([{"CA_MSG": {"area_id": "LS", "from": "0101", "to": "0103", "descr": "1A23", "time": "1700000000000"}}])


def test_kafka_config_uses_the_packaged_trust_bundle():
    config = TDKafkaFeed(Settings(), None, _TD())._config()
    assert config["security.protocol"] == "SASL_SSL"
    assert config["ssl.ca.location"] == certifi.where()


def test_kafka_records_reach_the_td_handler_marked_as_kafka():
    td = _TD()
    feed = TDKafkaFeed(Settings(), None, td)
    feed.handle(STEP.encode())
    assert td.got == [("kafka", STEP)]
    assert feed.live and feed.messages == 1 and feed.sample.startswith("[{")


def test_stomp_td_frames_are_skipped_while_kafka_delivers_and_resume_when_it_stops(monkeypatch):
    td = _TD()
    td.kafka = TDKafkaFeed(Settings(), None, td)
    hub = MultiplexStompFeed(Settings(), [td])
    hub.enqueue(td, STEP)
    assert hub.backlog() == 1          # Kafka has said nothing yet: STOMP is used
    td.kafka.handle(STEP.encode())
    hub.enqueue(td, STEP)
    assert hub.backlog() == 1          # Kafka live: the STOMP copy is not queued
    td.kafka.last_message_at = time.monotonic() - td_kafka.FRESH_S - 1
    hub.enqueue(td, STEP)
    assert hub.backlog() == 2          # Kafka quiet: STOMP takes over again


def test_td_collector_reads_kafka_payloads_and_ignores_stomp_while_kafka_is_live(tmp_path):
    from app.collectors.td import TDCollector
    from app.state import LiveState
    (tmp_path / "stations.json").write_text("{}")
    (tmp_path / "smart.json").write_text("[]")
    state = LiveState()
    td = TDCollector(Settings(), state, tmp_path / "stations.json", tmp_path / "smart.json")
    td.handle_message(("kafka", STEP))
    assert state.source("td").messages_seen == 1
    assert "Rail Data Marketplace" in state.source("td").message
    # A bare step without the CA_MSG wrapper is read as well.
    td.handle_message(("kafka", json.dumps({"msg_type": "CA", "area_id": "LS", "from": "1", "to": "2", "descr": "1A23"})))
    assert state.source("td").messages_seen == 2
    td.kafka = SimpleNamespace(live=True)
    td.handle_message(STEP)            # the STOMP copy, while Kafka is live
    assert state.source("td").messages_seen == 2


def test_feed_assigns_every_partition_from_a_few_minutes_back_without_a_consumer_group():
    calls = {}

    class Consumer:
        def list_topics(self, topic, timeout):
            return SimpleNamespace(topics={topic: SimpleNamespace(partitions={0: None, 1: None})})

        def offsets_for_times(self, wanted, timeout):
            calls["since"] = wanted[0].offset
            return [SimpleNamespace(partition=tp.partition, offset=100 + tp.partition if tp.partition else -1) for tp in wanted]

        def assign(self, starts):
            calls["assigned"] = [(tp.partition, tp.offset) for tp in starts]

        def subscribe(self, *_):
            raise AssertionError("must not join a consumer group")

    feed = TDKafkaFeed(Settings(), None, _TD(), consumer_factory=Consumer)
    assert feed._assign(Consumer()) == 2
    assert calls["assigned"] == [(0, -1), (1, 101)]
    assert abs(calls["since"] / 1000 - (time.time() - td_kafka.WARM_START_S)) < 5


def test_kafka_is_only_used_when_every_setting_is_present(monkeypatch):
    for name in ("RAILDATA_TD_KAFKA_GROUP", "RAILDATA_TD_KAFKA_USERNAME", "RAILDATA_TD_KAFKA_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    assert not Settings().raildata_td_kafka_ready
    assert not Settings(raildata_td_kafka_group="g", raildata_td_kafka_username="u").raildata_td_kafka_ready
    assert Settings(raildata_td_kafka_group="g", raildata_td_kafka_username="u",
                    raildata_td_kafka_password="p").raildata_td_kafka_ready
