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


def _json_for_script(value: dict) -> str:
    """Serialize JSON-LD without turning data into HTML entities.

    Script contents are raw text in HTML, so ``html.escape`` corrupts names
    such as "A & B" into the literal value "A &amp; B". Escaping the handful
    of characters that can end or confuse a script element keeps the JSON
    valid and preserves the original value after parsing.
    """
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def _breadcrumbs(origin: str, canonical: str, items: list[tuple[str, str]]) -> dict:
    return {
        "@type": "BreadcrumbList",
        "@id": f"{canonical}#breadcrumb",
        "itemListElement": [
            {"@type": "ListItem", "position": index, "name": name, "item": origin + path}
            for index, (name, path) in enumerate(items, start=1)
        ],
    }


_SEO_CSS: str | None = None


def _seo_css() -> str:
    """The page stylesheet, inlined: one fewer request before the page can draw.

    Lighthouse flagged the external stylesheet as render-blocking on every
    content page; at under 7 KB it costs less inline than a round trip.
    """
    global _SEO_CSS
    if _SEO_CSS is None:
        from pathlib import Path
        try:
            _SEO_CSS = (Path(__file__).resolve().parent / "static" / "seo.css").read_text(encoding="utf-8")
        except OSError:
            _SEO_CSS = ""
    return _SEO_CSS.replace("</", "<\\/")


