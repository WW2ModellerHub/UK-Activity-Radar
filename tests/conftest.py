"""Shared test setup."""
import pytest


@pytest.fixture(autouse=True)
def _no_live_metar(monkeypatch):
    # Airport pages ask aviationweather.gov for the wind; tests never go online.
    from app.airport_runways import MetarFeed
    monkeypatch.setattr(MetarFeed, "_get", lambda self, icaos: [])
