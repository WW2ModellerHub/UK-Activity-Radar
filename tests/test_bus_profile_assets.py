from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ASSET_DIR = ROOT / "app" / "static" / "assets" / "bus-profiles"
EXPECTED = {
    "enviro200-mmc.png",
    "enviro400-mmc.png",
    "wright-streetlite.png",
    "wright-streetdeck.png",
    "mercedes-citaro.png",
    "yutong-e12.png",
    "new-routemaster.png",
    "generic-intercity-coach.png",
}


def test_bus_profile_assets_are_real_pngs():
    for name in EXPECTED:
        payload = (ASSET_DIR / name).read_bytes()
        assert payload.startswith(b"\x89PNG\r\n\x1a\n")
        assert len(payload) > 100_000


def test_bus_profiles_are_wired_to_truthful_matching():
    javascript = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
    for name in EXPECTED:
        assert f"/static/assets/bus-profiles/{name}" in javascript
    assert "function busProfileMatch" in javascript
    assert "BODS usually publishes a fleet number, not a body model" in javascript
    assert "Typical coach illustration" in javascript
