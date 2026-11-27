"""Generated train profiles: cut per coach, small, and every image the page asks for exists."""
import re
from pathlib import Path

STATIC = Path(__file__).parents[1] / "app" / "static"


def test_every_profile_image_the_page_uses_exists_and_is_small():
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    sets = re.findall(r"\['(class-[\w-]+)',\[([\d,]+)\]\]", js)
    assert len(sets) >= 7
    for folder, widths in sets:
        for index in range(len(widths.split(","))):
            image = STATIC / "assets" / "train-profiles" / folder / f"{index}.webp"
            assert image.is_file(), image
            assert image.stat().st_size < 40_000            # was 0.5-2.3 MB per PNG
    for path in re.findall(r"/static/assets/train-profiles/(freight-[\w-]+\.webp)", js):
        assert (STATIC / "assets" / "train-profiles" / path).stat().st_size < 60_000
    assert ".png" not in "".join(re.findall(r"train-profiles/[^'\"`]+", js))


def test_coach_widths_in_the_page_match_the_cut_images():
    from PIL import Image
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    for folder, widths in re.findall(r"\['(class-[\w-]+)',\[([\d,]+)\]\]", js):
        for index, width in enumerate(int(w) for w in widths.split(",")):
            with Image.open(STATIC / "assets" / "train-profiles" / folder / f"{index}.webp") as image:
                assert image.size == (width, 96)


def test_every_train_and_bus_picture_the_page_uses_is_a_small_webp():
    """Whole pictures (locomotives, wagons, trams, specials, buses): the PNG sources are not served."""
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    names = set(re.findall(r"((?:train|bus)-profiles/[a-z0-9-]+\.webp)", js))
    names |= {f"train-profiles/{n}" for n in re.findall(r"'([a-z0-9-]+\.webp)'", js)}
    assert len(names) >= 40
    for name in names:
        path = STATIC / "assets" / name
        payload = path.read_bytes()
        assert payload[:4] == b"RIFF" and payload[8:12] == b"WEBP", name
        assert len(payload) < 80_000, name
    assert not list((STATIC / "assets").rglob("*.png")), "PNG sources belong in art/, not the served folder"


def test_bus_pictures_only_claim_a_model_the_feed_names():
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "function busProfileMatch" in js and "Typical coach illustration" in js


def test_emr_intercity_profiles_are_cut_per_coach():
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "'222':{'east-midlands':['class-222-emr-meridian',[286,225,244,222,285]]}" in js
    assert "'810':{'east-midlands':['class-810-emr-aurora',[275,209,245,202,267]]}" in js


def test_colas_and_rog_use_their_own_locomotive_profiles():
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "'37':{'rail-operations-group':'/static/assets/train-profiles/freight-class-37-rail-operations-group.webp'}" in js
    assert "colas:'/static/assets/train-profiles/freight-class-70-colas.webp'" in js
    assert "colas:['70','70'],'rail-operations-group':['37','37']" in js


def test_requested_passenger_classes_are_cut_per_coach_in_each_operator_livery():
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    sets = dict(re.findall(r"\['(class-[\w-]+)',\[([\d,]+)\]\]", js))
    for folder, coaches in (("class-387-great-northern-4", 4), ("class-387-gatwick-express-4", 4),
                            ("class-707-southeastern-5", 5), ("class-707-south-western-5", 5),
                            ("class-802-transpennine-5", 5), ("class-802-hull-trains-5", 5),
                            ("class-805-avanti-evero-5", 5), ("class-807-avanti-evero-7", 7),
                            ("class-153-east-midlands-1", 1), ("class-710-london-overground-5", 5)):
        assert len(sets[folder].split(",")) == coaches, folder
    assert "'807':{avanti:['class-807-avanti-evero-7'," in js
    assert "'170':{crosscountry:['class-170-crosscountry-turbostar'" in js   # existing liveries kept
    assert "'lumo'" in js and "'gatwick-express'" in js and "'island-line'" in js


def test_all_recognised_legacy_classes_now_have_raster_profiles_and_the_card_is_compact():
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    sets = dict(re.findall(r"\['(class-[\w-]+)',\[([\d,]+)\]\]", js))
    for folder, coaches in (("class-142-northern-2", 2), ("class-143-transport-for-wales-2", 2),
                            ("class-144-northern-2", 2), ("class-321-greater-anglia-4", 4),
                            ("class-365-great-northern-4", 4), ("class-456-south-western-2", 2),
                            ("class-507-merseyrail-3", 3), ("class-508-merseyrail-3", 3)):
        assert len(sets[folder].split(",")) == coaches, folder
    assert 'loading="eager" fetchpriority="high"' in js
    assert "Normally provided on the allocated unit" not in js
    assert "Coach layout from the allocated unit. Facilities are normally provided" not in js


def test_cut_points_are_taken_from_the_name_and_guided_where_the_bellows_match_the_body():
    import importlib.util
    spec = importlib.util.spec_from_file_location("build", Path(__file__).parents[1] / "scripts" / "build_train_profiles.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    counts = build.coach_counts()
    assert counts["class-807-avanti-evero-7"] == 7 and counts["class-153-east-midlands-1"] == 1
    assert "class-745-greater-anglia" not in counts and "class-43-gwr-castle" not in counts
    from PIL import Image
    assert build.cuts(Image.new("RGBA", (300, 96)), 1) == [0, 300]       # a single car is not cut
