"""Darwin Kafka enriches a service without pretending to move it."""
from datetime import datetime, timezone
import json

import certifi

from app.collectors.darwin_kafka import DarwinKafkaFeed, _delay_minutes
from app.config import Settings
from app.models import Vehicle
from app.state import LiveState


def _settings(**changes):
    values = {
        "raildata_darwin_kafka_group": "group",
        "raildata_darwin_kafka_username": "user",
        "raildata_darwin_kafka_password": "secret",
    }
    values.update(changes)
    return Settings(**values)


def _train(now):
    return Vehicle(
        id="nr:train", type="train", name="1A23", lat=52.5, lon=-1.2,
        train_uid="P19489", service_date="2026-11-24", rail_tiploc="NUNEAT",
        next_tiploc="LESTER", next_location="Leicester", source="Network Rail TRUST",
        status="On time", delay_minutes=0, last_updated=now,
    )


def test_config_is_separate_and_uses_packaged_ca():
    config = DarwinKafkaFeed(_settings(), LiveState())._config()
    assert config["ssl.ca.location"] == certifi.where()
    assert config["group.id"] == "group"
    assert config["client.id"] == "uk-activity-radar-darwin"
    assert _settings().raildata_darwin_kafka_ready
    assert not Settings(raildata_darwin_kafka_group="group").raildata_darwin_kafka_ready


def test_darwin_enriches_delay_and_platform_but_not_position_or_report_time():
    state = LiveState()
    reported = datetime.now(timezone.utc)
    train = _train(reported)
    train.service_date = reported.date().isoformat()
    state.upsert(train)
    feed = DarwinKafkaFeed(_settings(), state)
    feed.handle(json.dumps({
        "ts": reported.isoformat(), "version": "18.0",
        "uR": {"updateOrigin": "TD", "TS": {
            "rid": "service", "uid": "P19489", "ssd": train.service_date,
            "Location": [
                {"tpl": "NUNEAT", "wtd": "12:00", "dep": {"at": "12:01"}, "plat": {"content": "2"}},
                {"tpl": "LESTER", "pta": "12:20", "arr": {"et": "12:27"}, "plat": {"#text": "4"}},
            ],
        }},
    }).encode())
    train = state.get("nr:train")
    assert (train.lat, train.lon, train.last_updated) == (52.5, -1.2, reported)
    assert train.platform == "4"
    assert train.delay_minutes == 7 and train.status == "7 min late"
    assert state.rail_notes.get("P19489", train.service_date)["platforms"] == {"NUNEAT": "2", "LESTER": "4"}
    assert feed.messages == 1 and feed.services == 1 and feed.enriched == 1 and feed.live


def test_wrapped_lists_and_midnight_delay_are_supported():
    assert _delay_minutes({"wta": "23:58", "arr": {"et": "00:04"}}) == 6
    state = LiveState()
    feed = DarwinKafkaFeed(_settings(), state)
    feed.handle('''{"Pport":{"uR":[{"TS":[{"uid":"X1","ssd":"2026-11-24","Location":{"tpl":"AAA","plat":"1"}}]}]}}''')
    assert state.rail_notes.get("X1", "2026-11-24")["platforms"] == {"AAA": "1"}


def test_bad_payload_is_counted_not_raised():
    feed = DarwinKafkaFeed(_settings(), LiveState())
    feed.handle(b"not-json")
    assert feed.malformed == 1 and feed.messages == 0 and not feed.live
