"""Permanent pages of each day's unusual finds: /discover/2026-11-21."""
from __future__ import annotations

from datetime import date
from html import escape

from .seo_pages import _breadcrumbs, _shell

CATEGORY = {"rail": "On the railway", "air": "In the air", "rescue": "Rescue", "sea": "At sea"}
MIN_FOR_SITEMAP = 3


def _nice(day: str) -> str:
    return date.fromisoformat(day).strftime("%A %-d %B %Y")


def day_page(origin: str, day: str, rows: list[dict], previous: str | None, following: str | None) -> str:
    groups: dict[str, list[dict]] = {}
    for row in rows:
        groups.setdefault(row.get("category") or "other", []).append(row)
    sections = []
    for key in ("rail", "air", "rescue", "sea", "other"):
        items = groups.get(key) or []
        if not items:
            continue
        lis = "".join(
            "<li><strong>{}</strong> <span class=\"pill\">{}</span>{}{}<small>Seen {}–{}</small></li>".format(
                escape(row["name"] or ""), escape(row.get("badge") or ""),
                f"<span> {escape(row['explanation'])}</span>" if row.get("explanation") else "",
                f"<span> Near {escape(row['place'])}.</span>" if row.get("place") else "",
                escape((row.get("first_seen") or "")[11:16]), escape((row.get("last_seen") or "")[11:16]))
            for row in items)
        sections.append(f"<h2>{escape(CATEGORY.get(key, 'Other'))} ({len(items)})</h2><ul class=\"links finds\">{lis}</ul>")
    nav = " · ".join(x for x in (
        f'<a href="/discover/{previous}">← {escape(_nice(previous))}</a>' if previous else "",
        '<a href="/discover-archive">All days</a>',
        f'<a href="/discover/{following}">{escape(_nice(following))} →</a>' if following else "") if x)
    title_day = _nice(day)
    body = f"""<p class="eyebrow">Discover archive</p>
<h1>Unusual trains and aircraft on {escape(title_day)}</h1>
<p class="lede">{len(rows)} unusual find{'s' if len(rows) != 1 else ''} spotted on the live radar: steam and test trains, light engines, military and rescue aircraft. Times are UTC.</p>
{''.join(sections) or '<p>Nothing unusual was recorded on this day.</p>'}
<p>{nav}</p>
<p>Finds are recorded as the radar sees them in live railway and aircraft feeds; positions are rounded to a named place and exact locations of sensitive aircraft are never stored. See <a href="/">today's live Discover</a>.</p>"""
    canonical = f"{origin}/discover/{day}"
    return _shell(title=f"Unusual trains and aircraft, {title_day} | UK Activity Radar",
                  description=f"Steam trains, test trains and military aircraft seen on {title_day}: {len(rows)} finds from the UK live radar.",
                  canonical=canonical, body=body,
                  json_ld={"@context": "https://schema.org", "@graph": [
                      {"@type": "WebPage", "name": f"Unusual trains and aircraft on {title_day}", "url": canonical},
                      _breadcrumbs(origin, canonical, [("Home", "/"), ("Discover archive", "/discover-archive"), (title_day, f"/discover/{day}")])]})


def archive_index(origin: str, days: list[tuple[str, int]]) -> str:
    items = "".join(f'<li><a href="/discover/{d}">{escape(_nice(d))}</a> <small>{n} find{"s" if n != 1 else ""}</small></li>' for d, n in days)
    body = f"""<p class="eyebrow">Discover archive</p><h1>Unusual trains and aircraft, day by day</h1>
<p class="lede">Every day's steam trains, test trains, light engines and military and rescue aircraft seen on the live radar.</p>
<ul class="links">{items or '<li>The archive starts today; check back tomorrow.</li>'}</ul>"""
    canonical = origin + "/discover-archive"
    return _shell(title="Discover archive: unusual trains and aircraft by day | UK Activity Radar",
                  description="A daily record of unusual trains and aircraft seen across the UK: steam specials, test trains and military flights.",
                  canonical=canonical, body=body,
                  json_ld={"@context": "https://schema.org", "@type": "CollectionPage", "name": "Discover archive", "url": canonical})
