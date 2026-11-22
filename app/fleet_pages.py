"""Bus fleet pages: every bus an operator ran recently, and each bus's history.

Built only from what the live map has seen: every five minutes each bus's
route is recorded, so a bus page says which routes it worked on which days,
and whether it is out on the road now. Nothing is filled in from elsewhere.
"""
from __future__ import annotations

import re
from datetime import date
from html import escape

from .seo_pages import _breadcrumbs, _shell

_SAFE = re.compile(r"[^a-z0-9]+")


def vehicle_slug(vehicle: str) -> str:
    return _SAFE.sub("-", str(vehicle or "").lower()).strip("-")


def fleet_path(code: str) -> str:
    return f"/fleet/{str(code or '').lower()}"


def vehicle_path(code: str, vehicle: str) -> str:
    return f"{fleet_path(code)}/{vehicle_slug(vehicle)}"


def operator_label(code: str, learned: dict[str, str] | None = None) -> str:
    """The operator's name: learned from matched timetables, else a known group, else its code."""
    code = str(code or "").upper()
    if learned and learned.get(code):
        return learned[code]
    from .collectors.bods import COACH_OPERATORS
    from .media_reference import BUS_BRANDS, bus_group
    if code in COACH_OPERATORS:
        return COACH_OPERATORS[code]["name"]
    if code in BUS_BRANDS:
        return BUS_BRANDS[code]
    group = bus_group(code)
    return f"{group} ({code})" if group else code


def _when(day: str) -> str:
    try:
        value = date.fromisoformat(day)
    except ValueError:
        return day
    gap = (date.today() - value).days
    return "today" if gap == 0 else "yesterday" if gap == 1 else value.strftime("%a %-d %b")


def fleet_index(origin: str, operators: list[dict], learned: dict[str, str]) -> str:
    items = "".join(
        f'<li><a href="{escape(fleet_path(o["operator"]))}">{escape(operator_label(o["operator"], learned))}</a>'
        f'<small>{o["vehicles"]:,} buses · {o["routes"]:,} routes this week</small></li>'
        for o in operators)
    body = f"""<p class="eyebrow">Bus fleets</p><h1>Bus fleets and vehicle history</h1>
<p class="lede">Every bus the live map has seen this week, by operator, and the routes each one has worked.</p>
<ul class="links">{items or '<li>Fleet history is being collected; check back shortly.</li>'}</ul>
<p class="source">Recorded from live Bus Open Data Service positions every five minutes. A bus only appears once it has reported a route.</p>"""
    canonical = origin + "/fleet"
    return _shell(title="UK bus fleets and vehicle history | UK Activity Radar",
                  description="Which buses each UK operator has out this week and the routes each bus has worked, from live tracking.",
                  canonical=canonical, body=body,
                  json_ld={"@context": "https://schema.org", "@type": "CollectionPage", "name": "Bus fleets", "url": canonical})


def fleet_page(origin: str, code: str, label: str, vehicles: list[dict], live: set[str]) -> str:
    rows = "".join(
        "<tr><td><a href=\"{}\"><strong>{}</strong></a>{}</td><td>{}</td><td>{}</td></tr>".format(
            escape(vehicle_path(code, v["vehicle"])), escape(v["vehicle"]),
            ' <span class="pill good">on the road</span>' if v["vehicle"] in live else "",
            escape(", ".join(v["routes"][:8]) + (" …" if len(v["routes"]) > 8 else "")), escape(_when(v["last_day"])))
        for v in vehicles)
    out_now = sum(1 for v in vehicles if v["vehicle"] in live)
    body = f"""<p class="eyebrow">Bus fleet · {escape(code)}</p><h1>{escape(label)} fleet</h1>
<p class="lede">{len(vehicles):,} buses seen in the last two weeks; {out_now:,} on the road now.</p>
<table class="board"><thead><tr><th>Bus</th><th>Routes worked</th><th>Last seen</th></tr></thead><tbody>{rows}</tbody></table>
<p class="source">From live positions, recorded every five minutes. See <a href="/fleet">all bus fleets</a>.</p>"""
    canonical = origin + fleet_path(code)
    return _shell(title=f"{label} bus fleet and vehicle history | UK Activity Radar",
                  description=f"Every {label} bus seen in the last two weeks, the routes each one worked and which are on the road now.",
                  canonical=canonical, body=body,
                  json_ld={"@context": "https://schema.org", "@graph": [
                      {"@type": "CollectionPage", "name": f"{label} fleet", "url": canonical},
                      _breadcrumbs(origin, canonical, [("Home", "/"), ("Fleets", "/fleet"), (label, fleet_path(code))])]})


def vehicle_page(origin: str, code: str, label: str, vehicle: str, days: list[dict], live) -> str:
    now = ""
    if live is not None:
        where = f" to {escape(live.destination)}" if getattr(live, "destination", None) else ""
        now = (f'<p class="station-actions"><a class="button" href="/?open={escape(live.id)}">On route {escape(live.route or "")}{where} now: '
               f'see it on the live map</a></p>')
    rows = "".join(
        "<tr><td class=time>{}</td><td>{}</td></tr>".format(
            escape(_when(d["day"])),
            "<br>".join(
                "<strong>{}</strong>{}<small> {}</small>".format(
                    escape(w["route"]), f" to {escape(w['destination'])}" if w.get("destination") else "",
                    escape(f"{w['first']}–{w['last']}" if w.get("first") and w.get("last") and w["first"] != w["last"] else (w.get("first") or "")))
                for w in d["work"]))
        for d in days)
    routes = sorted({w["route"] for d in days for w in d["work"]})
    body = f"""<p class="eyebrow">{escape(label)} · bus {escape(vehicle)}</p><h1>Bus {escape(vehicle)}</h1>
<p class="lede">{escape(label)} bus {escape(vehicle)} has been seen on {len(days)} day{'s' if len(days) != 1 else ''}{f", working route{'s' if len(routes) != 1 else ''} {escape(', '.join(routes[:10]))}" if routes else ''}.</p>
{now}
{f'<table class="board"><thead><tr><th>Day</th><th>Routes worked (times seen)</th></tr></thead><tbody>{rows}</tbody></table>' if rows else '<p>No history recorded for this bus yet.</p>'}
<p class="source">Recorded from live positions every five minutes, kept for 90 days. See the <a href="{escape(fleet_path(code))}">{escape(label)} fleet</a>.</p>"""
    canonical = origin + vehicle_path(code, vehicle)
    return _shell(title=f"{label} bus {vehicle}: routes worked and where it is now | UK Activity Radar",
                  description=f"Which routes {label} bus {vehicle} has worked day by day, and where it is now.",
                  canonical=canonical, body=body,
                  json_ld={"@context": "https://schema.org", "@graph": [
                      {"@type": "WebPage", "name": f"{label} bus {vehicle}", "url": canonical},
                      _breadcrumbs(origin, canonical, [("Home", "/"), ("Fleets", "/fleet"), (label, fleet_path(code)),
                                                       (f"Bus {vehicle}", vehicle_path(code, vehicle))])]})
