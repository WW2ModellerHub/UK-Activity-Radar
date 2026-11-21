from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import math
import os
import re
import shutil
import threading
import sqlite3
import time

import requests
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import quote, urlsplit
from html import escape as html_escape

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.gzip import GZipMiddleware

from .collectors.demo import run_demo
from .collectors.bods import BODSCollector
from .collectors.bods_sx import BODSSXCollector
from .collectors.environment_agency import EnvironmentAgencyFloodCollector
from .collectors.aisstream import AISStreamCollector
from .collectors.rnli import RNLILaunchCollector
from .collectors.adsb_lol import ADSBLolSARCollector
from .collectors.london_fire import LondonFireCollector
from .collectors.met_office import MetOfficeWarningCollector
from .collectors.national_highways import (
    NationalHighwaysCollector, NationalHighwaysFeaturesCollector,
    NationalHighwaysSpeedCollector, NationalHighwaysVMSCollector,
)
from .collectors.network_rail import TrustCollector
from .collectors.td import TDCollector
from .collectors.vstp import VSTPCollector
from .collectors.stomp_client import MultiplexStompFeed
from .collectors.live_world import (
    BGSEarthquakeCollector, EALevelsCollector, WaveNetCollector, CarbonIntensityCollector,
    UKAirCollector, TfLLiveCollector, GBFSCollector, SpaceWeatherCollector, ISSCollector,
    PowerOutageCollector, GritterCollector,
)
from .config import settings
from .error_safety import public_error, same_origin_websocket
from .network_environment import blocked_proxy_variables
from .models import SourceStatus
from .engagement import build_today_payload, safe_share_payload
from .commute_alerts import (
    MAX_PLACE_ROAD_ALERTS_PER_DAY, CommuteAlertStore, alert_for_activity, journey_alerts, road_key_prefix, journey_bboxes, journey_due, leave_alert,
    normalise_saved_trains, saved_train_alerts, spotter_alert,
    saved_train_status, within_alert_hours,
)
from .coverage import coverage_for_event
from .media import WikimediaLookup
from .planespotters import PlanespottersPhotoLookup
from .search import ActivitySearch
from .product import ProductAnalytics, product_plans
from .traffic_flow import TrafficFlowService
from .smart_places import SmartPlacesService
from .cameras import TrafficCameraService
from .discoveries import (
    DiscoveryClaimError,
    catalogue_payload,
    claim_activity,
    missions_payload,
)
from .reference_data import sync_public_geography, sync_reference_data
from .train_reference import UKTrainReference
from .schedule_store import ScheduleStore, sync_schedule_data
from .request_budget import FixedWindowBudget
from .rail_snapshot import RailPositionSnapshot
from .rail_lines import RailLineLayer
from .flight_routes import FlightRouteLookup
from .rail_sections import delay_hotspots, line_at
from .traffic_incidents import TrafficIncidentService
from .tomtom_routing import TomTomRoutingClient
from .billing import (
    PLUS_FEATURES,
    AccessContext,
    BillingAuthenticationError,
    BillingConfiguration,
    BillingError,
    BillingPaymentPending,
    BillingStateError,
    BillingStore,
    BillingWebhookError,
    billing_configuration_errors,
    StripeBillingService,
)
from .street_manager_open_data import (
    StreetManagerMessageError,
    StreetManagerSnapshotStore,
    verify_and_parse_sns_envelope,
)
from .state import _estimated_train_position, one_per_train, state

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "app" / "static"
LEGAL_DIR = BASE_DIR / "app" / "legal"
DATA_DIR = Path(os.getenv("RADAR_DATA_DIR", str(BASE_DIR / "data"))).expanduser().resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
STATION_FILE = DATA_DIR / "stations.json"
SMART_FILE = DATA_DIR / "smart.json"
SCHEDULE_DB = DATA_DIR / "schedule.sqlite"
PRODUCT_DB = DATA_DIR / "product.sqlite"
STREET_MANAGER_DB = DATA_DIR / "street_manager.sqlite"
BILLING_DB = DATA_DIR / "billing.sqlite"
ALERTS_DB = DATA_DIR / "commute_alerts.sqlite"


def _provision_push_keys() -> None:
    """Give the hosted site its own Web Push keys when none were supplied.

    Only when alerts are switched on and the site is served over HTTPS; the
    key pair lives on the data disk (see ``push_keys``).
    """
    if not settings.push_alerts_enabled or not settings.public_base_url.startswith("https://"):
        return
    if not (settings.vapid_subject.startswith("mailto:") or settings.vapid_subject.startswith("https://")):
        object.__setattr__(settings, "vapid_subject", settings.public_base_url)
    if settings.vapid_public_key and settings.vapid_private_key:
        return
    try:
        from .push_keys import ensure
        public, private = ensure(DATA_DIR)
    except Exception:
        return
    object.__setattr__(settings, "vapid_public_key", public)
    object.__setattr__(settings, "vapid_private_key", private)


_provision_push_keys()
RAIL_POSITION_SNAPSHOT = DATA_DIR / "rail_positions.json"
UK_TRAIN_DATASET = BASE_DIR / "app" / "vendor" / "uk_train_dataset" / "trains.csv"
APP_VERSION = "1.5.0"
ACCESS_COOKIE = "__Host-radar_access"
CHECKOUT_COOKIE = "__Host-radar_checkout"
PREVIEW_COOKIE = "__Host-radar_preview"

schedules = ScheduleStore(SCHEDULE_DB)
train_reference = UKTrainReference(UK_TRAIN_DATASET)
trust_collector: TrustCollector | None = None
td_collector: TDCollector | None = None
vstp_collector: VSTPCollector | None = None
rail_feed_hub: MultiplexStompFeed | None = None
bods_collector: BODSCollector | None = None
bods_sx_collector: BODSSXCollector | None = None
roads_collector: NationalHighwaysCollector | None = None
vms_collector: NationalHighwaysVMSCollector | None = None
speed_collector: NationalHighwaysSpeedCollector | None = None
features_collector: NationalHighwaysFeaturesCollector | None = None
floods_collector: EnvironmentAgencyFloodCollector | None = None
ais_collector: AISStreamCollector | None = None
rnli_collector: RNLILaunchCollector | None = None
adsb_sar_collector: ADSBLolSARCollector | None = None
london_fire_collector: LondonFireCollector | None = None
met_office_collector: MetOfficeWarningCollector | None = None
traffic_flow_service: TrafficFlowService | None = None
traffic_incident_service: TrafficIncidentService | None = None
smart_places: SmartPlacesService | None = None
road_journey_planner = TomTomRoutingClient(
    settings.tomtom_traffic_api_key if settings.tomtom_routing_enabled else "",
    settings.tomtom_traffic_base_url,
    settings.otp_timeout_seconds,
)
street_manager_store: StreetManagerSnapshotStore | None = None
billing_store: BillingStore | None = None
billing_service: StripeBillingService | None = None
commute_alert_store: CommuteAlertStore | None = None
rail_position_snapshot = RailPositionSnapshot(RAIL_POSITION_SNAPSHOT)
rail_line_layer = RailLineLayer(Path(__file__).parent / "assets" / "rail_track_geometry.geojson")
world_collectors: list = []
media_lookup = WikimediaLookup(settings.wikimedia_api_url, enabled=settings.wikimedia_enabled)
planespotters_photos = PlanespottersPhotoLookup()
activity_search = ActivitySearch(state, settings.nominatim_url, enabled=settings.nominatim_enabled, tomtom_api_key=settings.tomtom_traffic_api_key, tomtom_base_url=settings.tomtom_traffic_base_url)
product_analytics = ProductAnalytics(PRODUCT_DB, enabled=settings.product_analytics_enabled)
traffic_cameras = TrafficCameraService(settings.tfl_api_base, settings.tfl_app_key)
viewer_counts: dict[str, int] = {}
viewer_lock = asyncio.Lock()
tile_request_budget = FixedWindowBudget(
    per_client=settings.public_tile_requests_per_minute,
    global_limit=settings.public_tile_global_per_minute,
)
lookup_request_budget = FixedWindowBudget(
    per_client=settings.public_lookup_requests_per_minute,
    global_limit=settings.public_lookup_global_per_minute,
)

# Typing a Radar+ device code: few tries, so codes cannot be guessed.
device_code_budget = FixedWindowBudget(per_client=10, global_limit=120)

billing_configuration = BillingConfiguration(
    enabled=settings.billing_enabled,
    public_base_url=settings.public_base_url,
    stripe_secret_key=settings.stripe_secret_key,
    stripe_webhook_secret=settings.stripe_webhook_secret,
    stripe_plus_price_id=settings.stripe_plus_price_id,
    stripe_plus_yearly_price_id=settings.stripe_plus_yearly_price_id,
    trial_days=settings.radar_plus_trial_days,
    token_pepper=settings.billing_token_pepper,
    cookie_secure=settings.billing_cookie_secure,
)


class ProductEventIn(BaseModel):
    session_id: str
    event_name: str
    feature: str = ""


class DiscoveryClaimIn(BaseModel):
    activity_id: str


class RadarPlusPreviewIn(BaseModel):
    code: str


class JourneyPlanIn(BaseModel):
    origin_name: str = ""
    destination_name: str = ""
    origin_lat: float | None = None
    origin_lon: float | None = None
    destination_lat: float | None = None
    destination_lon: float | None = None
    mode: str = "car"
    arrive_by: bool = False
    wheelchair: bool = False


class CommuteAlertIn(BaseModel):
    device_token: str
    subscription: dict
    preferences: dict


class CommuteAlertRemoveIn(BaseModel):
    device_token: str


def _route_distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat, dlon = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    value = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2) ** 2
    return radius * 2 * math.atan2(math.sqrt(value), math.sqrt(max(0.0, 1 - value)))


def _route_polyline_distance_km(lat: float, lon: float, points: list[tuple[float, float]]) -> float:
    """Shortest local distance to a route segment, not merely a route vertex."""
    lon_scale = 111.32 * math.cos(math.radians(lat))
    lat_scale = 111.32
    best = float("inf")
    for (lat1, lon1), (lat2, lon2) in zip(points, points[1:]):
        ax, ay = (lon1 - lon) * lon_scale, (lat1 - lat) * lat_scale
        bx, by = (lon2 - lon) * lon_scale, (lat2 - lat) * lat_scale
        dx, dy = bx - ax, by - ay
        denominator = dx * dx + dy * dy
        progress = 0.0 if denominator == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / denominator))
        best = min(best, math.hypot(ax + progress * dx, ay + progress * dy))
    return best


def _add_live_route_issues(result: dict) -> dict:
    """Attach current authoritative road events close to each returned route."""
    itineraries = result.get("itineraries") if isinstance(result, dict) else None
    if not isinstance(itineraries, list):
        return result
    for itinerary in itineraries:
        geometry = itinerary.get("geometry") if isinstance(itinerary, dict) else None
        if not isinstance(geometry, list) or len(geometry) < 2:
            continue
        try:
            points = [(float(row["lat"]), float(row["lon"])) for row in geometry]
        except (KeyError, TypeError, ValueError):
            continue
        lats, lons = [p[0] for p in points], [p[1] for p in points]
        candidates = state.all_events(bbox=(min(lons) - 0.06, min(lats) - 0.04, max(lons) + 0.06, max(lats) + 0.04))
        issues = []
        for event in candidates:
            if event.type not in {"road", "incident"}:
                continue
            distance = _route_polyline_distance_km(event.lat, event.lon, points)
            if distance > 3.0:
                continue
            issues.append({
                "id": event.id, "name": event.name, "type": event.type, "category": event.category,
                "road": event.road, "reason": event.reason, "status": event.status,
                "distance_km": round(distance, 1), "lat": event.lat, "lon": event.lon,
                "source": event.source, "last_updated": event.last_updated.isoformat(),
            })
        issues.sort(key=lambda row: (row["distance_km"], row["name"]))
        itinerary["route_issues"] = issues[:8]
    return result


_SOURCE_TTLS = {
    "network_rail": 150, "td": 150, "vstp": 900, "bods": 180, "bods_timetable": 86400, "bods_sx": 300,
    "national_highways": 300, "street_manager": 86400, "ea_floods": 1200, "aisstream": 150, "rnli": 1800,
    "adsb_sar": 420, "london_fire": 1800, "met_office": 10800,
    "bgs_earthquakes": 900, "ea_levels": 2400, "wavenet": 5400, "carbon_intensity": 5400,
    "uk_air": 10800, "tfl_live": 600, "gbfs": 1200, "space_weather": 1200, "iss": 120,
    "power_outages": 3600, "gritters": 600, "smart_places": 86400,
}


def _source_payload(status: SourceStatus) -> dict:
    row = status.model_dump(mode="json")
    now = datetime.now(timezone.utc)
    age = None
    if status.last_message_at is not None:
        stamp = status.last_message_at
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        age = max(0, int((now - stamp).total_seconds()))
    ttl = _SOURCE_TTLS.get(status.name)
    stale = bool(ttl and age is not None and age > ttl and status.connected and status.mode == "live")
    if not status.enabled or status.mode == "off": health = "off"
    elif status.mode == "setup": health = "setup"
    elif status.mode == "error": health = "error"
    elif stale: health = "stale"
    elif status.connected and status.mode == "live": health = "live"
    elif status.connected: health = "ready"
    elif status.mode in {"sync", "connecting", "waiting"}: health = "sync"
    else: health = "offline"
    row.update({"health": health, "age_seconds": age, "expected_fresh_seconds": ttl, "stale": stale})
    return row


def _readiness_payload() -> dict:
    sources = [_source_payload(s) for s in state.sources()]
    core = [s for s in sources if s["name"] not in {"schedule", "rail_reference", "demo"} and s["enabled"]]
    counts = {key: sum(s["health"] == key for s in core) for key in ["live", "ready", "sync", "stale", "error", "setup", "offline"]}
    if counts["error"] or counts["stale"] or counts["offline"]: overall = "degraded"
    elif counts["setup"] and not (counts["live"] or counts["ready"]): overall = "setup"
    elif counts["live"] or counts["ready"]: overall = "healthy"
    else: overall = "starting"
    storage = _storage_payload()
    bad_core_health = {"error", "stale", "offline", "setup"}
    launch_checks = {
        "commercial_profile": bool(settings.public_deployment and settings.commercial_mode),
        "tomtom_base_map": bool(settings.tomtom_traffic_api_key),
        "network_rail": (not settings.network_rail_enabled_flag) or settings.credentials_present,
        "bods": (not settings.bods_enabled) or bool(settings.bods_api_key),
        "national_highways": (not settings.national_highways_enabled) or bool(settings.national_highways_api_key),
        "billing_safe": (not settings.billing_enabled) or not billing_configuration_errors(billing_configuration),
        "seller_details": (not settings.billing_enabled) or settings.seller_details_ready,
        "push_alerts_safe": (not settings.push_alerts_enabled) or settings.push_alerts_ready,
        "storage_headroom": storage["healthy"],
        "core_feeds_healthy": not any(source["health"] in bad_core_health for source in core),
    }
    launch_blockers = [name for name, passed in launch_checks.items() if not passed]
    launch = {
        "ready": not launch_blockers,
        "checks": launch_checks,
        "blockers": launch_blockers,
        "note": "Credential values are never returned by this endpoint.",
    }
    return {
        "overall": overall,
        "counts": counts,
        "sources": sources,
        "launch": launch,
        "operations": {
            "storage": storage,
            "request_budgets": {
                "tiles": tile_request_budget.status(),
                "lookups": lookup_request_budget.status(),
            },
        },
        "version": APP_VERSION,
    }


def _storage_payload() -> dict:
    try:
        usage = shutil.disk_usage(DATA_DIR)
        total_mb = round(usage.total / 1024 / 1024)
        free_mb = round(usage.free / 1024 / 1024)
        used_percent = round((usage.used / usage.total) * 100, 1) if usage.total else 100.0
        return {
            "healthy": free_mb >= settings.min_free_disk_mb,
            "free_mb": free_mb,
            "total_mb": total_mb,
            "used_percent": used_percent,
            "minimum_free_mb": settings.min_free_disk_mb,
        }
    except OSError:
        return {
            "healthy": False,
            "free_mb": None,
            "total_mb": None,
            "used_percent": None,
            "minimum_free_mb": settings.min_free_disk_mb,
        }


def _mapped_location_count() -> int:
    try:
        raw = json.loads(STATION_FILE.read_text(encoding="utf-8"))
        return len(raw) if isinstance(raw, dict) else 0
    except Exception:
        return 0


def _file_mb(path: Path) -> float:
    try:
        return round(path.stat().st_size / 1024 / 1024, 1)
    except OSError:
        return 0.0


def _reload_collectors() -> None:
    if trust_collector:
        trust_collector.reload_references()
    if td_collector:
        td_collector.reload_references()


async def _sync_schedule() -> None:
    state.set_source(SourceStatus(
        name="schedule", enabled=True, connected=False, mode="sync",
        message="Starting Network Rail full SCHEDULE bootstrap…",
    ))

    def progress(stage: str, info: dict) -> None:
        if stage == "download-start":
            msg = f"SCHEDULE: authenticating and downloading from {info.get('host', 'Network Rail')}…"
        elif stage == "download":
            got = float(info.get("bytes", 0)) / 1024 / 1024
            expected = float(info.get("expected", 0)) / 1024 / 1024
            msg = f"SCHEDULE: downloading • {got:.1f} MB" + (f" / {expected:.1f} MB" if expected else "")
        elif stage == "download-complete":
            got = float(info.get("bytes", 0)) / 1024 / 1024
            msg = f"SCHEDULE: download complete • {got:.1f} MB • preparing station references…"
        elif stage == "download-reuse":
            got = float(info.get("bytes", 0)) / 1024 / 1024
            msg = f"SCHEDULE: reusing completed download • {got:.1f} MB • indexing now…"
        elif stage == "naptan":
            msg = "SCHEDULE: loading station reference data…"
        elif stage == "index-start":
            msg = "SCHEDULE: indexing timetable records into SQLite…"
        elif stage == "index":
            msg = f"SCHEDULE: indexing • {int(info.get('lines', 0)):,} records read • {int(info.get('schedules', 0)):,} schedules"
        elif stage == "cached":
            msg = f"SCHEDULE cache ready • {int(info.get('schedules', 0)):,} schedules"
        else:
            msg = f"SCHEDULE: {stage}"
        state.set_source(SourceStatus(name="schedule", enabled=True, connected=False, mode="sync", message=msg))

    try:
        result = await asyncio.to_thread(
            sync_schedule_data,
            settings.network_rail_username,
            settings.network_rail_password,
            DATA_DIR,
            force=settings.refresh_schedule,
            progress=progress,
        )
        _reload_collectors()
        cached = bool(result.get("cached"))
        count = int(result.get("schedules", schedules.count()) or 0)
        geocoded = result.get("geocoded_stanox")
        suffix = f" • {int(geocoded):,} station STANOX geocoded" if geocoded is not None else ""
        state.set_source(SourceStatus(
            name="schedule", enabled=True, connected=True, mode="ready",
            message=f"SCHEDULE {'cache' if cached else 'download'} ready • {count:,} schedules{suffix}",
        ))
    except Exception as exc:
        state.set_source(SourceStatus(
            name="schedule", enabled=True, connected=False, mode="error",
            message=f"SCHEDULE unavailable: {type(exc).__name__}: {exc}",
        ))


