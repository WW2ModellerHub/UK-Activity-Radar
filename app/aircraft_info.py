from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import requests


@dataclass
class _CacheEntry:
    expires_at: float
    payload: dict


class AircraftInfoService:
    """Low-rate, click-driven aircraft identity/photo enrichment.

    ADSB.lol already gives the live position plus, where available, registration
    and ICAO type designator. Airport-Data.com's public aircraft APIs are used
    only when a user opens an aircraft card, and responses are cached for 24h.
    A photo is only accepted when it is tied to the same Mode-S hex/registration;
    otherwise the UI shows identity data without a possibly-wrong image.
    """

    INFO_URL = "https://airport-data.com/api/ac_info.json"
    PHOTO_URL = "https://airport-data.com/api/ac_thumb.json"

    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "UKActivityRadar/1.2.1 aircraft-info"})
        self._lock = threading.RLock()
        self._cache: dict[str, _CacheEntry] = {}

    @staticmethod
    def _hex(value: str | None) -> str:
        raw = "".join(ch for ch in str(value or "").upper() if ch in "0123456789ABCDEF")
        return raw if len(raw) == 6 else ""

    @staticmethod
    def _reg(value: str | None) -> str:
        return str(value or "").strip().upper()

    def lookup(self, mode_s: str | None, registration: str | None = None) -> dict:
        hex_id = self._hex(mode_s)
        reg_hint = self._reg(registration)
        if not hex_id:
            return {"ok": False, "reason": "No valid Mode-S hex is available for this aircraft."}

        cache_key = f"{hex_id}:{reg_hint}"
        now = time.time()
        with self._lock:
            cached = self._cache.get(cache_key)
            if cached and cached.expires_at > now:
                return dict(cached.payload)

        info: dict = {}
        photo: dict = {}
        errors: list[str] = []

        try:
            r = self._session.get(self.INFO_URL, params={"m": hex_id}, timeout=(4, 10))
            if r.status_code == 200:
                body = r.json() if r.content else {}
                if isinstance(body, dict) and int(body.get("status") or 200) == 200:
                    info = body
            elif r.status_code not in (404, 429):
                r.raise_for_status()
            elif r.status_code == 429:
                errors.append("Aircraft identity service is rate-limited")
        except Exception as exc:
            errors.append(f"Identity lookup unavailable ({type(exc).__name__})")

        resolved_reg = self._reg(info.get("reg")) or reg_hint
        # A mismatched registration can mean the registration was re-used or the
        # external database is stale. In that case, do not surface its thumbnail.
        reg_matches = not reg_hint or not resolved_reg or resolved_reg == reg_hint
        try:
            params = {"m": hex_id, "n": 1}
            if reg_hint:
                params["r"] = reg_hint
            r = self._session.get(self.PHOTO_URL, params=params, timeout=(4, 10))
            if r.status_code == 200:
                body = r.json() if r.content else {}
                rows = body.get("data") if isinstance(body, dict) else None
                if reg_matches and isinstance(rows, list) and rows:
                    candidate = rows[0] if isinstance(rows[0], dict) else {}
                    if candidate.get("image") and candidate.get("link"):
                        photo = {
                            "thumbnail_url": candidate.get("image"),
                            "page_url": candidate.get("link"),
                            "photographer": candidate.get("photographer"),
                        }
            elif r.status_code == 429:
                errors.append("Aircraft photo service is rate-limited")
            elif r.status_code != 404:
                r.raise_for_status()
        except Exception as exc:
            errors.append(f"Photo lookup unavailable ({type(exc).__name__})")

        payload = {
            "ok": bool(info or photo),
            "mode_s": hex_id,
            "registration": resolved_reg or reg_hint or None,
            "model": info.get("model") or None,
            "construction_number": info.get("cn") or None,
            "country": info.get("country") or None,
            "profile_url": info.get("link") or (f"https://airport-data.com/aircraft/{resolved_reg}" if resolved_reg else None),
            "photo": photo or None,
            "source": "Airport-Data.com",
            "note": "; ".join(errors) if errors else None,
        }
        with self._lock:
            self._cache[cache_key] = _CacheEntry(now + 24 * 3600, payload)
            if len(self._cache) > 300:
                oldest = min(self._cache, key=lambda k: self._cache[k].expires_at)
                self._cache.pop(oldest, None)
        return dict(payload)
