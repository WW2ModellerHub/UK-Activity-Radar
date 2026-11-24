"""Train Describer steps from the Rail Data Marketplace Kafka topic.

The same berth steps as Network Rail's STOMP feed (TD_ALL_SIG_AREA), served
from Confluent Cloud. Two things it does better:

* After a restart or deploy it starts a few minutes back in the topic, so
  trains reappear at their berths at once instead of waiting for their next
  step.
* Partitions are assigned by this process, not shared through a consumer
  group, so an overlapping deploy never splits the feed between two
  instances.

While it is delivering, the STOMP copy of TD is ignored; if it goes quiet the
STOMP copy takes over again by itself.
"""
from __future__ import annotations

import threading
import time

import certifi

from ..models import SourceStatus

WARM_START_S = 5 * 60      # how far back to start after a restart
FRESH_S = 120              # Kafka counts as live for this long after a message
MAX_BACKLOG = 1000         # pause reading while the TD worker is this far behind


def decode(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return None
    return str(value)


class TDKafkaFeed:
    def __init__(self, settings, state, td, hub=None, consumer_factory=None) -> None:
        self.settings = settings
        self.state = state
        self.td = td
        self.hub = hub
        self._factory = consumer_factory
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_message_at = float("-inf")   # never: not live until a record arrives
        self.messages = 0
        self.error: str | None = None
        self.sample: str | None = None

    @property
    def live(self) -> bool:
        return time.monotonic() - self.last_message_at < FRESH_S

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="td-kafka", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)

    def report(self) -> dict:
        return {"live": self.live, "messages": self.messages, "error": self.error,
                "seconds_since_message": round(time.monotonic() - self.last_message_at) if self.messages else None,
                "sample": self.sample}

    def _config(self) -> dict:
        return {
            "bootstrap.servers": self.settings.raildata_td_kafka_bootstrap,
            "security.protocol": "SASL_SSL",
            "sasl.mechanisms": "PLAIN",
            "sasl.username": self.settings.raildata_td_kafka_username,
            "sasl.password": self.settings.raildata_td_kafka_password,
            # RDM requires a valid certificate bundle. Supplying certifi
            # explicitly avoids platform-dependent CA discovery failures in
            # slim Linux containers such as Render's runtime.
            "ssl.ca.location": certifi.where(),
            "group.id": self.settings.raildata_td_kafka_group,
            "enable.auto.commit": False,
            "auto.offset.reset": "latest",
            # Keep librdkafka's read-ahead small: the server has 2 GB.
            "queued.max.messages.kbytes": 8192,
            "fetch.max.bytes": 4 * 1024 * 1024,
            "client.id": "uk-activity-radar",
        }

    def _consumer(self):
        if self._factory:
            return self._factory()
        from confluent_kafka import Consumer
        return Consumer(self._config())

    def _assign(self, consumer) -> int:
        from confluent_kafka import TopicPartition
        topic = self.settings.raildata_td_kafka_topic
        meta = consumer.list_topics(topic, timeout=20)
        partitions = sorted((meta.topics.get(topic).partitions or {}).keys()) if meta.topics.get(topic) else []
        if not partitions:
            raise RuntimeError(f"topic {topic} has no partitions visible to this account")
        since_ms = int((time.time() - WARM_START_S) * 1000)
        wanted = [TopicPartition(topic, p, since_ms) for p in partitions]
        try:
            found = consumer.offsets_for_times(wanted, timeout=20)
            # -1: nothing that old is kept; read from the end instead.
            starts = [TopicPartition(topic, tp.partition, tp.offset if tp.offset >= 0 else -1) for tp in found]
        except Exception:
            starts = [TopicPartition(topic, p, -1) for p in partitions]   # -1 = latest
        consumer.assign(starts)
        return len(partitions)

    def handle(self, value) -> None:
        text = decode(value)
        if not text:
            return
        if self.sample is None:
            self.sample = text[:400]
        self.messages += 1
        self.last_message_at = time.monotonic()
        if self.hub is not None:
            # One worker thread handles every TD step, whichever feed it came from.
            self.hub.enqueue(self.td, ("kafka", text))
        else:
            self.td.handle_message(("kafka", text))

    def _run(self) -> None:
        delay = 15.0
        while not self._stop.is_set():
            consumer = None
            try:
                consumer = self._consumer()
                count = self._assign(consumer)
                self.error = None
                self._status(f"Rail Data Marketplace TD connected • {count} partitions")
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
                self._status(f"Rail Data Marketplace TD unavailable: {self.error}; Network Rail TD in use")
            finally:
                try:
                    if consumer is not None:
                        consumer.close()
                except Exception:
                    pass
            if self._stop.wait(delay):
                return
            delay = min(600.0, delay * 2)

    def _status(self, message: str) -> None:
        self.state.set_source(SourceStatus(name="td_kafka", enabled=True, connected=self.error is None,
                                           mode="live", message=message))