async def _sync_reference() -> None:
    state.set_source(SourceStatus(
        name="rail_reference", enabled=True, connected=False, mode="sync",
        message="Loading broad STANOX geography first…",
    ))
    try:
        # Critical v0.4.1 change: get a broad STANOX map into memory *before*
        # waiting for slower CORPUS/SMART/NaPTAN work. This lets buffered TRUST
        # movements appear on the map as soon as geography is available.
        geo = await asyncio.to_thread(
            sync_public_geography, DATA_DIR, force=settings.refresh_reference_data
        )
        _reload_collectors()
        state.set_source(SourceStatus(
            name="rail_reference", enabled=True, connected=True, mode="sync",
            message=f"Rail geography ready • {geo['stanox']:,} STANOX coordinates. Finishing CORPUS, SMART and NaPTAN…",
        ))

        result = await asyncio.to_thread(
            sync_reference_data,
            settings.network_rail_username,
            settings.network_rail_password,
            DATA_DIR,
            force=settings.refresh_reference_data,
        )
        _reload_collectors()
        rail_geo = str(result.get("rail_geo") or "not attempted")
        corpus = str(result.get("corpus") or "not attempted")
        smart = str(result.get("smart") or "not attempted")
        naptan = str(result.get("naptan") or "not attempted")
        okay = any("ready" in x for x in (rail_geo, corpus, smart, naptan))
        state.set_source(SourceStatus(
            name="rail_reference", enabled=True, connected=okay, mode="ready" if okay else "error",
            message=f"Geography {rail_geo} • CORPUS {corpus} • SMART {smart} • NaPTAN {naptan}",
        ))
    except Exception as exc:
        state.set_source(SourceStatus(
            name="rail_reference", enabled=True, connected=False, mode="error",
            message=f"Reference sync failed: {type(exc).__name__}: {exc}",
        ))


async def _schedule_sync_loop() -> None:
    """Keep the daily schedule cache fresh without relying on service restarts."""
    while True:
        await _sync_schedule()
        await asyncio.sleep(6 * 3600)


async def _reference_sync_loop() -> None:
    """Recheck slower-changing rail reference data once per day."""
    while True:
        await _sync_reference()
        await asyncio.sleep(24 * 3600)


def _send_commute_push(subscription: dict, payload: dict) -> None:
    from pywebpush import webpush

    webpush(
        subscription_info=subscription,
        data=json.dumps(payload),
        vapid_private_key=settings.vapid_private_key,
        vapid_claims={"sub": settings.vapid_subject},
        ttl=1800,
        timeout=12,
    )


from .rain_radar import POLL_SECONDS as RAIN_RADAR_POLL_SECONDS, RainRadar

rain_radar = RainRadar()


def _common_photo_plans() -> list[tuple[str, str | None, str | None]]:
    from .media_reference import TRAIN_FLEETS
    plans: list[tuple[str, str | None, str | None]] = [("train", name, None) for name in sorted(TRAIN_FLEETS)]
    plans += [("aircraft", airline, model) for airline, model in (
        ("Ryanair", "Boeing 737-800"), ("easyJet", "Airbus A320"), ("easyJet", "Airbus A319"),
        ("easyJet", "Airbus A320neo"), ("British Airways", "Airbus A320"), ("British Airways", "Airbus A320neo"),
        ("Jet2", "Boeing 737-800"), ("TUI Airways", "Boeing 737-800"), ("Wizz Air", "Airbus A321neo"),
        ("Loganair", "ATR 72-600"), ("Aer Lingus", "Airbus A320"),
        ("easyJet", "Airbus A321neo"), ("British Airways", "Airbus A319"), ("British Airways", "Airbus A321neo"),
        ("British Airways", "Boeing 777-300ER"), ("British Airways", "Boeing 787-9"), ("British Airways", "Airbus A350-1000"),
        ("Ryanair", "Boeing 737 MAX 8"), ("Jet2", "Airbus A321neo"), ("TUI Airways", "Boeing 787-8"),
        ("Virgin Atlantic", "Airbus A350-1000"), ("Virgin Atlantic", "Boeing 787-9"), ("Wizz Air", "Airbus A321"),
        ("Loganair", "Embraer ERJ-145"), ("Aer Lingus", "Airbus A320neo"), ("KLM", "Embraer 175"),
    )]
    return plans


history_store = None


async def _line_track_warm() -> None:
    """Route every named line over real track once, soon after start."""
    await asyncio.sleep(90)
    def warm() -> None:
        from .named_lines import LINES, resolve, track_for
        stations = _stations()
        for line in LINES:
            try:
                track_for(line, resolve(line, stations))
            except Exception:
                continue
    try:
        await asyncio.to_thread(warm)
    except Exception:
        pass


async def _orr_refresh_loop() -> None:
    """Official ORR figures: punctuality weekly, station usage monthly."""
    from .orr import refresh
    await asyncio.sleep(600)
    while True:
        try:
            if refresh.due(DATA_DIR / "operator_performance.json", 7):
                await asyncio.to_thread(refresh.refresh_performance, DATA_DIR)
            # Station usage is NOT refreshed here: reading ORR's yearly ODS
            # file whole needed more memory than the server has and restarted
            # it ten minutes after every start. It changes once a year; it is
            # rebuilt by hand (scripts/orr) and bundled.
        except Exception:
            pass
        await asyncio.sleep(24 * 3600)


async def _history_loop() -> None:
    await asyncio.sleep(120)
    while True:
        try:
            # Gather and write off the event loop: thousands of rows must not
            # hold up live requests.
            await asyncio.to_thread(lambda: history_store.record(
                state.all(include_buses=False, include_vessels=True, train_limit=None)))
        except Exception:
            pass
        await asyncio.sleep(300)


async def _photo_warm_loop() -> None:
    await asyncio.sleep(60)  # let the live feeds settle first
    while True:
        try:
            await asyncio.to_thread(media_lookup.warm, _common_photo_plans())
        except Exception:
            pass
        await asyncio.sleep(12 * 3600)


async def _rain_radar_loop() -> None:
    """Keep the last two hours of rain radar frames."""
    await asyncio.sleep(5)
    while True:
        try:
            await asyncio.to_thread(rain_radar.refresh)
        except Exception as exc:  # never let the radar take the server down
            rain_radar.last_error = f"radar refresh failed: {type(exc).__name__}"
        await asyncio.sleep(RAIN_RADAR_POLL_SECONDS)


_journey_checked: dict[str, float] = {}


async def _commute_alert_loop() -> None:
    while True:
        await asyncio.sleep(settings.push_poll_seconds)
        store = commute_alert_store
        if store is None:
            continue
        activities = state.all_activities()
        cool_now = state.cool_candidates(limit=50)
        for saved in store.active():
            # Saved trains alert whenever they change, not only in commute hours.
            train_alerts = [
                alert
                for train_id in saved.preferences.get("saved_trains") or []
                for alert in saved_train_alerts(_saved_train_status(train_id))
            ]
            place_alerts = saved.preferences.get("mode") != "trains-only" and within_alert_hours(saved.preferences)
            spotter = saved.preferences.get("spotter")
            spots = [spotter_alert(item, spotter) for item in cool_now] if spotter else []
            journey = saved.preferences.get("journey")
            drive_alerts: list[dict] = []
            if journey_due(journey):
                # Live incidents are only fetched where someone is looking;
                # fetch along this route at most every ten minutes.
                last = _journey_checked.get(saved.endpoint_hash, -1e9)
                if traffic_incident_service is not None and traffic_incident_service.ready and time.monotonic() - last > 600:
                    _journey_checked[saved.endpoint_hash] = time.monotonic()
                    for box in journey_bboxes(journey):
                        try:
                            await asyncio.to_thread(traffic_incident_service.ensure, box)
                        except Exception:
                            pass
                drive_alerts = journey_alerts(state.all_events(), journey)
            leave_alerts = [
                leave_alert(plan, _saved_train_status(plan["train_id"]))
                for plan in saved.preferences.get("leave_plans") or []
            ]
            if not place_alerts and not train_alerts and not any(spots) and not drive_alerts and not any(leave_alerts):
                continue
            nearby = []
            if place_alerts:
                road_cap_reached = store.delivered_count(saved.endpoint_hash, road_key_prefix()) >= MAX_PLACE_ROAD_ALERTS_PER_DAY
                scored = []
                for item in activities:
                    row = alert_for_activity(item, saved.preferences)
                    if row is None or (road_cap_reached and row["key"].startswith("road:")):
                        continue
                    scored.append((-((getattr(item, "impact", None) or {}).get("rank") or 0), row))
                # Most serious first: closures and crashes before lane closures.
                nearby = [row for _, row in sorted(scored, key=lambda pair: pair[0])]
            candidates = leave_alerts + train_alerts + drive_alerts + spots + nearby
            candidate = next((row for row in candidates if row and not store.already_delivered(saved.endpoint_hash, row["key"])), None)
            if candidate is None:
                continue
            # Opens the app on the item itself, not the shared summary page.
            candidate["url"] = f"{settings.public_base_url}/?open={quote(candidate['activity_id'], safe='')}"
            candidate["tag"] = f"commute-{hashlib.sha256(candidate['key'].encode()).hexdigest()[:20]}"
            try:
                await asyncio.to_thread(_send_commute_push, saved.subscription, candidate)
                store.mark_delivered(saved.endpoint_hash, candidate["key"])
            except Exception as exc:
                status_code = getattr(exc, "status_code", None)
                if status_code in {404, 410}:
                    store.deactivate(saved.endpoint_hash)


def _recent_trains():
    return [
        row for row in state.all(
            include_trains=True,
            train_limit=None,
            include_buses=False,
            include_vessels=False,
        )
        if row.type == "train"
    ]


async def _rail_position_snapshot_loop() -> None:
    while True:
        await asyncio.sleep(15)
        await asyncio.to_thread(rail_position_snapshot.save, _recent_trains())


async def _rail_estimate_rollover_loop() -> None:
    """Keep all mapped trains moving across timetable legs between reports."""
    while True:
        # One second keeps a completed leg from visibly pausing at a station
        # before its next mapped timetable leg is published to browsers.
        await asyncio.sleep(1)
        collector = td_collector
        if collector is not None:
            await asyncio.to_thread(collector.advance_live_estimates)


async def _state_maintenance_loop() -> None:
    """Bound long-running in-memory state without touching current live rows."""
    while True:
        await asyncio.sleep(300)
        state.prune_stale()


# How the server itself is doing, for /api/health: a stall (every worker
# thread busy, or the event loop held up) shows here before it shows as 502s.
_runtime = {"started": time.time(), "loop_lag_ms": 0.0, "loop_lag_max_ms": 0.0,
            "threads_busy": 0, "threads_total": 0, "threads_busy_max": 0}


def _memory_mb() -> float | None:
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return round(int(line.split()[1]) / 1024, 1)
    except (OSError, ValueError, IndexError):
        pass
    return None


def runtime_report() -> dict:
    return {
        "uptime_seconds": round(time.time() - _runtime["started"]),
        "memory_mb": _memory_mb(),
        "os_threads": threading.active_count(),
        "worker_threads_busy": _runtime["threads_busy"],
        "worker_threads_total": _runtime["threads_total"],
        "worker_threads_busy_max_5min": _runtime["threads_busy_max"],
        "loop_lag_ms": _runtime["loop_lag_ms"],
        "loop_lag_max_ms_5min": _runtime["loop_lag_max_ms"],
    }


