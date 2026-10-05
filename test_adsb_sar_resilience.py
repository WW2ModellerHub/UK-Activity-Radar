from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import requests

from app.collectors.adsb_lol import ADSBLolSARCollector
from app.models import Vehicle
from app.state import LiveState


def _vehicle(ident: str, when: datetime) -> Vehicle:
    return Vehicle(
        id=f"sar:{ident}", type="sar", name=ident, subtitle="test", lat=52.0, lon=-1.0,
        source="ADSB.lol", source_id=ident, last_updated=when,
    )


def _collector():
    settings = SimpleNamespace(
        adsb_sar_points=[(50.8, -3.5, 220), (52.7, -1.5, 220)],
        adsb_lol_base_url="https://example.invalid",
        interesting_aircraft_enabled=True,
        adsb_sar_poll_seconds=60,
    )
    return ADSBLolSARCollector(settings, LiveState())


def test_cached_snapshot_combines_recent_regions_and_expires_old_regions():
    c = _collector()
    now = datetime.now(timezone.utc)
    c._region_cache[0] = ([_vehicle("a", now)], [], 10, now)
    c._region_cache[1] = ([_vehicle("b", now)], [], 20, now - timedelta(minutes=11))
    rows, special, checked = c._cached_snapshot(max_age_minutes=10)
    assert [v.id for v in rows] == ["sar:a"]
    assert special == []
    assert checked == 10
    assert 1 not in c._region_cache


def test_retry_after_header_is_honoured_and_bounded():
    c = _collector()
    response = requests.Response()
    response.status_code = 429
    response.headers["Retry-After"] = "123"
    exc = requests.HTTPError("429", response=response)
    assert c._retry_after_seconds(exc, 60) == 123


def test_retry_after_falls_back_to_polite_backoff():
    c = _collector()
    response = requests.Response()
    response.status_code = 429
    exc = requests.HTTPError("429", response=response)
    assert c._retry_after_seconds(exc, 60) == 120
