"""Preview pictures for shared links (WhatsApp, iMessage, X, Facebook).

A link pasted into a chat shows the picture named in the page's og:image.
This draws a 1200 x 630 card for one live train, bus or aircraft from the
same safe facts as the shared page: what it is, where it is going, whether
it is late, roughly where it is. Never coordinates.
"""
from __future__ import annotations

import io
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONTS = Path(__file__).resolve().parent / "static" / "fonts"
WIDTH, HEIGHT = 1200, 630
BACKGROUND = (9, 16, 24)
PANEL = (17, 31, 42)
MINT = (84, 215, 165)
WHITE = (238, 247, 245)
GREY = (150, 172, 182)
TONES = {"late": (230, 160, 40), "bad": (226, 80, 80), "good": (84, 215, 165)}
TYPE_WORDS = {"train": "LIVE TRAIN", "bus": "LIVE BUS", "aircraft": "LIVE AIRCRAFT", "sar": "RESCUE AIRCRAFT"}


@lru_cache(maxsize=16)
def _font(weight: str, size: int) -> ImageFont.FreeTypeFont:
    name = {"regular": "Inter-Regular.otf", "bold": "Inter-Bold.otf", "black": "Inter-ExtraBold.otf"}[weight]
    try:
        return ImageFont.truetype(str(FONTS / name), size)
    except OSError:  # never fail a preview for want of a font
        return ImageFont.load_default(size)


def _fit(draw: ImageDraw.ImageDraw, text: str, weight: str, size: int, width: int, smallest: int = 28):
    """The largest font (down to ``smallest``) that fits; then trim with an ellipsis."""
    while size > smallest and draw.textlength(text, font=_font(weight, size)) > width:
        size -= 4
    font = _font(weight, size)
    if draw.textlength(text, font=font) <= width:
        return text, font
    while text and draw.textlength(text + "…", font=font) > width:
        text = text[:-1]
    return text.rstrip() + "…", font


def _place(text) -> str:
    return " ".join(w if any(c.islower() for c in w) else w.capitalize() for w in str(text or "").split())


def running_text(payload: dict) -> tuple[str, str] | None:
    if payload.get("cancelled"):
        return "Cancelled", "bad"
    delay = payload.get("delay_minutes")
    if delay is None:
        return None
    try:
        minutes = round(float(delay))
    except (TypeError, ValueError):
        return None
    if minutes >= 1:
        return f"{minutes} min late", "bad" if minutes >= 15 else "late"
    if minutes <= -1:
        return f"{-minutes} min early", "good"
    return "On time", "good"


def card_lines(payload: dict) -> dict:
    """The words on the card, from the shared-page facts."""
    origin, destination = _place(payload.get("origin")), _place(payload.get("destination"))
    journey = f"{origin} → {destination}" if origin and destination else (f"To {destination}" if destination else "")
    where = payload.get("near") or payload.get("location") or ""
    details = []
    if payload.get("aircraft_model"):
        details.append(payload["aircraft_model"])
    if payload.get("altitude_ft") is not None:
        details.append(f"{round(float(payload['altitude_ft'])):,} ft")
    if payload.get("operator") and payload.get("type") != "aircraft":
        details.append(payload["operator"])
    return {
        "eyebrow": str(payload.get("badge") or TYPE_WORDS.get(payload.get("type"), "LIVE")).upper(),
        "title": str(payload.get("name") or "Live activity"),
        "line": journey or where,
        "where": where if journey else "",
        "details": " · ".join(details),
        "running": running_text(payload),
    }


def render(payload: dict | None) -> bytes:
    """PNG bytes. With no payload, the site's own card."""
    image = Image.new("RGB", (WIDTH, HEIGHT), BACKGROUND)
    draw = ImageDraw.Draw(image)
    # A soft glow top left, like the app.
    for radius in range(520, 0, -20):
        shade = int(9 + 30 * (1 - radius / 520))
        draw.ellipse((-radius + 120, -radius + 40, radius + 120, radius + 40), fill=(9, shade, shade + 8))
    draw.rounded_rectangle((40, 40, WIDTH - 40, HEIGHT - 40), radius=36, fill=PANEL)
    # Brand.
    draw.rounded_rectangle((84, 82, 148, 146), radius=16, fill=MINT)
    draw.text((116, 114), "R", font=_font("black", 38), fill=BACKGROUND, anchor="mm")
    draw.text((168, 114), "UK Activity Radar", font=_font("bold", 32), fill=WHITE, anchor="lm")
    left, width = 84, WIDTH - 168
    if not payload:
        title, font = _fit(draw, "Live UK trains, buses, aircraft and roads", "black", 74, width)
        draw.text((left, 260), title, font=font, fill=WHITE)
        draw.text((left, 372), "One live map. Delays, platforms, rain radar and the unusual.", font=_font("regular", 36), fill=GREY)
        draw.text((left, 500), "See what is moving near you", font=_font("bold", 34), fill=MINT)
        return _png(image)
    lines = card_lines(payload)
    draw.text((left, 196), lines["eyebrow"], font=_font("bold", 30), fill=MINT)
    title, font = _fit(draw, lines["title"], "black", 112, width, smallest=56)
    draw.text((left, 236), title, font=font, fill=WHITE)
    y = 380
    if lines["line"]:
        text, font = _fit(draw, lines["line"], "bold", 46, width, smallest=30)
        draw.text((left, y), text, font=font, fill=WHITE)
        y += 64
    small = " · ".join(x for x in (lines["where"], lines["details"]) if x)
    if small:
        text, font = _fit(draw, small, "regular", 34, width, smallest=24)
        draw.text((left, y), text, font=font, fill=GREY)
    if lines["running"]:
        words, tone = lines["running"]
        chip = _font("bold", 34)
        chip_width = int(draw.textlength(words, font=chip)) + 48
        draw.rounded_rectangle((left, 498, left + chip_width, 556), radius=29, fill=TONES[tone])
        draw.text((left + chip_width / 2, 527), words, font=chip, fill=BACKGROUND, anchor="mm")
    draw.text((WIDTH - 84, 527), "Live now · tap to follow", font=_font("bold", 30), fill=MINT, anchor="rm")
    return _png(image)


def _png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, "PNG", optimize=True)
    return buffer.getvalue()
