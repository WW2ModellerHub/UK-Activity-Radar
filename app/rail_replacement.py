"""Where rail replacement buses stop at each station.

From the Rail Data Marketplace "Rail Replacement Location Data" product (DfT
NaPTAN rail replacement stops): about 3,600 stops, each with its station's
CRS code, its position and a description such as "Rail replacement buses
depart from public bus stop G outside the station towards Hertford North".

The API returns a short-lived download link; the file (~3.5 MB) is fetched at
most once a day and kept, reduced to what the site shows, in DATA_DIR.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

API_URL = ("https://api1.raildata.org.uk/1018-rail-replacement-location-data-api2_0"
           "/v1/rail-replacement/stops/download?format=json")
REFRESH_S = 24 * 3600


def parse_stops(payload: dict) -> dict[str, list[dict]]:
    """The NaPTAN-style file -> {CRS: [stop, ...]} with only the fields shown."""
    root = (payload or {}).get("RailReplacementLocation") or payload or {}
    by_station: dict[str, list[dict]] = {}
    for point in root.get("StopPoints") or []:
        if not isinstance(point, dict):
            continue
        replacement = ((point.get("StopClassification") or {}).get("RailReplacement") or {})
        crs = str(replacement.get("CrsRef") or "").strip().upper()
        where = (((point.get("Place") or {}).get("Location") or {}).get("Translation") or {})
        lat, lon = where.get("Latitude"), where.get("Longitude")
        if len(crs) != 3 or not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
            continue
        described = point.get("Descriptor") or {}
        by_station.setdefault(crs, []).append({
            "name": str(described.get("CommonName") or "").strip(),
            "street": str(described.get("Street") or "").strip() or None,
            # The source has the odd missing space ("Hertford Northand"); left as published.
            "description": str(described.get("Description") or "").strip() or None,
            "lat": round(float(lat), 6), "lon": round(float(lon), 6),
            "atco": str(point.get("AtcoCode") or "").strip() or None,
        })
    return by_station


class RailReplacementStops:
    def __init__(self, data_dir: Path, api_key: str, session=None):
        self._path = Path(data_dir) / "rail_replacement_stops.json"
        self._api_key = str(api_key or "").strip()
        self._session = session
        self._lock = threading.Lock()
        self._stops: dict[str, list[dict]] = {}
        self._loaded_at = 0.0
        self._load_saved()

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    def _load_saved(self) -> None:
        try:
            saved = json.loads(self._path.read_text(encoding="utf-8"))
            with self._lock:
                self._stops = saved.get("stops") or {}
                self._loaded_at = float(saved.get("fetched_at") or 0)
        except (OSError, ValueError, TypeError, AttributeError):
            pass

    def due(self) -> bool:
        return self.configured and time.time() - self._loaded_at > REFRESH_S

    def refresh(self) -> int:
        """Fetch the current file; keeps the last good copy on any failure."""
        if not self.configured:
            return 0
        import requests
        session = self._session or requests
        link = session.get(API_URL, headers={"x-apikey": self._api_key}, timeout=30)
        link.raise_for_status()
        url = (link.json() or {}).get("downloadUrl")
        if not url:
            raise ValueError("no downloadUrl in the rail replacement response")
        response = session.get(url, timeout=120)
        response.raise_for_status()
        stops = parse_stops(response.json())
        if not stops:
            raise ValueError("the rail replacement file had no usable stops")
        fetched_at = time.time()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"fetched_at": fetched_at, "stops": stops}), encoding="utf-8")
        temporary.replace(self._path)
        with self._lock:
            self._stops, self._loaded_at = stops, fetched_at
        return sum(len(rows) for rows in stops.values())

    def at(self, crs: str) -> list[dict]:
        with self._lock:
            return [dict(row) for row in self._stops.get(str(crs or "").strip().upper(), [])]