async def _runtime_monitor_loop() -> None:
    import anyio.to_thread
    window_started = time.monotonic()
    while True:
        before = time.monotonic()
        await asyncio.sleep(1.0)
        lag = max(0.0, (time.monotonic() - before - 1.0) * 1000)
        limiter = anyio.to_thread.current_default_thread_limiter()
        if time.monotonic() - window_started > 300:
            window_started = time.monotonic()
            _runtime["loop_lag_max_ms"] = 0.0
            _runtime["threads_busy_max"] = 0
        _runtime.update(
            loop_lag_ms=round(lag, 1),
            loop_lag_max_ms=round(max(_runtime["loop_lag_max_ms"], lag), 1),
            threads_busy=limiter.borrowed_tokens,
            threads_total=int(limiter.total_tokens),
            threads_busy_max=max(_runtime["threads_busy_max"], limiter.borrowed_tokens),
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    global trust_collector, td_collector, vstp_collector, rail_feed_hub, bods_collector, bods_sx_collector, roads_collector, vms_collector, speed_collector, features_collector, floods_collector, ais_collector, rnli_collector, adsb_sar_collector, london_fire_collector, met_office_collector, traffic_flow_service, world_collectors, smart_places, street_manager_store, billing_store, billing_service, commute_alert_store
    tasks: list[asyncio.Task] = []
    tasks.append(asyncio.create_task(_state_maintenance_loop()))
    tasks.append(asyncio.create_task(_runtime_monitor_loop()))

    # Remembers which platform each train uses, for "usually platform N".
    try:
        from .platform_history import PlatformHistory
        state.platform_history = PlatformHistory(DATA_DIR / "platform_history.sqlite")
    except Exception:
        state.platform_history = None

    # What the radar has seen, day by day: train lateness and unusual finds.
    global history_store
    try:
        from .history import HistoryStore
        history_store = HistoryStore(DATA_DIR / "history.sqlite")
        tasks.append(asyncio.create_task(_history_loop()))
        tasks.append(asyncio.create_task(_line_track_warm()))
    except Exception:
        history_store = None

    # Official ORR figures: refreshed copies saved earlier, then keep current.
    try:
        from .orr.refresh import load_saved
        load_saved(DATA_DIR)
    except Exception:
        pass
    if not settings.demo_mode:
        tasks.append(asyncio.create_task(_orr_refresh_loop()))

    if billing_configuration.enabled:
        billing_store = BillingStore(
            BILLING_DB,
            token_pepper=billing_configuration.token_pepper,
        )
        billing_service = StripeBillingService(billing_store, billing_configuration)

    if settings.rain_radar_enabled and not settings.demo_mode:
        tasks.append(asyncio.create_task(_rain_radar_loop()))

    # Example photos survive restarts, and the common ones are looked up
    # before anyone asks, so photos appear straight away.
    media_lookup.use_disk_cache(DATA_DIR / "media_examples.json")
    if settings.wikimedia_enabled and not settings.demo_mode:
        tasks.append(asyncio.create_task(_photo_warm_loop()))

    if settings.push_alerts_ready:
        commute_alert_store = CommuteAlertStore(ALERTS_DB)
        tasks.append(asyncio.create_task(_commute_alert_loop()))

    if settings.demo_mode:
        tasks.append(asyncio.create_task(run_demo(state)))

    if settings.network_rail_enabled:
        for restored_train in await asyncio.to_thread(rail_position_snapshot.load):
            state.upsert(restored_train)
        trust_collector = TrustCollector(settings, state, STATION_FILE, schedules)
        rail_feeds = [trust_collector]

        if settings.td_enabled:
            td_collector = TDCollector(settings, state, STATION_FILE, SMART_FILE, schedules)
            rail_feeds.append(td_collector)
        else:
            state.set_source(SourceStatus(name="td", enabled=False, mode="off", message="TD disabled in .env"))

        if settings.vstp_enabled:
            vstp_collector = VSTPCollector(settings, state, schedules)
            rail_feeds.append(vstp_collector)
        else:
            state.set_source(SourceStatus(name="vstp", enabled=False, mode="off", message="VSTP disabled in .env"))

        rail_feed_hub = MultiplexStompFeed(settings, rail_feeds)
        rail_feed_hub.start()

        if schedules.ready():
            state.set_source(SourceStatus(
                name="schedule", enabled=True, connected=True, mode="ready",
                message=f"Local SCHEDULE index loaded • {schedules.count():,} schedules",
            ))
            if not schedules.call_index_ready():
                tasks.append(asyncio.create_task(asyncio.to_thread(schedules.ensure_call_index)))
        if settings.auto_sync_schedule:
            tasks.append(asyncio.create_task(_schedule_sync_loop()))

        if _mapped_location_count() > 0:
            state.set_source(SourceStatus(
                name="rail_reference", enabled=True, connected=True, mode="ready",
                message=f"{_mapped_location_count():,} mapped STANOX locations loaded; background refresh pending",
            ))
        if settings.auto_sync_reference:
            tasks.append(asyncio.create_task(_reference_sync_loop()))
        tasks.append(asyncio.create_task(_rail_position_snapshot_loop()))
        tasks.append(asyncio.create_task(_rail_estimate_rollover_loop()))
    elif not settings.demo_mode:
        state.set_source(SourceStatus(
            name="network_rail", enabled=False, connected=False, mode="setup",
            message="Network Rail credentials are missing. Run configure_network_rail.bat; other feed keys will be preserved.",
        ))

    if settings.bods_enabled:
        bods_collector = BODSCollector(settings, state)
        bods_collector.start()
    else:
        state.set_source(SourceStatus(name="bods", enabled=False, mode="off", message="BODS live buses disabled in .env"))

    smart_places = SmartPlacesService(
        DATA_DIR,
        schedules,
        state,
        bods_collector_getter=lambda: bods_collector,
    )
    smart_places.start_background_sync()

    if settings.bods_sx_enabled:
        bods_sx_collector = BODSSXCollector(settings, state)
        bods_sx_collector.start()
    else:
        state.set_source(SourceStatus(name="bods_sx", enabled=False, mode="off", message="BODS disruptions/cancellations disabled in .env"))

    if settings.national_highways_enabled:
        roads_collector = NationalHighwaysCollector(settings, state)
        roads_collector.start()
    else:
        state.set_source(SourceStatus(name="national_highways", enabled=False, mode="off", message="National Highways road events disabled in .env"))

    if settings.national_highways_vms_enabled:
        vms_collector = NationalHighwaysVMSCollector(settings, state)
        vms_collector.start()
    else:
        state.set_source(SourceStatus(name="national_highways_vms", enabled=False, mode="off", message="Motorway signs off"))

    if settings.national_highways_speed_enabled and settings.national_highways_speed_url:
        speed_collector = NationalHighwaysSpeedCollector(settings, state)
        speed_collector.start()
    else:
        state.set_source(SourceStatus(name="national_highways_speed", enabled=False, mode="off", message="Managed-speed areas off"))

    if settings.national_highways_features_enabled and settings.national_highways_features_url:
        features_collector = NationalHighwaysFeaturesCollector(settings, state)
        features_collector.start()
    else:
        state.set_source(SourceStatus(name="national_highways_features", enabled=False, mode="off", message="Roadside features off"))

    if settings.street_manager_enabled:
        try:
            street_manager_store = StreetManagerSnapshotStore(STREET_MANAGER_DB)
            stored_events = street_manager_store.events()
            state.replace_events_for_source("DfT Street Manager Open Data", stored_events)
            state.set_source(SourceStatus(
                name="street_manager", enabled=True, connected=True,
                mode="live" if stored_events else "ready",
                message=(
                    f"Street Manager callback ready • {len(stored_events):,} active local-road events restored"
                    if stored_events else
                    "Street Manager callback ready • register /api/integrations/street-manager with DfT"
                ),
                movements_mapped=len(stored_events),
            ))
        except Exception as exc:
            state.set_source(SourceStatus(
                name="street_manager", enabled=True, connected=False, mode="error",
                message=f"Street Manager snapshot could not open: {type(exc).__name__}",
            ))
    else:
        state.set_source(SourceStatus(
            name="street_manager", enabled=False, connected=False, mode="off",
            message="Street Manager open-data callback disabled in .env",
        ))

    traffic_flow_service = TrafficFlowService(settings, state)
    traffic_flow_service.set_initial_status()
    global traffic_incident_service
    traffic_incident_service = TrafficIncidentService(
        settings, state, daily_limit=max(100, int(os.getenv("TOMTOM_INCIDENT_DAILY_LIMIT", "2000"))),
    )
    if traffic_flow_service.ready:
        tasks.append(asyncio.create_task(asyncio.to_thread(traffic_flow_service.self_test)))

    if settings.ea_floods_enabled:
        floods_collector = EnvironmentAgencyFloodCollector(settings, state, DATA_DIR)
        floods_collector.start()
    else:
        state.set_source(SourceStatus(name="ea_floods", enabled=False, mode="off", message="Environment Agency flood warnings disabled in .env"))

    if settings.aisstream_enabled:
        ais_collector = AISStreamCollector(settings, state)
        ais_collector.start()
    else:
        state.set_source(SourceStatus(name="aisstream", enabled=False, mode="off", message="AISStream maritime layer disabled in .env"))

    if settings.rnli_enabled:
        rnli_collector = RNLILaunchCollector(settings, state, DATA_DIR)
        rnli_collector.start()
    else:
        state.set_source(SourceStatus(name="rnli", enabled=False, mode="off", message="RNLI latest-launch layer disabled in .env"))

    if settings.adsb_sar_enabled:
        adsb_sar_collector = ADSBLolSARCollector(settings, state)
        adsb_sar_collector.start()
    else:
        state.set_source(SourceStatus(name="adsb_sar", enabled=False, mode="off", message="ADSB SAR layer disabled in .env"))

    if settings.london_fire_enabled:
        london_fire_collector = LondonFireCollector(settings, state)
        london_fire_collector.start()
    else:
        state.set_source(SourceStatus(name="london_fire", enabled=False, mode="off", message="London Fire incidents disabled in .env"))

    if settings.met_office_enabled:
        met_office_collector = MetOfficeWarningCollector(settings, state, DATA_DIR)
        met_office_collector.start()
    else:
        state.set_source(SourceStatus(name="met_office", enabled=False, mode="off", message="Met Office warnings disabled in .env"))

    # Broad free/open live-world layers. Each collector is isolated so one
    # upstream outage cannot take down the rest of the radar.
    world_collectors = []
    collector_specs = [
        (settings.bgs_earthquakes_enabled, BGSEarthquakeCollector, "bgs_earthquakes", "BGS earthquakes"),
        (settings.ea_levels_enabled, EALevelsCollector, "ea_levels", "EA river/tide levels"),
        (settings.wavenet_enabled, WaveNetCollector, "wavenet", "WaveNet"),
        (settings.carbon_intensity_enabled, CarbonIntensityCollector, "carbon_intensity", "Grid carbon intensity"),
        (settings.uk_air_enabled, UKAirCollector, "uk_air", "UK air quality"),
        (settings.tfl_live_enabled, TfLLiveCollector, "tfl_live", "TfL cycle hire"),
        (settings.gbfs_enabled, GBFSCollector, "gbfs", "GBFS shared mobility"),
        (settings.space_weather_enabled, SpaceWeatherCollector, "space_weather", "Space weather"),
        (settings.iss_enabled, ISSCollector, "iss", "ISS"),
        (settings.power_outages_enabled, PowerOutageCollector, "power_outages", "Power outages"),
        (settings.gritter_enabled, GritterCollector, "gritters", "Gritters"),
    ]
    for enabled, cls, source_name, label in collector_specs:
        if enabled:
            try:
                c = cls(settings, state); c.start(); world_collectors.append(c)
            except Exception as exc:
                state.set_source(SourceStatus(name=source_name, enabled=True, connected=False, mode="error", message=f"{label} start error: {type(exc).__name__}: {exc}"))
        else:
            state.set_source(SourceStatus(name=source_name, enabled=False, connected=False, mode="off", message=f"{label} disabled in .env"))

    yield

    if rail_feed_hub:
        rail_feed_hub.stop()
        rail_feed_hub = None
    for collector in (trust_collector, td_collector, vstp_collector, bods_collector, bods_sx_collector, roads_collector, vms_collector, speed_collector, features_collector, floods_collector, ais_collector, rnli_collector, adsb_sar_collector, london_fire_collector, met_office_collector):
        if collector:
            collector.stop()
    for collector in world_collectors:
        try:
            collector.stop()
        except Exception:
            pass
    if settings.network_rail_enabled:
        with suppress(Exception):
            await asyncio.to_thread(rail_position_snapshot.save, _recent_trains())
    for task in tasks:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
    if street_manager_store is not None:
        street_manager_store.close()
        street_manager_store = None
    if billing_store is not None:
        billing_store.close()
        billing_store = None
        billing_service = None
    if commute_alert_store is not None:
        commute_alert_store.close()
        commute_alert_store = None


app = FastAPI(
    title="UK Activity Radar",
    version=APP_VERSION,
    lifespan=lifespan,
    docs_url=None if settings.public_deployment else "/docs",
    redoc_url=None if settings.public_deployment else "/redoc",
    openapi_url=None if settings.public_deployment else "/openapi.json",
)
app.add_middleware(GZipMiddleware, minimum_size=1000, compresslevel=5)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def _valid_ga4_measurement_id(value: str) -> bool:
    return bool(re.fullmatch(r"G-[A-Z0-9]{6,20}", value or ""))


def _valid_gtm_container_id(value: str) -> bool:
    return bool(re.fullmatch(r"GTM-[A-Z0-9]{4,12}", value or ""))


def _analytics_ids() -> tuple[str, str]:
    """(GA4 id, Tag Manager id) to give the page; empty when analytics are off."""
    if not getattr(settings, "ga4_enabled", False):
        return "", ""
    ga4 = str(getattr(settings, "ga4_measurement_id", "") or "").strip().upper()
    gtm = str(getattr(settings, "gtm_container_id", "") or "").strip().upper()
    return (ga4 if _valid_ga4_measurement_id(ga4) else ""), (gtm if _valid_gtm_container_id(gtm) else "")


def _site_origin(request: Request | None = None) -> str:
    configured = getattr(settings, "public_base_url", "").strip().rstrip("/")
    parsed = urlsplit(configured)
    if parsed.scheme in {"http", "https"} and parsed.netloc and not parsed.path:
        return configured
    if request is not None:
        return str(request.base_url).rstrip("/")
    return "https://uk-activity-radar.onrender.com"


def _render_public_html(filename: str, request: Request) -> Response:
    page = (STATIC_DIR / filename).read_text(encoding="utf-8")
    origin = _site_origin(request)
    verification = getattr(settings, "google_site_verification", "").strip()
    measurement_id, container_id = _analytics_ids()
    verification_meta = (
        f'<meta name="google-site-verification" content="{html_escape(verification, quote=True)}">'
        if verification else ""
    )
    page = page.replace("{{PUBLIC_BASE_URL}}", html_escape(origin, quote=True))
    page = page.replace("{{GOOGLE_SITE_VERIFICATION_META}}", verification_meta)
    page = page.replace("{{GA4_MEASUREMENT_ID}}", html_escape(measurement_id, quote=True))
    page = page.replace("{{GTM_CONTAINER_ID}}", html_escape(container_id, quote=True))
    return Response(page, media_type="text/html", headers={"Cache-Control": "no-cache"})


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(self), microphone=(), geolocation=(self)")
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    response.headers.setdefault("X-Permitted-Cross-Domain-Policies", "none")
    if settings.public_deployment:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        analytics_active = any(_analytics_ids())
        # Google's tag hosts are allowed only while analytics are switched on.
        # Tag Manager "Custom HTML" tags stay blocked: no inline scripts.
        script_sources = "'self' https://www.googletagmanager.com" if analytics_active else "'self'"
        connect_sources = (
            "'self' wss: https://www.googletagmanager.com https://www.google-analytics.com "
            "https://region1.google-analytics.com https://analytics.google.com"
        ) if analytics_active else "'self' wss:"
        response.headers.setdefault(
            "Content-Security-Policy",
            f"default-src 'self'; script-src {script_sources}; style-src 'self' 'unsafe-inline'; "
            f"img-src 'self' data: https:; media-src 'self' https:; connect-src {connect_sources}; "
            "font-src 'self' data:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'",
        )
    path = request.url.path
    if path.startswith("/api/"):
        # Live positions, entitlements and account state must never be cached by
        # a shared proxy. Tile routes set their own safe public cache policy.
        response.headers.setdefault("Cache-Control", "private, no-store")
        response.headers.setdefault("X-Robots-Tag", "noindex, nofollow")
    elif path.startswith("/live/") or path.startswith("/billing/"):
        response.headers.setdefault("X-Robots-Tag", "noindex, nofollow")
    elif path.startswith("/static/"):
        if request.query_params.get("v"):
            # Every first-party CSS/JS URL carries a release version. These
            # names are immutable, so browsers and the CDN can keep them for a
            # year without making a returning visitor re-download the app.
            response.headers.setdefault("Cache-Control", "public, max-age=31536000, immutable")
        elif path.endswith((".png", ".ico", ".webp", ".woff2")) or "/vendor/" in path:
            response.headers.setdefault("Cache-Control", "public, max-age=86400, stale-while-revalidate=604800")
        else:
            response.headers.setdefault("Cache-Control", "public, max-age=300, stale-while-revalidate=86400")
    elif path == "/" or path.startswith("/live/") or path in {"/data-licences", "/terms"}:
        response.headers.setdefault("Cache-Control", "no-cache")
    return response


def _require_local_debug_route() -> None:
    if settings.public_deployment:
        raise HTTPException(status_code=404, detail="Not found")


def _enforce_public_budget(request: Request, budget: FixedWindowBudget) -> None:
    if not settings.public_deployment:
        return
    client = request.client.host if request.client else "unknown"
    allowed, retry_after = budget.consume(client)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Public beta request budget reached. Please wait before retrying.",
            headers={"Retry-After": str(retry_after)},
        )


def _billing_context(request: Request) -> AccessContext:
    if not settings.public_deployment and not billing_configuration.enabled:
        return AccessContext(
            token_valid=True,
            customer_id="local-development",
            plan="plus",
            subscription_status="active",
            features=PLUS_FEATURES,
        )
    preview = _preview_context(request.cookies.get(PREVIEW_COOKIE))
    if preview.plus:
        return preview
    if billing_store is None:
        return AccessContext()
    return billing_store.access_context(request.cookies.get(ACCESS_COOKIE))


def _preview_cookie_value() -> str:
    secret = settings.radar_plus_preview_key
    return hmac.new(secret.encode("utf-8"), b"radar-plus-launch-preview", hashlib.sha256).hexdigest() if secret else ""


def _preview_context(cookie: str | None) -> AccessContext:
    expected = _preview_cookie_value()
    if not expected or not cookie or not hmac.compare_digest(cookie, expected):
        return AccessContext()
    return AccessContext(
        token_valid=True,
        customer_id="launch-preview",
        plan="plus",
        subscription_status="active",
        features=PLUS_FEATURES,
    )


def _require_plus(request: Request, feature: str) -> AccessContext:
    context = _billing_context(request)
    if not context.has(feature):
        raise HTTPException(
            status_code=402,
            detail={
                "code": "radar_plus_required",
                "feature": feature,
                "message": "This advanced tool is included with Radar+.",
            },
            headers={"Cache-Control": "private, no-store", "Vary": "Cookie"},
        )
    return context


def _require_same_origin_post(request: Request) -> None:
    origin = request.headers.get("origin")
    host = request.headers.get("host")
    if origin and not same_origin_websocket(origin, host):
        raise HTTPException(status_code=403, detail="Cross-site request rejected")


@app.get("/", include_in_schema=False)
def home(request: Request):
    return _render_public_html("index.html", request)


@app.get("/about", include_in_schema=False)
def about(request: Request):
    return _render_public_html("about.html", request)


@app.get("/robots.txt", include_in_schema=False)
def robots(request: Request):
    origin = _site_origin(request)
    body = (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /api/\n"
        "Disallow: /billing/\n"
        f"Sitemap: {origin}/sitemap.xml\n"
    )
    return Response(body, media_type="text/plain", headers={"Cache-Control": "public, max-age=3600"})


# ---- Pages for search: stations and operators -----------------------------
_station_directory: tuple[float, list[dict]] = (-1e9, [])
_seo_page_cache: dict[str, tuple[float, str]] = {}
_MIN_PUBLIC_STATION_DIRECTORY = 2500


def _stations() -> list[dict]:
    """Every station with a CRS code, named and pathed, refreshed hourly."""
    global _station_directory
    from .seo_pages import directory
    built_at, rows = _station_directory
    if rows and time.monotonic() - built_at < 3600:
        return rows
    places = []
    if smart_places is not None:
        places = list(getattr(smart_places, "_rail", []) or []) + smart_places.rail_places((-9.9, 49.0, 3.5, 61.8), 6000)
    candidate = directory(places)
    if len(candidate) >= _MIN_PUBLIC_STATION_DIRECTORY:
        _station_directory = (time.monotonic(), candidate)
        return candidate
    # A provider can briefly expose only a fraction of the station catalogue
    # while its reference data warms. Never replace a known-complete snapshot
    # with that truncated result.
    if len(rows) >= _MIN_PUBLIC_STATION_DIRECTORY:
        return rows
    return candidate


def _operator_rows() -> list[dict]:
    global _operator_status_cache
    computed_at, rows = _operator_status_cache
    if time.monotonic() - computed_at > 30:
        from .line_status import operator_status
        rows = operator_status(_live_trains())
        _operator_status_cache = (time.monotonic(), rows)
    return rows


# Building a page with a live board is heavy. A search engine crawling
# thousands at once must never start duplicate builds for the same page, but
# one slow station must not make unrelated station, operator or index pages
# return 503. Locks are therefore scoped to a cache key.
_seo_build_locks: dict[str, threading.Lock] = {}
_seo_build_locks_guard = threading.Lock()
_seo_heavy_build_slots = threading.BoundedSemaphore(1)
_seo_cache_lock = threading.Lock()


def _seo_lock_for(key: str) -> threading.Lock:
    with _seo_build_locks_guard:
        return _seo_build_locks.setdefault(key, threading.Lock())


def _seo_busy() -> HTTPException:
    """Tell crawlers to retry instead of indexing an incomplete fallback page."""
    return HTTPException(
        status_code=503,
        detail="Busy, try again shortly",
        headers={"Retry-After": "30", "Cache-Control": "no-store"},
    )


def _cached_page(key: str, seconds: int, build, light=None, *, heavy: bool = False) -> Response:
    now = time.monotonic()
    with _seo_cache_lock:
        hit = _seo_page_cache.get(key)
    if hit and now - hit[0] <= seconds:
        return Response(hit[1], media_type="text/html", headers={"Cache-Control": f"public, max-age={seconds}"})
    build_lock = _seo_lock_for(key)
    if not build_lock.acquire(timeout=0.5 if light is None else 0):
        if hit:
            return Response(hit[1], media_type="text/html", headers={"Cache-Control": "public, max-age=60"})
        if light is not None:
            return Response(light(), media_type="text/html", headers={"Cache-Control": "public, max-age=60"})
        raise _seo_busy()
    try:
        # Another request may have filled the cache while this one waited for
        # the same key. Recheck inside the lock to avoid duplicate live-board
        # calls and duplicate rendering work.
        now = time.monotonic()
        with _seo_cache_lock:
            hit = _seo_page_cache.get(key)
        if hit and now - hit[0] <= seconds:
            return Response(hit[1], media_type="text/html", headers={"Cache-Control": f"public, max-age={seconds}"})

        heavy_slot = False
        if heavy:
            heavy_slot = _seo_heavy_build_slots.acquire(blocking=False)
            if not heavy_slot:
                if hit:
                    return Response(hit[1], media_type="text/html", headers={"Cache-Control": "public, max-age=60"})
                if light is not None:
                    return Response(light(), media_type="text/html", headers={"Cache-Control": "public, max-age=60"})
                raise _seo_busy()
        try:
            hit = (time.monotonic(), build())
            with _seo_cache_lock:
                _seo_page_cache[key] = hit
                if len(_seo_page_cache) > 3000:
                    for old in sorted(_seo_page_cache, key=lambda k: _seo_page_cache[k][0])[:500]:
                        _seo_page_cache.pop(old, None)
        finally:
            if heavy_slot:
                _seo_heavy_build_slots.release()
    finally:
        build_lock.release()
    return Response(hit[1], media_type="text/html", headers={"Cache-Control": f"public, max-age={seconds}"})


