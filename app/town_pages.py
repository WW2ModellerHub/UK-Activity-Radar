"""Town pages: everything moving in and around one place, now.

For searches such as "Hinckley live transport" or "Coventry travel
disruption today": the town's stations and next trains, live buses and the
routes they are on, roadworks and incidents close by, the railway lines
through it and the nearest airport, with a link that opens the map there.
Every town is anchored on its main station, so its position is real.
"""
from __future__ import annotations

from html import escape
from math import asin, cos, radians, sin, sqrt

from .seo_pages import _breadcrumbs, _clock, _now_clock, _shell, _status, slug, tidy_name

# (town, main station CRS, county or area)
TOWNS: tuple[tuple[str, str, str], ...] = (
    ("Hinckley", "HNK", "Leicestershire"), ("Nuneaton", "NUN", "Warwickshire"), ("Coventry", "COV", "West Midlands"),
    ("Leicester", "LEI", "Leicestershire"), ("Rugby", "RUG", "Warwickshire"), ("Birmingham", "BHM", "West Midlands"),
    ("Derby", "DBY", "Derbyshire"), ("Nottingham", "NOT", "Nottinghamshire"), ("Loughborough", "LBO", "Leicestershire"),
    ("Tamworth", "TAM", "Staffordshire"), ("Lichfield", "LIC", "Staffordshire"), ("Atherstone", "ATH", "Warwickshire"),
    ("Bedworth", "BEH", "Warwickshire"), ("Leamington Spa", "LMS", "Warwickshire"), ("Warwick", "WRW", "Warwickshire"),
    ("Stratford-upon-Avon", "SAV", "Warwickshire"), ("Solihull", "SOL", "West Midlands"),
    ("Wolverhampton", "WVH", "West Midlands"), ("Walsall", "WSL", "West Midlands"),
    ("Burton upon Trent", "BUT", "Staffordshire"), ("Market Harborough", "MHR", "Leicestershire"),
    ("Kettering", "KET", "Northamptonshire"), ("Northampton", "NMP", "Northamptonshire"),
    ("Milton Keynes", "MKC", "Buckinghamshire"), ("Oxford", "OXF", "Oxfordshire"), ("Reading", "RDG", "Berkshire"),
    ("Bristol", "BRI", "Bristol"), ("Bath", "BTH", "Somerset"), ("Cardiff", "CDF", "South Wales"),
    ("Swindon", "SWI", "Wiltshire"), ("Cheltenham", "CNM", "Gloucestershire"), ("Gloucester", "GCR", "Gloucestershire"),
    ("Worcester", "WOF", "Worcestershire"), ("Stoke-on-Trent", "SOT", "Staffordshire"), ("Stafford", "STA", "Staffordshire"),
    ("Crewe", "CRE", "Cheshire"), ("Manchester", "MAN", "Greater Manchester"), ("Liverpool", "LIV", "Merseyside"),
    ("Leeds", "LDS", "West Yorkshire"), ("Sheffield", "SHF", "South Yorkshire"), ("York", "YRK", "North Yorkshire"),
    ("Newcastle", "NCL", "Tyne and Wear"), ("Edinburgh", "EDB", "Scotland"), ("Glasgow", "GLC", "Scotland"),
    ("Peterborough", "PBO", "Cambridgeshire"), ("Cambridge", "CBG", "Cambridgeshire"), ("Norwich", "NRW", "Norfolk"),
    ("Brighton", "BTN", "East Sussex"), ("Southampton", "SOU", "Hampshire"), ("Portsmouth", "PMS", "Hampshire"),
    ("Exeter", "EXD", "Devon"), ("Plymouth", "PLY", "Devon"),
)
STATION_KM = 8
BUS_KM = 5
ROAD_KM = 10


def town_path(name: str) -> str:
    return f"/town/{slug(name)}"


def find(name: str) -> tuple[str, str, str] | None:
    return next((town for town in TOWNS if slug(town[0]) == name), None)


def km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = radians(lat1), radians(lat2)
    a = sin((p2 - p1) / 2) ** 2 + cos(p1) * cos(p2) * sin(radians(lon2 - lon1) / 2) ** 2
    return 2 * 6371 * asin(sqrt(a))


def bbox(lat: float, lon: float, radius_km: float) -> tuple[float, float, float, float]:
    dlat, dlon = radius_km / 111.0, radius_km / (111.0 * cos(radians(lat)))
    return (lon - dlon, lat - dlat, lon + dlon, lat + dlat)


