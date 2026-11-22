"""The site icon: served where browsers and Google look, in the sizes they use."""
from __future__ import annotations

from fastapi.testclient import TestClient
from PIL import Image

import app.main as main


def test_icons_are_served_at_the_root_with_long_caching():
    client = TestClient(main.app)
    icon = client.get("/favicon.ico")
    assert icon.status_code == 200 and icon.headers["content-type"] == "image/x-icon"
    assert "max-age=604800" in icon.headers["cache-control"]
    touch = client.get("/apple-touch-icon.png")
    assert touch.status_code == 200 and touch.headers["content-type"] == "image/png"
    assert client.get("/static/brand/logo.svg").status_code == 200


def test_favicon_has_the_sizes_google_and_browsers_use():
    sizes = Image.open(main.STATIC_DIR / "favicon.ico").info["sizes"]
    assert {(16, 16), (32, 32), (48, 48)} <= set(sizes)
    # Google wants a square icon whose side is a multiple of 48 px.
    for size in (48, 96, 144, 192, 512):
        image = Image.open(main.STATIC_DIR / f"icon-{size}.png")
        assert image.size == (size, size)


def test_every_page_head_points_at_the_icon():
    from app.seo_pages import _shell
    page = _shell(title="T", description="D", canonical="https://x.test/", body="")
    assert '<link rel="icon" href="/favicon.ico" sizes="48x48">' in page
    assert 'rel="apple-touch-icon" href="/apple-touch-icon.png"' in page
    index = (main.STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert 'href="/favicon.ico"' in index and "icon-512.png" in index