@app.get("/stations", include_in_schema=False)
def stations_page(request: Request):
    from .seo_pages import stations_index
    origin = _site_origin(request)
    return _cached_page("stations", 3600, lambda: stations_index(origin, _stations()))


@app.get("/stations/{letter}", include_in_schema=False)
def stations_letter_page_route(letter: str, request: Request):
    from .seo_pages import stations_letter_page
    wanted = str(letter).strip().upper()
    if not re.fullmatch(r"[A-Z]", wanted):
        raise HTTPException(status_code=404, detail="Station directory page not found")
    if letter != letter.lower():
        return RedirectResponse(f"/stations/{wanted.lower()}", status_code=301)
    stations = _stations()
    if not any(row["name"][:1].upper() == wanted for row in stations):
        raise HTTPException(status_code=404, detail="Station directory page not found")
    origin = _site_origin(request)
    return _cached_page(f"stations:{wanted}", 3600, lambda: stations_letter_page(origin, wanted, stations))


@app.get("/station/{name}", include_in_schema=False)
def station_page_route(name: str, request: Request):
    from .seo_pages import crs_from_path, station_page
    crs = crs_from_path(name)
    station = next((row for row in _stations() if row["crs"] == crs), None) if crs else None
    if station is None:
        raise HTTPException(status_code=404, detail="Station not found")
    if name != station["path"].rsplit("/", 1)[-1]:
        return RedirectResponse(station["path"], status_code=301)  # one address per station
    origin = _site_origin(request)

    def build() -> str:
        board = None
        if smart_places is not None:
            try:
                board = smart_places.rail_board(crs, 16)
            except Exception:
                board = None
        others = sorted(
            (row for row in _stations() if row["crs"] != crs),
            key=lambda row: _haversine_km(station["lat"], station["lon"], row["lat"], row["lon"]),
        )[:6]
        facts: dict = {}
        if smart_places is not None:
            try:
                facts["direct"] = smart_places.direct_destinations(crs)
            except Exception:
                facts["direct"] = None
            try:
                facts["bus_stops"] = smart_places.bus_stops_near(station["lat"], station["lon"])
            except Exception:
                pass
        # Each direct destination links to its route page ("Hinckley to Leicester trains").
        from .route_pages import route_path
        paths = {row["crs"]: route_path(station, row) for row in _stations() if row["crs"] != crs}
        return station_page(origin, station, board, others, _operator_rows(), settings.trainline_affiliate_url, facts, paths)
    # A reduced station shell is not a safe crawl fallback: at scale it makes
    # thousands of distinct URLs look like thin duplicates. The cache helper
    # will serve an expired complete copy while this page is rebuilding, or a
    # non-cacheable 503 when no complete copy exists yet.
    return _cached_page(f"station:{crs}", 600, build, heavy=True)


@app.get("/trains/{pair}", include_in_schema=False)
def route_page_route(pair: str, request: Request):
    from .route_pages import resolve, route_page, route_path
    found = resolve(pair, _stations())
    if found is None:
        raise HTTPException(status_code=404, detail="Route not found")
    frm, to = found
    if pair != route_path(frm, to).rsplit("/", 1)[-1]:
        return RedirectResponse(route_path(frm, to), status_code=301)
    origin = _site_origin(request)

    def build() -> str:
        data = None
        if smart_places is not None:
            try:
                data = smart_places.direct_trains(frm["crs"], to["crs"])
            except Exception:
                data = None
        return route_page(origin, frm, to, data, settings.trainline_affiliate_url)
    return _cached_page(f"route:{frm['crs']}:{to['crs']}", 600, build, heavy=True)


@app.get("/operators", include_in_schema=False)
def operators_page(request: Request):
    from .seo_pages import operators_index
    origin = _site_origin(request)
    return _cached_page("operators", 90, lambda: operators_index(origin, _operator_rows()))


@app.get("/operators/league-table", include_in_schema=False)
def operators_league_page(request: Request):
    from .seo_pages import operators_league
    return Response(operators_league(_site_origin(request)), media_type="text/html",
                    headers={"Cache-Control": "public, max-age=3600"})


@app.get("/operator/{name}", include_in_schema=False)
def operator_page_route(name: str, request: Request):
    from .line_status import PASSENGER_OPERATORS
    from .seo_pages import operator_page, slug
    wanted = next((op for op in sorted(set(PASSENGER_OPERATORS.values())) if slug(op) == name), None)
    if wanted is None:
        raise HTTPException(status_code=404, detail="Operator not found")
    origin = _site_origin(request)

    def build() -> str:
        row = next((r for r in _operator_rows() if r["operator"] == wanted), None) or {
            "operator": wanted, "status": "unknown", "label": "No trains reporting", "running": 0, "late": 0,
            "cancelled": 0, "average_delay": 0, "worst": []}
        return operator_page(origin, row)
    return _cached_page(f"operator:{wanted}", 90, build)


@app.get("/discover-archive", include_in_schema=False)
def discover_archive_page(request: Request):
    from .discover_archive import archive_index
    days = history_store.sighting_days() if history_store is not None else []
    return _cached_page("discover-archive", 300, lambda: archive_index(_site_origin(request), days))


@app.get("/discover/{day}", include_in_schema=False)
def discover_day_page(day: str, request: Request):
    from .discover_archive import day_page
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) or history_store is None:
        raise HTTPException(status_code=404, detail="No archive for that day")
    rows = history_store.sightings_on(day)
    if not rows:
        raise HTTPException(status_code=404, detail="No archive for that day")
    days = sorted(d for d, _ in history_store.sighting_days(limit=400))
    i = days.index(day) if day in days else -1
    previous = days[i - 1] if i > 0 else None
    following = days[i + 1] if 0 <= i < len(days) - 1 else None
    return _cached_page(f"discover:{day}", 300, lambda: day_page(_site_origin(request), day, rows, previous, following))


@app.get("/api/airports")
def airports_api():
    """The airports with pages, for markers on the map."""
    from .airport_pages import AIRPORTS, airport_path
    return JSONResponse([{"name": a["name"], "iata": a["iata"], "lat": a["lat"], "lon": a["lon"], "path": airport_path(a)}
                         for a in AIRPORTS], headers={"Cache-Control": "public, max-age=86400"})


def _bus_routes() -> list[dict]:
    service = getattr(smart_places, "bus_timetables", None) if smart_places is not None else None
    if service is None:
        return []
    from .bus_timetable import route_index
    try:
        return route_index(service)
    except Exception:
        return []


@app.get("/buses", include_in_schema=False)
def buses_page(request: Request):
    from .bus_route_pages import buses_index
    return _cached_page("buses", 600, lambda: buses_index(_site_origin(request), _bus_routes()))


@app.get("/bus/{operator}", include_in_schema=False)
def bus_operator_page(operator: str, request: Request):
    from .bus_route_pages import operator_routes_page
    from .seo_pages import slug
    routes = [r for r in _bus_routes() if slug(r["operator"]) == operator]
    if not routes:
        raise HTTPException(status_code=404, detail="Bus operator not found")
    return _cached_page(f"bus-op:{operator}", 600, lambda: operator_routes_page(_site_origin(request), routes[0]["operator"], routes))


@app.get("/bus/{operator}/{route}", include_in_schema=False)
def bus_route_page(operator: str, route: str, request: Request):
    from .bus_route_pages import live_on_route, route_page
    from .bus_timetable import route_profile
    from .seo_pages import slug
    found = next((r for r in _bus_routes() if slug(r["operator"]) == operator and slug(r["route"]) == route), None)
    if found is None:
        raise HTTPException(status_code=404, detail="Bus route not found")
    origin = _site_origin(request)

    def build() -> str:
        profile = route_profile(smart_places.bus_timetables, found["region"], found["operator"], found["route"])
        if profile is None:
            raise HTTPException(status_code=404, detail="Bus route not found")
        buses = [row for row in state.all(include_trains=False, include_vessels=False, bus_limit=20000) if row.type == "bus"]
        return route_page(origin, profile, live_on_route(profile, buses))
    return _cached_page(f"bus:{operator}/{route}", 120, build, heavy=True)


@app.get("/towns", include_in_schema=False)
def towns_page(request: Request):
    from .town_pages import towns_index
    return Response(towns_index(_site_origin(request)), media_type="text/html",
                    headers={"Cache-Control": "public, max-age=3600"})


@app.get("/town/{name}", include_in_schema=False)
def town_page_route(name: str, request: Request):
    from .airport_pages import AIRPORTS, airport_path
    from .named_lines import LINES, line_path, on_track, resolve, track_for
    from .town_pages import BUS_KM, ROAD_KM, bbox, find, km, town_page
    town = find(name)
    if town is None:
        raise HTTPException(status_code=404, detail="Town not found")
    origin = _site_origin(request)

    def build() -> str:
        stations = _stations()
        centre = next((row for row in stations if row["crs"] == town[1]), None)
        if centre is None:
            raise HTTPException(status_code=503, detail="Station directory is loading", headers={"Retry-After": "60"})
        lat, lon = centre["lat"], centre["lon"]
        board = None
        if smart_places is not None:
            try:
                board = smart_places.rail_board(town[1], 12)
            except Exception:
                board = None
        buses = [row for row in state.all(bbox=bbox(lat, lon, BUS_KM), include_trains=False, include_vessels=False, bus_limit=3000)
                 if row.type == "bus" and km(lat, lon, row.lat, row.lon) <= BUS_KM]
        roads = sorted((e for e in state.all_events(bbox=bbox(lat, lon, ROAD_KM))
                        if e.type in {"road", "incident"} and km(lat, lon, e.lat, e.lon) <= ROAD_KM),
                       key=lambda e: (e.type != "incident", km(lat, lon, e.lat, e.lon)))
        lines = []
        for line in LINES:
            points = resolve(line, stations)
            track = track_for(line, points, build=False)
            if any(p["crs"] == town[1] for p in points) or (track is not None and on_track(track, lat, lon) is not None):
                lines.append({"name": line["name"], "path": line_path(line["name"])})
        nearest = min(AIRPORTS, key=lambda a: km(lat, lon, a["lat"], a["lon"]))
        distance = km(lat, lon, nearest["lat"], nearest["lon"])
        airport = {"name": nearest["name"], "path": airport_path(nearest), "km": distance} if distance <= 90 else None
        return town_page(origin, town, centre, stations, board, buses, roads, lines, airport)
    return _cached_page(f"town:{town[1]}", 120, build, heavy=True)


@app.get("/airports", include_in_schema=False)
def airports_page(request: Request):
    from .airport_pages import airports_index
    return Response(airports_index(_site_origin(request)), media_type="text/html",
                    headers={"Cache-Control": "public, max-age=3600"})


@app.get("/airport/{name}", include_in_schema=False)
def airport_page_route(name: str, request: Request):
    from .airport_pages import RADIUS_NM, airport_page, find
    airport = find(name)
    if airport is None:
        raise HTTPException(status_code=404, detail="Airport not found")
    origin = _site_origin(request)

    def build() -> str:
        planes = []
        if adsb_sar_collector is not None:
            try:
                planes = adsb_sar_collector.fetch_nearby(airport["lat"], airport["lon"], RADIUS_NM)[0]
            except Exception:
                planes = []
        return airport_page(origin, airport, planes)
    return _cached_page(f"airport:{airport['iata']}", 60, build)


@app.get("/railway-lines", include_in_schema=False)
def railway_lines_page(request: Request):
    from .named_lines import lines_index
    return Response(lines_index(_site_origin(request)), media_type="text/html",
                    headers={"Cache-Control": "public, max-age=3600"})


@app.get("/railway-line/{name}", include_in_schema=False)
def railway_line_page(name: str, request: Request):
    from .named_lines import LINES, line_page, resolve
    from .seo_pages import slug
    line = next((row for row in LINES if slug(row["name"]) == name), None)
    if line is None:
        raise HTTPException(status_code=404, detail="Railway line not found")
    origin = _site_origin(request)
    return _cached_page(f"line:{name}", 90, lambda: line_page(origin, line, resolve(line, _stations()), _live_trains()))


@app.get("/guides", include_in_schema=False)
def guides_page(request: Request):
    from .seo_pages import guides_index
    return Response(guides_index(_site_origin(request)), media_type="text/html",
                    headers={"Cache-Control": "public, max-age=3600"})


@app.get("/guides/{name}", include_in_schema=False)
def guide_page_route(name: str, request: Request):
    from .seo_pages import guide_page, guide_slugs
    if name not in guide_slugs():
        raise HTTPException(status_code=404, detail="Guide not found")
    return Response(guide_page(_site_origin(request), name), media_type="text/html",
                    headers={"Cache-Control": "public, max-age=3600"})


@app.get("/military-aircraft-tracker", include_in_schema=False)
@app.get("/helicopter-tracker", include_in_schema=False)
@app.get("/police-helicopter-tracker", include_in_schema=False)
@app.get("/air-ambulance-tracker", include_in_schema=False)
@app.get("/what-plane-is-above-me", include_in_schema=False)
def aircraft_landing_page_route(request: Request):
    from .aircraft_seo_pages import aircraft_landing_page
    return Response(
        aircraft_landing_page(_site_origin(request), request.url.path),
        media_type="text/html",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@app.get("/sitemap.xml", include_in_schema=False)
def sitemap(request: Request):
    from .aircraft_seo_pages import aircraft_landing_paths
    from .line_status import PASSENGER_OPERATORS
    from .seo_pages import guide_slugs, operator_path, station_letter_path
    origin = html_escape(_site_origin(request), quote=True)
    stations = _stations()
    if settings.public_deployment and len(stations) < _MIN_PUBLIC_STATION_DIRECTORY:
        # A partial 200 sitemap can cause Google to forget thousands of valid
        # station URLs. Ask the crawler to retry until the stable directory is
        # available instead.
        raise HTTPException(status_code=503, detail="Station directory is loading",
                            headers={"Retry-After": "60", "Cache-Control": "no-store"})
    letters = sorted({row["name"][:1].upper() for row in stations if re.fullmatch(r"[A-Z]", row["name"][:1].upper())})
    from .airport_pages import AIRPORTS, airport_path
    from .named_lines import LINES, line_path
    from .town_pages import TOWNS, town_path
    from .bus_route_pages import operator_path as bus_operator_path, route_path as bus_route_path
    urls = ("/", "/about", "/privacy", "/terms", "/data-licences", "/stations", "/operators", "/guides",
            "/operators/league-table",
            "/railway-lines", *(line_path(line["name"]) for line in LINES),
            "/airports", *(airport_path(a) for a in AIRPORTS),
            "/towns", *(town_path(t[0]) for t in TOWNS),
            "/buses", *dict.fromkeys(bus_operator_path(r["operator"]) for r in _bus_routes()),
            *(bus_route_path(r["operator"], r["route"]) for r in _bus_routes()),
            "/discover-archive", *(f"/discover/{d}" for d, n in (history_store.sighting_days(limit=400) if history_store else []) if n >= 3),
            *aircraft_landing_paths(),
            *(f"/guides/{slug}" for slug in guide_slugs()),
            *(station_letter_path(letter) for letter in letters),
            *(operator_path(op) for op in sorted(set(PASSENGER_OPERATORS.values()))),
            *(row["path"] for row in stations))
    entries = "".join(f"<url><loc>{origin}{path}</loc></url>" for path in urls)
    body = f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{entries}</urlset>'
    return Response(body, media_type="application/xml", headers={"Cache-Control": "public, max-age=3600"})


@app.get("/data-licences", include_in_schema=False)
def data_licences(request: Request):
    return _render_public_html("data-licences.html", request)


def _legal_page(filename: str, request: Request) -> Response:
    page = (LEGAL_DIR / filename).read_text(encoding="utf-8")
    seller = settings.seller_legal_name or "Seller details pending"
    email = settings.support_email or "Support contact pending"
    page = page.replace("{{SELLER_LEGAL_NAME}}", html_escape(seller))
    page = page.replace("{{SUPPORT_EMAIL}}", html_escape(email))
    page = page.replace("{{SUPPORT_EMAIL_URL}}", quote(email, safe="@.+-_"))
    page = page.replace("{{PUBLIC_BASE_URL}}", html_escape(_site_origin(request), quote=True))
    return Response(
        page,
        media_type="text/html",
        headers={"Cache-Control": "public, max-age=300, stale-while-revalidate=3600"},
    )


@app.get("/terms", include_in_schema=False)
def service_terms(request: Request):
    return _legal_page("terms.html", request)


@app.get("/privacy", include_in_schema=False)
def privacy_notice(request: Request):
    return _legal_page("privacy.html", request)


def _share_payload_for(item) -> dict:
    """The safe shared facts for an item, with the nearest station for trains and buses."""
    nearest = None
    if item.type in {"train", "bus"}:
        best = None
        for station in _passenger_stations():
            distance = _haversine_km(item.lat, item.lon, station["lat"], station["lon"])
            if distance <= 15 and (best is None or distance < best[0]):
                best = (distance, station["name"])
        nearest = best[1] if best else None
    return safe_share_payload(item, nearest)


def _share_preview_text(payload: dict) -> tuple[str, str]:
    from .share_card import card_lines
    lines = card_lines(payload)
    running = lines["running"][0] if lines["running"] else None
    title = " · ".join(x for x in (lines["title"], lines["line"]) if x)
    description = " · ".join(x for x in (running, lines["where"], lines["details"], payload.get("what_it_is")) if x)
    if not description:
        kind = {"train": "train", "bus": "bus", "aircraft": "aircraft", "sar": "rescue aircraft"}.get(payload.get("type"), "activity")
        where = "n" + lines["line"][1:] if lines["line"].startswith("Near ") else ""
        description = f"Live {kind} {where}".strip() + " · follow it on the live map"
    return title, description


_card_cache: dict[str, tuple[float, bytes]] = {}


@app.get("/card.png", include_in_schema=False)
def site_card():
    from .share_card import render
    now = time.monotonic()
    hit = _card_cache.get("")
    if not hit or now - hit[0] > 86400:
        hit = (now, render(None))
        _card_cache[""] = hit
    return Response(hit[1], media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})


