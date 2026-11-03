from __future__ import annotations

import math
import threading
import time
from typing import Any
from urllib.parse import urlparse

import requests


class TrafficCameraService:
    """Small cached adapter for TfL's public JamCam place feed.

    TfL publishes current camera stills and short videos as open data.  The
    adapter deliberately accepts media only from the documented JamCam host so
    an unexpected upstream value can never turn the API into an arbitrary
    image/video proxy.
    """

    def __init__(
        self,
        api_base: str = "https://api.tfl.gov.uk",
        app_key: str = "",
        *,
        cache_seconds: int = 180,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.api_base = api_base.rstrip("/")
        self.app_key = app_key.strip()
        self.cache_seconds = max(30, int(cache_seconds))
        self.timeout_seconds = max(1.0, float(timeout_seconds))
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "UKActivityRadar/1.5.0 (TfL JamCam consumer)"})
        self._lock = threading.RLock()
        self._expires = 0.0
        self._cameras: list[dict[str, Any]] = []

    @staticmethod
    def _media_url(value: object) -> str | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = urlparse(text)
        except ValueError:
            return None
        host = (parsed.hostname or "").casefold()
        path = parsed.path.casefold()
        if parsed.scheme != "https":
            return None
        if host == "jamcams.tfl.gov.uk":
            return text
        if host == "s3-eu-west-1.amazonaws.com" and path.startswith("/jamcams.tfl.gov.uk/"):
            return text
        return None

    @staticmethod
    def _properties(row: dict[str, Any]) -> dict[str, str]:
        out: dict[str, str] = {}
        for item in row.get("additionalProperties") or []:
            if not isinstance(item, dict):
                continue
            key = str(item.get("key") or "").strip()
            if key:
                out[key] = str(item.get("value") or "").strip()
        return out

    @classmethod
    def _normalise(cls, row: object) -> dict[str, Any] | None:
        if not isinstance(row, dict):
            return None
        try:
            lat = float(row.get("lat"))
            lon = float(row.get("lon"))
        except (TypeError, ValueError):
            return None
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return None
        props = cls._properties(row)
        camera_id = str(row.get("id") or "").strip()
        image_url = cls._media_url(props.get("imageUrl"))
        video_url = cls._media_url(props.get("videoUrl"))
        if not camera_id or not (image_url or video_url):
            return None
        return {
            "id": f"tfl:{camera_id}",
            "type": "camera",
            "name": str(row.get("commonName") or "TfL traffic camera").strip(),
            "lat": lat,
            "lon": lon,
            "view": props.get("view") or None,
            "available": props.get("available", "true").casefold() == "true",
            "image_url": image_url,
            "video_url": video_url,
            "source": "Transport for London JamCam",
            "source_url": "https://tfl.gov.uk/traffic/status/",
            "refresh_seconds": 180,
        }

    def _fetch(self) -> tuple[list[dict[str, Any]], bool]:
        now = time.time()
        with self._lock:
            if self._cameras and now < self._expires:
                return list(self._cameras), False

        params = {"app_key": self.app_key} if self.app_key else None
        try:
            response = self._session.get(
                f"{self.api_base}/Place/Type/JamCam",
                params=params,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
            rows = payload if isinstance(payload, list) else []
            cameras = [camera for row in rows if (camera := self._normalise(row)) is not None]
            if not cameras:
                raise ValueError("TfL returned no usable JamCam records")
        except Exception:
            with self._lock:
                if self._cameras:
                    return list(self._cameras), True
            raise

        with self._lock:
            self._cameras = cameras
            self._expires = now + self.cache_seconds
        return list(cameras), False

    @staticmethod
    def _distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        radius_km = 6371.0088
        p1 = math.radians(lat1)
        p2 = math.radians(lat2)
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2) ** 2
        return radius_km * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    def for_bounds(
        self,
        *,
        west: float,
        south: float,
        east: float,
        north: float,
        limit: int = 250,
    ) -> dict[str, Any]:
        cameras, stale = self._fetch()
        bounded = [
            item for item in cameras
            if south <= float(item["lat"]) <= north and west <= float(item["lon"]) <= east
        ][: max(1, min(int(limit), 500))]
        return {
            "ok": True,
            "source": "Transport for London Open Data",
            "stale": stale,
            "cameras": bounded,
            "note": (
                "TfL JamCam images normally refresh every few minutes."
                if bounded else
                "No directly licensed camera feed is configured for this view. TfL is embedded now; Scotland and Wales require registered feeds, and National Highways images require a reuse licence."
            ),
        }

    def nearest(self, *, lat: float, lon: float, max_km: float = 25.0) -> dict[str, Any]:
        cameras, stale = self._fetch()
        ranked = sorted(
            ((self._distance_km(lat, lon, float(item["lat"]), float(item["lon"])), item) for item in cameras),
            key=lambda row: row[0],
        )
        if not ranked or ranked[0][0] > max_km:
            return {
                "ok": True,
                "source": "Transport for London Open Data",
                "stale": stale,
                "camera": None,
                "note": f"No licensed mapped traffic camera was found within {max_km:g} km. TfL is embedded now; wider UK camera images need the relevant registered or licensed provider feed.",
            }
        distance, camera = ranked[0]
        found = dict(camera)
        found["distance_km"] = round(distance, 2)
        return {
            "ok": True,
            "source": "Transport for London Open Data",
            "stale": stale,
            "camera": found,
        }
