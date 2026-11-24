"""Darwin service forecasts from Rail Data Marketplace's JSON Kafka topic.

Darwin tells us *how a service is running*: expected/actual times, platforms
and passenger-facing changes. It is intentionally never used as a position
feed. TRUST and Train Describer remain the only sources that move a marker.
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone

import certifi

from ..models import SourceStatus

FRESH_S = 120


def _list(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _text(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, (str, int, float)):
        result = str(value).strip()
        return result or None
    if isinstance(value, dict):
        for key in ("#text", "_text", "content", "value", "string"):
            result = _text(value.get(key))
            if result:
                return result
    return None


def _clock_minutes(value) -> int | None:
    raw = _text(value)
    if not raw:
        return None
    try:
        hour, minute = raw.split(":", 2)[:2]
        return int(hour) * 60 + int(minute)
    except (TypeError, ValueError):
        return None


def _delay_minutes(location: dict) -> int | None:
    """Return forecast minus working/public time, safely across midnight."""
    for movement, booked in (("arr", ("pta", "wta")), ("dep", ("ptd", "wtd")), ("pass", ("wtp",))):
        report = location.get(movement)
        if isinstance(report, list):
            report = report[-1] if report else None
        if not isinstance(report, dict):
            continue
        actual = next((_clock_minutes(report.get(key)) for key in ("at", "et", "wet")
                       if _clock_minutes(report.get(key)) is not None), None)
        planned = next((_clock_minutes(location.get(key)) for key in booked
                        if _clock_minutes(location.get(key)) is not None), None)
        if actual is None or planned is None:
            continue
        difference = actual - planned
        if difference > 12 * 60:
            difference -= 24 * 60
        elif difference < -12 * 60:
            difference += 24 * 60
        return max(0, difference)
    return None


def _updates(payload: dict) -> list[dict]:
    root = payload.get("Pport") if isinstance(payload.get("Pport"), dict) else payload
    ur = root.get("uR") if isinstance(root, dict) else None
    out = []
    for update in _list(ur):
        if not isinstance(update, dict):
            continue
        for service in _list(update.get("TS")):
            if isinstance(service, dict):
                out.append(service)
    return out


def _update_blocks(payload: dict) -> list[dict]:
    root = payload.get("Pport") if isinstance(payload.get("Pport"), dict) else payload
    blocks = []
    for key in ("uR", "sR"):
        blocks += [item for item in _list(root.get(key) if isinstance(root, dict) else None) if isinstance(item, dict)]
    return blocks


def _formation_coaches(formation: dict) -> list[dict]:
    """Darwin <formation><coaches><coach coachNumber coachClass><toilet>…"""
    holder = formation.get("coaches")
    raw = holder.get("coach") if isinstance(holder, dict) else holder
    coaches = []
    for coach in _list(raw):
        if not isinstance(coach, dict):
            continue
        toilet = coach.get("toilet")
        coaches.append({
            "letter": _text(coach.get("coachNumber")),
            "class": _text(coach.get("coachClass")),
            # The toilet's type is the element text; its status an attribute.
            "toilet": _text(toilet) if not isinstance(toilet, dict) or str(toilet.get("status") or "InService") != "NotInService" else None,
        })
    return coaches


MAX_FORMATIONS = 30000


class DarwinKafkaFeed:
    def __init__(self, settings, state, consumer_factory=None) -> None:
        self.settings = settings
        self.state = state
        self._factory = consumer_factory
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_message_at = float("-inf")
        self.messages = 0
        self.services = 0
        self.enriched = 0
        self.malformed = 0
        self.error: str | None = None
        self.broker_error: str | None = None
        self.sample: dict | None = None
        # Coaches per Darwin service (RID), and which timetable train each RID is.
        self._formations: dict[str, list[dict]] = {}
        self._rid_for: dict[tuple[str, str], str] = {}
        self._lock = threading.Lock()
        self.formation_messages = 0

    @property
    def live(self) -> bool:
        return time.monotonic() - self.last_message_at < FRESH_S

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="darwin-kafka", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)

    def report(self) -> dict:
        return {
            "broker_error": self.broker_error, "live": self.live, "messages": self.messages, "services": self.services,
            "formations": len(self._formations), "formation_messages": self.formation_messages,
            "trains_enriched": self.enriched, "malformed": self.malformed,
            "error": self.error,
            "seconds_since_message": round(time.monotonic() - self.last_message_at) if self.messages else None,
            "sample": self.sample,
        }

    def _config(self) -> dict:
        return {
            "bootstrap.servers": self.settings.raildata_darwin_kafka_bootstrap,
            "security.protocol": "SASL_SSL",
            "sasl.mechanisms": "PLAIN",
            "sasl.username": self.settings.raildata_darwin_kafka_username,
            "sasl.password": self.settings.raildata_darwin_kafka_password,
            "ssl.ca.location": certifi.where(),
            "group.id": self.settings.raildata_darwin_kafka_group,
            "auto.offset.reset": "latest",
            "enable.auto.commit": True,
            "queued.max.messages.kbytes": 16384,
            "fetch.max.bytes": 8 * 1024 * 1024,
            "client.id": "uk-activity-radar-darwin",
        }

    def _consumer(self):
        if self._factory:
            return self._factory()
        from confluent_kafka import Consumer
        # librdkafka's own errors say why a connection failed (DNS, TLS, refused,
        # SASL authentication); "Broker transport failure" alone does not.
        return Consumer({**self._config(), "error_cb": self._broker_error})

    def _broker_error(self, error) -> None:
        self.broker_error = str(error)[:300]

    def handle(self, value) -> None:
        try:
            raw = value.decode("utf-8") if isinstance(value, bytes) else str(value)
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("Darwin payload is not an object")
        except (UnicodeDecodeError, ValueError, TypeError, json.JSONDecodeError):
            self.malformed += 1
            return

        services = _updates(payload)
        self.messages += 1
        self.services += len(services)
        self.last_message_at = time.monotonic()
        if self.sample is None:
            self.sample = {
                "version": _text(payload.get("version")),
                "service_updates": len(services),
                "received_at": datetime.now(timezone.utc).isoformat(),
            }
        for service in services:
            self._remember_rid(service)
            self._service(service)
        for block in _update_blocks(payload):
            for schedule in _list(block.get("schedule")):
                if isinstance(schedule, dict):
                    self._remember_rid(schedule)
            for item in _list(block.get("scheduleFormations")):
                if isinstance(item, dict):
                    self._formation(item)

    def _remember_rid(self, service: dict) -> None:
        rid, uid = _text(service.get("rid")), _text(service.get("uid"))
        day = (_text(service.get("ssd")) or "")[:10]
        if rid and uid and day:
            with self._lock:
                self._rid_for[(uid, day)] = rid
                if len(self._rid_for) > MAX_FORMATIONS:
                    self._rid_for.pop(next(iter(self._rid_for)))

    def _formation(self, item: dict) -> None:
        rid = _text(item.get("rid"))
        formations = [f for f in _list(item.get("formation")) if isinstance(f, dict)]
        if not rid or not formations:
            return
        # A service that divides has one formation per part; the longest is the one that runs throughout.
        coaches = max((_formation_coaches(f) for f in formations), key=len)
        if not coaches:
            return
        self.formation_messages += 1
        with self._lock:
            self._formations.pop(rid, None)
            self._formations[rid] = coaches
            if len(self._formations) > MAX_FORMATIONS:
                self._formations.pop(next(iter(self._formations)))

    def formation_for(self, uid: str | None, service_date: str | None) -> list[dict]:
        """The coaches Darwin last published for this train, or []."""
        with self._lock:
            rid = self._rid_for.get((str(uid or "").strip(), str(service_date or "")[:10]))
            return [dict(row) for row in self._formations.get(rid, [])] if rid else []

    def _service(self, service: dict) -> None:
        uid = _text(service.get("uid"))
        service_date = (_text(service.get("ssd")) or "")[:10]
        if not uid or not service_date:
            return
        locations = [row for row in _list(service.get("Location")) if isinstance(row, dict)]
        for location in locations:
            self.state.rail_notes.platform(
                uid, service_date, _text(location.get("tpl")), _text(location.get("plat")),
            )

        train = self.state.find_train_by_uid(uid, service_date)
        if train is None:
            return
        # The next call is the most useful forecast for a moving train. Fall
        # back to its current reporting point when Darwin only sent that call.
        wanted = [str(train.next_tiploc or "").upper(), str(train.rail_tiploc or "").upper()]
        relevant = next(
            (row for code in wanted if code for row in locations
             if str(_text(row.get("tpl")) or "").upper() == code),
            None,
        )
        if relevant is None:
            return
        changed = False
        platform = _text(relevant.get("plat"))
        delay = _delay_minutes(relevant)
        if platform and platform != train.platform:
            train.platform = platform
            changed = True
        if delay is not None and delay != train.delay_minutes:
            train.delay_minutes = delay
            if not str(train.status or "").lower().startswith(("cancel", "terminat")):
                train.status = "On time" if delay <= 2 else f"{delay} min late"
            changed = True
        if changed:
            # Do not alter last_updated: it is the last *position* report and
            # drives stale-marker protection and the interpolation engine.
            self.state.upsert(train)
            self.enriched += 1

    def _run(self) -> None:
        delay = 15.0
        while not self._stop.is_set():
            consumer = None
            try:
                consumer = self._consumer()
                consumer.subscribe([self.settings.raildata_darwin_kafka_topic])
                self.error = None
                self._status("Rail Data Marketplace Darwin connected")
                delay = 15.0
                while not self._stop.is_set():
                    for record in consumer.consume(num_messages=100, timeout=1.0) or []:
                        problem = record.error()
                        if problem is None:
                            self.handle(record.value())
                        elif getattr(problem, "fatal", lambda: False)():
                            raise RuntimeError(str(problem))
            except Exception as exc:
                self.error = f"{type(exc).__name__}: {exc}"[:300]
                self._status(f"Rail Data Marketplace Darwin unavailable: {self.error}")
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
        self.state.set_source(SourceStatus(
            name="darwin_kafka", enabled=True, connected=self.error is None,
            mode="live", message=message,
        ))