def _shell(*, title: str, description: str, canonical: str, body: str, json_ld: dict | None = None) -> str:
    site = re.match(r"https?://[^/]+", canonical).group(0) if re.match(r"https?://[^/]+", canonical) else ""
    ld = f'<script type="application/ld+json">{_json_for_script(json_ld)}</script>' if json_ld else ""
    return f"""<!doctype html>
<html lang="en-GB"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)}</title><meta name="description" content="{escape(description, quote=True)}">
<link rel="canonical" href="{escape(canonical, quote=True)}"><meta name="robots" content="index,follow,max-image-preview:large">
<meta property="og:type" content="website"><meta property="og:site_name" content="UK Activity Radar">
<meta property="og:title" content="{escape(title, quote=True)}"><meta property="og:description" content="{escape(description, quote=True)}">
<meta property="og:url" content="{escape(canonical, quote=True)}"><meta property="og:image" content="{escape(site, quote=True)}/card.png">
<meta name="twitter:card" content="summary_large_image"><meta name="theme-color" content="#ffffff">
<link rel="icon" href="/favicon.ico" sizes="48x48"><link rel="icon" href="/static/brand/logo.svg" type="image/svg+xml"><link rel="apple-touch-icon" href="/apple-touch-icon.png"><style>{_seo_css()}</style>{ld}</head>
<body><header class="top"><a class="brand" href="/"><img src="/static/brand/logo.svg" width="32" height="32" alt="">UK Activity Radar</a><nav><a href="/">Live map</a><a href="/towns">Towns</a><a href="/stations">Stations</a><a href="/operators">Operators</a><a href="/railway-lines">Lines</a><a href="/buses">Buses</a><a href="/airports">Airports</a><a href="/discover-archive">Finds</a><a href="/military-aircraft-tracker">Aircraft</a><a href="/guides">Guides</a></nav></header>
<main>{body}</main>
<footer><a href="/">Live map</a> · <a href="/stations">All stations</a> · <a href="/operators">Train operators</a> · <a href="/military-aircraft-tracker">Aircraft trackers</a> · <a href="/guides">Tracking guides</a> · <a href="/about">About</a> · <a href="/data-licences">Data licences</a><p>Times and delays from Network Rail open data. Contains information of Network Rail Infrastructure Limited.</p></footer>
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


_USAGE: dict | None = None


def station_usage(crs: str) -> dict | None:
    """Official yearly passenger numbers for a station (ORR table 1415), bundled."""
    global _USAGE
    if _USAGE is None:
        import json
        from pathlib import Path
        try:
            _USAGE = json.loads((Path(__file__).resolve().parent / "assets" / "station_usage.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _USAGE = {"stations": {}}
    row = (_USAGE.get("stations") or {}).get(str(crs or "").upper())
    return {**row, "latest": _USAGE.get("latest"), "ranked": _USAGE.get("ranked")} if row else None


def _people(value: int) -> str:
    return f"{value / 1_000_000:.1f} million" if value >= 1_000_000 else f"{value:,}"


def _day_label(day: str) -> str:
    from datetime import date
    try:
        return date.fromisoformat(day).strftime("%A %-d %B")
    except ValueError:
        return day


def engineering_html(name: str, days: list[dict]) -> str:
    """Engineering works: the days this week when buses replace trains here."""
    if not days:
        return ""
    items = "".join(
        "<li><strong>{}</strong>: {} replacement bus{}{}{}</li>".format(
            escape(_day_label(d["day"])), d["buses"], "es" if d["buses"] != 1 else "",
            f", {escape(d['first'])} to {escape(d['last'])}" if d.get("first") and d.get("last") else "",
            f", serving {escape(', '.join(tidy_name(p) for p in d.get('between', [])[:4]))}" if d.get("between") else "")
        for d in days[:7])
    return (f'<section class="engineering"><h2>Engineering works at {escape(name)}</h2>'
            f'<p>Buses replace some trains on these days, from the published timetable:</p><ul>{items}</ul>'
            '<p class="source">Check with the train company before you travel; replacement buses usually leave from outside the station.</p></section>')


def station_facts_html(crs: str, name: str, facts: dict, paths: dict[str, str]) -> tuple[str, str]:
    """Quick facts as chips, then where the station's trains go direct today.

    Built only from data we hold: the timetable (direct destinations and
    trains a day), ORR station usage (how busy) and NaPTAN (bus stops close
    by). Staffing, step-free access and parking need the station
    knowledgebase feed and are added when it is connected.
    """
    direct = facts.get("direct") or {}
    destinations = direct.get("destinations") or []
    usage = station_usage(crs) or {}
    chips = []
    if destinations:
        chips.append(f"{len(destinations):,} direct destination{'s' if len(destinations) != 1 else ''}")
    if direct.get("trains"):
        chips.append(f"{direct['trains']:,} departure{'s' if direct['trains'] != 1 else ''} today")
    if usage.get("rank"):
        chips.append(f"{usage['rank']:,}{_ordinal(usage['rank'])} busiest in Britain")
    if facts.get("bus_stops"):
        chips.append(f"{facts['bus_stops']} bus stop{'s' if facts['bus_stops'] != 1 else ''} within 400 m")
    works = engineering_html(name, facts.get("engineering") or [])
    if not chips:
        return works, ""
    chip_html = "".join(f"<li>{escape(chip)}</li>" for chip in chips)
    rows = "".join(
        "<li>{}<small>{} train{} today</small></li>".format(
            f'<a href="{escape(paths[d["crs"]])}">{escape(tidy_name(d["name"]))}</a>' if d["crs"] in paths else escape(tidy_name(d["name"])),
            d["trains"], "s" if d["trains"] != 1 else "")
        for d in destinations[:40])
    more = f"<p>And {len(destinations) - 40} more stations.</p>" if len(destinations) > 40 else ""
    section = (f'<h2>Direct trains from {escape(name)}</h2><p>Stations you can reach from {escape(name)} today without changing, '
               f'with how many trains go there.</p><ul class="links direct-list">{rows}</ul>{more}') if rows else ""
    return f'<ul class="station-facts" aria-label="{escape(name)} at a glance">{chip_html}</ul>{works}', section


def station_usage_html(crs: str, name: str) -> str:
    row = station_usage(crs)
    if not row:
        return ""
    years = list(row["usage"].items())
    latest_year, latest = years[-1]
    lines = [f"About <strong>{_people(latest)}</strong> passenger entries and exits in {escape(latest_year)}"
             + (f", the {row['rank']:,}{_ordinal(row['rank'])} busiest of {row['ranked']:,} stations in Great Britain." if row.get("rank") else ".")]
    if len(years) > 1 and years[-2][1]:
        change = (latest - years[-2][1]) * 100 / years[-2][1]
        lines.append(f"{'Up' if change >= 0 else 'Down'} {abs(change):.0f}% on {escape(years[-2][0])}.")
    pre = row["usage"].get("2019-20")
    if pre:
        lines.append(f"{round(latest * 100 / pre)}% of its level before the pandemic (2019-20).")
    if row.get("interchanges"):
        lines.append(f"About {_people(row['interchanges'])} people change trains here a year.")
    top = max(value for _, value in years) or 1
    bars = "".join(
        f'<li><span>{escape(year)}</span><i style="width:{max(2, round(value * 100 / top))}%"></i><b>{_people(value)}</b></li>'
        for year, value in years)
    where = ", ".join(x for x in (row.get("district"), row.get("region")) if x)
    return f"""<h2>How busy is {escape(name)}?</h2>
