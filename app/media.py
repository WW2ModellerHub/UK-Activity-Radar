from __future__ import annotations

import html
import re
import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import requests

from .media_reference import mentions_all, reference_for, shows_the_outside
from .models import Event, Vehicle

# Specials whose stock is unknown: an operator's everyday train would be a
# misleading picture of a steam railtour.
_NO_REFERENCE_TRAIN_SUBTYPES = {"steam", "railtour-special", "heritage", "royal"}


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

_GENERIC_VESSEL_NAMES = {
    "warship", "french warship", "naval vessel", "military vessel", "ship", "vessel",
    "patrol vessel", "specialist vessel", "unknown vessel",
}


def _commercial_commons_licence(meta: dict[str, Any]) -> tuple[str, str | None] | None:
    """Return a display licence only when commercial reuse is explicit.

    Wikimedia hosts files under many different terms.  A file being on
    Commons is not, by itself, enough evidence for a paid product, so missing,
    non-commercial and no-derivatives metadata all fail closed.
    """
    raw = WikimediaLookup._plain(meta.get("LicenseShortName") or meta.get("UsageTerms"))
    if not raw:
        return None
    normalised = re.sub(r"\s+", " ", raw).strip().casefold()
    if any(marker in normalised for marker in ("noncommercial", "non-commercial", "cc nc", "cc-nc", "by-nc", "no derivatives", "no-derivatives", "cc nd", "cc-nd", "by-nd")):
        return None
    allowed = (
        "public domain" in normalised
        or normalised == "pd"
        or "cc0" in normalised
        or "creative commons attribution" in normalised
        or normalised.startswith("cc by")
        or normalised.startswith("cc-by")
    )
    if not allowed:
        return None
    return raw, WikimediaLookup._plain(meta.get("LicenseUrl"))


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
        self._session.headers.update({"User-Agent": "UKActivityRadar/1.4.0 (Wikimedia Commons consumer)"})
        self._cache: dict[str, _CacheItem] = {}
        self._lock = threading.RLock()

    @staticmethod
    def query_for(activity: Vehicle | Event) -> str:
        if isinstance(activity, Event):
            if activity.type == "rescue":
                # A photograph of a station or another launch is not evidence
                # of this launch.  Only an exact event-owned media URL should
                # ever be shown for a live RNLI event, and the launch feed does
                # not currently publish one.
                return ""
            # Event imagery is often historic/generic and can be actively
            # misleading, so only rescue events are auto-matched.
            return ""

        if activity.type == "train":
            # Never use transient headcodes or signalling locations as an image
            # query. Without a real unit/vehicle identity an operator-level train
            # photo would look authoritative while possibly being the wrong stock.
            if (activity.vehicle_ref or "").strip():
                return " ".join(x for x in [activity.operator, activity.vehicle_ref, "train"] if x)
            return ""
        if activity.type == "bus":
            if not (activity.vehicle_ref or "").strip():
                return ""
            return " ".join(x for x in [activity.operator, activity.vehicle_ref, "bus"] if x)
        if activity.type == "vessel":
            name = str(activity.name or "").strip()
            # A provider label such as "French warship" is a category, not an
            # identity.  Search by MMSI only in that case; a generic warship
            # photograph must never look like the exact live vessel.
            identity = None if name.casefold() in _GENERIC_VESSEL_NAMES else name
            return " ".join(x for x in [identity, activity.vehicle_ref, "ship"] if x)
        if activity.type in {"sar", "aircraft"}:
            # Prefer the registration because it can identify the exact
            # airframe.  If ADS-B has only resolved a type, a clearly labelled
            # type-reference image is still useful (Apache vs Chinook, etc.)
            # but must never be presented as a photo of this exact aircraft.
            if (activity.vehicle_ref or "").strip():
                return " ".join(x for x in [activity.vehicle_ref, activity.aircraft_model, "aircraft"] if x)
            if (activity.aircraft_model or "").strip():
                return f"{activity.aircraft_model} aircraft"
            return ""
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

        if isinstance(activity, Vehicle) and activity.type == "train" and not (activity.vehicle_ref or "").strip():
            return -100.0

        q_tokens = self._tokens(query)
        overlap = len(q_tokens & self._tokens(description))
        score = overlap * 4.0

        # Strong identifiers matter much more than generic words.
        identifiers = []
        if isinstance(activity, Vehicle):
            if activity.type == "train" and not (activity.vehicle_ref or "").strip():
                # Do not let a live headcode stored in ``name`` turn a subtype
                # reference search into an apparent exact-train match.
                identifiers = []
            else:
                identifiers.extend([activity.vehicle_ref, activity.aircraft_model, activity.aircraft_type_code, activity.operator, activity.name])
        else:
            identifiers.append(activity.name)
        for raw in identifiers:
            raw = (raw or "").strip()
            if len(raw) >= 4 and raw.casefold() in hay:
                score += 10.0

        if isinstance(activity, Vehicle) and activity.type == "vessel":
            name = str(activity.name or "").strip().casefold()
            mmsi = str(activity.vehicle_ref or "").strip().casefold()
            exact_name = bool(name and name not in _GENERIC_VESSEL_NAMES and len(name) >= 5 and name in hay)
            exact_mmsi = bool(mmsi and len(mmsi) >= 7 and mmsi in hay)
            if not (exact_name or exact_mmsi):
                return -100.0

        if isinstance(activity, Vehicle):
            if activity.type == "vessel" and any(x in hay for x in ("ship", "vessel", "boat")):
                score += 2.0
            elif activity.type == "sar" and any(x in hay for x in ("helicopter", "coastguard", "rescue")):
                score += 2.0
            elif activity.type == "aircraft" and any(x in hay for x in ("aircraft", "aeroplane", "fighter", "helicopter")):
                score += 2.0
            elif activity.type == "bus" and "bus" in hay:
                score += 2.0
            elif activity.type == "train" and any(x in hay for x in ("train", "railway", "locomotive", "multiple unit")):
                score += 2.0

        return score

    def lookup(self, activity: Vehicle | Event) -> dict[str, Any]:
        exact = self._lookup_exact(activity)
        if exact.get("ok") or not isinstance(activity, Vehicle) or not self.enabled:
            return exact
        if activity.type == "train" and str(activity.cool_subtype or "") in _NO_REFERENCE_TRAIN_SUBTYPES:
            return exact
        try:
            example = self.reference(activity.type, activity.operator, activity.aircraft_model, activity.name)
        except Exception:
            return exact
        return example if example.get("ok") else exact

    def reference(self, kind: str, operator: str | None = None, model: str | None = None,
                  callsign: str | None = None) -> dict[str, Any]:
        """An example photo of this operator's vehicles or this aircraft type."""
        plan = reference_for(kind, operator, model, callsign)
        if not self.enabled or not plan:
            return {"ok": False, "reason": "no_reference" if self.enabled else "disabled"}
        for step in (plan, plan.get("fallback")):
            if not step:
                continue
            key = f"reference|{step['query']}".casefold()
            with self._lock:
                hit = self._cache.get(key)
                if hit and hit.expires > time.time():
                    value = hit.value
                    if value.get("ok"):
                        return value
                    continue
            value = {"ok": False, "query": step["query"], "reason": "no_confident_match",
                     "search_url": f"https://commons.wikimedia.org/w/index.php?search={quote(step['query'])}"}
            best = None
            for page, info, title in self._search(step["query"], limit=12):
                meta = info.get("extmetadata") or {}
                text = " ".join(filter(None, [title, self._plain(meta.get("ImageDescription")),
                                              self._plain(meta.get("ObjectName")), self._plain(meta.get("Categories"))]))
                if any(word in text.casefold() for word in _BAD_TITLE_WORDS) or not mentions_all(text, step["require"]):
                    continue
                if not shows_the_outside(f"{title} {self._plain(meta.get('ImageDescription')) or ''}"):
                    continue
                # Prefer photos named for exactly this (title words), then wide ones.
                score = len(self._tokens(step["query"]) & self._tokens(title)) * 4
                width, height = info.get("width") or 0, info.get("height") or 0
                if width and height and width >= height:
                    score += 2
                if best is None or score > best[0]:
                    best = (score, page, info, title)
            if best:
                _, page, info, title = best
                value = self._payload(step["query"], 0.0, page, info, title)
                if value.get("ok"):
                    value.update({"match_kind": "type_reference", "reference_label": step["label"]})
            with self._lock:
                self._cache[key] = _CacheItem(time.time() + (24 * 3600 if value.get("ok") else 3 * 3600), value)
            if value.get("ok"):
                return value
        return value

    def _search(self, query: str, limit: int = 8) -> list[tuple[dict[str, Any], dict[str, Any], str]]:
        params = {
            "action": "query", "generator": "search", "gsrsearch": f"{query} filetype:bitmap",
            "gsrnamespace": 6, "gsrlimit": limit, "prop": "imageinfo",
            "iiprop": "url|extmetadata|size", "iiurlwidth": 640, "format": "json", "formatversion": 2,
        }
        response = self._session.get(self.api_url, params=params, timeout=(1.25, 3.0))
        if getattr(response, "status_code", 200) == 429:
            # Commons limits bursts; one short wait usually clears it.
            try:
                wait = min(float(response.headers.get("Retry-After") or 1), 1.5)
            except (TypeError, ValueError):
                wait = 1.0
            time.sleep(max(wait, 0.3))
            response = self._session.get(self.api_url, params=params, timeout=(1.25, 3.0))
        response.raise_for_status()
        out = []
        for page in (response.json().get("query") or {}).get("pages") or []:
            infos = page.get("imageinfo") or []
            if not infos:
                continue
            info = infos[0]
            if _commercial_commons_licence(info.get("extmetadata") or {}) is None:
                continue
            if not (info.get("thumburl") or info.get("url")):
                continue
            out.append((page, info, str(page.get("title") or "").replace("File:", "").strip()))
        return out

    def _payload(self, query: str, score: float, page: dict[str, Any], info: dict[str, Any], title: str) -> dict[str, Any]:
        meta = info.get("extmetadata") or {}
        licence = _commercial_commons_licence(meta)
        if licence is None:
            return {"ok": False, "query": query, "reason": "commercial_licence_unavailable"}
        licence_name, licence_url = licence
        page_id = page.get("pageid")
        return {
            "ok": True,
            "query": query,
            "confidence": round(score, 1),
            "title": title,
            "thumbnail_url": info.get("thumburl") or info.get("url"),
            "original_url": info.get("url"),
            "page_url": f"https://commons.wikimedia.org/?curid={page_id}" if page_id else info.get("descriptionurl"),
            "artist": self._plain(meta.get("Artist")),
            "credit": self._plain(meta.get("Credit")),
            "license": licence_name,
            "license_url": licence_url,
            "search_url": f"https://commons.wikimedia.org/w/index.php?search={quote(query)}",
            "source": "Wikimedia Commons",
        }

    def _lookup_exact(self, activity: Vehicle | Event) -> dict[str, Any]:
        query = self.query_for(activity).strip()
        if not self.enabled or not query:
            suppress_fallback = (
                (isinstance(activity, Event) and activity.type == "rescue")
                or (
                    isinstance(activity, Vehicle)
                    and activity.type == "train"
                    and not (activity.vehicle_ref or "").strip()
                )
            )
            fallback_query = "" if suppress_fallback else " ".join(
                x for x in [getattr(activity, "name", None), getattr(activity, "operator", None), getattr(activity, "type", None)] if x
            ).strip()
            return {
                "ok": False,
                "query": query or fallback_query,
                "search_url": f"https://commons.wikimedia.org/w/index.php?search={quote(fallback_query)}" if fallback_query else None,
                "reason": "event_specific_media_unavailable" if isinstance(activity, Event) and activity.type == "rescue" else ("insufficient_identity" if self.enabled else "disabled"),
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
            # Eight strong candidates are enough for the conservative matcher
            # and noticeably reduce Commons response and metadata time.
            "gsrlimit": 8,
            "prop": "imageinfo",
            "iiprop": "url|extmetadata",
            "iiurlwidth": 520,
            "format": "json",
            "formatversion": 2,
        }
        response = self._session.get(self.api_url, params=params, timeout=(1.25, 3.0))
        response.raise_for_status()
        pages = ((response.json().get("query") or {}).get("pages") or [])

        ranked: list[tuple[float, dict[str, Any], dict[str, Any], str]] = []
        for page in pages:
            infos = page.get("imageinfo") or []
            if not infos:
                continue
            info = infos[0]
            licence = _commercial_commons_licence(info.get("extmetadata") or {})
            if licence is None:
                continue
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
            licence = _commercial_commons_licence(meta)
            if licence is None:  # Defensive: ranked candidates already filter this.
                return {"ok": False, "query": query, "reason": "commercial_licence_unavailable"}
            licence_name, licence_url = licence
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
                "license": licence_name,
                "license_url": licence_url,
                "search_url": f"https://commons.wikimedia.org/w/index.php?search={quote(query)}",
                "source": "Wikimedia Commons",
            }
            if isinstance(activity, Vehicle) and activity.type in {"sar", "aircraft"}:
                registration = str(activity.vehicle_ref or "").strip().casefold()
                metadata_text = " ".join(filter(None, [title, self._plain((info.get("extmetadata") or {}).get("ImageDescription"))])).casefold()
                value["match_kind"] = "exact_airframe" if registration and registration in metadata_text else "type_reference"
            elif isinstance(activity, Vehicle) and activity.type == "train":
                vehicle_ref = str(activity.vehicle_ref or "").strip().casefold()
                metadata_text = " ".join(filter(None, [
                    title,
                    self._plain((info.get("extmetadata") or {}).get("ImageDescription")),
                    self._plain((info.get("extmetadata") or {}).get("ObjectName")),
                ])).casefold()
                value["match_kind"] = "exact_vehicle" if vehicle_ref and vehicle_ref in metadata_text else "type_reference"
            elif isinstance(activity, Vehicle) and activity.type == "vessel":
                value["match_kind"] = "exact_vessel"

        with self._lock:
            self._cache[key] = _CacheItem(time.time() + 6 * 3600, value)
        return value
