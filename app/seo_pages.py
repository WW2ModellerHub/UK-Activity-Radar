"""Readable pages for search engines and people: one per station, one per
train operator, and an index of each.

People search "Rugby departures", "Avanti delays today". A map that draws
itself in the browser gives a search engine nothing to read, so these pages
are written on the server with the live facts in them, and link into the
live map.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

LONDON = ZoneInfo("Europe/London")
_SUFFIX = re.compile(r"\s+(rail(way)?\s+station|station)$", re.I)
_SMALL = {"and", "of", "on", "upon", "the", "in", "by", "le", "la", "de"}
_KEEP_UPPER = {"DLR", "UK", "LUL", "II"}


def tidy_name(name: str) -> str:
    """'Rugby Rail Station' -> 'Rugby'; 'LONDON EUSTON' -> 'London Euston'."""
    text = _SUFFIX.sub("", str(name or "").strip())
    if text.isupper() or text.islower():
        words = []
        for index, word in enumerate(text.split()):
            low = word.lower()
            if word.upper() in _KEEP_UPPER:
                words.append(word.upper())
            elif index and low in _SMALL:
                words.append(low)
            else:
                parts = low.split("-")
                words.append("-".join(part if i and part in _SMALL else part[:1].upper() + part[1:] for i, part in enumerate(parts)))
        text = " ".join(words)
    return text.replace("Intl", "International").strip()


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower().replace("&", " and ")).strip("-")


def station_path(name: str, crs: str) -> str:
    return f"/station/{slug(name)}-{crs.lower()}"


def operator_path(name: str) -> str:
    return f"/operator/{slug(name)}"


def crs_from_path(value: str) -> str | None:
    match = re.fullmatch(r"[a-z0-9-]*?-?([a-z]{3})", str(value or "").lower())
    return match.group(1).upper() if match else None


def directory(places: list[dict]) -> list[dict]:
    """One row per station: name, CRS code, position, path. Sorted by name."""
    seen: dict[str, dict] = {}
    for place in places:
        crs = str(place.get("crs") or "").strip().upper()
        if not re.fullmatch(r"[A-Z]{3}", crs):
            continue
        name = tidy_name(place.get("name") or crs)
        try:
            lat, lon = float(place["lat"]), float(place["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        # NaPTAN names ("Rugby Rail Station") read better than timetable ones.
        current = seen.get(crs)
        if current is None or ("station" in str(place.get("name", "")).lower() and not current["naptan"]):
            seen[crs] = {"crs": crs, "name": name, "lat": lat, "lon": lon, "path": station_path(name, crs),
                         "naptan": "station" in str(place.get("name", "")).lower()}
    return sorted(seen.values(), key=lambda row: (row["name"].lower(), row["crs"]))


def _now_clock() -> str:
    return datetime.now(LONDON).strftime("%H:%M")


def _clock(value) -> str:
    text = str(value or "")
    if "T" in text:
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(LONDON).strftime("%H:%M")
        except ValueError:
            return ""
    return text[:5]


def _shell(*, title: str, description: str, canonical: str, body: str, json_ld: dict | None = None) -> str:
    site = re.match(r"https?://[^/]+", canonical).group(0) if re.match(r"https?://[^/]+", canonical) else ""
    ld = f'<script type="application/ld+json">{escape(json.dumps(json_ld), quote=False)}</script>' if json_ld else ""
    return f"""<!doctype html>