@app.get("/live/{activity_id:path}/card.png", include_in_schema=False)
def shared_live_card(activity_id: str):
    """The preview picture for a shared link: 1200 x 630, no coordinates."""
    from .share_card import render
    item = state.get(activity_id)
    if item is None:
        return site_card()
    now = time.monotonic()
    hit = _card_cache.get(activity_id)
    if not hit or now - hit[0] > 120:
        hit = (now, render(_share_payload_for(item)))
        _card_cache[activity_id] = hit
        if len(_card_cache) > 500:  # keep the cache small
            for key in sorted(_card_cache, key=lambda k: _card_cache[k][0])[:100]:
                _card_cache.pop(key, None)
    return Response(hit[1], media_type="image/png", headers={"Cache-Control": "public, max-age=120"})


@app.get("/live/{activity_id:path}", include_in_schema=False)
def shared_live_page(activity_id: str, request: Request):
    """The shared page, with this item's title and picture for link previews."""
    page = (STATIC_DIR / "share.html").read_text(encoding="utf-8")
    item = state.get(activity_id)
    origin = _site_origin(request)
    if item is not None:
        title, description = _share_preview_text(_share_payload_for(item))
        image = f"{origin}/live/{quote(activity_id, safe='')}/card.png"
    else:
        title, description, image = "UK Activity Radar", "Live UK trains, buses, aircraft and roads on one map.", f"{origin}/card.png"
    url = f"{origin}/live/{quote(activity_id, safe='')}"
    tags = "".join([
        f'<meta property="og:type" content="website"><meta property="og:site_name" content="UK Activity Radar">',
        f'<meta property="og:title" content="{html_escape(title, quote=True)}">',
        f'<meta property="og:description" content="{html_escape(description, quote=True)}">',
        f'<meta property="og:url" content="{html_escape(url, quote=True)}">',
        f'<meta property="og:image" content="{html_escape(image, quote=True)}"><meta property="og:image:width" content="1200"><meta property="og:image:height" content="630">',
        f'<meta name="twitter:card" content="summary_large_image"><meta name="twitter:title" content="{html_escape(title, quote=True)}">',
        f'<meta name="twitter:description" content="{html_escape(description, quote=True)}"><meta name="twitter:image" content="{html_escape(image, quote=True)}">',
    ])
    page = page.replace("<title>Live activity — UK Activity Radar</title>", f"<title>{html_escape(title)} — UK Activity Radar</title>{tags}", 1)
    return Response(page, media_type="text/html", headers={"Cache-Control": "no-cache"})


@app.get("/service-worker.js", include_in_schema=False)
def service_worker():
    """The worker, served from the top of the site.

    A worker only covers pages at or below its own path. Served from
    /static/ it never covered the app's page at "/", so phone alerts (which
    wait for a worker covering the page) could not be switched on.
    """
    return FileResponse(
        STATIC_DIR / "service-worker.js", media_type="text/javascript",
        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"},
    )


def _portal_login_url() -> str:
    url = str(getattr(settings, "stripe_portal_login_url", "") or "").strip()
    return url if re.fullmatch(r"https://billing\.stripe\.com/p/login/[A-Za-z0-9_-]{6,80}", url) else ""


@app.get("/api/billing/status")
def billing_status(request: Request, response: Response):
    context = _billing_context(request)
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Vary"] = "Cookie"
    return {
        "enabled": bool(billing_service),
        "plan": context.plan,
        "plus": context.plus,
        "subscription_status": context.subscription_status,
        "features": sorted(context.features),
        "price_display": settings.radar_plus_price_display,
        "yearly_available": bool(billing_service and settings.stripe_plus_yearly_price_id),
        "yearly_price_display": settings.radar_plus_yearly_price_display,
        "trial_days": settings.radar_plus_trial_days if billing_service else 0,
        "portal_login_url": _portal_login_url() if billing_service else "",
        "preview_available": len(settings.radar_plus_preview_key) >= 24,
        "free_features": ["live_map", "core_layers", "basic_cool", "field_guide", "basic_my_radar"],
        "plus_features": sorted(PLUS_FEATURES),
    }


@app.post("/api/billing/preview")
def billing_preview(payload: RadarPlusPreviewIn, request: Request):
    """Grant an HTTP-only owner preview cookie without requiring Stripe."""
    _require_same_origin_post(request)
    _enforce_public_budget(request, lookup_request_budget)
    secret = settings.radar_plus_preview_key
    if len(secret) < 24 or not hmac.compare_digest(payload.code.strip(), secret):
        raise HTTPException(status_code=403, detail="Preview code not recognised")
    response = JSONResponse({"ok": True, "plus": True})
    response.set_cookie(
        PREVIEW_COOKIE,
        _preview_cookie_value(),
        max_age=60 * 60 * 24 * 14,
        path="/",
        secure=True,
        httponly=True,
        samesite="lax",
    )
    response.headers["Cache-Control"] = "private, no-store"
    return response


@app.get("/api/weather/radar")
def rain_radar_index(response: Response):
    """Which rain radar frames are available, where they go and how to credit them."""
    response.headers["Cache-Control"] = "public, max-age=60"
    return {**rain_radar.index(), "enabled": settings.rain_radar_enabled}


@app.get("/api/weather/radar/{key}.png", include_in_schema=False)
def rain_radar_frame(key: str):
    if not re.fullmatch(r"\d{8}T\d{4}", key):
        raise HTTPException(status_code=404, detail="No such radar frame")
    png = rain_radar.png(key)
    if png is None:
        raise HTTPException(status_code=404, detail="That radar frame is no longer kept")
    # A frame never changes once made.
    return Response(content=png, media_type="image/png", headers={"Cache-Control": "public, max-age=86400, immutable"})


@app.get("/api/alerts/status")
def commute_alert_status(request: Request, response: Response):
    device_token = request.headers.get("x-radar-device") or ""
    saved = commute_alert_store.status(device_token) if commute_alert_store is not None and device_token else None
    response.headers["Cache-Control"] = "private, no-store"
    return {
        "configured": settings.push_alerts_ready,
        "public_key": settings.vapid_public_key if settings.push_alerts_ready else None,
        "subscribed": bool(saved and saved["active"]),
        "preferences": saved["preferences"] if saved and saved["active"] else None,
        "requires_https": True,
    }


FREE_SAVED_TRAIN_ALERTS = 1


def _free_alert_allowance(preferences: dict, request: Request) -> dict:
    """Without Radar+ (once payments are on): alerts for one saved train only.

    While payments are off nobody can buy Radar+, so nothing is limited.
    """
    if billing_service is None or _billing_context(request).has("alerts_pro"):
        return preferences
    trimmed = dict(preferences or {})
    trimmed["saved_trains"] = list(trimmed.get("saved_trains") or [])[:FREE_SAVED_TRAIN_ALERTS]
    for key in ("spotter", "journey"):
        trimmed[key] = None
    trimmed["leave_plans"] = []
    trimmed["mode"] = "trains-only"
    return trimmed


@app.post("/api/alerts/subscribe")
def subscribe_commute_alerts(payload: CommuteAlertIn, request: Request):
    _require_same_origin_post(request)
    _enforce_public_budget(request, lookup_request_budget)
    if commute_alert_store is None or not settings.push_alerts_ready:
        raise HTTPException(status_code=503, detail="Commute alerts will become available when Web Push is configured on the hosted site")
    preferences = _free_alert_allowance(payload.preferences, request)
    try:
        commute_alert_store.upsert(payload.device_token, payload.subscription, preferences)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "message": "Commute alerts are active on this device"}


@app.post("/api/alerts/test")
async def test_commute_alert(payload: CommuteAlertRemoveIn, request: Request):
    """Send one notification to this device, to prove alerts reach it."""
    _require_same_origin_post(request)
    _enforce_public_budget(request, lookup_request_budget)
    if commute_alert_store is None or not settings.push_alerts_ready:
        raise HTTPException(status_code=503, detail="Alerts are not available")
    saved = commute_alert_store.subscription_for(payload.device_token)
    if saved is None:
        raise HTTPException(status_code=404, detail="Alerts are not on for this device")
    try:
        await asyncio.to_thread(_send_commute_push, saved.subscription, {
            "title": "Alerts are working", "body": "This is how UK Activity Radar will tell you about your train.",
            "url": f"{settings.public_base_url}/", "tag": "radar-test", "mode": "test",
        })
    except Exception as exc:
        if getattr(exc, "status_code", None) in {404, 410} or getattr(getattr(exc, "response", None), "status_code", None) in {404, 410}:
            commute_alert_store.deactivate(saved.endpoint_hash)
            raise HTTPException(status_code=410, detail="This device's alert registration has expired. Turn alerts on again.") from exc
        raise HTTPException(status_code=502, detail="The test alert could not be sent") from exc
    return {"ok": True}


@app.post("/api/alerts/unsubscribe")
def unsubscribe_commute_alerts(payload: CommuteAlertRemoveIn, request: Request):
    _require_same_origin_post(request)
    if commute_alert_store is not None:
        commute_alert_store.remove(payload.device_token)
    return {"ok": True}


@app.post("/api/billing/checkout")
def billing_checkout(request: Request, response: Response, period: str = "month"):
    _require_same_origin_post(request)
    _enforce_public_budget(request, lookup_request_budget)
    if billing_service is None:
        raise HTTPException(status_code=503, detail="Radar+ checkout is not configured yet")
    if period not in {"month", "year"}:
        raise HTTPException(status_code=400, detail="Choose monthly or yearly")
    try:
        started = billing_service.create_checkout(period=period)
    except BillingError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    response.set_cookie(
        CHECKOUT_COOKIE,
        started.state_token,
        max_age=max(60, started.expires_at - int(time.time())),
        path="/",
        secure=True,
        httponly=True,
        samesite="lax",
    )
    response.headers["Cache-Control"] = "private, no-store"
    return {"checkout_url": started.checkout_url}


@app.get("/billing/success", include_in_schema=False)
def billing_success(request: Request, session_id: str):
    if billing_service is None:
        return RedirectResponse(url="/?billing=unavailable", status_code=303)
    state_token = request.cookies.get(CHECKOUT_COOKIE) or ""
    try:
        confirmation = billing_service.confirm_checkout(session_id, state_token)
    except BillingPaymentPending:
        return RedirectResponse(url="/?billing=pending", status_code=303)
    except BillingError:
        return RedirectResponse(url="/?billing=failed", status_code=303)
    response = RedirectResponse(url="/?billing=success", status_code=303)
    response.set_cookie(
        ACCESS_COOKIE,
        confirmation.access_token,
        max_age=billing_configuration.access_ttl_seconds,
        path="/",
        secure=True,
        httponly=True,
        samesite="lax",
    )
    response.delete_cookie(CHECKOUT_COOKIE, path="/", secure=True, httponly=True, samesite="lax")
    response.headers["Cache-Control"] = "private, no-store"
    return response


@app.post("/api/billing/portal")
def billing_portal(request: Request):
    _require_same_origin_post(request)
    if billing_service is None:
        raise HTTPException(status_code=503, detail="Radar+ billing is not configured yet")
    try:
        url = billing_service.create_portal(request.cookies.get(ACCESS_COOKIE) or "")
    except BillingAuthenticationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except BillingError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"portal_url": url}


@app.post("/api/billing/logout")
def billing_logout(request: Request):
    _require_same_origin_post(request)
    token = request.cookies.get(ACCESS_COOKIE)
    if billing_store is not None and token:
        billing_store.revoke_access_token(token)
    response = Response(status_code=204)
    response.delete_cookie(ACCESS_COOKIE, path="/", secure=True, httponly=True, samesite="lax")
    response.delete_cookie(PREVIEW_COOKIE, path="/", secure=True, httponly=True, samesite="lax")
    response.headers["Cache-Control"] = "private, no-store"
    return response


class DeviceLinkRequest(BaseModel):
    code: str = Field(min_length=4, max_length=16)


@app.post("/api/billing/device-code")
def billing_device_code(request: Request):
    """A one-time code to add this subscription to another device."""
    _require_same_origin_post(request)
    if billing_store is None or billing_service is None:
        raise HTTPException(status_code=503, detail="Radar+ billing is not configured yet")
    context = _billing_context(request)
    if not (context.plus and context.customer_id and context.token_valid):
        raise HTTPException(status_code=401, detail="Radar+ is not active on this device")
    code = billing_store.create_device_code(context.customer_id)
    return JSONResponse({"code": code, "expires_in": 600}, headers={"Cache-Control": "private, no-store"})


@app.post("/api/billing/device-link")
def billing_device_link(request: Request, payload: DeviceLinkRequest):
    """Turn on Radar+ here with a code from a device that already has it."""
    _require_same_origin_post(request)
    _enforce_public_budget(request, device_code_budget)
    if billing_store is None or billing_service is None:
        raise HTTPException(status_code=503, detail="Radar+ billing is not configured yet")
    customer_id = billing_store.consume_device_code(payload.code)
    if not customer_id:
        raise HTTPException(status_code=400, detail="That code is not right or has run out. Get a new one on the device that has Radar+.")
    token = billing_store.issue_access_token(customer_id, ttl_seconds=billing_configuration.access_ttl_seconds)
    response = JSONResponse({"ok": True}, headers={"Cache-Control": "private, no-store"})
    response.set_cookie(ACCESS_COOKIE, token, max_age=billing_configuration.access_ttl_seconds,
                        path="/", secure=True, httponly=True, samesite="lax")
    return response


@app.post("/api/billing/webhook", include_in_schema=False)
async def billing_webhook(request: Request):
    if billing_service is None:
        raise HTTPException(status_code=404, detail="Not found")
    raw = await request.body()
    try:
        result = billing_service.handle_webhook(raw, request.headers.get("stripe-signature") or "")
    except BillingWebhookError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "received": True,
        "processed": result.processed,
        "duplicate": result.duplicate,
        "ignored": result.ignored,
    }


@app.get("/api/health/runtime")
async def health_runtime():
    """Answers even when every worker thread is busy (it needs none)."""
    return JSONResponse(runtime_report(), headers={"Cache-Control": "no-store"})


@app.get("/api/health")
def health():
    mode = "live" if settings.network_rail_enabled else ("demo" if settings.demo_mode else "setup")
    counts = state.type_counts({
        "bus": 5,
        "vessel": 10,
        "sar": 10,
        "gritter": 10,
        "aircraft": 10,
        "satellite": 10,
    })
    storage = _storage_payload()
    return {
        "ok": True,
        "version": APP_VERSION,
        "outbound_proxy_blocked": bool(blocked_proxy_variables()),
        "mode": mode,
        "network_rail_credentials_present": settings.credentials_present,
        "open_trip_planner_configured": bool(settings.otp_graphql_url),
        "mapped_rail_locations": _mapped_location_count(),
        "schedule_count": schedules.count(),
        "td_enabled": settings.td_enabled,
        "vstp_enabled": settings.vstp_enabled,
        "bods_enabled": settings.bods_enabled,
        "bods_api_key_present": bool(settings.bods_api_key),
        "live_bus_count": counts.get("bus", 0),
        "bods_sx_enabled": settings.bods_sx_enabled,
        "national_highways_enabled": settings.national_highways_enabled,
        "national_highways_api_key_present": bool(settings.national_highways_api_key),
        "road_event_count": counts.get("road", 0) + counts.get("incident", 0),
        "street_manager_enabled": settings.street_manager_enabled,
        "street_manager_callback_path": "/api/integrations/street-manager" if settings.street_manager_enabled else None,
        "traffic_flow_enabled": settings.traffic_flow_enabled,
        "traffic_flow_api_key_present": bool(settings.tomtom_traffic_api_key),
        "traffic_flow_ready": settings.traffic_flow_ready,
        "base_map_provider": "Mapbox" if settings.mapbox_public_token else "TomTom fallback",
        "base_map_ready": bool(settings.mapbox_public_token or (traffic_flow_service and traffic_flow_service.map_ready)),
        "commercial_profile": bool(settings.public_deployment and settings.commercial_mode),
        "ea_floods_enabled": settings.ea_floods_enabled,
        "flood_event_count": counts.get("flood", 0),
        "aisstream_enabled": settings.aisstream_enabled,
        "aisstream_api_key_present": bool(settings.aisstream_api_key),
        "live_vessel_count": counts.get("vessel", 0),
        "live_sar_count": counts.get("sar", 0),
        "rnli_enabled": settings.rnli_enabled,
        "rnli_launch_count": counts.get("rescue", 0),
        "adsb_sar_enabled": settings.adsb_sar_enabled,
        "adsb_sar_count": counts.get("sar", 0),
        "london_fire_enabled": settings.london_fire_enabled,
        "met_office_enabled": settings.met_office_enabled,
        "weather_warning_count": counts.get("weather", 0),
        "earthquake_count": counts.get("earthquake", 0),
        "river_level_count": counts.get("river", 0),
        "tide_gauge_count": counts.get("tide", 0),
        "wave_buoy_count": counts.get("wave", 0),
        "grid_region_count": counts.get("grid", 0),
        "air_quality_count": counts.get("airquality", 0),
        "bike_point_count": counts.get("bike", 0),
        "power_outage_count": counts.get("outage", 0),
        "gritter_count": counts.get("gritter", 0),
        "interesting_aircraft_count": counts.get("aircraft", 0),
        "satellite_count": counts.get("satellite", 0),
        "storage_healthy": storage["healthy"],
        "storage_free_mb": storage["free_mb"],
        "billing_enabled": bool(billing_service),
        "runtime": runtime_report(),
        "seller_details_ready": settings.seller_details_ready,
    }


