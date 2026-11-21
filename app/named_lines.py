"""Named railway lines: their stations in order and the trains on them now.

Pages for searches such as "West Coast Main Line live map" or "trains on the
Midland Main Line". Lines are listed by station names, resolved against the
real station directory, so a name that is not found is left out rather than
guessed. Trains count as "on the line" when their live position is within a
short distance of the line drawn between its stations: an honest
approximation, and the page says so.
"""
from __future__ import annotations

from html import escape
from math import cos, radians, sqrt

from .seo_pages import _breadcrumbs, _now_clock, _shell, slug, tidy_name

NEAR_LINE_KM = 1.0

LINES: tuple[dict, ...] = (
    {"name": "West Coast Main Line", "region": "London to Glasgow",
     "about": "Britain's busiest intercity railway, from London Euston through the West Midlands and the North West to Glasgow, used by Avanti West Coast, London Northwestern, West Midlands Trains and many freight trains.",
     "stations": ["London Euston", "Watford Junction", "Milton Keynes Central", "Rugby", "Nuneaton", "Tamworth",
                  "Lichfield Trent Valley", "Stafford", "Crewe", "Warrington Bank Quay", "Wigan North Western",
                  "Preston", "Lancaster", "Oxenholme Lake District", "Penrith North Lakes", "Carlisle", "Lockerbie",
                  "Glasgow Central"]},
    {"name": "East Coast Main Line", "region": "London to Edinburgh",
     "about": "The fast route from London King's Cross through Peterborough, Doncaster, York and Newcastle to Edinburgh, used by LNER, Grand Central, Lumo, CrossCountry and others.",
     "stations": ["London Kings Cross", "Stevenage", "Peterborough", "Grantham", "Newark North Gate", "Retford",
                  "Doncaster", "York", "Darlington", "Durham", "Newcastle", "Alnmouth", "Berwick-upon-Tweed",
                  "Edinburgh"]},
    {"name": "Midland Main Line", "region": "London to Sheffield",
     "about": "From London St Pancras through Bedford, Leicester and Derby to Sheffield, with East Midlands Railway intercity trains and Thameslink in the south.",
     "stations": ["London St Pancras International", "Luton", "Bedford", "Wellingborough", "Kettering",
                  "Market Harborough", "Leicester", "Loughborough (Leics)", "East Midlands Parkway", "Derby",
                  "Chesterfield", "Sheffield"]},
    {"name": "Great Western Main Line", "region": "London to Bristol",
     "about": "Brunel's railway from London Paddington through Reading and Swindon to Bath and Bristol, run mainly by Great Western Railway.",
     "stations": ["London Paddington", "Reading", "Didcot Parkway", "Swindon", "Chippenham", "Bath Spa",
                  "Bristol Temple Meads"]},
    {"name": "Settle–Carlisle Line", "region": "Settle to Carlisle",
     "about": "One of Britain's most scenic railways, crossing the Ribblehead Viaduct through the Yorkshire Dales and Eden Valley; a favourite for steam specials and charters.",
     "stations": ["Settle", "Horton-in-Ribblesdale", "Ribblehead", "Dent", "Garsdale", "Kirkby Stephen",
                  "Appleby", "Langwathby", "Lazonby & Kirkoswald", "Armathwaite", "Carlisle"]},
    {"name": "Chiltern Main Line", "region": "London to Birmingham",
     "about": "Chiltern Railways' route from London Marylebone through High Wycombe, Banbury and Leamington Spa to Birmingham.",
     "stations": ["London Marylebone", "High Wycombe", "Bicester North", "Banbury", "Leamington Spa", "Warwick",
                  "Solihull", "Birmingham Moor Street"]},
    {"name": "Brighton Main Line", "region": "London to Brighton",
     "about": "From London Victoria through Clapham Junction, East Croydon and Gatwick Airport to Brighton, with Southern, Thameslink and Gatwick Express.",
     "stations": ["London Victoria", "Clapham Junction", "East Croydon", "Gatwick Airport", "Haywards Heath",
                  "Brighton"]},
    {"name": "South Western Main Line", "region": "London to Weymouth",
     "about": "South Western Railway's main route from London Waterloo through Woking, Basingstoke and Southampton to Bournemouth and Weymouth.",
     "stations": ["London Waterloo", "Clapham Junction", "Woking", "Basingstoke", "Winchester",
                  "Southampton Central", "Brockenhurst", "Bournemouth", "Poole", "Weymouth"]},
    {"name": "Birmingham to Leicester Line", "region": "via Nuneaton and Hinckley",
     "about": "CrossCountry's route between Birmingham New Street and Leicester, calling at Coleshill Parkway, Nuneaton, Hinckley and Narborough.",
     "stations": ["Birmingham New Street", "Water Orton", "Coleshill Parkway", "Nuneaton", "Hinckley",
                  "Narborough", "South Wigston", "Leicester"]},
    {"name": "Coventry to Nuneaton Line", "region": "via Coventry Arena and Bedworth",
     "about": "The local line linking Coventry and Nuneaton through Coventry Arena and Bedworth, run by West Midlands Railway.",
     "stations": ["Coventry", "Coventry Arena", "Bedworth", "Bermuda Park", "Nuneaton"]},
    {"name": "Cross City Line", "region": "Lichfield to Redditch via Birmingham",
     "about": "West Midlands Railway's busy line across Birmingham, from Lichfield through Sutton Coldfield and Birmingham New Street to University and Redditch.",
     "stations": ["Lichfield Trent Valley", "Lichfield City", "Sutton Coldfield", "Birmingham New Street",
                  "Five Ways", "University", "Longbridge", "Bromsgrove", "Redditch"]},
    {"name": "Cross Country Route", "region": "Bristol to Newcastle via Birmingham",
     "about": "CrossCountry's long-distance spine from Bristol through Cheltenham, Birmingham, Derby, Sheffield and York to Newcastle.",
     "stations": ["Bristol Temple Meads", "Cheltenham Spa", "Birmingham New Street", "Tamworth", "Burton-on-Trent",
                  "Derby", "Chesterfield", "Sheffield", "Wakefield Westgate", "Leeds", "York", "Darlington",
                  "Durham", "Newcastle"]},
)