<html lang="en-GB"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)}</title><meta name="description" content="{escape(description, quote=True)}">
<link rel="canonical" href="{escape(canonical, quote=True)}"><meta name="robots" content="index,follow,max-image-preview:large">
<meta property="og:type" content="website"><meta property="og:site_name" content="UK Activity Radar">
<meta property="og:title" content="{escape(title, quote=True)}"><meta property="og:description" content="{escape(description, quote=True)}">
<meta property="og:url" content="{escape(canonical, quote=True)}"><meta property="og:image" content="{escape(site, quote=True)}/card.png">
<meta name="twitter:card" content="summary_large_image"><meta name="theme-color" content="#ffffff">
<link rel="icon" href="/static/icon-192.png"><link rel="stylesheet" href="/static/seo.css">{ld}</head>
<body><header class="top"><a class="brand" href="/"><span>R</span>UK Activity Radar</a><nav><a href="/">Live map</a><a href="/stations">Stations</a><a href="/operators">Operators</a></nav></header>
<main>{body}</main>
<footer><a href="/">Live map</a> · <a href="/stations">All stations</a> · <a href="/operators">Train operators</a> · <a href="/about">About</a> · <a href="/data-licences">Data licences</a><p>Times and delays from Network Rail open data. Contains information of Network Rail Infrastructure Limited.</p></footer>
</body></html>"""


def _status(row: dict) -> tuple[str, str]:
    if row.get("cancelled") or "cancel" in str(row.get("status", "")).lower():
        return "Cancelled", "bad"
    delay = row.get("delay_minutes")
    if isinstance(delay, (int, float)) and delay >= 1:
        expected = _clock(row.get("expected"))
        text = f"Expected {expected}" if expected and expected != _clock(row.get("scheduled")) else f"{round(delay)} min late"
        # The railway counts up to five minutes as on time; so do we.
        return text, ("bad" if delay >= 15 else "late" if delay >= 5 else "good")
    if isinstance(delay, (int, float)):
        return "On time", "good"
    return "Scheduled", ""


def station_page(origin: str, station: dict, board: dict | None, nearby: list[dict], operator_rows: list[dict]) -> str:
    name, crs = station["name"], station["crs"]
    departures = [row for row in (board or {}).get("departures") or [] if int(row.get("minutes") or 0) >= -2][:12]
    rows, late, cancelled, known = [], 0, 0, 0
    operators: list[str] = []
    for row in departures:
        status, tone = _status(row)
        known += tone in {"good", "late", "bad"}
        late += tone in {"late", "bad"} and status != "Cancelled"
        cancelled += status == "Cancelled"
        operator = row.get("operator_name") or ""
        if operator and operator not in operators:
            operators.append(operator)
        platform = row.get("platform") or (f"usually {row['usual_platform']['platform']}" if row.get("usual_platform") else "")
        rows.append(
            f"<tr><td class=time>{escape(_clock(row.get('scheduled')))}</td>"
            f"<td><strong>{escape(tidy_name(row.get('destination') or ''))}</strong><small>{escape(operator)}</small></td>"
            f"<td>{escape(str(platform))}</td><td class=\"status {tone}\">{escape(status)}</td></tr>"
        )
    if not departures:
        summary = f"No departures are due from {escape(name)} in the next few hours."
    else:
        parts = [f"{known - late - cancelled} of the next {len(departures)} trains are on time or within 5 minutes"] if known else []
        if late:
            parts.append(f"{late} running late")
        if cancelled:
            parts.append(f"{cancelled} cancelled")
        summary = (", ".join(parts) or f"The next {len(departures)} departures") + f" (at {_now_clock()})."
    status_by_name = {row["operator"]: row for row in operator_rows}
    operator_links = "".join(
        f'<li><a href="{escape(operator_path(op))}">{escape(op)}</a> <span class="pill {escape(status_by_name[op]["status"])}">{escape(status_by_name[op]["label"])}</span></li>'
        if op in status_by_name else f"<li>{escape(op)}</li>"
        for op in operators
    )
    nearby_links = "".join(f'<li><a href="{escape(s["path"])}">{escape(s["name"])}</a></li>' for s in nearby)
    table = (f'<table class="board"><thead><tr><th>Due</th><th>Going to</th><th>Plat.</th><th>Status</th></tr></thead><tbody>{"".join(rows)}</tbody></table>'
             if rows else "")
    body = f"""<p class="eyebrow">Live train times · {escape(crs)}</p>