def town_page(origin: str, town: tuple[str, str, str], centre: dict, stations: list[dict], board: dict | None,
              buses: list, roads: list, lines: list[dict], airport: dict | None) -> str:
    name, _, area = town
    lat, lon = centre["lat"], centre["lon"]
    unique = {s["crs"]: s for s in stations if km(lat, lon, s["lat"], s["lon"]) <= STATION_KM}
    nearby = sorted(unique.values(), key=lambda s: km(lat, lon, s["lat"], s["lon"]))[:8]
    departures = [row for row in (board or {}).get("departures") or [] if int(row.get("minutes") or 0) >= -2][:8]
    dep_rows = "".join(
        "<tr><td>{}</td><td><strong>{}</strong>{}</td><td class=\"status {}\">{}</td></tr>".format(
            escape(_clock(row.get("scheduled"))), escape(tidy_name(row.get("destination") or "")),
            f"<small>{escape(row.get('operator_name') or '')}</small>" if row.get("operator_name") else "",
            _status(row)[1], escape(_status(row)[0]))
        for row in departures)
    routes: dict[str, int] = {}
    for bus in buses:
        if getattr(bus, "route", None):
            routes[bus.route] = routes.get(bus.route, 0) + 1
    route_list = ", ".join(f"{escape(r)}" for r, _ in sorted(routes.items(), key=lambda kv: (-kv[1], kv[0]))[:16])
    def road_line(e) -> str:
        impact = getattr(e, "impact", None) or {}
        what = impact.get("title") or e.name
        detail = " · ".join(x for x in (e.name if e.name != what else None, impact.get("where"), impact.get("impact")) if x)
        return f"<li><strong>{escape(what)}</strong>{f'<small> {escape(detail)}</small>' if detail else ''}</li>"
    road_items = "".join(road_line(e) for e in roads[:10])
    late = sum(1 for row in departures if _status(row)[1] in {"late", "bad"})
    summary = (f"At {_now_clock()}: {len(departures)} train{'s' if len(departures) != 1 else ''} due from {escape(centre['name'])}"
               + (f" ({late} late or cancelled)" if late else "")
               + f", {len(buses)} bus{'es' if len(buses) != 1 else ''} moving within {BUS_KM} km and {len(roads)} roadworks or incidents within {ROAD_KM} km.")
    station_links = "".join(
        f'<li><a href="{escape(s["path"])}">{escape(s["name"])}</a> <small>{km(lat, lon, s["lat"], s["lon"]):.1f} km</small></li>'
        for s in nearby)
    line_links = ", ".join(f'<a href="{escape(l["path"])}">{escape(l["name"])}</a>' for l in lines)
    body = f"""<p class="eyebrow">Live transport · {escape(area)}</p>
<h1>{escape(name)} live transport today</h1>
<p class="lede">{summary}</p>
<p><a class="button" href="/?lat={lat:.4f}&amp;lon={lon:.4f}&amp;zoom=13&amp;layer=train">See {escape(name)} on the live map</a></p>
<h2>Next trains from {escape(centre['name'])}</h2>
{f'<table class="board"><tbody>{dep_rows}</tbody></table>' if dep_rows else '<p>No departures are due in the next few hours.</p>'}
<p><a href="{escape(centre['path'])}">Full {escape(centre['name'])} station board and passenger numbers</a></p>
{f'<h2>Railway lines</h2><p>{escape(name)} is on the {line_links}.</p>' if line_links else ''}
<h2>Buses</h2><p>{len(buses)} bus{'es are' if len(buses) != 1 else ' is'} moving within {BUS_KM} km of the town centre right now{f', on routes {route_list}' if route_list else ''}. Open the live map to see where each one is and when it reaches your stop.</p>
<h2>Roads</h2>{f'<ul class="links">{road_items}</ul>' if road_items else f'<p>No roadworks or incidents are reported within {ROAD_KM} km right now.</p>'}
{f'<h2>Stations near {escape(name)}</h2><ul class="links">{station_links}</ul>' if station_links else ''}
{f'<h2>Nearest airport</h2><p><a href="{escape(airport["path"])}">{escape(airport["name"])} Airport</a>, about {airport["km"]:.0f} km away: see what is arriving, leaving and flying over it now.</p>' if airport else ''}
<p>Live positions come from Network Rail, the Bus Open Data Service and National Highways open data, and update all day. See <a href="/towns">other towns and cities</a>.</p>"""
    canonical = origin + town_path(name)
    return _shell(
        title=f"{name} live transport today: trains, buses and roadworks | UK Activity Radar",
        description=f"Live trains, buses and roadworks in and around {name} right now: next departures from {centre['name']}, bus routes running, and disruption nearby.",
        canonical=canonical, body=body,
        json_ld={"@context": "https://schema.org", "@graph": [
            {"@type": "WebPage", "name": f"{name} live transport today", "url": canonical},
            {"@type": "Place", "name": name, "geo": {"@type": "GeoCoordinates", "latitude": lat, "longitude": lon}},
            _breadcrumbs(origin, canonical, [("Home", "/"), ("Towns", "/towns"), (name, town_path(name))])]})


def towns_index(origin: str) -> str:
    items = "".join(f'<li><a href="{escape(town_path(n))}">{escape(n)}</a> <small>{escape(a)}</small></li>'
                    for n, _, a in sorted(TOWNS))
    body = f"""<p class="eyebrow">Towns and cities</p><h1>Live transport by town</h1>
<p class="lede">Trains, buses, roadworks and planes in and around each place, right now.</p><ul class="links">{items}</ul>"""
    canonical = origin + "/towns"
    return _shell(title="Live transport by town: trains, buses and roadworks | UK Activity Radar",
                  description="Live trains, buses and roadworks for UK towns and cities: Hinckley, Nuneaton, Coventry, Leicester, Birmingham and more.",
                  canonical=canonical, body=body,
                  json_ld={"@context": "https://schema.org", "@type": "CollectionPage", "name": "Live transport by town", "url": canonical})