def line_path(name: str) -> str:
    return f"/railway-line/{slug(name)}"


def _key(name: str) -> str:
    return "".join(ch for ch in tidy_name(name).casefold().replace("&", "and") if ch.isalnum())


def resolve(line: dict, stations: list[dict]) -> list[dict]:
    """The line's stations found in the directory, in order. Unknown names are skipped."""
    by_name = {}
    for row in stations:
        by_name.setdefault(_key(row["name"]), row)
    return [by_name[_key(name)] for name in line["stations"] if _key(name) in by_name]


ON_TRACK_KM = 0.3
_CELL = 0.01
_TRACKS: dict[str, dict] = {}
_TRACK_LOCK = __import__("threading").Lock()


def track_for(line: dict, points: list[dict], *, build: bool = True) -> dict | None:
    """The line drawn along real track between its stations, with a grid index.

    Routed once per line (and set of stations) over the physical railway
    graph. A pair that cannot be routed falls back to a straight hop, so one
    gap does not lose the whole line.
    """
    key = line["name"] + "|" + ",".join(p["crs"] for p in points)
    with _TRACK_LOCK:
        if key in _TRACKS:
            return _TRACKS[key]
    if len(points) < 2 or not build:
        return None
    try:
        from pathlib import Path as _P
        from .schedule_store import _shared_physical_graph
        graph = _shared_physical_graph(_P(__file__).resolve().parent / "assets" / "rail_track_geometry.geojson")
    except Exception:
        graph = None
    coords: list[tuple[float, float]] = []
    for a, b in zip(points, points[1:]):
        pair = []
        if graph is not None:
            try:
                pair = graph.route([[a["lat"], a["lon"]], [b["lat"], b["lon"]]], max_points=800)
            except Exception:
                pair = []
        hop = [(float(x[0]), float(x[1])) for x in pair] or [(a["lat"], a["lon"]), (b["lat"], b["lon"])]
        coords.extend(hop if not coords else hop[1:])
    cells: dict[tuple[int, int], list[int]] = {}
    for i in range(len(coords) - 1):
        (la1, lo1), (la2, lo2) = coords[i], coords[i + 1]
        for cy in range(int(min(la1, la2) // _CELL) - 1, int(max(la1, la2) // _CELL) + 2):
            for cx in range(int(min(lo1, lo2) // _CELL) - 1, int(max(lo1, lo2) // _CELL) + 2):
                cells.setdefault((cy, cx), []).append(i)
    lengths = [0.0]
    for i in range(len(coords) - 1):
        lengths.append(lengths[-1] + _segment_km(coords[i][0], coords[i][1], {"lat": coords[i + 1][0], "lon": coords[i + 1][1]}, {"lat": coords[i + 1][0], "lon": coords[i + 1][1]}))
    track = {"coords": coords, "cells": cells, "along": lengths}
    with _TRACK_LOCK:
        _TRACKS[key] = track
    return track


def on_track(track: dict, lat: float, lon: float) -> float | None:
    """Distance along the line (km) when the point is on its track, else None."""
    coords, cells = track["coords"], track["cells"]
    near = cells.get((int(lat // _CELL), int(lon // _CELL)))
    if not near:
        return None
    best = None
    for i in near:
        a = {"lat": coords[i][0], "lon": coords[i][1]}
        b = {"lat": coords[i + 1][0], "lon": coords[i + 1][1]}
        d = _segment_km(lat, lon, a, b)
        if d <= ON_TRACK_KM and (best is None or d < best[0]):
            best = (d, track["along"][i] + _segment_fraction(lat, lon, a, b) * _segment_km(a["lat"], a["lon"], b, b))
    return best[1] if best else None


def _segment_km(lat: float, lon: float, a: dict, b: dict) -> float:
    """Distance from a point to the straight segment a-b (flat-earth, fine at these scales)."""
    kx = 111.32 * cos(radians(lat))
    px, py = lon * kx, lat * 110.57
    ax, ay, bx, by = a["lon"] * kx, a["lat"] * 110.57, b["lon"] * kx, b["lat"] * 110.57
    dx, dy = bx - ax, by - ay
    length = dx * dx + dy * dy
    t = 0.0 if not length else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length))
    return sqrt((px - ax - t * dx) ** 2 + (py - ay - t * dy) ** 2)


def trains_on(points: list[dict], trains: list, track: dict | None = None) -> list[dict]:
    """Live trains on the line, in line order, each with the nearest station.

    With the line's real track, a train counts only when it is on that track
    (within 300 m); without it, within NEAR_LINE_KM of the straight hops.
    """
    if len(points) < 2:
        return []
    if track is not None:
        found = []
        for train in trains:
            lat, lon = getattr(train, "lat", None), getattr(train, "lon", None)
            if lat is None or lon is None or getattr(train, "restored_snapshot", False):
                continue
            along = on_track(track, lat, lon)
            if along is None:
                continue
            nearest = min(points, key=lambda p: (p["lat"] - lat) ** 2 + ((p["lon"] - lon) * cos(radians(lat))) ** 2)
            found.append({"train": train, "order": along, "near": nearest["name"]})
        found.sort(key=lambda row: row["order"])
        return found
    out = []
    for train in trains:
        lat, lon = getattr(train, "lat", None), getattr(train, "lon", None)
        if lat is None or lon is None:
            continue
        best = min(range(len(points) - 1), key=lambda i: _segment_km(lat, lon, points[i], points[i + 1]))
        if _segment_km(lat, lon, points[best], points[best + 1]) > NEAR_LINE_KM:
            continue
        nearest = min(points, key=lambda p: (p["lat"] - lat) ** 2 + ((p["lon"] - lon) * cos(radians(lat))) ** 2)
        out.append({"train": train, "order": best + _segment_fraction(lat, lon, points[best], points[best + 1]),
                    "near": nearest["name"]})
    out.sort(key=lambda row: row["order"])
    return out


def _segment_fraction(lat, lon, a, b) -> float:
    dx, dy = b["lon"] - a["lon"], b["lat"] - a["lat"]
    length = dx * dx + dy * dy
    return 0.0 if not length else max(0.0, min(1.0, ((lon - a["lon"]) * dx + (lat - a["lat"]) * dy) / length))


def _delay_text(train) -> tuple[str, str]:
    if getattr(train, "cancelled_at", None):
        return "Cancelled", "bad"
    delay = getattr(train, "delay_minutes", None)
    if delay is None:
        return "Running", ""
    if delay >= 5:
        return f"{round(delay)} min late", "late" if delay < 15 else "bad"
    if delay <= -1:
        return f"{-round(delay)} min early", "good"
    return "On time", "good"


def line_page(origin: str, line: dict, points: list[dict], trains: list) -> str:
    on_line = trains_on(points, trains, track_for(line, points))
    running = len(on_line)
    late = [row for row in on_line if (getattr(row["train"], "delay_minutes", None) or 0) >= 5]
    cancelled = sum(1 for row in on_line if getattr(row["train"], "cancelled_at", None))
    delays = [getattr(row["train"], "delay_minutes", None) for row in late]
    average = round(sum(delays) / len(delays)) if delays else 0
    if running == 0:
        summary = "No trains are reporting on this line right now."
        state, label = "unknown", "No trains reporting"
    else:
        summary = f"{running} train{'s' if running != 1 else ''} near the line at {_now_clock()}: {len(late)} running five or more minutes late"
        summary += f", about {average} minutes on average" if late else ""
        summary += f"; {cancelled} cancelled." if cancelled else "."
        share = len(late) / running
        state, label = ("good", "Good service") if share < 0.2 else (("late", "Some delays") if share < 0.5 else ("bad", "Major delays"))
    station_items = "".join(
        f'<li><a href="{escape(p["path"])}">{escape(p["name"])}</a></li>' for p in points)
    rows = "".join(
        "<tr><td><strong>{}</strong>{}</td><td>near {}</td><td class=\"status {}\">{}</td></tr>".format(
            escape(str(getattr(r["train"], "headcode", None) or getattr(r["train"], "name", ""))),
            f"<small>to {escape(tidy_name(getattr(r['train'], 'destination', '')))}</small>" if getattr(r["train"], "destination", None) else "",
            escape(r["near"]), _delay_text(r["train"])[1], escape(_delay_text(r["train"])[0]))
        for r in on_line[:40])
    operators = sorted({str(getattr(r["train"], "operator", "") or "") for r in on_line} - {""})
    first = points[0] if points else None
    map_link = f'/?place=rail-station:{escape(first["crs"])}' if first else "/"
    name = line["name"]
    body = f"""<p class="eyebrow">Railway line · {escape(line['region'])}</p>
<h1>{escape(name)} live trains</h1>
<p class="status-big {state}">{escape(label)}</p>
<p class="lede">{escape(summary)}</p>
<p><a class="button" href="{map_link}">See the {escape(name)} on the live map</a></p>
{f'<h2>Trains on the line now</h2><table class="board"><tbody>{rows}</tbody></table>' if rows else ''}
{f'<p>Operators seen on the line now: {escape(", ".join(operators))}.</p>' if operators else ''}
<h2>Stations on the {escape(name)}</h2><ol class="links">{station_items}</ol>
<h2>About the {escape(name)}</h2><p>{escape(line['about'])}</p>
<p>Trains are counted when their live position is on this line's track (within {ON_TRACK_KM * 1000:.0f} m), so trains on routes that share the same tracks for a stretch appear too. Positions and delays come from Network Rail open data and update through the day. See <a href="/railway-lines">all railway lines</a> or <a href="/operators">train operators today</a>.</p>"""
    canonical = origin + line_path(name)
    return _shell(
        title=f"{name} live trains, map and delays today | UK Activity Radar",
        description=f"Live trains on the {name} ({line['region']}): where they are now, which are late, and every station in order.",
        canonical=canonical, body=body,
        json_ld={"@context": "https://schema.org", "@graph": [
            {"@type": "WebPage", "name": f"{name} live trains", "url": canonical,
             "description": f"Live trains, delays and stations on the {name}."},
            {"@type": "ItemList", "name": f"Stations on the {name}",
             "itemListElement": [{"@type": "ListItem", "position": i + 1, "name": p["name"], "url": origin + p["path"]}
                                 for i, p in enumerate(points)]},
            _breadcrumbs(origin, canonical, [("Home", "/"), ("Railway lines", "/railway-lines"), (name, line_path(name))]),
        ]})


def lines_index(origin: str) -> str:
    items = "".join(
        f'<li><a href="{escape(line_path(line["name"]))}">{escape(line["name"])}</a> <small>{escape(line["region"])}</small></li>'
        for line in LINES)
    body = f"""<p class="eyebrow">Railway lines</p><h1>UK railway lines: live trains</h1>
<p class="lede">Pick a line to see the trains on it right now, which are running late, and its stations in order.</p><ul class="links">{items}</ul>"""
    canonical = origin + "/railway-lines"
    return _shell(title="UK railway lines: live trains and delays | UK Activity Radar",
                  description="Live trains on Britain's main railway lines: West Coast, East Coast, Midland, Great Western, Settle–Carlisle and more.",
                  canonical=canonical, body=body,
                  json_ld={"@context": "https://schema.org", "@type": "CollectionPage",
                           "name": "UK railway lines: live trains", "url": canonical})


def lines_for(train, stations: list[dict]) -> list[dict]:
    """The named lines a live train is on now, for a link from its panel."""
    lat, lon = getattr(train, "lat", None), getattr(train, "lon", None)
    if lat is None or lon is None:
        return []
    out = []
    for line in LINES:
        points = resolve(line, stations)
        track = track_for(line, points, build=False)   # only lines already routed: never slow a tap
        if track is not None and on_track(track, lat, lon) is not None:
            out.append({"name": line["name"], "path": line_path(line["name"])})
    return out
