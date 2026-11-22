"""Memory limits: glibc arena cap, trimming, and a size-bounded page cache."""
from __future__ import annotations

from fastapi.testclient import TestClient

import app.main as main
from app import memory


def test_memory_helpers_never_raise():
    assert isinstance(memory.limit_malloc_arenas(2), bool)
    assert isinstance(memory.trim(), bool)


def test_page_cache_is_bounded_by_size_not_count(monkeypatch):
    monkeypatch.setattr(main, "_seo_page_cache", {})
    monkeypatch.setattr(main, "_seo_page_cache_bytes", 0)
    monkeypatch.setattr(main, "SEO_PAGE_CACHE_MAX_BYTES", 10_000)
    for index in range(30):
        main._store_page(f"page:{index}", (float(index), "x" * 1_000))
    assert main._seo_page_cache_bytes <= 10_000
    assert main._seo_page_cache_bytes == sum(len(v[1]) for v in main._seo_page_cache.values())
    assert "page:29" in main._seo_page_cache and "page:0" not in main._seo_page_cache   # oldest went first
    main._store_page("page:29", (99.0, "y" * 500))                                     # replacing keeps the count right
    assert main._seo_page_cache_bytes == sum(len(v[1]) for v in main._seo_page_cache.values())


def test_runtime_health_reports_a_memory_breakdown_on_request():
    client = TestClient(main.app)
    plain = client.get("/api/health/runtime").json()
    assert "memory" not in plain and "memory_peak_mb_5min" in plain
    detail = client.get("/api/health/runtime", params={"detail": "true"}).json()
    assert "page_cache" in detail["memory"]
