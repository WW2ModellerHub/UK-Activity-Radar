from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import quote_plus

import requests

from .config import settings
from .models import Event


def x_search_url(query: str) -> str:
    return f"https://x.com/search?q={quote_plus(query)}&src=typed_query&f=live"


def google_news_search_url(query: str) -> str:
    # Direct Google News result page rather than a generic web-search tab.
    return f"https://news.google.com/search?q={quote_plus(query)}&hl=en-GB&gl=GB&ceid=GB%3Aen"


def bbc_search_url(query: str) -> str:
    return f"https://www.bbc.co.uk/search?q={quote_plus(query)}"


def google_maps_url(lat: float, lon: float) -> str:
    return f"https://www.google.com/maps/search/?api=1&query={lat:.6f}%2C{lon:.6f}"


def national_highways_alert_search_url(query: str) -> str:
    # Some National Highways feed records do not have a public per-event URL.
    # A domain-restricted user-click search is the most specific free fallback.
    q = f"site:nationalhighways.co.uk/roads-and-travel/live-travel-updates/travel-alerts/ {query}"
    return f"https://www.google.com/search?q={quote_plus(q)}"


def gdelt_articles(event: Event, limit: int = 6) -> list[dict]:
    if not settings.coverage_enabled:
        return []
    query = (event.news_query or event.name or "").strip()
    if not query:
        return []
    try:
        response = requests.get(
            settings.gdelt_url,
            params={
                "query": query,
                "mode": "artlist",
                "format": "json",
                "maxrecords": max(1, min(20, limit)),
                "sort": "datedesc",
                "timespan": "1week",
            },
            timeout=12,
            headers={"User-Agent": "UKActivityRadar/1.5.0"},
        )
        response.raise_for_status()
        payload = response.json()
        rows = payload.get("articles", []) if isinstance(payload, dict) else []
        out = []
        for row in rows:
            if not isinstance(row, dict) or not row.get("url"):
                continue
            if event.source == "RNLI" and not _rnli_article_matches(event, row):
                continue
            out.append({
                "title": row.get("title") or row.get("url"),
                "url": row.get("url"),
                "source": row.get("domain") or "News",
                "seen": row.get("seendate"),
                "verified_match": event.source == "RNLI",
            })
            if len(out) >= limit:
                break
        return out
    except Exception:
        return []


def _rnli_station_name(event: Event) -> str:
    return re.sub(r"\s+(?:lifeboat station\s+)?launch\s*$", "", event.name, flags=re.I).strip()


def _rnli_article_matches(event: Event, row: dict) -> bool:
    """Require strong station, rescue and time agreement for RNLI coverage.

    GDELT is discovery, not an authority.  This guard prevents generic rescue
    articles, similarly named places and old launch stories being presented as
    coverage of the selected live record.
    """
    title = str(row.get("title") or "")
    haystack = title.casefold()
    station_tokens = {
        token for token in re.findall(r"[a-z0-9]{4,}", _rnli_station_name(event).casefold())
        if token not in {"rnli", "lifeboat", "station", "launch"}
    }
    if not station_tokens or not any(token in haystack for token in station_tokens):
        return False
    if not any(word in haystack for word in ("rnli", "lifeboat", "rescue")):
        return False

    seen = str(row.get("seendate") or "").strip()
    if event.start_time and seen:
        parsed = None
        for fmt in ("%Y%m%dT%H%M%SZ", "%Y-%m-%dT%H:%M:%SZ"):
            try:
                parsed = datetime.strptime(seen, fmt).replace(tzinfo=timezone.utc)
                break
            except ValueError:
                continue
        if parsed is None:
            return False
        started = event.start_time
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        if abs((parsed - started.astimezone(timezone.utc)).total_seconds()) > 3 * 86400:
            return False
    return True


def coverage_for_event(event: Event) -> dict:
    query = (event.social_query or event.news_query or event.name).strip()
    live_query = event.news_query or query
    if event.source == "RNLI":
        # Do not imply that broad social/news searches or generic station
        # photographs document this launch.  Return only strongly matched
        # coverage plus the official source record.
        return {
            "event_id": event.id,
            "query": query,
            "articles": gdelt_articles(event),
            "links": [
                {"label": "Open verified launch location", "url": google_maps_url(event.lat, event.lon), "type": "map"},
            ],
            "official_url": event.official_url,
            "official_label": "Official RNLI launch record",
            "verified_only": True,
            "verification_note": "Only coverage matching this station, rescue context and launch date is shown. No generic or historic substitute media is used.",
        }
    links = [
        {"label": "Live stories • Google News", "url": google_news_search_url(live_query), "type": "news_search"},
        {"label": "BBC search", "url": bbc_search_url(live_query), "type": "news_search"},
        {"label": "Latest posts on X", "url": x_search_url(query), "type": "social"},
        {"label": "Open event location", "url": google_maps_url(event.lat, event.lon), "type": "map"},
    ]
    official_label = "Official source"
    if event.source == "National Highways":
        official_label = "National Highways live incidents"
        links.insert(0, {
            "label": "Find specific National Highways alert",
            "url": national_highways_alert_search_url(event.news_query or query),
            "type": "official_search",
        })
    return {
        "event_id": event.id,
        "query": query,
        "articles": gdelt_articles(event),
        "links": links,
        "official_url": event.official_url,
        "official_label": official_label,
    }
