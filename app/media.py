from __future__ import annotations

import html
import re
import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import requests

from .models import Event, Vehicle


@dataclass
class _CacheItem:
    expires: float
    value: dict[str, Any]


_BAD_TITLE_WORDS = {
    "wreck", "accident", "crash", "disaster", "montparnasse", "newspaper", "poster",
    "map", "diagram", "icon", "logo", "coat of arms", "drawing", "painting", "stamp",
    "advert", "advertisement", "timetable", "ticket", "brochure", "magazine", "book cover",
}

_GENERIC_STOP_WORDS = {
    "the", "and", "for", "with", "from", "train", "bus", "ship", "vessel", "helicopter",
    "aircraft", "railway", "service", "route", "united", "kingdom", "live",
}


class WikimediaLookup:
    """Conservative free/no-key Wikimedia Commons image lookup.

    The old matcher accepted the first vaguely relevant Commons result, which is
    how a live TD train could end up with an 1895 railway-crash photograph. This
    version prefers returning *no automatic image* to showing a misleading one.
    It still returns a Commons search URL so the user can browse manually.
    """

    def __init__(self, api_url: str, enabled: bool = True) -> None:
        self.api_url = api_url
        self.enabled = enabled
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "UKActivityRadar/1.2.1 (Wikimedia Commons consumer)"})
        self._cache: dict[str, _CacheItem] = {}
        self._lock = threading.RLock()

    @staticmethod
    def query_for(activity: Vehicle | Event) -> str:
        if isinstance(activity, Event):
            if activity.type == "rescue":
                return " ".join(x for x in [activity.name, "RNLI lifeboat"] if x)
            # Event imagery is often historic/generic and can be actively
            # misleading, so only rescue events are auto-matched.
            return ""

        if activity.type == "train":
            # Never use transient headcodes or signalling locations as an image
            # query. Without a real unit/vehicle identity an operator-level train
            # photo would look authoritative while possibly being the wrong stock.
            if not (activity.vehicle_ref or "").strip():
                return ""
            return " ".join(x for x in [activity.operator, activity.vehicle_ref, "train"] if x)
        if activity.type == "bus":
            if not (activity.vehicle_ref or "").strip():
                return ""
            return " ".join(x for x in [activity.operator, activity.vehicle_ref, "bus"] if x)
        if activity.type == "vessel":
            return " ".join(x for x in [activity.name, activity.vehicle_ref, "ship"] if x)
        if activity.type == "sar":
            return " ".join(x for x in [activity.vehicle_ref, activity.name, "helicopter"] if x)
        return ""

    @staticmethod
    def _plain(value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, dict):
            value = value.get("value") or value.get("Value")
        if value is None:
            return None
        text = html.unescape(re.sub(r"<[^>]+>", " ", str(value)))
        text = re.sub(r"\s+", " ", text).strip()
        return text or None

    @staticmethod
    def _tokens(value: str) -> set[str]:
        return {
            t for t in re.findall(r"[a-z0-9]{3,}", value.casefold())
            if t not in _GENERIC_STOP_WORDS
        }

    def _candidate_score(self, query: str, title: str, info: dict[str, Any], activity: Vehicle | Event) -> float:
        lower = title.casefold()
        if any(word in lower for word in _BAD_TITLE_WORDS):
            return -100.0

        meta = info.get("extmetadata") or {}
        description = " ".join(filter(None, [
            title,
            self._plain(meta.get("ImageDescription")),
            self._plain(meta.get("ObjectName")),
            self._plain(meta.get("Categories")),
        ]))
        hay = description.casefold()
        q_tokens = self._tokens(query)
        overlap = len(q_tokens & self._tokens(description))
        score = overlap * 4.0

        # Strong identifiers matter much more than generic words.
        identifiers = []
        if isinstance(activity, Vehicle):
            identifiers.extend([activity.vehicle_ref, activity.operator, activity.name])
        else:
            identifiers.append(activity.name)
        for raw in identifiers:
            raw = (raw or "").strip()
            if len(raw) >= 4 and raw.casefold() in hay:
                score += 10.0

        if isinstance(activity, Vehicle):
            if activity.type == "vessel" and any(x in hay for x in ("ship", "vessel", "boat")):
                score += 2.0
            elif activity.type == "sar" and any(x in hay for x in ("helicopter", "coastguard", "rescue")):
                score += 2.0
            elif activity.type == "bus" and "bus" in hay:
                score += 2.0
            elif activity.type == "train" and any(x in hay for x in ("train", "railway", "locomotive", "multiple unit")):
                score += 2.0

        return score

    def lookup(self, activity: Vehicle | Event) -> dict[str, Any]:
        query = self.query_for(activity).strip()
        if not self.enabled or not query:
            fallback_query = " ".join(x for x in [getattr(activity, "name", None), getattr(activity, "operator", None), getattr(activity, "type", None)] if x).strip()
            return {
                "ok": False,
                "query": query or fallback_query,
                "search_url": f"https://commons.wikimedia.org/w/index.php?search={quote(fallback_query)}" if fallback_query else None,
                "reason": "insufficient_identity" if self.enabled else "disabled",
            }

        key = f"{getattr(activity, 'id', '')}|{query}".casefold()
        with self._lock:
            hit = self._cache.get(key)
            if hit and hit.expires > time.time():
                return hit.value

        params = {
            "action": "query",
            "generator": "search",
            "gsrsearch": query,
            "gsrnamespace": 6,
            "gsrlimit": 15,
            "prop": "imageinfo",
            "iiprop": "url|extmetadata",
            "iiurlwidth": 720,
            "format": "json",
            "formatversion": 2,
        }
        response = self._session.get(self.api_url, params=params, timeout=12)
        response.raise_for_status()
        pages = ((response.json().get("query") or {}).get("pages") or [])

        ranked: list[tuple[float, dict[str, Any], dict[str, Any], str]] = []
        for page in pages:
            infos = page.get("imageinfo") or []
            if not infos:
                continue
            info = infos[0]
            thumb = info.get("thumburl") or info.get("url")
            if not thumb:
                continue
            title = str(page.get("title") or "").replace("File:", "").strip()
            ranked.append((self._candidate_score(query, title, info, activity), page, info, title))

        ranked.sort(key=lambda row: row[0], reverse=True)
        chosen = ranked[0] if ranked and ranked[0][0] >= 8.0 else None

        if chosen is None:
            value = {
                "ok": False,
                "query": query,
                "search_url": f"https://commons.wikimedia.org/w/index.php?search={quote(query)}",
                "reason": "no_confident_match",
            }
        else:
            score, page, info, title = chosen
            meta = info.get("extmetadata") or {}
            page_id = page.get("pageid")
            page_url = f"https://commons.wikimedia.org/?curid={page_id}" if page_id else info.get("descriptionurl")
            value = {
                "ok": True,
                "query": query,
                "confidence": round(score, 1),
                "title": title,
                "thumbnail_url": info.get("thumburl") or info.get("url"),
                "original_url": info.get("url"),
                "page_url": page_url,
                "artist": self._plain(meta.get("Artist")),
                "credit": self._plain(meta.get("Credit")),
                "license": self._plain(meta.get("LicenseShortName") or meta.get("UsageTerms")),
                "license_url": self._plain(meta.get("LicenseUrl")),
                "search_url": f"https://commons.wikimedia.org/w/index.php?search={quote(query)}",
                "source": "Wikimedia Commons",
            }

        with self._lock:
            self._cache[key] = _CacheItem(time.time() + 6 * 3600, value)
        return value