<p>{" ".join(lines)}{f" In {escape(where)}." if where else ""}</p>
<ul class="usage-bars" aria-label="Passenger entries and exits by year">{bars}</ul>
<p class="source">Source: Office of Rail and Road, Estimates of station usage (table 1415), financial years April to March. Open Government Licence v3.0.</p>"""


def _ordinal(n: int) -> str:
    return "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


def station_page(
    origin: str,
    station: dict,
    board: dict | None,
    nearby: list[dict],
    operator_rows: list[dict],
    trainline_affiliate_url: str = "",
    facts: dict | None = None,
    paths: dict[str, str] | None = None,
) -> str:
    name, crs = station["name"], station["crs"]
    facts_chips, direct_section = station_facts_html(crs, name, facts or {}, paths or {})
    departures = [row for row in (board or {}).get("departures") or [] if int(row.get("minutes") or 0) >= -2][:12]
    rows, late, cancelled, known = [], 0, 0, 0
    operators: list[str] = []
    destinations: list[str] = []
    platforms: set[str] = set()
    for row in departures:
        status, tone = _status(row)
        known += tone in {"good", "late", "bad"}
        late += tone in {"late", "bad"} and status != "Cancelled"
        cancelled += status == "Cancelled"
        operator = row.get("operator_name") or ""
        if operator and operator not in operators:
            operators.append(operator)
        platform = row.get("platform") or (f"usually {row['usual_platform']['platform']}" if row.get("usual_platform") else "")
        destination = tidy_name(row.get("destination") or "")
        if destination and destination not in destinations:
            destinations.append(destination)
        if platform:
            platforms.add(str(platform))
        rows.append(
            f"<tr><td class=time>{escape(_clock(row.get('scheduled')))}</td>"
            f"<td><strong>{escape(tidy_name(row.get('destination') or ''))}</strong><small>{escape(operator)}</small></td>"
            f"<td>{escape(str(platform))}</td><td class=\"status {tone}\">{escape(status)}</td></tr>"
        )
    if board is None:
        # The light version, served while the server is busy: no board, and
        # no claim that nothing is due.
        summary = f"Live departures, platforms and delays for {escape(name)} are on the live map."
    elif not departures:
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
    on_time = max(0, known - late - cancelled)
    on_time_percent = round(on_time * 100 / known) if known else None
    snapshot = ""
    if departures:
        segments = ""
        if known:
            segment_rows = (("good", on_time, "On time or within 5 minutes"), ("late", late, "More than 5 minutes late"), ("bad", cancelled, "Cancelled"))
            segments = "".join(
                f'<i class="{tone}" style="width:{count * 100 / known:.2f}%" title="{escape(label)}: {count}"></i>'
                for tone, count, label in segment_rows if count
            )
        status_summary = f"{on_time} on time or within 5 minutes, {late} late and {cancelled} cancelled"
        direct = "".join(f"<span>{escape(value)}</span>" for value in destinations[:6])
        snapshot = f'''<section class="station-snapshot" aria-labelledby="snapshot-title"><h2 id="snapshot-title">Live station snapshot</h2>
<div class="stat-grid"><div><strong>{len(departures)}</strong><span>departures loaded</span></div><div><strong>{len(destinations)}</strong><span>destinations</span></div><div><strong>{str(on_time_percent) + '%' if on_time_percent is not None else '—'}</strong><span>on time within 5 min</span></div><div><strong>{len(platforms)}</strong><span>platforms shown</span></div></div>
{f'<div class="status-chart" role="img" aria-label="{escape(status_summary)}">{segments}</div><p class="chart-summary">{escape(status_summary)}.</p>' if known else ''}
{f'<p class="direct-now"><b>Direct destinations in this live snapshot</b>{direct}</p>' if direct else ''}</section>'''
    affiliate_url = str(trainline_affiliate_url or "").strip()
    affiliate = ""
    if affiliate_url.startswith("https://"):
        affiliate = f'<a class="button secondary" href="{escape(affiliate_url, quote=True)}" target="_blank" rel="sponsored nofollow noopener">Check tickets on Trainline</a><small class="affiliate-note">Affiliate link · UK Activity Radar may earn a commission if you book.</small>'
    body = f"""<p class="eyebrow">Live train times · {escape(crs)}</p>
