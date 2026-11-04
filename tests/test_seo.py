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
    assert "Disallow: /live/" in robots
    assert "Sitemap: https://example.test/sitemap.xml" in robots
    assert "https://example.test/about" in sitemap
    assert "https://example.test/privacy" in sitemap
    assert "/api/" not in sitemap
    assert "/live/" not in sitemap


def test_shared_live_pages_are_explicitly_noindex():
    share = (main.STATIC_DIR / "share.html").read_text(encoding="utf-8")
    assert '<meta name="robots" content="noindex,nofollow">' in share
