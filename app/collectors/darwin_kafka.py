"""Darwin service forecasts from Rail Data Marketplace's JSON Kafka topic.

Darwin tells us *how a service is running*: expected/actual times, platforms
and passenger-facing changes. It is intentionally never used as a position
feed. TRUST and Train Describer remain the only sources that move a marker.
"""
from __future__ import annotations

import json
import os
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


def _forecast_clock(report) -> str | None:
    """'08:06' from a Darwin arr/dep report: actual time if it happened, else the estimate."""
    if isinstance(report, list):
        report = report[-1] if report else None
    if not isinstance(report, dict):
        return None
    for key in ("at", "et", "wet"):
        value = _text(report.get(key))
        if value and _clock_minutes(value) is not None:
            return value[:5]
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


def _plain_keys(value):
    """Drop XML namespace prefixes ("ns5:schedule" -> "schedule", "@rid" -> "rid")."""
    if isinstance(value, dict):
        return {str(key).split(":")[-1].lstrip("@$"): _plain_keys(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain_keys(item) for item in value]
    return value


def _unwrap(payload):
    """Rail Data Marketplace wraps each Push Port message in a JMS-style envelope:
    {"messageID": ..., "bytes": "<the Darwin message as a JSON string>", ...}."""
    for _ in range(3):
        if not isinstance(payload, dict):
            break
        inner = next((payload.get(key) for key in ("bytes", "body", "message", "payload", "value")
                      if isinstance(payload.get(key), (str, dict))), None)
        if inner is None or any(key in payload for key in ("uR", "sR", "Pport")):
            break
        if isinstance(inner, str):
            try:
                inner = json.loads(inner)
            except ValueError:
                break
        payload = inner
    return _plain_keys(payload)


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


def _days_kept() -> set[str]:
    """Yesterday (late running), today and tomorrow, as service dates."""
    from datetime import date, timedelta
    today = date.today()
    return {(today + timedelta(days=offset)).isoformat() for offset in (-1, 0, 1)}



def _broker_logger(name: str):
    """A logger for librdkafka that keeps its last few warnings and errors (no credentials are logged)."""
    import logging
    from collections import deque
    lines: deque = deque(maxlen=5)

    class _Keep(logging.Handler):
        def emit(self, record):
            lines.append(record.getMessage()[:300])

    log = logging.getLogger(f"radar.kafka.{name}")
    log.setLevel(logging.WARNING)
    log.propagate = False
    log.handlers = [_Keep()]
    return log, lines

class DarwinKafkaFeed:
    def __init__(self, settings, state, consumer_factory=None, store_path=None) -> None:
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
        self.raw_sample: str | None = None
        self._log, self.broker_log = _broker_logger(type(self).__name__)
        self.sample: dict | None = None
        # Coaches per Darwin service (RID), and which timetable train each RID is.
        self._formations: dict[str, list[dict]] = {}
        self._rid_for: dict[tuple[str, str], str] = {}
        # Coach details are published ahead of each train, often hours before:
        # kept on disk so a restart does not lose the day's formations.
        self._store_path = store_path
        self._dirty = False
        self._saved_at = time.monotonic()
        self._lock = threading.Lock()
        self.formation_messages = 0
        self.load()

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
        self.save(force=True)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)

    def report(self) -> dict:
        return {
            "broker_error": self.broker_error, "broker_log": list(self.broker_log),
            "server": self.settings.raildata_darwin_kafka_bootstrap, "live": self.live, "messages": self.messages, "services": self.services,
            "formations": len(self._formations), "formation_messages": self.formation_messages,
            "raw_sample": self.raw_sample,
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
        return Consumer({**self._config(), "error_cb": self._broker_error, "logger": self._log})

    def _broker_error(self, error) -> None:
        self.broker_error = str(error)[:300]

    def handle(self, value) -> None:
        try:
            raw = value.decode("utf-8") if isinstance(value, bytes) else str(value)
            if self.raw_sample is None:
                self.raw_sample = raw[:700]   # public timetable data: lets the format be checked live
            payload = _unwrap(json.loads(raw))
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
        self.save()
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
                if self._rid_for.get((uid, day)) != rid:
                    self._rid_for[(uid, day)] = rid
                    self._dirty = True
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
            self._dirty = True
            if len(self._formations) > MAX_FORMATIONS:
                self._formations.pop(next(iter(self._formations)))

    def load(self) -> None:
        if not self._store_path:
            return
        try:
            saved = json.loads(open(self._store_path, encoding="utf-8").read())
        except (OSError, ValueError):
            return
        keep = _days_kept()
        with self._lock:
            for row in saved.get("rids") or []:
                if isinstance(row, list) and len(row) == 3 and str(row[1])[:10] in keep:
                    self._rid_for[(str(row[0]), str(row[1]))] = str(row[2])
            wanted = set(self._rid_for.values())
            for rid, coaches in (saved.get("formations") or {}).items():
                if rid in wanted and isinstance(coaches, list):
                    self._formations[rid] = coaches

    def save(self, force: bool = False) -> None:
        """Write formations for today and tomorrow, at most once a minute."""
        if not self._store_path or not self._dirty or (not force and time.monotonic() - self._saved_at < 60):
            return
        keep = _days_kept()
        with self._lock:
            rids = [[uid, day, rid] for (uid, day), rid in self._rid_for.items() if day[:10] in keep]
            wanted = {row[2] for row in rids}
            formations = {rid: coaches for rid, coaches in self._formations.items() if rid in wanted}
            self._dirty = False
            self._saved_at = time.monotonic()
        try:
            temporary = f"{self._store_path}.tmp"
            with open(temporary, "w", encoding="utf-8") as handle:
                json.dump({"rids": rids, "formations": formations}, handle, separators=(",", ":"))
            os.replace(temporary, self._store_path)
        except OSError:
            self._dirty = True

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
            # Darwin's own expected (or actual) time at each call: what the
            # station's departure screens show.
            self.state.rail_notes.forecast(
                uid, service_date, _text(location.get("tpl")),
                arrival=_forecast_clock(location.get("arr")), departure=_forecast_clock(location.get("dep")),
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
        changes = {}
        platform = _text(relevant.get("plat"))
        delay = _delay_minutes(relevant)
        if platform and platform != train.platform:
            changes["platform"] = platform
        if delay is not None and delay != train.delay_minutes:
            changes["delay_minutes"] = delay
            if not str(train.status or "").lower().startswith(("cancel", "terminat")):
                changes["status"] = "On time" if delay <= 2 else f"{delay} min late"
        # Only these fields: the train may have stepped on since it was looked
        # up, and last_updated stays the last *position* report.
        if changes and self.state.patch_train(train.id, **changes):
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
                        if self.error:
                            consumer.poll(0.5)   # delivers librdkafka's log lines: why it failed
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
