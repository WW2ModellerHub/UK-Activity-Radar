from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests

from ..models import Event, SourceStatus


class EnvironmentAgencyFloodCollector:
    """Current Environment Agency flood warnings/alerts (free, no API key)."""

    def __init__(self, settings, state, data_dir: Path):
        self.settings = settings
        self.state = state
        self.cache_path = data_dir / "ea_flood_area_coords.json"
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._coords = self._load_cache()

    def _load_cache(self) -> dict[str, dict]:
        try:
            raw = json.loads(self.cache_path.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else {}
        except Exception:
            return {}

    def _save_cache(self) -> None:
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(json.dumps(self._coords, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="ea-floods", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    @staticmethod
    def _dt(value) -> datetime | None:
        if not value:
            return None
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:
            return None

    @staticmethod
    def _area_code(uri: str) -> str:
        return urlparse(uri).path.rstrip("/").split("/")[-1]

    def _get_area(self, uri: str) -> dict | None:
        code = self._area_code(uri)
        cached = self._coords.get(code)
        if cached and "lat" in cached and "lon" in cached:
            return cached
        try:
            response = requests.get(uri, timeout=12, headers={"User-Agent": "UKActivityRadar/0.7"})
            response.raise_for_status()
            payload = response.json()
            item = payload.get("items") or payload.get("item") or payload
            if isinstance(item, list):
                item = item[0] if item else None
            if not isinstance(item, dict):
                return None
            lat = float(item.get("lat"))
            lon = float(item.get("long"))
            if not (49 <= lat <= 61 and -9 <= lon <= 3):
                return None
            result = {
                "lat": lat,
                "lon": lon,
                "county": item.get("county"),
                "label": item.get("label"),
                "river": item.get("riverOrSea"),
            }
            self._coords[code] = result
            self._save_cache()
            return result
        except Exception:
            return None

    def fetch_once(self) -> list[Event]:
        response = requests.get(
            self.settings.ea_floods_url,
            timeout=20,
            headers={"User-Agent": "UKActivityRadar/0.7", "Accept": "application/json"},
        )
        response.raise_for_status()
        payload = response.json()
        warnings = payload.get("items", []) if isinstance(payload, dict) else []
        events: list[Event] = []
        now = datetime.now(timezone.utc)
        for row in warnings:
            if not isinstance(row, dict):
                continue
            # Severity 4 means warning no longer in force; keep the live map clean.
            try:
                level = int(row.get("severityLevel") or 4)
            except Exception:
                level = 4
            if level >= 4:
                continue
            area_uri = row.get("floodArea")
            if isinstance(area_uri, dict):
                area_uri = area_uri.get("@id") or area_uri.get("id")
            if not area_uri:
                continue
            area = self._get_area(str(area_uri))
            if not area:
                continue
            desc = str(row.get("description") or area.get("label") or "Flood warning").strip()
            severity = str(row.get("severity") or {1:"Severe Flood Warning",2:"Flood Warning",3:"Flood Alert"}.get(level,"Flood warning"))
            message = " ".join(str(row.get("message") or "").split())
            area_code = str(row.get("floodAreaID") or self._area_code(str(area_uri)))
            river = area.get("river")
            county = area.get("county")
            subtitle_parts = [severity]
            if river:
                subtitle_parts.append(str(river))
            if county:
                subtitle_parts.append(str(county))
            events.append(Event(
                id=f"ea-flood:{area_code}",
                type="flood",
                name=desc,
                subtitle=" • ".join(subtitle_parts),
                lat=float(area["lat"]),
                lon=float(area["lon"]),
                category="Flood warning",
                status=severity,
                severity=str(level),
                reason=(message[:900] if message else None),
                start_time=self._dt(row.get("timeRaised")),
                position_method="static",
                position_label="Environment Agency flood-area centroid",
                source="Environment Agency",
                source_id=area_code,
                official_url=str(row.get("@id") or area_uri),
                news_query=f'"{desc}" flood',
                social_query=f'"{desc}" flood',
                interesting=level <= 2,
                last_updated=self._dt(row.get("timeMessageChanged")) or self._dt(row.get("timeSeverityChanged")) or now,
            ))
        return events

    def _run(self) -> None:
        status = SourceStatus(name="ea_floods", enabled=True, connected=False, mode="connecting", message="Loading current Environment Agency flood warnings…")
        self.state.set_source(status)
        while not self._stop.is_set():
            try:
                events = self.fetch_once()
                self.state.replace_events_for_source("Environment Agency", events)
                status = SourceStatus(
                    name="ea_floods", enabled=True, connected=True, mode="live" if events else "ready",
                    message=f"{len(events):,} current flood warnings/alerts mapped • no API key required",
                    last_message_at=datetime.now(timezone.utc), messages_seen=len(events), movements_mapped=len(events),
                )
                self.state.set_source(status)
            except Exception as exc:
                status = SourceStatus(
                    name="ea_floods", enabled=True, connected=False, mode="error",
                    message=f"Flood feed error: {type(exc).__name__}: {exc}",
                )
                self.state.set_source(status)
            self._stop.wait(self.settings.ea_floods_poll_seconds)
