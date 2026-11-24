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
