"""Rail Data Marketplace experience-API client.

The marketplace gives each subscribed product a separate ``x-apikey``.  This
module deliberately keeps those keys in one server-only boundary, allow-lists
every upstream path, caches successful replies and can serve the last good
reply during a short provider outage.
"""
from __future__ import annotations

from dataclasses import dataclass
import copy
import re
import threading
import time
from typing import Any, Mapping

import requests


@dataclass(frozen=True)
class Product:
    base_url: str
    api_key: str


class RailDataMarketplace:
    BASES = {
        "stations": "https://api1.raildata.org.uk/1010-nationalrail-knowledgebase-stations-feed-_json_---production5_0",
        "query": "https://api1.raildata.org.uk/1010-query-services-and-service-details1_0/LDBSVWS/api/20220120",
        "hsp": "https://api1.raildata.org.uk/1010-historical-service-performance-_hsp_1_1",
        "disruptions": "https://api1.raildata.org.uk/1010-disruptions-experience-api-11_1",
        "performance_reference": "https://api1.raildata.org.uk/1033-realtime-performance-data-experience-api---reference-datav4_0_1",
        "performance": "https://api1.raildata.org.uk/1033-realtime-performance-data-experience-api---performance-datav4_0",
        "reference": "https://api1.raildata.org.uk/1010-reference-data1_0/LDBSVWS/api/ref/20211101",
        "rail_replacement": "https://api1.raildata.org.uk/1018-rail-replacement-location-data-api2_0/v1/rail-replacement",
    }
    PATHS = {
        "stations": (r"/stations", r"/stations/[A-Z0-9]{3}", r"/stations/tocs/[A-Z0-9]{2,4}"),
        "query": (r"/QueryServices/[A-Z0-9]+/(?:\d{8}|\d{4}-\d{2}-\d{2})", r"/GetServiceDetailsByRID/[A-Za-z0-9_-]+"),
        "hsp": (r"/serviceDetails", r"/serviceMetrics"),
        "disruptions": (
            r"/stations/disruptions/incidents", r"/stations/disruptions/stationMessages",
            r"/tocs/[A-Z0-9]+/serviceIndicators", r"/tocs/serviceIndicators",
            r"/disruptions/incidents/[^/]+", r"/disruptions/incidents/search",
            r"/bulletins/search", r"/bulletins/getLatest",
        ),
        "performance_reference": (
            r"/referenceData/rag", r"/referenceData/rag/[^/]+", r"/referenceData/stanox",
            r"/referenceData/operator", r"/referenceData/stanoxGroup",
            r"/referenceData/stanoxGroup/[^/]+/listMembers", r"/referenceData/route",
        ),
        "performance": (
            r"/performanceData/[^/]+", r"/performanceData/[^/]+/sector/[^/]+",
            r"/performanceData/[^/]+/toc/[^/]+", r"/performanceData/[^/]+/serviceGroup/[^/]+",
            r"/performanceData/RTOTM/route", r"/performanceData/RTOTM/route/[^/]+",
            r"/performanceData/RTOTM/toc/[^/]+/stanox",
            r"/performanceData/RTOTM/stanoxGroup/[^/]+",
        ),
        "reference": (
            r"/GetLoadingCategoryData/[^/]+", r"/GetTOCList/[^/]+", r"/GetStationList/[^/]+",
            r"/GetReasonCodeList/[^/]+", r"/GetReasonCode/[^/]+", r"/GetSourceInstanceNames/[^/]+",
        ),
        "rail_replacement": (r"/stops/download", r"/last-updated"),
    }

    def __init__(self, settings, *, session: requests.Session | None = None) -> None:
        self.timeout = float(settings.rdm_timeout_seconds)
        self.products = {}
        for name, base in self.BASES.items():
            attribute = "raildata_replacement_api_key" if name == "rail_replacement" else f"rdm_{name}_api_key"
            self.products[name] = Product(base, str(getattr(settings, attribute, "") or "").strip())
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": "UK-Activity-Radar/1.5 (+https://activityradar.uk)"})
        self._cache: dict[tuple, tuple[float, Any]] = {}
        self._lock = threading.RLock()

    def status(self) -> dict[str, bool]:
        return {name: bool(product.api_key) for name, product in self.products.items()}

    def _path_allowed(self, product: str, path: str) -> bool:
        return bool(path.startswith("/") and any(re.fullmatch(pattern, path) for pattern in self.PATHS.get(product, ())))

    @staticmethod
    def _cache_key(product: str, path: str, params: Mapping[str, Any] | None) -> tuple:
        cleaned = tuple(sorted((str(k), str(v)) for k, v in (params or {}).items()))
        return product, path, cleaned

    def get(
        self, product: str, path: str, *, params: Mapping[str, Any] | None = None,
        ttl: int = 60, stale_ttl: int = 900,
    ) -> Any:
        definition = self.products.get(product)
        if definition is None or not self._path_allowed(product, path):
            raise ValueError("Unsupported Rail Data Marketplace operation")
        if not definition.api_key:
            raise RuntimeError(f"Rail Data Marketplace {product} subscription is not configured")
        key = self._cache_key(product, path, params)
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(key)
            if cached and now - cached[0] <= ttl:
                return copy.deepcopy(cached[1])
        try:
            response = self.session.get(
                definition.base_url + path,
                params=dict(params or {}),
                headers={"x-apikey": definition.api_key, "Accept": "application/json"},
                timeout=(min(4.0, self.timeout), self.timeout),
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError):
            with self._lock:
                cached = self._cache.get(key)
                if cached and now - cached[0] <= stale_ttl:
                    return copy.deepcopy(cached[1])
            raise
        with self._lock:
            self._cache[key] = (now, copy.deepcopy(payload))
        return payload

    def post(
        self, product: str, path: str, payload: Mapping[str, Any], *,
        ttl: int = 60, stale_ttl: int = 900,
    ) -> Any:
        """POST to one allow-listed product operation without exposing its key."""
        definition = self.products.get(product)
        if definition is None or not self._path_allowed(product, path):
            raise ValueError("Unsupported Rail Data Marketplace operation")
        if not definition.api_key:
            raise RuntimeError(f"Rail Data Marketplace {product} subscription is not configured")
        body = copy.deepcopy(dict(payload or {}))
        key = self._cache_key(product, path, {"body": repr(sorted(body.items()))})
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(key)
            if cached and now - cached[0] <= ttl:
                return copy.deepcopy(cached[1])
        try:
            response = self.session.post(
                definition.base_url + path, json=body,
                headers={"x-apikey": definition.api_key, "Accept": "application/json"},
                timeout=(min(4.0, self.timeout), self.timeout),
            )
            response.raise_for_status()
            result = response.json()
        except (requests.RequestException, ValueError):
            with self._lock:
                cached = self._cache.get(key)
                if cached and now - cached[0] <= stale_ttl:
                    return copy.deepcopy(cached[1])
            raise
        with self._lock:
            self._cache[key] = (now, copy.deepcopy(result))
        return result

    def query_services(self, service_id: str, service_date: str) -> Any:
        identifier = str(service_id or "").strip().upper()
        day = str(service_date or "").strip()
        if not re.fullmatch(r"[A-Z0-9]+", identifier) or not re.fullmatch(r"(?:\d{8}|\d{4}-\d{2}-\d{2})", day):
            raise ValueError("Invalid service identifier or date")
        return self.get("query", f"/QueryServices/{identifier}/{day}", ttl=60, stale_ttl=600)

    def service_by_rid(self, rid: str) -> Any:
        identifier = str(rid or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]+", identifier):
            raise ValueError("Invalid RID")
        return self.get("query", f"/GetServiceDetailsByRID/{identifier}", ttl=60, stale_ttl=600)

    def historical_service(self, payload: Mapping[str, Any], *, metrics: bool = False) -> Any:
        if not isinstance(payload, Mapping) or not payload or len(payload) > 30:
            raise ValueError("Historical service request must be a non-empty object")
        return self.post("hsp", "/serviceMetrics" if metrics else "/serviceDetails", payload,
                         ttl=300, stale_ttl=3600)

    def performance_reference(self, dataset: str) -> Any:
        paths = {
            "rag": "/referenceData/rag",
            "stanox": "/referenceData/stanox",
            "operators": "/referenceData/operator",
            "stanox-groups": "/referenceData/stanoxGroup",
            "routes": "/referenceData/route",
        }
        path = paths.get(str(dataset or "").strip().lower())
        if not path:
            raise ValueError("Unknown performance reference dataset")
        return self.get("performance_reference", path, ttl=86400, stale_ttl=604800)

    def station(self, crs: str) -> Any:
        code = str(crs or "").strip().upper()
        if not re.fullmatch(r"[A-Z0-9]{3}", code):
            raise ValueError("Invalid station CRS")
        return self.get("stations", f"/stations/{code}", ttl=3600, stale_ttl=86400)

    def service_indicators(self) -> Any:
        """Each train company's status from National Rail ("Good service", or what is wrong)."""
        return self.get("disruptions", "/tocs/serviceIndicators", ttl=120, stale_ttl=1800)

    def incident(self, incident_id: str) -> Any:
        code = str(incident_id or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9]{8,64}", code):
            raise ValueError("Invalid incident id")
        return self.get("disruptions", f"/disruptions/incidents/{code}", ttl=600, stale_ttl=3600)

    def station_disruptions(self, crs: str) -> dict[str, Any]:
        code = str(crs or "").strip().upper()
        if not re.fullmatch(r"[A-Z0-9]{3}", code):
            raise ValueError("Invalid station CRS")
        # The experience API filters the national response by CRS query. Keep
        # the unfiltered feed away from browsers and cache this short-lived view.
        incidents = self.get(
            "disruptions", "/stations/disruptions/incidents",
            params={"crsCode": code}, ttl=45, stale_ttl=600,
        )
        # Station messages are a second, optional call: a refusal there must
        # not hide the incidents.
        try:
            messages = self.get(
                "disruptions", "/stations/disruptions/stationMessages",
                params={"crsCode": code}, ttl=45, stale_ttl=600,
            )
        except requests.RequestException:
            messages = None
        return {"incidents": incidents, "station_messages": messages}
