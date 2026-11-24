"""TRUST train movements from Rail Data Marketplace Kafka.

The payload is the same TRUST JSON understood by ``TrustCollector``. Direct
partition assignment lets a new deployment warm itself from recent records
without splitting the national feed with an overlapping Render instance.
"""
from __future__ import annotations

import threading
import time

import certifi

from ..models import SourceStatus

WARM_START_S = 10 * 60
FRESH_S = 120
MAX_BACKLOG = 1000


def _decode(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return None
    return str(value)


class TrustKafkaFeed:
    def __init__(self, settings, state, trust, hub=None, consumer_factory=None) -> None:
        self.settings = settings
        self.state = state
        self.trust = trust
        self.hub = hub
        self._factory = consumer_factory
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_message_at = float("-inf")
        self.messages = 0
        self.error: str | None = None

    @property
    def live(self) -> bool:
        return time.monotonic() - self.last_message_at < FRESH_S

    def _config(self) -> dict:
        return {
            "bootstrap.servers": self.settings.raildata_trust_kafka_bootstrap,
            "security.protocol": "SASL_SSL",
            "sasl.mechanisms": "PLAIN",
            "sasl.username": self.settings.raildata_trust_kafka_username,
            "sasl.password": self.settings.raildata_trust_kafka_password,
            "ssl.ca.location": certifi.where(),
            "group.id": self.settings.raildata_trust_kafka_group,
            "enable.auto.commit": False,
            "auto.offset.reset": "latest",
            "queued.max.messages.kbytes": 8192,
            "fetch.max.bytes": 4 * 1024 * 1024,
            "client.id": "uk-activity-radar-trust",
        }

    def _consumer(self):
        if self._factory:
            return self._factory()
        from confluent_kafka import Consumer
        return Consumer(self._config())

    def _assign(self, consumer) -> int:
        from confluent_kafka import TopicPartition
        topic = self.settings.raildata_trust_kafka_topic
        meta = consumer.list_topics(topic, timeout=20)
        partitions = sorted((meta.topics.get(topic).partitions or {}).keys()) if meta.topics.get(topic) else []
        if not partitions:
            raise RuntimeError(f"topic {topic} has no partitions visible to this account")
        wanted = [TopicPartition(topic, part, int((time.time() - WARM_START_S) * 1000)) for part in partitions]
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
        self.messages += 1
        self.last_message_at = time.monotonic()
        if self.hub is not None:
            self.hub.enqueue(self.trust, ("kafka", text))
        else:
            self.trust.handle_message(("kafka", text))

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="trust-kafka", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)

    def report(self) -> dict:
        return {"live": self.live, "messages": self.messages, "error": self.error}

    def _status(self, message: str) -> None:
        self.state.set_source(SourceStatus(name="trust_kafka", enabled=True,
                                           connected=self.error is None, mode="live", message=message))

    def _run(self) -> None:
        delay = 15.0
        while not self._stop.is_set():
            consumer = None
            try:
                consumer = self._consumer()
                count = self._assign(consumer)
                self.error = None
                self._status(f"Rail Data Marketplace train movements connected • {count} partitions")
                delay = 15.0
                while not self._stop.is_set():
                    if self.hub is not None and self.hub.backlog() > MAX_BACKLOG:
                        self._stop.wait(0.2)
                        continue
                    for record in consumer.consume(num_messages=200, timeout=1.0) or []:
                        problem = record.error()
                        if problem is None:
                            self.handle(record.value())
                        elif getattr(problem, "fatal", lambda: False)():
                            raise RuntimeError(str(problem))
            except Exception as exc:
                self.error = f"{type(exc).__name__}: {exc}"[:300]
                self._status(f"Rail Data Marketplace train movements unavailable: {self.error}; Network Rail TRUST in use")
            finally:
                try:
                    if consumer is not None:
                        consumer.close()
                except Exception:
                    pass
            if self._stop.wait(delay):
                return
            delay = min(600.0, delay * 2)