<h1>{escape(name)} departures</h1>
<p class="lede">{summary}</p>
<p class="station-actions"><a class="button" href="/?place=rail-station:{escape(crs)}">See them moving on the live map</a>{affiliate}</p>
{facts_chips}
{snapshot}
{table}
{f'<h2>Operators at {escape(name)}</h2><ul class="links">{operator_links}</ul>' if operator_links else ''}
{direct_section}
{station_usage_html(crs, name)}
<h2>About this page</h2><p>Departures from {escape(name)} ({escape(crs)}) for the next few hours, from the published timetable with live running from Network Rail where the train can be matched. Delays update every minute on the <a href="/?place=rail-station:{escape(crs)}">live map</a>, where you can also save the station, follow a train and get alerts when it is late, cancelled or changes platform. Read <a href="/guides/how-live-train-positions-work">how UK live train positions are estimated between railway reports</a>.</p>
{f'<h2>Stations near {escape(name)}</h2><ul class="links">{nearby_links}</ul>' if nearby_links else ''}"""
    canonical = origin + station["path"]
    return _shell(
        title=f"{name} live departures and delays | UK Activity Radar",
        description=f"Live departures from {name} ({crs}): the next trains, platforms and delays right now, and every train near {name} on a live map.",
        canonical=canonical, body=body,
        json_ld={"@context": "https://schema.org", "@graph": [
            {"@type": "TrainStation", "@id": f"{canonical}#station", "name": f"{name} station",
             "identifier": crs, "url": canonical,
             "geo": {"@type": "GeoCoordinates", "latitude": round(station["lat"], 4), "longitude": round(station["lon"], 4)}},
            _breadcrumbs(origin, canonical, [("Home", "/"), ("Stations", "/stations"), (name, station["path"])]),
        ]},
    )


def station_letter_path(letter: str) -> str:
    return f"/stations/{str(letter).strip().lower()}"


def stations_index(origin: str, stations: list[dict]) -> str:
    groups: dict[str, list[dict]] = {}
    for station in stations:
        letter = station["name"][:1].upper()
        if re.fullmatch(r"[A-Z]", letter):
            groups.setdefault(letter, []).append(station)
    letters = "".join(
        f'<a href="{station_letter_path(letter)}" aria-label="Stations beginning with {escape(letter)}">{escape(letter)}</a>'
        for letter in sorted(groups)
    )
    cards = "".join(
        f'<li><a href="{station_letter_path(letter)}"><strong>{escape(letter)}</strong><span>{len(rows):,} stations</span></a></li>'
        for letter, rows in sorted(groups.items())
    )
    body = f"""<p class="eyebrow">Live train times</p><h1>UK railway stations</h1>
<p class="lede">Live departures, platforms and delays for {len(stations):,} stations in Great Britain.</p>
<p>Choose a letter to find a station. Each station page shows its live departure board, current delays, operators and nearby stations.</p>
<nav class="letters" aria-label="Stations A to Z">{letters}</nav><ul class="hub-grid">{cards}</ul>"""
    canonical = origin + "/stations"
    return _shell(title="Live departures for every UK railway station | UK Activity Radar",
                  description="Live departures, platforms and delays for every railway station in Great Britain, with the trains on a live map.",
                  canonical=canonical, body=body,
                  json_ld={"@context": "https://schema.org", "@type": "CollectionPage", "name": "UK railway stations",
                           "url": canonical, "description": f"Live departure pages for {len(stations):,} railway stations in Great Britain."})


def stations_letter_page(origin: str, letter: str, stations: list[dict]) -> str:
    wanted = str(letter).upper()
    rows = [station for station in stations if station["name"][:1].upper() == wanted]
    links = "".join(f'<li><a href="{escape(row["path"])}">{escape(row["name"])}</a> <span>{escape(row["crs"])}</span></li>' for row in rows)
    all_letters = sorted({station["name"][:1].upper() for station in stations
                          if re.fullmatch(r"[A-Z]", station["name"][:1].upper())})
    letters = "".join(f'<a href="{station_letter_path(item)}">{escape(item)}</a>' for item in all_letters)
    canonical = origin + station_letter_path(wanted)
    body = f"""<p class="eyebrow">Station directory</p><h1>UK railway stations beginning with {escape(wanted)}</h1>