<h1>{escape(name)} departures</h1>
<p class="lede">{summary}</p>
<p><a class="button" href="/?place=rail-station:{escape(crs)}">See them moving on the live map</a></p>
{table}
{f'<h2>Operators at {escape(name)}</h2><ul class="links">{operator_links}</ul>' if operator_links else ''}
<h2>About this page</h2><p>Departures from {escape(name)} ({escape(crs)}) for the next few hours, from the published timetable with live running from Network Rail where the train can be matched. Delays update every minute on the <a href="/?place=rail-station:{escape(crs)}">live map</a>, where you can also save the station, follow a train and get alerts when it is late, cancelled or changes platform.</p>
{f'<h2>Stations near {escape(name)}</h2><ul class="links">{nearby_links}</ul>' if nearby_links else ''}"""
    canonical = origin + station["path"]
    return _shell(
        title=f"{name} live departures and delays | UK Activity Radar",
        description=f"Live departures from {name} ({crs}): the next trains, platforms and delays right now, and every train near {name} on a live map.",
        canonical=canonical, body=body,
        json_ld={"@context": "https://schema.org", "@type": "TrainStation", "name": f"{name} station",
                 "identifier": crs, "url": canonical,
                 "geo": {"@type": "GeoCoordinates", "latitude": round(station["lat"], 4), "longitude": round(station["lon"], 4)}},
    )


def stations_index(origin: str, stations: list[dict]) -> str:
    groups: dict[str, list[dict]] = {}
    for station in stations:
        groups.setdefault(station["name"][:1].upper(), []).append(station)
    letters = "".join(f'<a href="#{escape(letter)}">{escape(letter)}</a>' for letter in sorted(groups))
    sections = "".join(
        f'<h2 id="{escape(letter)}">{escape(letter)}</h2><ul class="columns">'
        + "".join(f'<li><a href="{escape(s["path"])}">{escape(s["name"])}</a></li>' for s in rows) + "</ul>"
        for letter, rows in sorted(groups.items())
    )
    body = f"""<p class="eyebrow">Live train times</p><h1>UK railway stations</h1>
<p class="lede">Live departures, platforms and delays for {len(stations):,} stations in Great Britain.</p>
<nav class="letters">{letters}</nav>{sections}"""
    return _shell(title="Live departures for every UK railway station | UK Activity Radar",
                  description="Live departures, platforms and delays for every railway station in Great Britain, with the trains on a live map.",
                  canonical=origin + "/stations", body=body)


def _operator_summary(row: dict) -> str:
    if row["status"] == "unknown":
        return f"Too few {row['operator']} trains are reporting right now to judge."
    text = f"{row['late']} of {row['running']} trains running are 5 or more minutes late"
    if row["late"]:
        text += f", by about {row['average_delay']} minutes on average"
    if row["cancelled"]:
        text += f"; {row['cancelled']} cancelled"
    return text + f" (at {_now_clock()})."


def operator_page(origin: str, row: dict) -> str:
    worst = "".join(
        "<tr><td><strong>{}</strong>{}</td><td class=\"status {}\">{}</td></tr>".format(
            escape(t["name"]),
            f"<small>to {escape(tidy_name(t['destination']))}</small>" if t.get("destination") else "",
            "bad" if t.get("cancelled") else "late",
            "Cancelled" if t.get("cancelled") else f"{t['delay_minutes']} min late",
        )
        for t in row.get("worst") or []
    )
    name = row["operator"]
    body = f"""<p class="eyebrow">Live service status</p>
<h1>{escape(name)} today</h1>
<p class="status-big {escape(row['status'])}">{escape(row['label'])}</p>
<p class="lede">{escape(_operator_summary(row))}</p>
<p><a class="button" href="/">See {escape(name)} trains on the live map</a></p>
{f'<h2>Most delayed right now</h2><table class="board"><tbody>{worst}</tbody></table>' if worst else ''}
<h2>About this page</h2><p>How {escape(name)} trains are running now, worked out from every {escape(name)} train reporting to Network Rail: the share running five or more minutes late, how late, and cancellations. It updates through the day. See <a href="/operators">all train operators</a>.</p>"""
    return _shell(title=f"{name} delays and service status today | UK Activity Radar",
                  description=f"Is {name} running on time? Live status for {name} trains right now: delays, cancellations and the most delayed services.",
                  canonical=origin + operator_path(name), body=body)


def operators_index(origin: str, rows: list[dict]) -> str:
    items = "".join(
        f'<li><a href="{escape(operator_path(r["operator"]))}">{escape(r["operator"])}</a> <span class="pill {escape(r["status"])}">{escape(r["label"])}</span></li>'
        for r in sorted(rows, key=lambda r: r["operator"].lower())
    )
    body = f"""<p class="eyebrow">Live service status</p><h1>UK train operators today</h1>
<p class="lede">How every passenger train operator is running right now (at {_now_clock()}).</p><ul class="links">{items}</ul>"""
    return _shell(title="UK train operators: live delays and status today | UK Activity Radar",
                  description="Live service status for every UK train operator: which are running well and which have delays or cancellations right now.",
                  canonical=origin + "/operators", body=body)