@app.get("/api/diagnostics")
def diagnostics():
    _require_local_debug_route()
    return {
        "version": APP_VERSION,
        "mapped_stanox": _mapped_location_count(),
        "schedule_count": schedules.count(),
        "schedule_last_sync": schedules.last_sync(),
        "files_mb": {
            "schedule_db": _file_mb(SCHEDULE_DB),
            "schedule_download": _file_mb(DATA_DIR / "schedule-full.dat"),
            "smart": _file_mb(SMART_FILE),
            "stations": _file_mb(STATION_FILE),
            "naptan_cache": _file_mb(DATA_DIR / "naptan_rail.json"),
            "public_rail_geo": _file_mb(DATA_DIR / "public_rail_geo.json"),
        },
        "sources": [_source_payload(s) for s in state.sources()],
    }


@app.get("/api/vehicles")
def vehicles(type: str | None = None, q: str | None = None):
    _require_local_debug_route()
    items = state.all()
    if type and type != "all":
        items = [v for v in items if v.type == type]
    if q:
        needle = q.lower().strip()
        items = [
            v for v in items
            if needle in " ".join(filter(None, [v.name, v.subtitle, v.operator, v.route, v.origin, v.destination, v.headcode, v.train_uid, v.vehicle_ref, v.journey_ref])).lower()
        ]
    return [v.model_dump(mode="json") for v in items]


@app.get("/api/vehicles/{vehicle_id:path}")
def vehicle(vehicle_id: str):
    item = state.get(vehicle_id)
    if not item:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    return item.model_dump(mode="json")


@app.get("/api/aircraft/nearby")
def nearby_aircraft(request: Request, lat: float, lon: float, radius_miles: float = 25):
    """Resolve aircraft around one user-selected point without widening the background feed."""
    _enforce_public_budget(request, lookup_request_budget)
    if adsb_sar_collector is None:
        raise HTTPException(status_code=503, detail="Nearby aircraft feed is not ready")
    if not (48.0 <= lat <= 62.5 and -13.5 <= lon <= 5.0):
        raise HTTPException(status_code=400, detail="Location is outside UK aircraft coverage")
    miles = max(5.0, min(50.0, float(radius_miles)))
    radius_nm = max(5, round(miles / 1.15078))
    degraded = False
    warning = None
    source_name = None
    try:
        result = adsb_sar_collector.fetch_nearby(lat, lon, radius_nm)
        if len(result) == 3:
            rows, checked, source_name = result
        else:  # Compatibility with test/dry-run collectors from older builds.
            rows, checked = result
            source_name = "ADSB.lol"
    except Exception as exc:
        # The point feed is a useful enhancement, but the broad live collector
        # already holds verified aircraft positions.  Do not turn a temporary
        # upstream timeout/rate limit into an empty map (or, historically, a
        # 500 caused while formatting this error).  Return nearby retained
        # positions immediately and let the browser retry its point lookup.
        retained_lookup = getattr(adsb_sar_collector, "retained_nearby", None)
        try:
            rows, source_name = (
                retained_lookup(lat, lon, miles) if callable(retained_lookup) else ([], None)
            )
        except Exception:
            # A partially initialised rotating cache must never turn an
            # upstream timeout into a public 500. The state-store fallback
            # below is independent and remains safe during startup.
            rows, source_name = [], None
        if not rows:
            retained = state.all(
                max_age_minutes=10,
                include_trains=False,
                include_buses=False,
                include_vessels=False,
            )
            rows = [
                row for row in retained
                if row.type in {"aircraft", "sar"}
                and _haversine_km(lat, lon, float(row.lat), float(row.lon)) <= miles * 1.609344
            ]
            rows.sort(key=lambda row: _haversine_km(lat, lon, float(row.lat), float(row.lon)))
            if rows:
                source_name = "Retained UK aircraft feed"
        checked = len(rows)
        degraded = True
        warning = (
            "The live point lookup is retrying; showing the latest retained "
            "aircraft positions."
        )
        # Keep a safe diagnostic in server logs/source status without exposing
        # upstream internals to the visitor.
        try:
            state.set_source(SourceStatus(
                name="adsb_nearby", enabled=True, connected=False, mode="waiting",
                message=f"Nearby ADS-B lookup degraded • {public_error(exc)}",
            ))
        except Exception:
            pass
    # Start finding their photos now, so a tapped plane shows one at once.
    try:
        media_lookup.prefetch([
            ("sar" if row.type == "sar" else "aircraft", row.operator, row.aircraft_model or row.aircraft_type_code, row.name)
            for row in rows
            if row.aircraft_model or row.aircraft_type_code
        ])
    except Exception:
        pass
    if degraded:
        response_source = (
            source_name
            if source_name == "Retained UK aircraft feed"
            else (f"{source_name} retained snapshot" if source_name else "Retained UK aircraft feed")
        )
    else:
        response_source = f"{source_name} point feed" if source_name else "Live aircraft point feed"
    return {
        "activities": [row.model_dump(mode="json") for row in rows],
        "checked": checked,
        "radius_miles": miles,
        "source": response_source,
        "degraded": degraded,
        "warning": warning,
    }


@app.get("/api/events")
def events(type: str | None = None, q: str | None = None):
    _require_local_debug_route()
    items = state.all_events()
    if type and type != "all":
        items = [e for e in items if e.type == type]
    if q:
        needle = q.lower().strip()
        items = [e for e in items if needle in " ".join(filter(None, [e.name, e.subtitle, e.category, e.road, e.reason, e.source])).lower()]
    return [e.model_dump(mode="json") for e in items]




@app.get("/api/places")
def places(west: float, south: float, east: float, north: float, zoom: float = 10, mode: str = "all"):
    if smart_places is None:
        return {"places": [], "ready": False}
    bbox=(west,south,east,north)
    return {"places": smart_places.list_places(bbox, zoom, mode), "ready": True, "mode": mode}


@app.get("/api/places/nearest-rail")
def nearest_rail_place(request: Request, lat: float, lon: float):
    _enforce_public_budget(request, lookup_request_budget)
    if smart_places is None:
        raise HTTPException(status_code=503, detail="Smart places are not ready")
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise HTTPException(status_code=400, detail="Invalid coordinates")
    place=smart_places.nearest_rail_station(
        lat, lon, tomtom_api_key=settings.tomtom_traffic_api_key,
        tomtom_base_url=settings.tomtom_traffic_base_url,
    )
    if not place:
        raise HTTPException(status_code=404, detail="No rail-station geography is ready near this map point yet")
    return {"place":place}


@app.get("/api/places/nearest-bus")
def nearest_bus_place(request: Request, lat: float, lon: float):
    _enforce_public_budget(request, lookup_request_budget)
    if smart_places is None:
        raise HTTPException(status_code=503, detail="Smart places are not ready")
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise HTTPException(status_code=400, detail="Invalid coordinates")
    place=smart_places.nearest_bus_stop(lat,lon)
    if not place:
        raise HTTPException(status_code=404, detail="No cached bus stop is available near this location yet")
    return {"place":place}


@app.get("/api/places/{place_id:path}/board")
def place_board(place_id: str, request: Request, limit: int = 10):
    _enforce_public_budget(request, lookup_request_budget)
    if smart_places is None:
        raise HTTPException(status_code=503, detail="Smart places are not ready")
    place=smart_places.get_place(place_id)
    if not place:
        raise HTTPException(status_code=404, detail="Place not found")
    limit=max(1,min(25,int(limit)))
    typ=place.get("place_type")
    if typ == "rail_station": payload=smart_places.rail_board(place.get("crs") or "", limit)
    elif typ == "bus_stop": payload=smart_places.bus_board(place, limit)
    elif typ == "ferry_terminal": payload=smart_places.ferry_board(place, limit)
    elif typ == "rain_gauge": payload=smart_places.rain_board(place)
    else: payload={"rows": []}
    return {"place": place, "board": payload}


@app.get("/api/journeys/status")
def journey_status():
    return {
        "provider": "TomTom Routing",
        "configured": road_journey_planner.configured,
        "available": road_journey_planner.configured,
        "modes": ["car"],
        "message": "Traffic-aware driving routes are available" if road_journey_planner.configured else "Driving routes are not configured",
    }


@app.post("/api/journeys/plan")
def plan_journey(request: Request, plan: JourneyPlanIn):
    """Plan a traffic-aware driving journey without inventing transit routes."""
    _enforce_public_budget(request, lookup_request_budget)
    _require_plus(request, "journey_planner")
    mode = str(plan.mode or "car").strip().casefold()
    if mode != "car":
        raise HTTPException(status_code=400, detail="The journey planner currently supports driving routes only")
    coordinates = (
        plan.origin_lat, plan.origin_lon,
        plan.destination_lat, plan.destination_lon,
    )
    if road_journey_planner.configured:
        if any(value is None for value in coordinates):
            raise HTTPException(status_code=400, detail="Origin and destination coordinates are required for routing")
        try:
            result = road_journey_planner.plan(
                float(plan.origin_lat), float(plan.origin_lon),
                float(plan.destination_lat), float(plan.destination_lon),
                mode, from_name=plan.origin_name, to_name=plan.destination_name,
            )
            return _add_live_route_issues(result)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except requests.RequestException as exc:
            raise HTTPException(status_code=503, detail="TomTom routing is temporarily unavailable") from exc

    return {
        "status": "unavailable", "available": False,
        "provider": "TomTom Routing",
        "message": "Driving routes are temporarily unavailable.",
        "itineraries": [],
    }


@app.get("/api/environment/measure")
def environment_measure(request: Request, url: str):
    _enforce_public_budget(request, lookup_request_budget)
    if smart_places is None:
        raise HTTPException(status_code=503, detail="Smart places are not ready")
    try:
        return smart_places.environment_measure(url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Environment Agency lookup failed: {type(exc).__name__}: {exc}") from exc


@app.post("/api/integrations/street-manager", include_in_schema=False)
async def street_manager_webhook(request: Request):
    """Receive the signed DfT Street Manager public AWS SNS stream."""
    if not settings.street_manager_enabled or street_manager_store is None:
        raise HTTPException(status_code=404, detail="Not found")
    try:
        content_length = int(request.headers.get("content-length") or 0)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid content length")
    if content_length > 1_000_000:
        raise HTTPException(status_code=413, detail="Street Manager message is too large")
    raw = await request.body()
    if len(raw) > 1_000_000:
        raise HTTPException(status_code=413, detail="Street Manager message is too large")
    try:
        message = await asyncio.to_thread(verify_and_parse_sns_envelope, raw)
    except StreetManagerMessageError as exc:
        raise HTTPException(status_code=403, detail="Street Manager SNS message could not be verified") from exc

    if message.message_type == "SubscriptionConfirmation":
        confirmed = False
        if settings.street_manager_auto_confirm and message.subscribe_url:
            try:
                def _confirm_subscription() -> None:
                    response = requests.get(message.subscribe_url, timeout=12, allow_redirects=False)
                    response.raise_for_status()
                await asyncio.to_thread(_confirm_subscription)
                confirmed = True
            except requests.RequestException as exc:
                raise HTTPException(status_code=503, detail="Street Manager subscription confirmation failed") from exc
        state.set_source(SourceStatus(
            name="street_manager", enabled=True, connected=confirmed,
            mode="ready" if confirmed else "setup",
            message=(
                "Street Manager open-data subscription confirmed"
                if confirmed else
                "A valid Street Manager subscription request was received; automatic confirmation is disabled"
            ),
            last_message_at=datetime.now(timezone.utc), messages_seen=1,
        ))
        return {"accepted": True, "confirmed": confirmed}

    try:
        changed = street_manager_store.apply(message)
        events = street_manager_store.events()
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Street Manager snapshot could not be updated") from exc
    if changed:
        state.replace_events_for_source("DfT Street Manager Open Data", events)
    stats = street_manager_store.stats()
    state.set_source(SourceStatus(
        name="street_manager", enabled=True, connected=True,
        mode="live" if events else "ready",
        message=f"Street Manager open data • {len(events):,} active local-road events",
        last_message_at=datetime.now(timezone.utc),
        messages_seen=stats["objects"], movements_mapped=len(events),
    ))
    return {"accepted": True, "changed": changed, "active_events": len(events)}


@app.get("/api/traffic/status")
def traffic_status():
    if traffic_flow_service is None:
        return {"enabled": False, "configured": False, "provider": "TomTom Traffic Flow"}
    return traffic_flow_service.status_payload()


# Where "Book" buttons go. An affiliate link from the environment when one is
# set (so it can change without a release), else the operator's own site.
_BOOKING_SITES = {"national-express": "https://www.nationalexpress.com/"}


def _affiliate_url(partner: str) -> str | None:
    url = {"national-express": settings.national_express_affiliate_url}.get(partner, "")
    return url if url.startswith("https://") else None


@app.get("/go/{partner}", include_in_schema=False)
def booking_redirect(partner: str):
    target = _affiliate_url(partner) or _BOOKING_SITES.get(partner)
    if not target:
        raise HTTPException(status_code=404, detail="Unknown booking partner")
    return RedirectResponse(target, status_code=302, headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow"})


@app.get("/api/map/config")
def map_config():
    """Publish only the read-only browser map configuration.

    Mapbox public tokens are designed for client-side use. The production token
    should be URL-restricted to the deployed domain in the Mapbox dashboard.
    """
    configured = bool(settings.mapbox_public_token)
    payload = {
        "provider": "Mapbox",
        "configured": configured,
        # One pale base map everywhere (Mapbox Light). Railways are drawn by
        # the app's own rail line layer, so the map no longer swaps style when
        # trains are on, and the dark lines read clearly on the pale map.
        "tile_url": (
            "https://api.mapbox.com/styles/v1/mapbox/light-v11/tiles/512/"
            "{z}/{x}/{y}?access_token=" + settings.mapbox_public_token
        ) if configured else None,
        # Kept for pages cached before this change; null disables the swap.
        "rail_tile_url": None,
        "tile_size": 512,
        "zoom_offset": -1,
        "max_zoom": 20,
        "live_object_renderer": True,
        "attribution": (
            '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> '
            '&copy; <a href="https://www.mapbox.com/about/maps/" target="_blank" rel="noopener">Mapbox</a>'
        ),
        "aircraft_attribution": (
            'Aircraft: <a href="https://flyitalyadsb.com/" target="_blank" rel="noopener">FlyItalyADSB</a> '
            '(CC BY-SA 4.0) · <a href="https://www.adsb.lol/" target="_blank" rel="noopener">ADSB.lol</a> (ODbL)'
            if settings.flyitaly_adsb_api_key else
            'Aircraft: <a href="https://www.adsb.lol/" target="_blank" rel="noopener">ADSB.lol</a> (ODbL)'
        ),
        "message": None if configured else "Add MAPBOX_PUBLIC_TOKEN to enable the commercial basemap.",
        # Booking links that pay the site a commission must say so.
        "affiliates": {"national-express": _affiliate_url("national-express") is not None},
    }
    # A visitor may have opened the site before the production token was added.
    # Prevent that old fallback configuration from pinning them to TomTom.
    return JSONResponse(payload, headers={"Cache-Control": "no-store"})


@app.get("/api/map/tile/{z}/{x}/{y}.png")
def map_tile(request: Request, z: int, x: int, y: int, style: str = "street-light"):
    """Legacy TomTom base-map fallback retained during the Mapbox migration."""
    _enforce_public_budget(request, tile_request_budget)
    if traffic_flow_service is None or not traffic_flow_service.map_ready:
        raise HTTPException(status_code=503, detail="The production base map is not configured")
    try:
        tile = traffic_flow_service.get_map_tile(z, x, y, style)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Map provider unavailable: {type(exc).__name__}") from exc
    return Response(
        content=tile.content,
        media_type=tile.content_type,
        headers={"Cache-Control": tile.cache_control},
    )


@app.get("/api/traffic/tile/{z}/{x}/{y}.png")
def traffic_tile(request: Request, z: int, x: int, y: int, style: str = "relative0"):
    _enforce_public_budget(request, tile_request_budget)
    _require_plus(request, "traffic_pro")
    if traffic_flow_service is None or not traffic_flow_service.ready:
        raise HTTPException(status_code=503, detail="Live traffic flow is not configured. Run configure_traffic.bat.")
    try:
        tile = traffic_flow_service.get_tile(z, x, y, style)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Traffic provider unavailable: {type(exc).__name__}") from exc
    return Response(
        content=tile.content,
        media_type=tile.content_type,
        headers={"Cache-Control": f"public, max-age={min(settings.traffic_flow_cache_seconds, 60)}"},
    )


@app.get("/api/traffic/incidents/tile/{z}/{x}/{y}.png")
def traffic_incident_tile(request: Request, z: int, x: int, y: int):
    _enforce_public_budget(request, tile_request_budget)
    # Current incident awareness is a core safety layer. Radar+ continues to
    # unlock flow speeds, delay inspection and the combined traffic view.
    if traffic_flow_service is None or not traffic_flow_service.ready:
        raise HTTPException(status_code=503, detail="Live traffic incidents are not configured. Run configure_traffic.bat.")
    try:
        tile = traffic_flow_service.get_incident_tile(z, x, y)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Traffic incident provider unavailable: {type(exc).__name__}") from exc
    return Response(
        content=tile.content,
        media_type=tile.content_type,
        headers={"Cache-Control": f"public, max-age={min(settings.traffic_flow_cache_seconds, 60)}"},
    )


@app.get("/api/traffic/segment")
def traffic_segment(request: Request, lat: float, lon: float, zoom: int = 10):
    _enforce_public_budget(request, lookup_request_budget)
    _require_plus(request, "traffic_pro")
    if traffic_flow_service is None or not traffic_flow_service.ready:
        raise HTTPException(status_code=503, detail="Live traffic flow is not configured. Run configure_traffic.bat.")
    try:
        return traffic_flow_service.get_segment(lat, lon, zoom)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Traffic segment provider unavailable: {type(exc).__name__}") from exc

@app.get("/api/search")
def search(request: Request, q: str, lat: float | None = None, lon: float | None = None):
    _enforce_public_budget(request, lookup_request_budget)
    if lat is not None and lon is not None and not (-90 <= lat <= 90 and -180 <= lon <= 180):
        lat = lon = None
    # Where the map is looking, so "5" finds the nearest buses on route 5.
    payload = activity_search.search(q, lat=lat, lon=lon)
    if smart_places is not None:
        try:
            place_matches = smart_places.search_places(q, limit=8)
            existing = payload.get("matches") or []
            payload["matches"] = place_matches + existing
        except Exception:
            pass
    return payload


@app.get("/api/cameras")
def cameras(request: Request, west: float, south: float, east: float, north: float, limit: int = 250):
    _enforce_public_budget(request, lookup_request_budget)
    _require_plus(request, "live_cameras")
    if not (-180 <= west <= 180 and -180 <= east <= 180 and -90 <= south <= 90 and -90 <= north <= 90):
        raise HTTPException(status_code=400, detail="Invalid map bounds")
    if west > east or south > north:
        raise HTTPException(status_code=400, detail="Invalid map bounds")
    try:
        return traffic_cameras.for_bounds(west=west, south=south, east=east, north=north, limit=limit)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"TfL JamCam feed unavailable: {type(exc).__name__}") from exc


@app.get("/api/cameras/nearest")
def nearest_camera(request: Request, lat: float, lon: float, max_km: float = 25.0):
    _enforce_public_budget(request, lookup_request_budget)
    _require_plus(request, "live_cameras")
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise HTTPException(status_code=400, detail="Invalid coordinates")
    max_km = max(1.0, min(float(max_km), 100.0))
    try:
        return traffic_cameras.nearest(lat=lat, lon=lon, max_km=max_km)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"TfL JamCam feed unavailable: {type(exc).__name__}") from exc


