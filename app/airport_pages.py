"""Airport pages: what is arriving, leaving and flying over a UK airport now.

Built only from live aircraft tracking (ADS-B): a flight counts as arriving or
departing when its published route names this airport, or when it is low and
descending or climbing close to it. Not a timetable: there is no licensed
arrivals/departures board here, and the page says so.
"""
from __future__ import annotations

from html import escape
from math import asin, cos, radians, sin, sqrt

from .seo_pages import _breadcrumbs, _now_clock, _shell, slug

RADIUS_NM = 12

# elevation_ft is the published aerodrome elevation: a plane on the runway at
# Birmingham reports about 341 ft, not 0.
AIRPORTS: tuple[dict, ...] = (
    {"name": "London Heathrow", "iata": "LHR", "icao": "EGLL", "lat": 51.4700, "lon": -0.4543, "elevation_ft": 83, "runways": "09L/27R and 09R/27L", "area": "west London"},
    {"name": "London Gatwick", "iata": "LGW", "icao": "EGKK", "lat": 51.1537, "lon": -0.1821, "elevation_ft": 202, "runways": "08R/26L (main) and 08L/26R", "area": "West Sussex"},
    {"name": "Manchester", "iata": "MAN", "icao": "EGCC", "lat": 53.3537, "lon": -2.2750, "elevation_ft": 257, "runways": "05L/23R and 05R/23L", "area": "Greater Manchester"},
    {"name": "London Stansted", "iata": "STN", "icao": "EGSS", "lat": 51.8860, "lon": 0.2389, "elevation_ft": 348, "runways": "04/22", "area": "Essex"},
    {"name": "London Luton", "iata": "LTN", "icao": "EGGW", "lat": 51.8747, "lon": -0.3683, "elevation_ft": 526, "runways": "07/25", "area": "Bedfordshire"},
    {"name": "Birmingham", "iata": "BHX", "icao": "EGBB", "lat": 52.4539, "lon": -1.7480, "elevation_ft": 341, "runways": "15/33", "area": "the West Midlands"},
    {"name": "East Midlands", "iata": "EMA", "icao": "EGNX", "lat": 52.8311, "lon": -1.3281, "elevation_ft": 306, "runways": "09/27", "area": "Leicestershire"},
    {"name": "Bristol", "iata": "BRS", "icao": "EGGD", "lat": 51.3827, "lon": -2.7191, "elevation_ft": 622, "runways": "09/27", "area": "North Somerset"},
    {"name": "Edinburgh", "iata": "EDI", "icao": "EGPH", "lat": 55.9500, "lon": -3.3725, "elevation_ft": 135, "runways": "06/24", "area": "Edinburgh"},
    {"name": "Glasgow", "iata": "GLA", "icao": "EGPF", "lat": 55.8719, "lon": -4.4331, "elevation_ft": 26, "runways": "05/23", "area": "Renfrewshire"},
    {"name": "Newcastle", "iata": "NCL", "icao": "EGNT", "lat": 55.0375, "lon": -1.6917, "elevation_ft": 266, "runways": "07/25", "area": "Tyne and Wear"},
    {"name": "Liverpool John Lennon", "iata": "LPL", "icao": "EGGP", "lat": 53.3336, "lon": -2.8497, "elevation_ft": 80, "runways": "09/27", "area": "Merseyside"},
    {"name": "Leeds Bradford", "iata": "LBA", "icao": "EGNM", "lat": 53.8659, "lon": -1.6606, "elevation_ft": 681, "runways": "14/32", "area": "West Yorkshire"},
)


def airport_path(airport: dict) -> str:
    return f"/airport/{slug(airport['name'])}-{airport['iata'].lower()}"


def find(name: str) -> dict | None:
    return next((a for a in AIRPORTS if airport_path(a).rsplit("/", 1)[-1] == name), None)


def _miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = radians(lat1), radians(lat2)
    a = sin((p2 - p1) / 2) ** 2 + cos(p1) * cos(p2) * sin(radians(lon2 - lon1) / 2) ** 2
    return 2 * 3958.8 * asin(sqrt(a))


def classify(airport: dict, planes: list) -> dict[str, list[dict]]:
    """Arriving, departing, on the ground and overhead, nearest first."""
    groups: dict[str, list[dict]] = {"arriving": [], "departing": [], "ground": [], "overhead": []}
    code = airport["iata"]
    for plane in planes:
        if getattr(plane, "type", None) not in {"aircraft", "sar"} or plane.lat is None or plane.lon is None:
            continue
        miles = _miles(airport["lat"], airport["lon"], plane.lat, plane.lon)
        altitude = getattr(plane, "altitude_ft", None)
        rate = getattr(plane, "vertical_rate_fpm", None) or 0
        to, frm = (getattr(plane, "destination", None) or "").upper(), (getattr(plane, "origin", None) or "").upper()
        ground = str(getattr(plane, "status", "")).lower() == "on ground" or (altitude is not None and altitude < 100 and miles < 2)
        if str(getattr(plane, "aircraft_type_code", "")).upper() in {"TWR", "GND", "SVC"}:
            continue   # airport vehicles and fixed transmitters, not aircraft
        speed = getattr(plane, "speed", None)
        if ground and speed is not None and speed < 2:
            continue   # parked: clutter, as on the live map
        row = {"plane": plane, "miles": miles}
        if ground and miles < 3:
            groups["ground"].append(row)
        elif to == code or (not to and altitude is not None and altitude < 6000 and rate < -300 and miles < 15):
            groups["arriving"].append(row)
        elif frm == code or (not frm and altitude is not None and altitude < 6000 and rate > 300 and miles < 15):
            groups["departing"].append(row)
        else:
            groups["overhead"].append(row)
    for rows in groups.values():
        rows.sort(key=lambda row: row["miles"])
    return groups


