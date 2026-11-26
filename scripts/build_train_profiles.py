"""Cut the generated train side profiles into one image per coach.

A profile shows one unit (e.g. a 3-coach Turbostar). Cut at the gangways, the
page can build a train of any length from its front, middle and rear coaches
and put each coach's letter, first class and toilets under that coach.

    python scripts/build_train_profiles.py
Reads art/train-profiles/*.png, writes
app/static/assets/train-profiles/<name>/<n>.webp and prints the coach counts.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "art" / "train-profiles"          # the generated PNGs (large; not served)
DIR = ROOT / "app" / "static" / "assets" / "train-profiles"   # what the page loads
COACHES = {  # coaches drawn in each generated image
    "class-170-crosscountry-turbostar": 3, "class-195-northern-civity": 3, "class-220-crosscountry-voyager": 3,
    "class-350-lnwr-desiro": 4, "class-390-pendolino": 7, "class-800-gwr-iet": 5, "class-800-lner-azuma": 5,
    "class-150-northern": 2, "class-158-northern": 2, "class-159-south-western": 3, "class-165-gwr": 3,
    "class-168-chiltern": 4, "class-185-transpennine": 3, "class-196-west-midlands": 4,
    "class-197-transport-for-wales": 3, "class-345-elizabeth-line": 9, "class-377-southern": 4,
    "class-385-scotrail": 4, "class-395-southeastern": 6, "class-444-south-western": 5,
    "class-700-thameslink": 8, "class-720-greater-anglia": 5,
    "class-222-emr-meridian": 5, "class-810-emr-aurora": 5,
}
# Where the gangways are, as shares of the length, for pictures whose gangway
# bellows are bodyside-coloured (articulated FLIRTs and Aventras): the cut is
# made at the slimmest point near each.
GUIDES = {
    "class-170-northern-3": (0.341, 0.670),
    "class-231-transport-for-wales-4": (0.297, 0.527, 0.754),
    "class-710-london-overground-5": (0.221, 0.418, 0.611, 0.805),
    "class-730-london-northwestern-5": (0.224, 0.412, 0.599, 0.781),
}
# Shown whole: locomotives and wagons, trams and specials, and the articulated
# class 745 (its short carriages share bogies: no clean gap to cut at).
WHOLE = ("freight-*", "class-745-*", "heritage-*", "london-underground", "postal-train", "rail-engineering-machine",
         "sleeper-train", "tram-*")
BUS_SOURCE = ROOT / "art" / "bus-profiles"
BUS_DIR = ROOT / "app" / "static" / "assets" / "bus-profiles"
HEIGHT = 96   # px of each coach image (shown at about a third of that, sharp on phones)


def trim(image: Image.Image) -> Image.Image:
    alpha = image.getchannel("A").point(lambda a: 255 if a > 40 else 0)
    return image.crop(alpha.getbbox())


def cuts(image: Image.Image, count: int, guides: tuple[float, ...] = ()) -> list[int]:
    """x of each gangway: the count-1 deepest dips in body height along the train.

    Gangways are narrower and lower than the bodyside, so the opaque height
    of each column dips there. End cars are longer than middle ones, so
    the dips are found along the whole train, not at even spacing.
    """
    # Bodyside: opaque and not near-black. Gaps between coaches are see-through;
    # gangway bellows are near-black. Either way the count dips there.
    rgba = image.load()
    width, height = image.size
    top, bottom = int(height * 0.12), int(height * 0.72)    # bodyside, above the bogies

    def bright(pixel) -> int:
        r, g, b, a = pixel
        return 1 if a > 128 and (r * 299 + g * 587 + b * 114) // 1000 > 55 else 0
    body = [sum(bright(rgba[x, y]) for y in range(top, bottom)) for x in range(width)]
    smooth = [min(body[max(0, x - 2):x + 3]) for x in range(width)]
    if count <= 1:
        return [0, width]
    if guides:
        window = max(3, width // 70)
        picked = []
        for share in guides:
            centre = int(share * width)
            span = range(max(0, centre - window), min(width, centre + window + 1))
            picked.append(min(span, key=lambda x: (smooth[x], abs(x - centre))))
        return [0, *sorted(picked), width]
    margin = width // (count * 3)                            # no gangway inside a cab
    spacing = width // (count * 2)                           # nor two within half a coach
    found: list[int] = []
    for x in sorted(range(margin, width - margin), key=lambda x: (smooth[x], abs(x - width / 2))):
        if all(abs(x - other) >= spacing for other in found):
            found.append(x)
        if len(found) == count - 1:
            break
    # The middle of each dip, not its edge.
    centred = []
    for x in found:
        left = right = x
        while left > 0 and smooth[left - 1] <= smooth[x] + 2:
            left -= 1
        while right < width - 1 and smooth[right + 1] <= smooth[x] + 2:
            right += 1
        centred.append((left + right) // 2)
    return [0, *sorted(centred), width]


def build(name: str, count: int) -> list[int]:
    image = trim(Image.open(SOURCE / f"{name}.png").convert("RGBA"))
    edges = cuts(image, count, GUIDES.get(name, ()))
    out = DIR / name
    out.mkdir(exist_ok=True)
    widths = []
    for index, (left, right) in enumerate(zip(edges, edges[1:])):
        coach = image.crop((left, 0, right, image.height))
        coach = coach.resize((max(1, round(coach.width * HEIGHT / coach.height)), HEIGHT), Image.LANCZOS)
        coach.save(out / f"{index}.webp", "WEBP", quality=82, method=6)
        widths.append(coach.width)
    return widths


def build_single(name: str, source: Path = SOURCE, target: Path = DIR, height: int = HEIGHT) -> int:
    """A whole picture (locomotive, wagon, tram, bus): trimmed and made small (the PNGs are 0.5-2.3 MB each)."""
    image = trim(Image.open(source / f"{name}.png").convert("RGBA"))
    image = image.resize((max(1, round(image.width * height / image.height)), height), Image.LANCZOS)
    target.mkdir(parents=True, exist_ok=True)
    image.save(target / f"{name}.webp", "WEBP", quality=82, method=6)
    return image.width


def coach_counts() -> dict[str, int]:
    """COACHES, plus every "class-<n>-<livery>-<coaches>.png" (the count is in the name)."""
    counts = dict(COACHES)
    for path in sorted(SOURCE.glob("class-*.png")):
        found = re.fullmatch(r"class-\d+-.+-(\d+)", path.stem)
        if found and path.stem not in counts:
            counts[path.stem] = int(found.group(1))
    return counts


if __name__ == "__main__":
    for name, count in coach_counts().items():
        print(name, build(name, count))
    for pattern in WHOLE:
        for path in sorted(SOURCE.glob(f"{pattern}.png")):
            print(path.stem, build_single(path.stem))
    for path in sorted(BUS_SOURCE.glob("*.png")):     # buses are taller than they are long: more pixels
        print(path.stem, build_single(path.stem, BUS_SOURCE, BUS_DIR, 160))