<p class="lede">Live departures, platforms and delays for {len(rows):,} stations beginning with {escape(wanted)}.</p>
<nav class="letters" aria-label="Stations A to Z">{letters}</nav><ul class="station-directory">{links}</ul>
<p><a href="/stations">Return to all UK railway stations</a>.</p>"""
    return _shell(title=f"UK railway stations beginning with {wanted} | Live departures",
                  description=f"Find live departures, platforms and delays for UK railway stations beginning with {wanted}.",
                  canonical=canonical, body=body,
                  json_ld={"@context": "https://schema.org", "@graph": [
                      {"@type": "CollectionPage", "name": f"UK railway stations beginning with {wanted}", "url": canonical},
                      _breadcrumbs(origin, canonical, [("Home", "/"), ("Stations", "/stations"), (wanted, station_letter_path(wanted))]),
                  ]})


def _operator_summary(row: dict) -> str:
    if row["status"] == "unknown":
        return f"Too few {row['operator']} trains are reporting right now to judge."
    text = f"{row['late']} of {row['running']} trains running are 5 or more minutes late"
    if row["late"]:
        text += f", by about {row['average_delay']} minutes on average"
    if row["cancelled"]:
        text += f"; {row['cancelled']} cancelled"
    return text + f" (at {_now_clock()})."


# Our operator names to ORR's. Thameslink, Southern, Great Northern and
# Gatwick Express are reported together as Govia Thameslink Railway.
_ORR_OPERATOR = {"LNER": "London North Eastern Railway", "Northern": "Northern Trains",
                 "Transport for Wales": "TfW Rail", "Lumo": "Lumo (East Coast)",
                 "Thameslink": "Greater Thameslink Railway", "Southern": "Greater Thameslink Railway",
                 "Great Northern": "Greater Thameslink Railway", "Gatwick Express": "Greater Thameslink Railway"}
_PERFORMANCE: dict | None = None


def operator_performance(name: str) -> tuple[str, list[dict], list[dict]] | None:
    """(ORR name, the operator's last 13 periods, Great Britain's) from ORR tables 3138 and 3124."""
    global _PERFORMANCE
    if _PERFORMANCE is None:
        import json
        from pathlib import Path
        try:
            _PERFORMANCE = json.loads((Path(__file__).resolve().parent / "assets" / "operator_performance.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _PERFORMANCE = {"operators": {}}
    rows = _PERFORMANCE.get("operators") or {}
    orr = _ORR_OPERATOR.get(name, name)
    if orr not in rows:
        return None
    return orr, rows[orr], rows.get("Great Britain") or []


def operator_performance_html(name: str) -> str:
    found = operator_performance(name)
    if not found:
        return ""
    orr, periods, britain = found
    last = periods[-1]
    gb = next((p for p in reversed(britain) if p["period"] == last["period"]), None)
    facts = [f"<strong>{last['on_time']:.0f}%</strong> of station stops on time (to the minute)"]
    if last.get("within_3") is not None:
        facts.append(f"{last['within_3']:.0f}% within 3 minutes")
    if last.get("cancelled") is not None:
        facts.append(f"{last['cancelled']:.1f}% of trains cancelled")
    compare = f" Great Britain overall: {gb['on_time']:.0f}% on time, {gb['cancelled']:.1f}% cancelled." if gb and gb.get("cancelled") is not None else ""
    year = f" Over the last year: {last['on_time_year']:.0f}% on time." if last.get("on_time_year") else ""
    shared = f" Figures are for {escape(orr)}, which includes {escape(name)}." if orr != name else ""
    bars = "".join(
        f'<li><span>{escape(p["period"])}</span><i style="width:{max(2, round(p["on_time"]))}%"></i><b>{p["on_time"]:.0f}%</b></li>'
        for p in periods)
    return f"""<h2>Official punctuality</h2>