def _row(row: dict, airport: dict) -> str:
    plane = row["plane"]
    model = getattr(plane, "aircraft_model", None) or getattr(plane, "aircraft_type_code", None) or ""
    route = " → ".join(x for x in (getattr(plane, "origin", None), getattr(plane, "destination", None)) if x)
    altitude = getattr(plane, "altitude_ft", None)
    height = "on the ground" if altitude is None or altitude < 100 else f"{round(altitude):,} ft"
    return ("<tr><td><strong>{}</strong><small>{}</small></td><td>{}</td><td>{} · {:.0f} mi</td></tr>".format(
        escape(str(plane.name or "")), escape(" · ".join(x for x in (model, getattr(plane, "operator", None) or "") if x)),
        escape(route or "Route not published"), escape(height), row["miles"]))


def airport_page(origin: str, airport: dict, planes: list, runways: str | None = None) -> str:
    groups = classify(airport, planes)
    name, code = airport["name"], airport["iata"]
    counts = {key: len(rows) for key, rows in groups.items()}
    summary = (f"At {_now_clock()}: {counts['arriving']} arriving, {counts['departing']} departing, "
               f"{counts['ground']} on the ground and {counts['overhead']} passing within {RADIUS_NM} nautical miles.")
    sections = []
    for key, title in (("arriving", f"Arriving at {name}"), ("departing", f"Leaving {name}"),
                       ("ground", "On the ground"), ("overhead", "Passing overhead")):
        rows = "".join(_row(row, airport) for row in groups[key][:25])
        if rows:
            sections.append(f'<h2>{escape(title)}</h2><table class="board"><tbody>{rows}</tbody></table>')
    types: dict[str, int] = {}
    for rows in groups.values():
        for row in rows:
            model = getattr(row["plane"], "aircraft_model", None) or getattr(row["plane"], "aircraft_type_code", None)
            if model:
                types[model] = types.get(model, 0) + 1
    common = ", ".join(f"{escape(m)} ({n})" for m, n in sorted(types.items(), key=lambda kv: -kv[1])[:6])
    body = f"""<p class="eyebrow">Airport · {escape(code)} / {escape(airport['icao'])}</p>
<h1>{escape(name)} Airport live aircraft</h1>
<p class="lede">{escape(summary)}</p>
{f'<p class="runways-now"><strong>Runways now:</strong> {escape(runways)}</p>' if runways else ''}
<p><a class="button" href="/?airport={escape(code)}">See planes around {escape(name)} on the live map</a></p>
{''.join(sections) if sections else '<p>No aircraft are being tracked near the airport right now.</p>'}
{f'<p>Aircraft types seen now: {common}.</p>' if common else ''}
<h2>About {escape(name)} Airport</h2><p>{escape(name)} Airport ({escape(code)}) is in {escape(airport['area'])}. Runway{'s' if 'and' in airport['runways'] else ''}: {escape(airport['runways'])}; aircraft normally take off and land into the wind, so the direction of arrivals changes with the weather.</p>
<p>This page shows aircraft tracked live by ADS-B receivers within {RADIUS_NM} nautical miles of the airport. A flight counts as arriving or leaving when its published route names {escape(code)}, or when it is low and descending or climbing close to the airport. It is not an official arrivals board: times, gates and delays are not shown. Learn <a href="/what-plane-is-above-me">how to find the plane above you</a> or see <a href="/airports">other UK airports</a>.</p>"""
    canonical = origin + airport_path(airport)
    return _shell(
        title=f"{name} Airport live aircraft tracker: arrivals and departures now | UK Activity Radar",
        description=f"Planes arriving at, leaving and flying over {name} Airport ({code}) right now, from live aircraft tracking: routes, aircraft types and heights.",
        canonical=canonical, body=body,
        json_ld={"@context": "https://schema.org", "@graph": [
            {"@type": "WebPage", "name": f"{name} Airport live aircraft", "url": canonical},
            {"@type": "Airport", "name": f"{name} Airport", "iataCode": code, "icaoCode": airport["icao"],
             "geo": {"@type": "GeoCoordinates", "latitude": airport["lat"], "longitude": airport["lon"]}},
            _breadcrumbs(origin, canonical, [("Home", "/"), ("Airports", "/airports"), (name, airport_path(airport))]),
        ]})


def airports_index(origin: str) -> str:
    items = "".join(f'<li><a href="{escape(airport_path(a))}">{escape(a["name"])}</a> <small>{escape(a["iata"])}</small></li>' for a in AIRPORTS)
    body = f"""<p class="eyebrow">Airports</p><h1>UK airports: live aircraft</h1>
<p class="lede">Pick an airport to see what is arriving, leaving and flying over it right now.</p><ul class="links">{items}</ul>"""
    canonical = origin + "/airports"
    return _shell(title="UK airports live aircraft tracker | UK Activity Radar",
                  description="Live planes arriving at and leaving Heathrow, Gatwick, Manchester, Birmingham, East Midlands and other UK airports.",
                  canonical=canonical, body=body,
                  json_ld={"@context": "https://schema.org", "@type": "CollectionPage", "name": "UK airports: live aircraft", "url": canonical})
