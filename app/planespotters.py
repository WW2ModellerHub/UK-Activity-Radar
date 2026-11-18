from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from typing import Any

import requests


_ICAO_HEX = re.compile(r"^[0-9a-f]{6}$", re.IGNORECASE)


@dataclass
class _CachedPhoto:
    expires: float
    payload: dict[str, Any]


class PlanespottersPhotoLookup:
    """Small, cached client for Planespotters' public linked-thumbnail API."""

    API_ROOT = "https://api.planespotters.net/pub/photos/hex"
    TERMS_URL = "https://www.planespotters.net/legal/termsofuse"

    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": "UKActivityRadar/1.5 (+https://uk-activity-radar.onrender.com/about)",
            "Accept": "application/json",
        })
        self._cache: dict[str, _CachedPhoto] = {}
        self._lock = threading.RLock()

    def lookup(self, icao_hex: str | None) -> dict[str, Any]:
        key = str(icao_hex or "").strip().lower()
        if not _ICAO_HEX.fullmatch(key):
            return {"ok": False, "reason": "invalid_icao_hex"}
        now = time.time()
        with self._lock:
            cached = self._cache.get(key)
            if cached and cached.expires > now:
                return cached.payload

        response = self._session.get(f"{self.API_ROOT}/{key.upper()}", timeout=5)
        response.raise_for_status()
        rows = response.json().get("photos") or []
        row = rows[0] if rows and isinstance(rows[0], dict) else None
        thumbnail = (row or {}).get("thumbnail_large") or (row or {}).get("thumbnail") or {}
        source = str(thumbnail.get("src") or "")
        page = str((row or {}).get("link") or "")
        photographer = str((row or {}).get("photographer") or "").strip()
        size = thumbnail.get("size") if isinstance(thumbnail, dict) else {}
        if not (source.startswith("https://") and page.startswith("https://www.planespotters.net/")):
            payload = {"ok": False, "reason": "no_photo"}
        else:
            payload = {
                "ok": True,
                "thumbnail_url": source,
                "thumbnail_width": int(size.get("width") or 0) or None,
                "thumbnail_height": int(size.get("height") or 0) or None,
                "page_url": page,
                "license_url": self.TERMS_URL,
                "title": "Aircraft photo",
                "artist": f"© {photographer}" if photographer else "© Planespotters contributor",
                "license": "linked thumbnail",
                "source": "Planespotters.net",
                "match_kind": "exact_airframe",
            }
        with self._lock:
            self._cache[key] = _CachedPhoto(now + (24 * 3600 if payload["ok"] else 3600), payload)
            if len(self._cache) > 1000:
                oldest = min(self._cache, key=lambda item: self._cache[item].expires)
                self._cache.pop(oldest, None)
        return payload
