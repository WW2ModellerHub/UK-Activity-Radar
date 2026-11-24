"""Shared test setup."""
import pytest


@pytest.fixture(autouse=True)
def _no_live_metar(monkeypatch):
    # Airport pages ask aviationweather.gov for the wind; tests never go online.
    from app.airport_runways import MetarFeed
    monkeypatch.setattr(MetarFeed, "_get", lambda self, icaos: [])


@pytest.fixture(autouse=True)
def _own_berth_steps_only(monkeypatch, tmp_path_factory):
    """TD tests bring their own small SMART file; keep the bundled berth steps out of them."""
    import app.collectors.td as td
    monkeypatch.setattr(td, "BERTH_STEPS_FILE", tmp_path_factory.getbasetemp() / "no-berth-steps.csv.gz")
