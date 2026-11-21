"""National Express coaches: named as coaches, with a booking link."""
from __future__ import annotations

from dataclasses import replace

from fastapi.testclient import TestClient

import app.main as main
from app.collectors.bods import parse_siri_vm
from tests.test_bods import SAMPLE


def test_a_national_express_coach_is_named_as_one_and_keeps_its_operator_code():
    coach = parse_siri_vm(SAMPLE.replace(b"<OperatorRef>ARMD</OperatorRef>", b"<OperatorRef>NATX</OperatorRef>")
                          .replace(b"<PublishedLineName>158</PublishedLineName>", b"<PublishedLineName>460</PublishedLineName>"))[0]
    assert coach.name == "National Express 460"
    assert coach.operator == "NATX" and coach.id.startswith("bus:NATX:")
    assert parse_siri_vm(SAMPLE)[0].name == "Bus 158"


def test_book_link_goes_to_the_operator_until_an_affiliate_link_is_set(monkeypatch):
    client = TestClient(main.app)
    monkeypatch.setattr(main, "settings", replace(main.settings, national_express_affiliate_url=""))
    plain = client.get("/go/national-express", follow_redirects=False)
    assert plain.status_code == 302 and plain.headers["location"] == "https://www.nationalexpress.com/"
    assert client.get("/api/map/config").json()["affiliates"] == {"national-express": False}

    monkeypatch.setattr(main, "settings", replace(main.settings, national_express_affiliate_url="https://example.test/track?id=1"))
    tracked = client.get("/go/national-express", follow_redirects=False)
    assert tracked.headers["location"] == "https://example.test/track?id=1"
    assert "noindex" in tracked.headers["x-robots-tag"]
    assert client.get("/api/map/config").json()["affiliates"] == {"national-express": True}

    # Only https links are used; anything else falls back to the operator.
    monkeypatch.setattr(main, "settings", replace(main.settings, national_express_affiliate_url="javascript:alert(1)"))
    assert client.get("/go/national-express", follow_redirects=False).headers["location"] == "https://www.nationalexpress.com/"
    assert client.get("/go/somewhere-else", follow_redirects=False).status_code == 404
