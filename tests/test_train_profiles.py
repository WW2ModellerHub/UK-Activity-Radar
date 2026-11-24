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
    for asset in (
        "class-170-crosscountry-turbostar.png",
        "class-195-northern-civity.png",
        "class-220-crosscountry-voyager.png",
        "class-350-lnwr-desiro.png",
        "class-800-gwr-iet.png",
        "class-800-lner-azuma.png",
        "freight-class-66-db-cargo.png",
        "freight-wagon-aggregate.png",
        "freight-wagon-automotive.png",
        "freight-wagon-intermodal.png",
        "freight-wagon-steel.png",
        "freight-wagon-tank.png",
    ):
        assert asset in js
        image = static / "assets" / "train-profiles" / asset
        assert image.is_file() and image.stat().st_size > 10_000
        assert image.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert "function trainProfileImage" in js
    assert "const FREIGHT_PROFILE_IMAGES" in js
    assert "exactClass==='66'&&theme.key==='db-cargo'" in js
    assert "published coaches" in js
    assert "Accessible toilet" in js
    assert "function trainFormationHtml" in js
    assert "function freightProfileHtml" in js
    assert "function locoSvg" in js
    for kind in ("coal", "aggregate", "containers", "cars", "tank", "chemical", "metals", "ballast", "brake"):
        assert f"{kind}:" in js or f"'{kind}'" in js
    assert "formation=freight?freightProfileHtml" in js