@app.get("/api/activity/{activity_id:path}/usually")
def activity_usually(request: Request, activity_id: str):
    """How this train has run on earlier days, from the radar's own record."""
    _enforce_public_budget(request, lookup_request_budget)
    item = state.get(activity_id)
    if item is None or item.type != "train" or history_store is None:
        return {"days": 0}
    return history_store.usually(item) or {"days": 0}


@app.get("/api/activity/{activity_id:path}/lines")
def activity_lines(request: Request, activity_id: str):
    """The named railway lines a live train is on now, for a link from its panel."""
    _enforce_public_budget(request, lookup_request_budget)
    from .named_lines import lines_for
    item = state.get(activity_id)
    if item is None or item.type != "train":
        return {"lines": []}
    return {"lines": lines_for(item, _stations())}


@app.get("/api/activity/{activity_id:path}/media")
def activity_media(request: Request, activity_id: str, kind: str | None = None, model: str | None = None,
                   callsign: str | None = None, operator: str | None = None):
    _enforce_public_budget(request, lookup_request_budget)
    # A photograph is part of the core identification experience, not an
    # advanced-analysis feature. Keep the lookup rate limited, but available
    # to every visitor so the detail panel never turns into a paywall-shaped
    # blank space.
    item = state.get(activity_id)
    if item is not None and item.type == "bus":
        # Bus photos are off: Commons rarely has the right bus, and a wrong
        # one (once a 15th-century atlas page) misleads more than none.
        return {"ok": False, "reason": "no_bus_photos"}
    aircraft_hex = None
    if item is not None and item.type in {"aircraft", "sar"}:
        aircraft_hex = item.source_id
    elif item is None:
        match = re.fullmatch(r"(?:aircraft|sar):([0-9a-fA-F]{6})", activity_id)
        aircraft_hex = match.group(1) if match else None
    if aircraft_hex:
        try:
            exact_photo = planespotters_photos.lookup(aircraft_hex)
            if exact_photo.get("ok"):
                return exact_photo
        except requests.RequestException:
            pass
    if aircraft_hex or (item is not None and item.type in {"aircraft", "sar"}) or kind in {"aircraft", "sar"}:
        # No photo of this exact aircraft: an example of its type, in the
        # airline's colours where known, clearly labelled as an example.
        plane_kind = (item.type if item is not None else None) or (kind if kind in {"aircraft", "sar"} else "aircraft")
        plane_model = (getattr(item, "aircraft_model", None) or getattr(item, "aircraft_type_code", None)) if item is not None else None
        plane_model = plane_model or model
        try:
            example = media_lookup.reference(plane_kind, (getattr(item, "operator", None) if item is not None else None) or operator,
                                             plane_model, (getattr(item, "name", None) if item is not None else None) or callsign)
            if example.get("ok"):
                return example
        except Exception:
            pass
        return {"ok": False, "reason": "no_exact_airframe_photo"}
    if not item:
        raise HTTPException(status_code=404, detail="Activity not found")
    try:
        return media_lookup.lookup(item)
    except Exception as exc:
        return {
            "ok": False,
            "query": media_lookup.query_for(item),
            "reason": f"Wikimedia lookup unavailable: {type(exc).__name__}",
        }


@app.get("/api/media/reference")
def media_reference(request: Request, kind: str, operator: str | None = None, model: str | None = None,
                    callsign: str | None = None):
    """An example photo of a kind of vehicle: this operator's colours, this type.

    For things the server does not keep (everyday aircraft from "planes above
    me"); the caller passes what it already knows.
    """
    _enforce_public_budget(request, lookup_request_budget)
    if kind != "train" or any(len(v or "") > 80 for v in (operator, model, callsign)):
        raise HTTPException(status_code=400, detail="Unsupported reference request")
    try:
        return media_lookup.reference(kind, operator, model, callsign)
    except Exception as exc:
        return {"ok": False, "reason": f"Wikimedia lookup unavailable: {type(exc).__name__}"}


flight_route_lookup = FlightRouteLookup(settings.adsb_lol_base_url)


@app.get("/api/activity/{activity_id:path}/flight-route")
def activity_flight_route(request: Request, activity_id: str, callsign: str | None = None,
                          lat: float | None = None, lon: float | None = None):
    """Departure and arrival airports for an airline flight, when known.

    Everyday aircraft from "planes above me" are not kept on the server, so
    the caller may pass the callsign and position it already has.
    """
    _enforce_public_budget(request, lookup_request_budget)
    item = state.get(activity_id)
    if item is not None and item.type in {"sar", "aircraft"}:
        callsign, lat, lon = item.name, item.lat, item.lon
    elif not (callsign and lat is not None and lon is not None and -90 <= lat <= 90 and -180 <= lon <= 180):
        raise HTTPException(status_code=404, detail="Aircraft activity not found")
    route = flight_route_lookup.lookup(callsign, lat, lon)
    payload = {"found": route is not None, "route": route}
    if route is None:
        # Why there is no route: no published route for this callsign, or the
        # route services could not be reached (with their error, no secrets).
        payload["reason"] = flight_route_lookup.last_reason
        if flight_route_lookup.last_reason == "lookup_failed":
            payload["error"] = flight_route_lookup.last_error
    return payload


@app.get("/api/activity/{activity_id:path}/aircraft")
def activity_aircraft(request: Request, activity_id: str):
    _enforce_public_budget(request, lookup_request_budget)
    _require_plus(request, "advanced_identity")
    item = state.get(activity_id)
    if not item or item.type not in {"sar", "aircraft"}:
        raise HTTPException(status_code=404, detail="Aircraft activity not found")
    return {
        "ok": True,
        "source": item.source,
        "hex": item.source_id,
        "registration": item.vehicle_ref,
        "type_code": item.aircraft_type_code,
        "model": item.aircraft_model,
        "operator": item.operator,
        "photo": None,
    }


@app.get("/api/activity/{activity_id:path}/rail")
def activity_rail(request: Request, activity_id: str):
    _enforce_public_budget(request, lookup_request_budget)
    item = state.get(activity_id)
    if not item or item.type != "train":
        raise HTTPException(status_code=404, detail="Train activity not found")
    if smart_places is None:
        raise HTTPException(status_code=503, detail="Rail intelligence is not ready")
    detail = smart_places.train_vehicle_detail(item)
    detail["rolling_stock"] = train_reference.lookup(item.vehicle_ref)
    return {"activity": activity_id, "detail": detail}


@app.get("/api/rail/schedule/{train_uid}")
def rail_schedule(request: Request, train_uid: str, headcode: str | None = None, service_date: str | None = None):
    """Published route fallback for a station-board train without a live marker."""
    _enforce_public_budget(request, lookup_request_budget)
    if smart_places is None:
        raise HTTPException(status_code=503, detail="Rail intelligence is not ready")
    return {"train_uid": train_uid, "detail": smart_places.scheduled_train_detail(train_uid, headcode, service_date)}


_RAIL_LINE_CACHE = "public, max-age=86400, stale-while-revalidate=604800"


@app.get("/api/rail/lines/national")
def rail_lines_national(request: Request):
    """Simplified railway network for national zooms (drawn when trains are on)."""
    _enforce_public_budget(request, tile_request_budget)
    return JSONResponse(rail_line_layer.national(), headers={"Cache-Control": _RAIL_LINE_CACHE})


@app.get("/api/rail/lines/{level}/{cx}/{cy}")
def rail_lines_cell(request: Request, level: int, cx: int, cy: int):
    """Detailed railway lines for one half-degree map cell."""
    _enforce_public_budget(request, tile_request_budget)
    try:
        payload = rail_line_layer.cell(level, cx, cy)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(payload, headers={"Cache-Control": _RAIL_LINE_CACHE})


_passenger_stations_cache: tuple[float, list[dict]] = (0.0, [])


def _passenger_stations() -> list[dict]:
    """Stations with a passenger code, for naming stretches of line."""
    global _passenger_stations_cache
    loaded_at, rows = _passenger_stations_cache
    if rows and time.monotonic() - loaded_at < 3600:
        return rows
    found: dict[str, dict] = {}
    try:
        with sqlite3.connect(schedules.db_path) as conn:
            for crs, name, lat, lon in conn.execute(
                "SELECT crs, description, lat, lon FROM tiplocs WHERE crs IS NOT NULL AND crs<>'' AND lat IS NOT NULL AND lon IS NOT NULL"
            ):
                found.setdefault(str(crs), {"name": str(name or crs), "lat": float(lat), "lon": float(lon)})
    except sqlite3.Error:
        return rows
    _passenger_stations_cache = (time.monotonic(), list(found.values()))
    return _passenger_stations_cache[1]


def _live_trains() -> list:
    return [row for row in state.all(include_buses=False, include_vessels=False, train_limit=None) if row.type == "train"]


@app.get("/api/rail/line")
def rail_line_at(request: Request, lat: float, lon: float, zoom: float = 12):
    """The stretch of railway at a tapped point, with the trains on it in order."""
    _enforce_public_budget(request, lookup_request_budget)
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise HTTPException(status_code=400, detail="Invalid location")
    # About 18 screen pixels at the caller's zoom, within sensible bounds.
    metres_per_pixel = 156543.03 * math.cos(math.radians(lat)) / (2 ** max(5.0, min(19.0, zoom)))
    reach_km = max(0.08, min(3.0, 18 * metres_per_pixel / 1000))
    return line_at(rail_line_layer, _live_trains(), _passenger_stations(), lat, lon, reach_km, datetime.now(timezone.utc))


_delay_hotspot_cache: tuple[float, list[dict]] = (-1e9, [])


@app.get("/api/rail/delays")
def rail_delay_hotspots(request: Request):
    """Stretches of line where trains are running late right now."""
    global _delay_hotspot_cache
    _enforce_public_budget(request, lookup_request_budget)
    computed_at, rows = _delay_hotspot_cache
    if time.monotonic() - computed_at > 20:
        rows = delay_hotspots(rail_line_layer, _live_trains(), _passenger_stations(), datetime.now(timezone.utc))
        _delay_hotspot_cache = (time.monotonic(), rows)
    return {"hotspots": rows}


# "Never computed" must be older than any clock reading, including just after boot.
_operator_status_cache: tuple[float, list[dict]] = (-1e9, [])


@app.get("/api/rail/status")
def rail_operator_status(request: Request):
    """Each passenger operator: good service, minor or severe delays."""
    global _operator_status_cache
    _enforce_public_budget(request, lookup_request_budget)
    computed_at, rows = _operator_status_cache
    if time.monotonic() - computed_at > 30:
        from .line_status import operator_status
        rows = operator_status(_live_trains())
        _operator_status_cache = (time.monotonic(), rows)
    return {"operators": rows}


@app.get("/api/rail/locate")
def locate_rail_service(request: Request, train_uid: str | None = None, headcode: str | None = None, service_date: str | None = None):
    """Locate a station-board service even when its layer is not streamed."""
    _enforce_public_budget(request, lookup_request_budget)
    if smart_places is None:
        raise HTTPException(status_code=503, detail="Rail intelligence is not ready")
    item = smart_places.locate_live_train(headcode, train_uid, service_date)
    return {
        "live": item is not None,
        "activity": item.model_dump(mode="json", exclude_none=True) if item is not None else None,
        "message": "Live train position found." if item is not None else "No live train position is currently published for this service.",
    }


@app.get("/api/activity/{activity_id:path}/bus")
def activity_bus(request: Request, activity_id: str, limit: int = 30):
    _enforce_public_budget(request, lookup_request_budget)
    # A clicked live bus must always be able to show its official route and
    # next stops. Charging for this core context made every free-user request
    # fail with 402 and the browser misleadingly reported the route unavailable.
    item = state.get(activity_id)
    if not item or item.type != "bus":
        raise HTTPException(status_code=404, detail="Bus activity not found")
    if smart_places is None:
        raise HTTPException(status_code=503, detail="Bus intelligence is not ready")
    return {
        "activity": activity_id,
        "detail": smart_places.bus_vehicle_journey(item, limit=max(3, min(int(limit), 30))),
    }


_incident_refresh_running = False


def _refresh_incidents_for(viewport: dict) -> None:
    """Fetch live incidents for the area on screen when the layer is on."""
    global _incident_refresh_running
    service = traffic_incident_service
    types = viewport.get("types")
    if service is None or not service.ready or _incident_refresh_running:
        return
    if types is not None and not ({"incident", "road"} & set(types)):
        return
    bbox = viewport.get("bbox")
    if not bbox:
        return
    _incident_refresh_running = True

    def run() -> None:
        global _incident_refresh_running
        try:
            service.ensure(bbox)
        finally:
            _incident_refresh_running = False

    threading.Thread(target=run, name="traffic-incidents", daemon=True).start()


def _saved_train_status(train_id: str) -> dict:
    vehicle = state.get(train_id)
    if vehicle is None or getattr(vehicle, "type", None) != "train":
        return {"found": False, "id": train_id}
    note = state.rail_notes.get(vehicle.train_uid, vehicle.service_date)
    return saved_train_status(vehicle, note)


@app.get("/api/trains/near")
def trains_near(request: Request, lat: float, lon: float, km: float = 5.0):
    """Trains within ``km`` of a point, nearest first, at their moving position."""
    _enforce_public_budget(request, lookup_request_budget)
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise HTTPException(status_code=400, detail="Invalid location")
    radius = max(0.5, min(float(km), 25.0))
    now = datetime.now(timezone.utc)
    rows = []
    for vehicle in _live_trains():
        position = _estimated_train_position(vehicle, now) or (float(vehicle.lat), float(vehicle.lon))
        distance = _haversine_km(lat, lon, position[0], position[1])
        if distance <= radius:
            rows.append({
                "id": vehicle.id, "name": vehicle.name, "origin": vehicle.origin, "destination": vehicle.destination,
                "operator": vehicle.operator, "delay_minutes": vehicle.delay_minutes,
                "cancelled": bool(vehicle.cancelled_at), "next_location": vehicle.next_location,
                "distance_km": round(distance, 2), "lat": round(position[0], 5), "lon": round(position[1], 5),
            })
    rows.sort(key=lambda row: row["distance_km"])
    return {"radius_km": radius, "trains": rows[:12]}


@app.get("/api/buses/disruptions")
def bus_disruptions(
    request: Request,
    west: float,
    south: float,
    east: float,
    north: float,
    limit: int = 30,
):
    """Return official bus cancellations, diversions and stop closures in view.

    BODS SIRI-SX rows are incident events rather than moving bus vehicles, so
    they are not part of a browser viewport that has only the Buses layer on.
    This bounded endpoint lets Buses > Disruptions work without also enabling
    every road incident on the map.
    """
    _enforce_public_budget(request, lookup_request_budget)
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise HTTPException(status_code=400, detail="Invalid map bounds")
    rows = [
        event for event in state.all_events(bbox=(west, south, east, north))
        if bool((event.details or {}).get("bus_disruption"))
    ]
    return {
        "disruptions": [
            event.model_dump(mode="json", exclude_none=True)
            for event in rows[: max(1, min(int(limit), 100))]
        ],
        "count": len(rows),
    }


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


@app.get("/api/around")
def vehicles_around(request: Request, lat: float, lon: float):
    """Trains within 3 km and buses within 1 km of a point, for the camera view."""
    _enforce_public_budget(request, lookup_request_budget)
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise HTTPException(status_code=400, detail="Invalid location")
    now = datetime.now(timezone.utc)
    rows = []
    for vehicle in _live_trains():
        if vehicle.cancelled_at or vehicle.terminated_at:
            continue
        position = _estimated_train_position(vehicle, now) or (float(vehicle.lat), float(vehicle.lon))
        distance = _haversine_km(lat, lon, position[0], position[1])
        if distance <= 3.0:
            rows.append({"id": vehicle.id, "type": "train", "name": vehicle.name, "destination": vehicle.destination,
                         "operator": vehicle.operator, "delay_minutes": vehicle.delay_minutes,
                         "distance_km": round(distance, 2), "lat": round(position[0], 5), "lon": round(position[1], 5)})
    span_lat, span_lon = 1.0 / 111.32, 1.0 / (111.32 * max(0.2, math.cos(math.radians(lat))))
    for vehicle in state.all(bbox=(lon - span_lon, lat - span_lat, lon + span_lon, lat + span_lat),
                             include_trains=False, include_vessels=False, max_age_minutes=5):
        if vehicle.type != "bus":
            continue
        distance = _haversine_km(lat, lon, float(vehicle.lat), float(vehicle.lon))
        if distance <= 1.0:
            rows.append({"id": vehicle.id, "type": "bus", "name": vehicle.name, "destination": vehicle.destination,
                         "operator": vehicle.operator, "delay_minutes": None,
                         "distance_km": round(distance, 2), "lat": round(float(vehicle.lat), 5), "lon": round(float(vehicle.lon), 5)})
    rows.sort(key=lambda row: row["distance_km"])
    return {"vehicles": rows[:30]}


