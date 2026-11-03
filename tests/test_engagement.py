from datetime import datetime, timedelta, timezone

from app.engagement import build_today_payload, safe_share_payload
from app.models import Event, Vehicle


def test_safe_share_never_exposes_coordinates_and_protects_rescue():
    item = Vehicle(
        id="sar:one",
        type="sar",
        name="Coastguard helicopter",
        lat=50.82,
        lon=-1.08,
        source="Official feed",
        cool_category="rescue",
        cool_candidate=True,
    )

    payload = safe_share_payload(item)

    assert payload["sensitive"] is True
    assert payload["location"] == "Southern Britain"
    assert "lat" not in payload
    assert "lon" not in payload
    assert "Never travel" in payload["safety_note"]


def test_today_prefers_verified_interest_and_lists_major_disruption():
    now = datetime.now(timezone.utc)
    railtour = Vehicle(
        id="train:1z80",
        type="train",
        name="1Z80 Railtour",
        lat=53.2,
        lon=-1.4,
        source="Network Rail",
        cool_score=88,
        cool_candidate=True,
        cool_explanation="Charter / special passenger movement",
        last_updated=now,
    )
    incident = Event(
        id="incident:m1",
        type="incident",
        name="M1 closed",
        lat=52.9,
        lon=-1.2,
        source="National Highways",
        severity="severe",
        reason="Motorway closed",
        last_updated=now,
    )

    payload = build_today_payload([railtour, incident], since=now - timedelta(minutes=2))

    assert payload["best"]["activity_id"] == "train:1z80"
    assert {row["activity_id"] for row in payload["new_since"]} == {"train:1z80", "incident:m1"}
    assert payload["disruptions"][0]["activity_id"] == "incident:m1"