<p>In railway period {escape(last['period'])}: {", ".join(facts)}.{compare}{year}{shared}</p>
<ul class="usage-bars" aria-label="Share of station stops on time, by four-week railway period">{bars}</ul>
<p class="source">Source: Office of Rail and Road, tables 3138 and 3124. "On time" means arriving within 59 seconds of the timetable at each recorded station stop. Railway periods are four weeks long. Open Government Licence v3.0.</p>"""


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
{operator_performance_html(name)}
<h2>About this page</h2><p>How {escape(name)} trains are running now, worked out from every {escape(name)} train reporting to Network Rail: the share running five or more minutes late, how late, and cancellations. It updates through the day. See <a href="/operators">all train operators</a> or learn <a href="/guides/how-live-train-positions-work">how live train positions work between railway reports</a>.</p>"""
    canonical = origin + operator_path(name)
    return _shell(title=f"{name} delays and service status today | UK Activity Radar",
                  description=f"Is {name} running on time? Live status for {name} trains right now: delays, cancellations and the most delayed services.",
                  canonical=canonical, body=body,
                  json_ld={"@context": "https://schema.org", "@graph": [
                      {"@type": "WebPage", "name": f"{name} delays and service status today", "url": canonical,
                       "description": f"Live status for {name} trains, including delays and cancellations."},
                      _breadcrumbs(origin, canonical, [("Home", "/"), ("Train operators", "/operators"),
                                                       (name, operator_path(name))]),
                  ]})


def operators_index(origin: str, rows: list[dict]) -> str:
    items = "".join(
        f'<li><a href="{escape(operator_path(r["operator"]))}">{escape(r["operator"])}</a> <span class="pill {escape(r["status"])}">{escape(r["label"])}</span></li>'
        for r in sorted(rows, key=lambda r: r["operator"].lower())
    )
    body = f"""<p class="eyebrow">Live service status</p><h1>UK train operators today</h1>
<p class="lede">How every passenger train operator is running right now (at {_now_clock()}). See the <a href="/operators/league-table">official punctuality league table</a>.</p><ul class="links">{items}</ul>"""
    canonical = origin + "/operators"
    return _shell(title="UK train operators: live delays and status today | UK Activity Radar",
                  description="Live service status for every UK train operator: which are running well and which have delays or cancellations right now.",
                  canonical=canonical, body=body,
                  json_ld={"@context": "https://schema.org", "@type": "CollectionPage",
                           "name": "UK train operators: live service status", "url": canonical})


_GUIDES = {
    "how-live-train-positions-work": {
        "title": "How live UK train positions work | UK Activity Radar",
        "description": "How live train maps estimate a train between railway reports, why markers can pause or jump, and how to judge position freshness.",
        "eyebrow": "Train tracking explained",
        "heading": "How live UK train positions work",
        "lede": "A railway map can look like GPS, but most UK passenger-train positions are reconstructed from railway movement reports and the timetable. Understanding that difference makes the map much more useful.",
        "sections": [
            ("A report, not a continuous GPS dot", [
                "The railway records events when a train reaches or passes recognised locations. Those events can include a station arrival or departure, a timing point or a signalling location. A feed publishes the latest report with the train identity and its planned journey.",
                "That means the newest confirmed fact is usually that the train passed a known railway point at a particular time. It is not normally a fresh latitude and longitude every second.",
            ]),
            ("What happens between reports", [
                "UK Activity Radar matches the service to its published calling and timing points, then follows mapped railway geometry between them. The marker can move smoothly along that route while waiting for the next confirmed railway report. This is an estimate of progress, not a claim that the train transmitted that exact position.",
                "When a new report arrives, the estimate is corrected against the confirmed location. A good live map makes that correction gently where possible and labels the age of the underlying report.",
            ]),
            ("Why a train may pause, jump or disappear", [
                "Reports can arrive late, out of order or with a gap of several minutes. A service may be activated in the timetable before its first movement, change identity, be diverted, divide into two portions or finish without a final report. The route may also be unavailable for a service that cannot be matched confidently.",
                "When the evidence is weak, the honest behaviour is to show a stale or estimated state instead of inventing precision. A sudden correction often means a delayed railway report has arrived, not that the physical train jumped.",
            ]),
            ("How to read the map", [
                "Check the last railway report time, the next reporting point and whether the position is labelled confirmed or estimated. Open the route to see the published stops and the railway path used by the estimate. Station departure boards are useful for the latest expected time, platform and cancellation information.",
                "For a journey decision, always allow for feed delay and confirm important changes with the train operator or station information. The radar is an independent view of open data and is not a safety-critical railway system.",
            ]),
        ],
    },
    "how-ads-b-aircraft-tracking-works": {
        "title": "How ADS-B aircraft tracking works in the UK | UK Activity Radar",
        "description": "Learn how ADS-B aircraft trackers receive positions, why coverage varies, and why military or rescue aircraft may be missing or delayed.",
        "eyebrow": "Aircraft tracking explained",
        "heading": "How ADS-B aircraft tracking works in the UK",
        "lede": "Many aircraft broadcast useful flight data by radio. A live tracker turns those broadcasts into map positions, but coverage and identification are never complete.",
        "sections": [
            ("What ADS-B broadcasts", [
                "An equipped aircraft can broadcast its identity, position, altitude, speed and direction. Ground receivers hear those radio messages and a data network combines them into a live feed. The map can update whenever a newer message for that aircraft arrives.",
                "The callsign is an operational label and may not be the registration or public flight number. Aircraft type information is matched from published identifiers where available.",
            ]),
            ("Why coverage changes", [
                "Reception depends on line of sight between the aircraft and a receiver. Low-flying helicopters can drop behind terrain or buildings, while high-altitude aircraft are usually heard over a much wider area. Receiver outages, network delay and incomplete aircraft metadata can also create gaps.",
                "Not every aircraft transmits a usable public position. Some messages are filtered by a provider, some aircraft use other surveillance methods, and military activity may be absent, anonymous or deliberately limited. No marker should be taken as proof that the sky is otherwise empty.",
            ]),
            ("Smooth movement versus fresh evidence", [
                "A renderer can interpolate between two received positions so an icon moves smoothly rather than stepping across the map. That visual movement does not create new flight data. The last-update age still tells you how fresh the real observation is.",
                "When a new report differs from the estimate, the marker must converge on the received position. Good tracking balances smooth animation with a clear indication of stale or uncertain data.",
            ]),
            ("Aircraft icons and identification", [
                "UK Activity Radar uses the available type code and aircraft metadata to choose different symbols for helicopters, airliners, propeller aircraft, fast jets and large transports. If the metadata is missing or ambiguous, a generic symbol is safer than guessing a specific aircraft.",
                "Use the detail panel to check the callsign, registration, type, altitude, speed, heading and report age. For emergency or safety decisions, rely on official sources rather than a public tracking website.",
            ]),
        ],
    },
}