@app.get("/api/trains/status")
def saved_trains_status(request: Request, response: Response, ids: str = ""):
    """Current state of up to twelve saved trains, wherever they are."""
    _enforce_public_budget(request, lookup_request_budget)
    response.headers["Cache-Control"] = "private, no-store"
    wanted = normalise_saved_trains(ids.split(","))
    trains = [_saved_train_status(train_id) for train_id in wanted]
    for row in trains:
        row["alerts"] = [
            {"key": alert["key"], "title": alert["title"], "body": alert["body"]}
            for alert in saved_train_alerts(row)
        ]
    return {"trains": trains}


@app.get("/api/activity/{activity_id:path}")
def activity_detail(request: Request, activity_id: str):
    """Resolve one current item independently of the caller's map viewport."""
    _enforce_public_budget(request, lookup_request_budget)
    item = state.get(activity_id)
    if item is None:
        raise HTTPException(status_code=404, detail="This live activity has ended")
    return item.model_dump(mode="json")


@app.get("/api/events/{event_id}/coverage")
def event_coverage(request: Request, event_id: str):
    _enforce_public_budget(request, lookup_request_budget)
    item = state.get_event(event_id)
    if not item:
        raise HTTPException(status_code=404, detail="Event not found")
    return coverage_for_event(item)


@app.get("/api/events/{event_id}")
def event(event_id: str):
    item = state.get_event(event_id)
    if not item:
        raise HTTPException(status_code=404, detail="Event not found")
    return item.model_dump(mode="json")


@app.get("/api/activities")
def activities(type: str | None = None, q: str | None = None):
    _require_local_debug_route()
    items = state.all_activities()
    if type and type != "all":
        items = [x for x in items if x.type == type]
    if q:
        needle = q.lower().strip()
        items = [x for x in items if needle in " ".join(str(v) for v in x.model_dump().values() if v is not None).lower()]
    return [x.model_dump(mode="json") for x in items]


@app.get("/api/summary")
def summary():
    return state.summary()


FREE_DISCOVER_FINDS = 10


@app.get("/api/cool")
def cool_activities(request: Request, limit: int = 20, category: str | None = None):
    """Small national Cool priority feed, independent of the current map viewport."""
    allowed_categories = {"air", "rail", "sea", "rescue"}
    wanted = (category or "").strip().lower() or None
    if wanted and wanted not in allowed_categories:
        raise HTTPException(status_code=400, detail="Unknown Cool category")
    context = _billing_context(request)
    # Everything while Radar+ cannot be bought; once it can, free gets a
    # good taste (10) and Radar+ the full list.
    full = billing_service is None or context.has("cool_full")
    maximum = 50 if full else FREE_DISCOVER_FINDS
    available = state.cool_candidates(limit=50, category=wanted)
    requested = max(1, min(limit, maximum))
    if full or wanted:
        rows = available[:requested]
    else:
        # A straight score sort made the free preview look aircraft-only.
        # Prefer one example from each visible Discover category, then fill
        # unused slots by score. This changes presentation, not entitlement.
        rows = []
        for category_name in ("air", "rail", "rescue"):
            match = next(
                (row for row in available if str(row.cool_category or "").lower() == category_name),
                None,
            )
            if match is not None and match not in rows:
                rows.append(match)
            if len(rows) >= requested:
                break
        rows.extend(row for row in available if row not in rows)
        rows = rows[:requested]
    available_counts = {
        name: sum(1 for row in available if str(row.cool_category or "").lower() == name)
        for name in ("air", "rail", "rescue")
    }
    return {
        "activities": [row.model_dump(mode="json") for row in rows],
        "count": len(rows),
        "available_count": len(available),
        "available_counts": available_counts,
        "full_access": full,
        "limit": maximum,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/today")
def today_on_radar(request: Request, since: datetime | None = None, lat: float | None = None, lon: float | None = None):
    """A small, public editorial summary built only from current verified state."""
    _enforce_public_budget(request, lookup_request_budget)
    if lat is not None and lon is not None and not (-90 <= lat <= 90 and -180 <= lon <= 180):
        lat = lon = None
    return build_today_payload(state.all_activities(), since=since, lat=lat, lon=lon)


@app.get("/api/share/{activity_id:path}")
def shared_activity(activity_id: str, request: Request):
    """Facts for a stable share page; precise coordinates are never returned."""
    _enforce_public_budget(request, lookup_request_budget)
    item = state.get(activity_id)
    if item is None:
        raise HTTPException(status_code=404, detail="This live activity is no longer available")
    payload = _share_payload_for(item)
    payload["share_url"] = str(request.base_url).rstrip("/") + "/live/" + quote(activity_id, safe="")
    payload["open_url"] = "/?open=" + quote(activity_id, safe="")
    return payload


@app.get("/api/discoveries/catalogue")
def discovery_catalogue():
    """Return the stable, server-owned map discovery catalogue."""
    return catalogue_payload()


@app.get("/api/discoveries/missions")
def discovery_missions():
    """Return stateless weekly map missions; progress remains in the browser."""
    return missions_payload()


@app.post("/api/discoveries/claim")
def discovery_claim(payload: DiscoveryClaimIn):
    try:
        discovery = claim_activity(state.get(payload.activity_id))
    except DiscoveryClaimError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
    return {"ok": True, "discovery": discovery}


@app.get("/api/feed-status")
def feed_status():
    return [_source_payload(s) for s in state.sources()]


@app.get("/api/readiness")
def readiness():
    return _readiness_payload()


@app.get("/api/product/plans")
def plans():
    return product_plans(
        payments_enabled=bool(billing_service),
        price_display=settings.radar_plus_price_display,
    )


@app.post("/api/product/event")
def product_event(payload: ProductEventIn):
    accepted = product_analytics.record(payload.session_id, payload.event_name, payload.feature)
    if not accepted:
        raise HTTPException(status_code=400, detail="Unsupported product event")
    return {"ok": True}


@app.get("/api/product/summary")
def product_summary():
    _require_local_debug_route()
    return product_analytics.summary()


@app.get("/api/unmapped")
def unmapped():
    _require_local_debug_route()
    return state.unmapped()


@app.websocket("/ws")
async def websocket_updates(websocket: WebSocket):
    if not same_origin_websocket(websocket.headers.get("origin"), websocket.headers.get("host")):
        await websocket.close(code=1008, reason="WebSocket origin is not allowed")
        return
    await websocket.accept()
    if not settings.public_deployment and not billing_configuration.enabled:
        websocket_access = AccessContext(
            token_valid=True, customer_id="local-development", plan="plus",
            subscription_status="active", features=PLUS_FEATURES,
        )
    elif _preview_context(websocket.cookies.get(PREVIEW_COOKIE)).plus:
        websocket_access = _preview_context(websocket.cookies.get(PREVIEW_COOKIE))
    elif billing_store is not None:
        websocket_access = billing_store.access_context(websocket.cookies.get(ACCESS_COOKIE))
    else:
        websocket_access = AccessContext()
    # Start with a broad Britain viewport. Buses are intentionally hidden at
    # national zoom to avoid shipping tens of thousands of GPS points every
    # second. The browser sends its real viewport/zoom immediately after open.
    viewport: dict[str, object] = {
        "bbox": (-8.7, 49.5, 2.1, 59.2),
        "zoom": 6.0,
        # Empty means the default Useful map is intentionally clear. ``None``
        # means Cool mode needs the complete candidate pool for classification.
        "types": set(),
    }
    viewport_revision = 0
    viewport_changed = asyncio.Event()

    watched_id: str | None = None

    async def set_watching(new_id: str | None) -> None:
        nonlocal watched_id
        new_id = (new_id or "").strip() or None
        if new_id and not websocket_access.has("live_tracking"):
            return
        if new_id == watched_id:
            return
        async with viewer_lock:
            if watched_id:
                viewer_counts[watched_id] = max(0, viewer_counts.get(watched_id, 1) - 1)
                state.set_tracking_count(watched_id, viewer_counts[watched_id])
                if viewer_counts[watched_id] == 0:
                    viewer_counts.pop(watched_id, None)
            watched_id = new_id
            if watched_id and state.get(watched_id):
                viewer_counts[watched_id] = viewer_counts.get(watched_id, 0) + 1
                state.set_tracking_count(watched_id, viewer_counts[watched_id])

    async def receive_client() -> None:
        nonlocal viewport_revision
        try:
            while True:
                message = await websocket.receive_json()
                action = message.get("action")
                if action == "watching":
                    await set_watching(str(message.get("activity_id") or "") or None)
                    continue
                if action != "viewport":
                    continue
                raw = message.get("bbox")
                try:
                    if isinstance(raw, list) and len(raw) == 4:
                        west, south, east, north = [float(x) for x in raw]
                        if -180 <= west <= east <= 180 and -90 <= south <= north <= 90:
                            viewport["bbox"] = (west, south, east, north)
                    viewport["zoom"] = float(message.get("zoom", viewport["zoom"]))
                    if "types" in message:
                        requested_types = message.get("types")
                        allowed_types = {
                            "train", "bus", "vessel", "sar", "aircraft", "rescue",
                            "road", "roadfeature", "incident", "weather", "flood", "earthquake",
                            "river", "tide", "wave", "grid", "airquality", "bike",
                            "space", "satellite", "outage", "gritter", "camera", "other",
                        }
                        if requested_types is None:
                            viewport["types"] = None
                        elif isinstance(requested_types, list):
                            viewport["types"] = {
                                str(item) for item in requested_types
                                if str(item) in allowed_types
                            }
                    viewport_revision += 1
                    viewport_changed.set()
                    _refresh_incidents_for(viewport)
                except (TypeError, ValueError):
                    pass
        except (WebSocketDisconnect, RuntimeError):
            return

    receiver = asyncio.create_task(receive_client())
    # The browser sends its real viewport as soon as the socket opens. Waiting
    # briefly avoids sending a redundant national snapshot immediately before
    # the useful local one; that removes hundreds of kilobytes from cold start.
    with suppress(asyncio.TimeoutError):
        await asyncio.wait_for(viewport_changed.wait(), timeout=0.35)
    viewport_changed.clear()

    async def wait_for_refresh() -> None:
        """Keep the normal cadence, but send a newly requested viewport now."""
        base_cadence = max(0.5, settings.websocket_refresh_seconds)
        # Deliver fresh TRUST reports promptly to everyone. Radar+ halves the
        # delivery interval, while the underlying reporting-point data remains
        # source-faithful rather than being visually interpolated as GPS.
        cadence = max(0.5, base_cadence / 2) if websocket_access.has("fast_updates") else base_cadence
        with suppress(asyncio.TimeoutError):
            await asyncio.wait_for(
                viewport_changed.wait(),
                timeout=cadence,
            )
        viewport_changed.clear()
    last_state_revision = -1
    last_viewport_revision = -1
    last_activity_signature = None
    last_activity_payload: dict[str, dict] = {}
    last_send_at = 0.0
    streamed_train_ids: set[str] = set()
    try:
        while True:
            current_state_revision = state.revision
            current_viewport_revision = viewport_revision
            if (
                current_state_revision == last_state_revision
                and current_viewport_revision == last_viewport_revision
                and time.monotonic() - last_send_at < 10.0
            ):
                await wait_for_refresh()
                continue
            zoom = float(viewport["zoom"])
            requested_types = viewport.get("types")
            all_types = requested_types is None
            visible_types = set() if all_types else set(requested_types or set())
            include_trains = all_types or "train" in visible_types
            include_buses = (all_types or "bus" in visible_types) and zoom >= settings.bus_min_map_zoom
            include_vessels = (all_types or bool({"vessel", "sar"} & visible_types)) and zoom >= settings.maritime_min_map_zoom
            # At regional zooms clusters communicate density better than
            # thousands of individual records. Raise the caps as the user
            # zooms in so local detail remains complete while the broad map
            # stays responsive on ordinary laptops and phones.
            train_limit = min(
                settings.train_max_stream,
                250 if zoom < 7 else 800 if zoom < 9 else 1400 if zoom < 10 else settings.train_max_stream,
            )
            bus_limit = min(
                settings.bus_max_stream,
                900 if zoom < 9 else 1600 if zoom < 10 else settings.bus_max_stream,
            )
            vessel_limit = min(
                settings.maritime_max_stream,
                450 if zoom < 7 else 600 if zoom < 9 else 1000 if zoom < 10 else settings.maritime_max_stream,
            )
            west, south, east, north = viewport["bbox"]
            # A train can be visually interpolated ahead of its last confirmed
            # reporting point. Keep a modest off-screen rail buffer so crossing
            # a viewport edge or panning a few pixels cannot remove/recreate it.
            lon_pad = max(0.01, abs(east - west) * 0.2)
            lat_pad = max(0.01, abs(north - south) * 0.2)
            train_bbox = (west - lon_pad, south - lat_pad, east + lon_pad, north + lat_pad)
            streamed_vehicles = state.all(
                bbox=viewport["bbox"],
                train_bbox=train_bbox,
                include_trains=include_trains,
                train_limit=train_limit,
                cool_trains_only=zoom < settings.train_min_map_zoom,
                include_buses=include_buses,
                bus_limit=bus_limit,
                include_vessels=include_vessels,
                vessel_limit=vessel_limit,
                train_priority_ids=streamed_train_ids | ({watched_id} if watched_id else set()),
            )
            if not all_types:
                streamed_vehicles = [row for row in streamed_vehicles if row.type in visible_types]
            dense_event_types: set[str] = set()
            if zoom < 10:
                dense_event_types.add("bike")
            if zoom < 8:
                dense_event_types.update({"tide", "river", "wave", "airquality"})
            streamed_events = state.all_events(
                bbox=viewport["bbox"],
                exclude_types=dense_event_types,
            )
            if not all_types:
                streamed_events = [row for row in streamed_events if row.type in visible_types]
            streamed = [*streamed_vehicles, *streamed_events]
            if all_types and not websocket_access.has("cool_full"):
                # ``types: null`` is the Discover request. Free visitors get the
                # same number of finds as the Discover list; the browser cannot
                # bypass the Radar+ limit by reading the websocket directly.
                streamed = one_per_train(sorted(
                    (row for row in streamed if row.cool_candidate),
                    key=lambda row: (row.cool_score or 0, row.last_updated),
                    reverse=True,
                ))[:FREE_DISCOVER_FINDS]
                allowed_ids = {row.id for row in streamed}
                streamed_vehicles = [row for row in streamed_vehicles if row.id in allowed_ids]
                streamed_events = [row for row in streamed_events if row.id in allowed_ids]
            activity_signature = tuple(
                (
                    item.id,
                    item.last_updated,
                    round(item.lat, 6),
                    round(item.lon, 6),
                    item.tracking_count,
                    item.cool_candidate,
                )
                for item in streamed
            )
            now_monotonic = time.monotonic()
            if (
                current_viewport_revision == last_viewport_revision
                and activity_signature == last_activity_signature
                and now_monotonic - last_send_at < 10.0
            ):
                last_state_revision = current_state_revision
                await wait_for_refresh()
                continue
            summary_payload = state.summary()
            live_counts = state.type_counts({"train": 45, "bus": 5, "vessel": 10, "sar": 10})
            activity_rows = [x.model_dump(mode="json", exclude_none=True) for x in streamed]
            activity_payload = {str(row["id"]): row for row in activity_rows}
            force_full_snapshot = current_viewport_revision != last_viewport_revision or last_viewport_revision < 0
            if force_full_snapshot:
                activity_transport = {"activities": activity_rows}
                transport_mode = "full"
            else:
                upsert = [row for key, row in activity_payload.items() if last_activity_payload.get(key) != row]
                removed = [key for key in last_activity_payload if key not in activity_payload]
                activity_transport = {"activity_delta": {"upsert": upsert, "remove": removed}}
                transport_mode = "delta"
            payload = {
                **activity_transport,
                "summary": summary_payload,
                # Rail estimates are timed on the server clock. The browser
                # uses this to correct for a fast/slow device clock, which
                # otherwise makes every train start late, run ahead or jump.
                "server_time_ms": int(time.time() * 1000),
                "sources": [_source_payload(s) for s in state.sources()],
                "stream": {
                    "train_min_zoom": settings.train_min_map_zoom,
                    "trains_in_memory": live_counts.get("train", 0),
                    "trains_streamed": sum(v.type == "train" for v in streamed_vehicles),
                    "train_limit": train_limit,
                    "environment_detail_min_zoom": 8,
                    "bike_min_zoom": 10,
                    "bus_min_zoom": settings.bus_min_map_zoom,
                    "buses_in_memory": live_counts.get("bus", 0),
                    "buses_streamed": sum(v.type == "bus" for v in streamed_vehicles),
                    "bus_limit": bus_limit,
                    "maritime_min_zoom": settings.maritime_min_map_zoom,
                    "vessels_in_memory": live_counts.get("vessel", 0),
                    "sar_in_memory": live_counts.get("sar", 0),
                    "vessels_streamed": sum(v.type in {"vessel", "sar"} for v in streamed_vehicles),
                    "maritime_limit": vessel_limit,
                    "road_events": sum(e.type in {"road", "incident"} for e in streamed_events),
                    "flood_events": sum(e.type == "flood" for e in streamed_events),
                    "rnli_events": sum(e.type == "rescue" for e in streamed_events),
                    "weather_events": sum(e.type == "weather" for e in streamed_events),
                    "zoom": zoom,
                    "selected_types": sorted(visible_types) if not all_types else None,
                    "transport": transport_mode,
                    "upserts": len(activity_rows) if force_full_snapshot else len(activity_transport["activity_delta"]["upsert"]),
                    "removals": 0 if force_full_snapshot else len(activity_transport["activity_delta"]["remove"]),
                },
            }
            await websocket.send_json(payload)
            last_state_revision = current_state_revision
            last_viewport_revision = current_viewport_revision
            last_activity_signature = activity_signature
            last_activity_payload = activity_payload
            streamed_train_ids = {v.id for v in streamed_vehicles if v.type == "train"}
            last_send_at = now_monotonic
            await wait_for_refresh()
    except (WebSocketDisconnect, asyncio.CancelledError):
        return
    finally:
        await set_watching(None)
        receiver.cancel()
        with suppress(asyncio.CancelledError, WebSocketDisconnect, RuntimeError):
            await receiver
