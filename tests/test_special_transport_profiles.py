from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "app" / "static" / "assets" / "train-profiles"
EXPECTED = {
    "london-underground.png", "tram-croydon.png", "tram-manchester.png",
    "tram-edinburgh.png", "tram-nottingham.png", "sleeper-train.png",
    "heritage-steam-charter.png", "rail-engineering-machine.png",
    "postal-train.png", "freight-wagon-biomass.png",
    "freight-wagon-nuclear-flask.png", "freight-wagon-timber.png",
    "freight-wagon-engineering.png",
}

def test_special_transport_assets_are_real_pngs():
    javascript = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
    for name in EXPECTED:
        payload = (ASSETS / name).read_bytes()
        assert payload.startswith(b"\x89PNG\r\n\x1a\n")
        assert len(payload) > 100_000
        assert name in javascript