def guide_slugs() -> tuple[str, ...]:
    return tuple(_GUIDES)


def guides_index(origin: str) -> str:
    items = "".join(
        f'<li><a href="/guides/{escape(slug)}"><strong>{escape(guide["heading"])}</strong>'
        f'<span>{escape(guide["description"])}</span></a></li>'
        for slug, guide in _GUIDES.items()
    )
    canonical = origin + "/guides"
    trackers = "".join(
        f'<li><a href="{path}"><strong>{title}</strong><span>{description}</span></a></li>'
        for path, title, description in (
            ("/military-aircraft-tracker", "UK military aircraft tracker", "See military and special aircraft broadcasting a public position."),
            ("/helicopter-tracker", "UK helicopter tracker", "Find helicopters visible in the live aircraft feed."),
            ("/police-helicopter-tracker", "Police helicopter tracker", "Understand circling flights and public tracking limits."),
            ("/air-ambulance-tracker", "Air ambulance tracker", "Follow identified emergency medical helicopters."),
            ("/what-plane-is-above-me", "What plane is above me?", "Use your location to identify nearby aircraft."),
        )
    )
    body = f"""<p class="eyebrow">How the radar works</p><h1>Live tracking guides</h1>
<p class="lede">Plain-English guides to the data behind live trains and aircraft, including what is confirmed, what is estimated and why coverage can change.</p>
<ul class="guide-list">{items}</ul><h2>Live aircraft trackers</h2><ul class="guide-list">{trackers}</ul>"""
    return _shell(title="Live train and aircraft tracking guides | UK Activity Radar",
                  description="Understand how live UK train positions and ADS-B aircraft tracking work, including estimates, delays and coverage gaps.",
                  canonical=canonical, body=body,
                  json_ld={"@context": "https://schema.org", "@type": "CollectionPage",
                           "name": "Live tracking guides", "url": canonical})


