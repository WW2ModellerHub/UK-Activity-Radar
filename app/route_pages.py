"""Direct train route pages: "Hinckley to Leicester trains".

Every direct train between two stations today from the published timetable:
when it leaves and arrives, how long it takes and who runs it, with the first
and last train, the typical journey time and a link to buy tickets.
"""
from __future__ import annotations

from datetime import datetime
from html import escape
from math import asin, cos, radians, sin, sqrt
from statistics import median
from zoneinfo import ZoneInfo

from .seo_pages import _breadcrumbs, _shell, slug

LONDON = ZoneInfo("Europe/London")


def route_path(from_station: dict, to_station: dict) -> str:
    return f"/trains/{slug(from_station['name'])}-to-{slug(to_station['name'])}"


def resolve(pair: str, stations: list[dict]) -> tuple[dict, dict] | None:
    """Both stations from "hinckley-to-leicester"; station slugs may themselves hold "-to-"."""
    by_slug: dict[str, dict] = {}
    for station in stations:
        by_slug.setdefault(slug(station["name"]), station)
    text = str(pair or "").lower()
    start = 0
    while (index := text.find("-to-", start)) != -1:
        left, right = by_slug.get(text[:index]), by_slug.get(text[index + 4:])
        if left and right and left["crs"] != right["crs"]:
            return left, right
        start = index + 1
    return None


def _miles(a: dict, b: dict) -> float:
    p1, p2 = radians(a["lat"]), radians(b["lat"])
    h = sin((p2 - p1) / 2) ** 2 + cos(p1) * cos(p2) * sin(radians(b["lon"] - a["lon"]) / 2) ** 2
    return 2 * 3958.8 * asin(sqrt(h))


def _took(minutes: int) -> str:
    return f"{minutes // 60}h {minutes % 60:02d}m" if minutes >= 60 else f"{minutes} min"


def route_page(origin: str, frm: dict, to: dict, data: dict | None, trainline_affiliate_url: str = "",
               now: datetime | None = None) -> str:
    trains = (data or {}).get("trains") or []
    now = now or datetime.now(LONDON)
    clock_now = now.strftime("%H:%M")
    fname, tname = frm["name"], to["name"]
    miles = _miles(frm, to)
    if trains:
        durations = [t["minutes"] for t in trains]
        fastest, typical = min(durations), round(median(durations))
        upcoming = [t for t in trains if t["depart"] >= clock_now]
        facts = [f"{len(trains)} direct train{'s' if len(trains) != 1 else ''} today",
                 f"typically {_took(typical)}" + (f", fastest {_took(fastest)}" if fastest < typical else ""),
                 f"first {trains[0]['depart']}, last {trains[-1]['depart']}",
                 f"about {miles:.0f} miles apart"]
        nxt = upcoming[0] if upcoming else None
        lede = (f"The next direct train leaves {escape(fname)} at <strong>{escape(nxt['depart'])}</strong> and arrives at "
                f"{escape(tname)} at {escape(nxt['arrive'])}." if nxt else
                f"The last direct train today has left; the first tomorrow is usually around {escape(trains[0]['depart'])}.")
        def rows(selection: list[dict], gone: bool = False) -> str:
            return "".join(
                '<tr{}><td class=time>{}</td><td class=time>{}</td><td class=took>{}</td><td>{}<small>{}</small></td></tr>'.format(
                    ' class="gone"' if gone else "", escape(t["depart"]), escape(t["arrive"]), escape(_took(t["minutes"])),
                    escape(t.get("operator") or ""),
                    escape("non-stop" if not t.get("stops_between") else f"{t['stops_between']} stop{'s' if t['stops_between'] != 1 else ''} between"))
                for t in selection)
        head = '<thead><tr><th>Leaves</th><th>Arrives</th><th>Takes</th><th>Train</th></tr></thead>'
        earlier = [t for t in trains if t["depart"] < clock_now]
        # The trains still to come first; the ones that have gone fold away.
        table = (f'<table class="board route-table">{head}<tbody>{rows(upcoming)}</tbody></table>' if upcoming
                 else "<p>No more direct trains today.</p>")
        if earlier:
            table += (f'<details class="route-earlier"><summary>Earlier today ({len(earlier)})</summary>'
                      f'<table class="board route-table">{head}<tbody>{rows(earlier, gone=True)}</tbody></table></details>')
        operators = sorted({t["operator"] for t in trains if t.get("operator")})
        chips = "".join(f"<li>{escape(f)}</li>" for f in facts)
    else:
        lede = f"There are no direct trains from {escape(fname)} to {escape(tname)} in today's timetable."
        table, operators, chips = "", [], ""
    affiliate_url = str(trainline_affiliate_url or "").strip()
    tickets = (f'<a class="button secondary" href="{escape(affiliate_url, quote=True)}" target="_blank" rel="sponsored nofollow noopener">'
               f'Check tickets on Trainline</a><small class="affiliate-note">Affiliate link · UK Activity Radar may earn a commission if you book.</small>'
               if affiliate_url.startswith("https://") else "")
    back = route_path(to, frm)
    body = f"""<p class="eyebrow">Direct trains · {escape(frm['crs'])} to {escape(to['crs'])}</p>
<h1>{escape(fname)} to {escape(tname)} trains</h1>
<p class="lede">{lede}</p>
{f'<ul class="station-facts">{chips}</ul>' if chips else ''}
<p class="station-actions"><a class="button" href="/?place=rail-station:{escape(frm['crs'])}">Live departures from {escape(fname)}</a>{tickets}</p>
{f'<h2>Every direct train today</h2>{table}' if table else ''}
{f"<p>Run by {escape(', '.join(operators))}.</p>" if operators else ''}
<p>Going the other way? See <a href="{escape(back)}">{escape(tname)} to {escape(fname)} trains</a>. Station pages: <a href="{escape(frm['path'])}">{escape(fname)}</a> and <a href="{escape(to['path'])}">{escape(tname)}</a>.</p>
<p class="source">From the published Network Rail timetable for {escape(now.strftime('%A %-d %B'))}. Times can change: check before you travel. Live running and delays are on the <a href="/?place=rail-station:{escape(frm['crs'])}">live map</a>.</p>"""
    canonical = origin + route_path(frm, to)
    summary = (f"{len(trains)} direct trains today, typically {_took(round(median([t['minutes'] for t in trains])))}"
               if trains else "Direct trains")
    return _shell(
        title=f"{fname} to {tname} trains: times today and journey time | UK Activity Radar",
        description=f"{summary} from {fname} to {tname}: first and last train, every departure and arrival time, and live running.",
        canonical=canonical, body=body,
        json_ld={"@context": "https://schema.org", "@graph": [
            {"@type": "WebPage", "name": f"{fname} to {tname} trains", "url": canonical},
            _breadcrumbs(origin, canonical, [("Home", "/"), ("Stations", "/stations"), (fname, frm["path"]),
                                             (f"to {tname}", route_path(frm, to))])]})
