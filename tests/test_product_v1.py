from pathlib import Path

from app.models import Vehicle
from app.product import ProductAnalytics, product_plans
from app.search import ActivitySearch
from app.state import LiveState


def test_product_analytics_only_accepts_whitelist(tmp_path: Path):
    store = ProductAnalytics(tmp_path / "product.sqlite")
    assert store.record("session-1", "app_open", "web") is True
    assert store.record("session-1", "pricing_interest", "plus") is True
    assert store.record("session-1", "steal_password", "x") is False
    summary = store.summary()
    assert summary["anonymous_sessions"] == 1
    assert summary["events"]["app_open"] == 1
    assert summary["pricing_interest"]["plus"] == 1


def test_plan_prices_are_hypotheses_not_payments():
    payload = product_plans()
    assert payload["payments_enabled"] is False
    assert {p["id"] for p in payload["plans"]} == {"free", "plus", "enthusiast"}


def test_live_tracking_count_can_be_updated_without_changing_source_time():
    state = LiveState()
    vehicle = Vehicle(id="train:1", type="train", name="1A01", lat=52.0, lon=-1.0)
    before = vehicle.last_updated
    state.upsert(vehicle)
    state.set_tracking_count("train:1", 3)
    saved = state.get("train:1")
    assert saved.tracking_count == 3
    assert saved.last_updated == before


def test_natural_near_search_intent_parser():
    assert ActivitySearch._parse_intent("ships near Portsmouth") == ("vessel", "Portsmouth")
    assert ActivitySearch._parse_intent("trains around Hinckley") == ("train", "Hinckley")
    assert ActivitySearch._parse_intent("RNLI in Cornwall") == ("rescue", "Cornwall")
