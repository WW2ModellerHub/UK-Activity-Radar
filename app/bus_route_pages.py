"""Bus route pages, from the official BODS timetables plus live buses.

/buses (operators) → /bus/{operator} (their routes) → /bus/{operator}/{route}:
stops in order each way, how many buses run today, first and last, how often
around midday and how long the full run takes, and the buses on the route now.
"""
from __future__ import annotations

from html import escape
from math import cos, radians

from .seo_pages import _breadcrumbs, _now_clock, _shell, slug


def tidy_name(value) -> str:
    """Bus stop and destination names as written, capitals tidied; "Bus Station" kept."""
    text = " ".join(str(value or "").split())
    if text.isupper():
        text = " ".join(w if len(w) <= 3 and w.isalpha() and w in {"NHS", "UK"} else w.capitalize() for w in text.split())
    return text


def operator_path(operator: str) -> str:
    return f"/bus/{slug(operator)}"


def route_path(operator: str, route: str) -> str:
    return f"/bus/{slug(operator)}/{slug(route)}"


def _near(lat: float, lon: float, stops: list[dict]) -> tuple[float, str]:
    best = min(stops, key=lambda s: (s["lat"] - lat) ** 2 + ((s["lon"] - lon) * cos(radians(lat))) ** 2)
    d = ((best["lat"] - lat) ** 2 + ((best["lon"] - lon) * cos(radians(lat))) ** 2) ** 0.5 * 111
    return d, best["name"]


def live_on_route(profile: dict, buses: list) -> list[dict]:
    """Live buses showing this route number and running along its stops (within 1 km)."""
    stops = [s for d in profile["directions"] for s in d["stops"] if s.get("lat") is not None]
    if not stops:
        return []
    out = []
    for bus in buses:
        if str(getattr(bus, "route", "") or "").strip().upper() != profile["route"].upper():
            continue
        distance, near = _near(bus.lat, bus.lon, stops)
        if distance <= 1.0:
            out.append({"bus": bus, "near": near})
    return out


def route_page(origin: str, profile: dict, live: list[dict]) -> str:
    operator, route = profile["operator"], profile["route"]
    ends = [tidy_name(d["towards"]) for d in profile["directions"]]
    title_route = " – ".join(dict.fromkeys(ends)) if ends else ""
    sections = []
    for d in profile["directions"]:
        facts = []
        if d["today"]:
            facts.append(f"{d['today']} buses today")
        if d["first"] and d["last"]:
            facts.append(f"first {d['first']}, last {d['last']}")
        if d["every"]:
            facts.append(f"about every {d['every']} minutes around midday")
        if d["journey_minutes"]:
            facts.append(f"{d['journey_minutes']} minutes end to end")
        stops = "".join(f"<li>{escape(tidy_name(s['name']))}</li>" for s in d["stops"])
        sections.append(
            f"<h2>Towards {escape(tidy_name(d['towards']))}</h2>"
            f"<p>{escape('; '.join(facts).capitalize()) if facts else 'No buses run this way today.'}</p>"
            f'<details><summary>{len(d["stops"])} stops in order</summary><ol class="links stops">{stops}</ol></details>')
    live_rows = "".join(
        "<li><strong>{}</strong>{}<small> near {}</small></li>".format(
            escape(getattr(r["bus"], "name", "") or f"Bus {route}"),
            f" to {escape(tidy_name(r['bus'].destination))}" if getattr(r["bus"], "destination", None) else "",
            escape(tidy_name(r["near"])))
        for r in live[:20])
    middle = profile["directions"][0]["stops"][len(profile["directions"][0]["stops"]) // 2] if profile["directions"] else None
    map_link = f"/?lat={middle['lat']:.4f}&amp;lon={middle['lon']:.4f}&amp;zoom=13&amp;layer=bus" if middle and middle.get("lat") else "/"
    body = f"""<p class="eyebrow">Bus route · {escape(operator)}</p>
<h1>Bus {escape(route)}{f": {escape(title_route)}" if title_route else ""}</h1>
<p class="lede">{len(live)} bus{'es' if len(live) != 1 else ''} on route {escape(route)} reporting live at {_now_clock()}.</p>
<p><a class="button" href="{map_link}">See route {escape(route)} buses on the live map</a></p>
{f'<h2>On the route now</h2><ul class="links">{live_rows}</ul>' if live_rows else ''}
{''.join(sections)}
<p class="source">Timetable from the Department for Transport's Bus Open Data Service (BODS); live positions from BODS vehicle locations. Times can change: check with {escape(operator)} before travelling. See <a href="{escape(operator_path(operator))}">all {escape(operator)} routes</a>.</p>"""
    canonical = origin + route_path(operator, route)
    return _shell(
        title=f"Bus {route} {title_route} timetable and live buses | {operator} | UK Activity Radar",
        description=f"Bus {route} ({operator}){f' between {title_route}' if title_route else ''}: stops in order, first and last buses today, how often it runs and where the buses are now.",
        canonical=canonical, body=body,
        json_ld={"@context": "https://schema.org", "@graph": [
            {"@type": "WebPage", "name": f"Bus {route} {title_route}", "url": canonical},
            {"@type": "BusTrip", "name": f"Bus {route}", "provider": {"@type": "Organization", "name": operator}},
            _breadcrumbs(origin, canonical, [("Home", "/"), ("Buses", "/buses"), (operator, operator_path(operator)),
                                             (f"Bus {route}", route_path(operator, route))])]})


def operator_routes_page(origin: str, operator: str, routes: list[dict]) -> str:
    items = "".join(
        f'<li><a href="{escape(route_path(operator, r["route"]))}">Bus {escape(r["route"])}</a> <small>{escape(" / ".join(tidy_name(d) for d in r["destinations"][:3]))}</small></li>'
        for r in routes)
    body = f"""<p class="eyebrow">Bus operator</p><h1>{escape(operator)} bus routes</h1>
<p class="lede">{len(routes)} routes with timetables and live buses.</p><ul class="links">{items}</ul>"""
    canonical = origin + operator_path(operator)
    return _shell(title=f"{operator} bus routes, timetables and live buses | UK Activity Radar",
                  description=f"Every {operator} bus route: stops, first and last buses and live positions.",
                  canonical=canonical, body=body,
                  json_ld={"@context": "https://schema.org", "@type": "CollectionPage", "name": f"{operator} bus routes", "url": canonical})


def buses_index(origin: str, routes: list[dict]) -> str:
    counts: dict[str, int] = {}
    for r in routes:
        counts[r["operator"]] = counts.get(r["operator"], 0) + 1
    items = "".join(f'<li><a href="{escape(operator_path(op))}">{escape(op)}</a> <small>{n} routes</small></li>'
                    for op, n in sorted(counts.items(), key=lambda kv: kv[0].lower()))
    body = f"""<p class="eyebrow">Buses</p><h1>Bus routes and live buses</h1>
<p class="lede">Timetables and live buses by operator. More regions appear as they are used on the live map.</p>
<ul class="links">{items or '<li>Bus timetables are loading; check back shortly.</li>'}</ul>"""
    canonical = origin + "/buses"
    return _shell(title="Bus routes, timetables and live buses by operator | UK Activity Radar",
                  description="Bus routes for UK operators: stops in order, first and last buses, frequency and live bus positions.",
                  canonical=canonical, body=body,
                  json_ld={"@context": "https://schema.org", "@type": "CollectionPage", "name": "Bus routes", "url": canonical})
