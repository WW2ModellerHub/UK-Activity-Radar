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
    assert len(names) >= 100
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


def test_requested_passenger_classes_choose_the_operator_livery():
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "'387':{'great-northern':'class-387-great-northern-4.webp'" in js
    assert "'gatwick-express':'class-387-gatwick-express-4.webp'" in js
    assert "'707':{southeastern:'class-707-southeastern-5.webp','south-western':'class-707-south-western-5.webp'}" in js
    assert "'802':{transpennine:'class-802-transpennine-5.webp','hull-trains':'class-802-hull-trains-5.webp'}" in js
    assert "'805':{avanti:'class-805-avanti-evero-5.webp'}" in js
    assert "'807':{avanti:'class-807-avanti-evero-7.webp'}" in js
    assert "'lumo'" in js and "'gatwick-express'" in js and "'island-line'" in js