def guide_page(origin: str, slug: str) -> str:
    guide = _GUIDES[slug]
    canonical = f"{origin}/guides/{slug}"
    sections = "".join(
        f'<section><h2>{escape(heading)}</h2>{"".join(f"<p>{escape(paragraph)}</p>" for paragraph in paragraphs)}</section>'
        for heading, paragraphs in guide["sections"]
    )
    related = (
        '<aside class="related"><h2>Try the live aircraft tools</h2><p><a href="/what-plane-is-above-me">Identify a plane above you</a>, '
        '<a href="/helicopter-tracker">track UK helicopters</a> or open the '
        '<a href="/military-aircraft-tracker">military aircraft tracker</a>.</p></aside>'
        if slug == "how-ads-b-aircraft-tracking-works"
        else '<aside class="related"><h2>See the data on the map</h2><p>Browse <a href="/stations">live station departures</a> or open the <a href="/">live UK train map</a>.</p></aside>'
    )
    body = f"""<article class="guide"><p class="eyebrow">{escape(guide['eyebrow'])}</p>
<h1>{escape(guide['heading'])}</h1><p class="lede">{escape(guide['lede'])}</p>{sections}
{related}<p class="guide-cta"><a class="button" href="/">Open the live UK map</a></p></article>"""
    return _shell(title=guide["title"], description=guide["description"], canonical=canonical, body=body,
                  json_ld={"@context": "https://schema.org", "@graph": [
                      {"@type": "Article", "headline": guide["heading"], "description": guide["description"],
                       "mainEntityOfPage": canonical, "author": {"@type": "Organization", "name": "UK Activity Radar"},
                       "publisher": {"@type": "Organization", "name": "UK Activity Radar", "url": origin}},
                      _breadcrumbs(origin, canonical, [("Home", "/"), ("Guides", "/guides"),
                                                       (guide["heading"], f"/guides/{slug}")]),
                  ]})


def operators_league(origin: str) -> str:
    """Every operator ranked by the latest official punctuality (ORR)."""
    operator_performance("Great Britain")   # loads the figures
    rows = []
    for name, periods in ((_PERFORMANCE or {}).get("operators") or {}).items():
        if name in {"Great Britain", "England and Wales", "Scotland"} or not periods:
            continue
        last = periods[-1]
        if last.get("on_time") is None:
            continue
        rows.append((name, last))
    rows.sort(key=lambda row: -row[1]["on_time"])
    gb = ((_PERFORMANCE or {}).get("operators") or {}).get("Great Britain", [{}])[-1]
    period = rows[0][1]["period"] if rows else ""
    body_rows = "".join(
        "<tr><td>{}</td><td><strong>{}</strong></td><td>{:.0f}%</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
            i, escape(name), last["on_time"],
            f"{last['within_3']:.0f}%" if last.get("within_3") is not None else "—",
            f"{last['cancelled']:.1f}%" if last.get("cancelled") is not None else "—",
            f"{last['on_time_year']:.0f}%" if last.get("on_time_year") is not None else "—")
        for i, (name, last) in enumerate(rows, 1))
    best, worst = (rows[0][0], rows[-1][0]) if rows else ("", "")
    body = f"""<p class="eyebrow">Official figures · railway period {escape(period)}</p>
<h1>UK train operators ranked by punctuality</h1>
<p class="lede">Most punctual: <strong>{escape(best)}</strong>. Least punctual: <strong>{escape(worst)}</strong>. Great Britain overall: {gb.get('on_time', 0):.0f}% of station stops on time and {gb.get('cancelled', 0):.1f}% of trains cancelled.</p>
<table class="board league"><thead><tr><th>#</th><th>Operator</th><th>On time</th><th>Within 3 min</th><th>Cancelled</th><th>Last year</th></tr></thead><tbody>{body_rows}</tbody></table>
<p class="source">Source: Office of Rail and Road, tables 3138 and 3124, latest four-week railway period. "On time" means within 59 seconds of the timetable at each recorded station stop; "Last year" is the moving annual average. Open Government Licence v3.0. See <a href="/operators">how each operator is running right now</a>.</p>"""
    canonical = origin + "/operators/league-table"
    return _shell(title="Most and least punctual UK train operators: league table | UK Activity Radar",
                  description=f"Every UK train operator ranked by official punctuality and cancellations for railway period {period}: most punctual {best}, least punctual {worst}.",
                  canonical=canonical, body=body,
                  json_ld={"@context": "https://schema.org", "@graph": [
                      {"@type": "WebPage", "name": "UK train operators ranked by punctuality", "url": canonical},
                      _breadcrumbs(origin, canonical, [("Home", "/"), ("Train operators", "/operators"), ("League table", "/operators/league-table")])]})
