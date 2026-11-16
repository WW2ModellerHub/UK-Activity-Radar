from types import SimpleNamespace

from starlette.requests import Request

import app.main as main


def _request(path: str = "/") -> Request:
    return Request({
        "type": "http",
        "method": "GET",
        "path": path,
        "headers": [(b"host", b"example.test")],
        "query_string": b"",
        "client": ("127.0.0.1", 12345),
        "server": ("example.test", 443),
        "scheme": "https",
    })


def _settings(**overrides):
    values = {
        "public_base_url": "https://example.test",
        "google_site_verification": "search-console-token",
        "ga4_enabled": True,
        "ga4_measurement_id": "G-ABC12345",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_home_has_search_social_and_structured_metadata(monkeypatch):
    monkeypatch.setattr(main, "settings", _settings())
    response = main.home(_request())
    html = response.body.decode()

    assert '<link rel="canonical" href="https://example.test/">' in html
    assert '<meta name="google-site-verification" content="search-console-token">' in html
    assert 'property="og:title"' in html
    assert 'type="application/ld+json"' in html
    assert '"@type":"WebApplication"' in html
    assert 'data-ga4-measurement-id="G-ABC12345"' in html
    assert "{{PUBLIC_BASE_URL}}" not in html


def test_invalid_or_disabled_ga4_id_is_not_exposed(monkeypatch):
    monkeypatch.setattr(main, "settings", _settings(ga4_enabled=False, ga4_measurement_id="G-ABC12345"))
    assert 'data-ga4-measurement-id=""' in main.home(_request()).body.decode()
    monkeypatch.setattr(main, "settings", _settings(ga4_enabled=True, ga4_measurement_id="not-an-id"))
    assert 'data-ga4-measurement-id=""' in main.home(_request()).body.decode()


def test_robots_and_sitemap_publish_only_preferred_public_pages(monkeypatch):
    monkeypatch.setattr(main, "settings", _settings())
    robots = main.robots(_request("/robots.txt")).body.decode()
    sitemap = main.sitemap(_request("/sitemap.xml")).body.decode()

    assert "Disallow: /api/" in robots
    # Link-preview crawlers must be able to read shared pages; those pages
    # carry their own noindex instead.
    assert "Disallow: /live/" not in robots
    assert "Sitemap: https://example.test/sitemap.xml" in robots
    assert "https://example.test/about" in sitemap
    assert "https://example.test/privacy" in sitemap
    assert "/api/" not in sitemap
    assert "/live/" not in sitemap


def test_shared_live_pages_are_explicitly_noindex():
    share = (main.STATIC_DIR / "share.html").read_text(encoding="utf-8")
    assert '<meta name="robots" content="noindex,nofollow">' in share


def test_a_shared_link_previews_with_its_own_title_and_picture(monkeypatch):
    from datetime import datetime, timezone

    from fastapi.testclient import TestClient

    from app.models import Vehicle
    from app.state import LiveState

    state = LiveState()
    state.upsert(Vehicle(id="nr:1D90", type="train", name="1D90", headcode="1D90", lat=52.38, lon=-1.26,
                         origin="LONDON EUSTON", destination="CHESTER", operator="Avanti West Coast", delay_minutes=11,
                         source="Network Rail TRUST", last_updated=datetime.now(timezone.utc)))
    monkeypatch.setattr(main, "state", state)
    monkeypatch.setattr(main, "_card_cache", {})
    client = TestClient(main.app)
    page = client.get("/live/nr%3A1D90").text
    assert '<meta property="og:title" content="1D90 · London Euston → Chester">' in page
    assert 'og:description" content="11 min late · Southern Britain · Avanti West Coast"' in page  # no stations loaded in the test
    assert '/live/nr%3A1D90/card.png' in page and 'summary_large_image' in page
    assert 'name="robots" content="noindex' in page          # still kept out of search results
    assert "52.38" not in page and "-1.26" not in page        # no coordinates, even in the tags
    card = client.get("/live/nr%3A1D90/card.png")
    assert card.status_code == 200 and card.content.startswith(b"\x89PNG")
    from PIL import Image
    import io
    assert Image.open(io.BytesIO(card.content)).size == (1200, 630)
    gone = client.get("/live/nr%3Agone")
    assert 'content="UK Activity Radar"' in gone.text and "/card.png" in gone.text
    assert client.get("/card.png").content.startswith(b"\x89PNG")


def test_tag_manager_is_given_to_the_page_and_allowed_only_when_analytics_are_on(monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setattr(main, "settings", _settings(ga4_measurement_id="", gtm_container_id="GTM-N2JVR3QZ"))
    assert 'data-gtm-container-id="GTM-N2JVR3QZ"' in main.home(_request()).body.decode()
    monkeypatch.setattr(main, "settings", _settings(ga4_enabled=False, gtm_container_id="GTM-N2JVR3QZ"))
    assert 'data-gtm-container-id=""' in main.home(_request()).body.decode()
    monkeypatch.setattr(main, "settings", _settings(gtm_container_id="<script>"))
    assert 'data-gtm-container-id=""' in main.home(_request()).body.decode()

    from dataclasses import replace

    from app.config import settings as base
    for enabled, expected in ((True, True), (False, False)):
        live = replace(base, public_deployment=True, ga4_enabled=enabled, ga4_measurement_id="", gtm_container_id="GTM-N2JVR3QZ")
        monkeypatch.setattr(main, "settings", live)
        policy = TestClient(main.app).get("/robots.txt").headers["content-security-policy"]
        assert ("https://www.googletagmanager.com" in policy) is expected
        assert "'unsafe-inline'" not in policy.split("script-src", 1)[1].split(";", 1)[0]
