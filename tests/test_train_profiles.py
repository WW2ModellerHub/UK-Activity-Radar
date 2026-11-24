from pathlib import Path


def test_passenger_consists_and_cargo_aware_freight_profiles_are_shipped():
    static = Path(__file__).parents[1] / "app" / "static"
    js = (static / "app.js").read_text(encoding="utf-8")
    css = (static / "app.css").read_text(encoding="utf-8")
    assert "function trainCoachSvg" in js
    assert "function pendolinoCoachSvg" in js
    assert "pendolino-svg" in js
    assert "profile-class-390" in css
    assert "TRAIN_PROFILE_IMAGES" in js
    assert "class-390-pendolino.png" in js
    assert "function trainFormationHtml" in js
    assert "function freightProfileHtml" in js
    assert "function locoSvg" in js
    for kind in ("coal", "aggregate", "containers", "cars", "tank", "chemical", "metals", "ballast", "brake"):
        assert f"{kind}:" in js or f"'{kind}'" in js
    assert "formation=freight?freightProfileHtml" in js
