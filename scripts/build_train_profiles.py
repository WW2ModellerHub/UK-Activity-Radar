"""Cut the generated train side profiles into one image per coach.

A profile shows one unit (e.g. a 3-coach Turbostar). Cut at the gangways, the
page can build a train of any length from its front, middle and rear coaches
and put each coach's letter, first class and toilets under that coach.

    python scripts/build_train_profiles.py
Reads art/train-profiles/*.png, writes
app/static/assets/train-profiles/<name>/<n>.webp and prints the coach counts.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "art" / "train-profiles"          # the generated PNGs (large; not served)
DIR = ROOT / "app" / "static" / "assets" / "train-profiles"   # what the page loads
COACHES = {  # coaches drawn in each generated image
    "class-170-crosscountry-turbostar": 3, "class-195-northern-civity": 3, "class-220-crosscountry-voyager": 3,
    "class-350-lnwr-desiro": 4, "class-390-pendolino": 7, "class-800-gwr-iet": 5, "class-800-lner-azuma": 5,
}
HEIGHT = 96   # px of each coach image (shown at about a third of that, sharp on phones)


def trim(image: Image.Image) -> Image.Image:
    alpha = image.getchannel("A").point(lambda a: 255 if a > 40 else 0)
    return image.crop(alpha.getbbox())


def cuts(image: Image.Image, count: int) -> list[int]:
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
    edges = cuts(image, count)
    out = DIR / name
    out.mkdir(exist_ok=True)
    widths = []
    for index, (left, right) in enumerate(zip(edges, edges[1:])):
        coach = image.crop((left, 0, right, image.height))
        coach = coach.resize((max(1, round(coach.width * HEIGHT / coach.height)), HEIGHT), Image.LANCZOS)
        coach.save(out / f"{index}.webp", "WEBP", quality=82, method=6)
        widths.append(coach.width)
    return widths


def build_single(name: str) -> int:
    """A locomotive or wagon: trimmed and made small (the PNGs are 1.5-2.3 MB each)."""
    image = trim(Image.open(SOURCE / f"{name}.png").convert("RGBA"))
    image = image.resize((max(1, round(image.width * HEIGHT / image.height)), HEIGHT), Image.LANCZOS)
    image.save(DIR / f"{name}.webp", "WEBP", quality=82, method=6)
    return image.width


if __name__ == "__main__":
    for name, count in COACHES.items():
        print(name, build(name, count))
    for path in sorted(SOURCE.glob("freight-*.png")):
        print(path.stem, build_single(path.stem))
