from __future__ import annotations

import asyncio
import json
import time

import requests
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from .collectors.demo import run_demo
from .collectors.bods import BODSCollector
from .collectors.bods_sx import BODSSXCollector
from .collectors.environment_agency import EnvironmentAgencyFloodCollector
from .collectors.aisstream import AISStreamCollector
from .collectors.rnli import RNLILaunchCollector
from .collectors.adsb_lol import ADSBLolSARCollector
from .collectors.london_fire import LondonFireCollector
from .collectors.met_office import MetOfficeWarningCollector
from .collectors.national_highways import NationalHighwaysCollector
from .collectors.network_rail import TrustCollector
from .collectors.td import TDCollector
from .collectors.vstp import VSTPCollector
from .collectors.live_world import (
    BGSEarthquakeCollector, EALevelsCollector, WaveNetCollector, CarbonIntensityCollector,
    UKAirCollector, TfLLiveCollector, GBFSCollector, SpaceWeatherCollector, ISSCollector,
    PowerOutageCollector, GritterCollector,
)
from .config import settings
from .error_safety import same_origin_websocket
from .models import SourceStatus
from .coverage import coverage_for_event
from .media import WikimediaLookup
from .aircraft_info import AircraftInfoService
from .radio import RadioBrowserLookup
from .search import ActivitySearch
from .product import ProductAnalytics, product_plans
from .traffic_flow import TrafficFlowService
from .weather_radar import rainviewer_payload
from .smart_places import SmartPlacesService
from .reference_data import sync_public_geography, sync_reference_data
from .schedule_store import ScheduleStore, sync_schedule_data
from .state import state

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "app" / "static"
DATA_DIR = BASE_DIR / "data"
STATION_FILE = DATA_DIR / "stations.json"
SMART_FILE = DATA_DIR / "smart.json"
SCHEDULE_DB = DATA_DIR / "schedule.sqlite"
PRODUCT_DB = DATA_DIR / "product.sqlite"

schedules = ScheduleStore(SCHEDULE_DB)
trust_collector: TrustCollector | None = None
td_collector: TDCollector | None = None
vstp_collector: VSTPCollector | None = None
bods_collector: BODSCollector | None = None
bods_sx_collector: BODSSXCollector | None = None
roads_collector: NationalHighwaysCollector | None = None
floods_collector: EnvironmentAgencyFloodCollector | None = None
ais_collector: AISStreamCollector | None = None
rnli_collector: RNLILaunchCollector | None = None
adsb_sar_collector: ADSBLolSARCollector | None = None
london_fire_collector: LondonFireCollector | None = None
met_office_collector: MetOfficeWarningCollector | None = None
traffic_flow_service: TrafficFlowService | None = None
smart_places: SmartPlacesService | None = None
world_collectors: list = []
media_lookup = WikimediaLookup(settings.wikimedia_api_url, enabled=settings.wikimedia_enabled)
aircraft_info = AircraftInfoService()
activity_search = ActivitySearch(state, settings.nominatim_url, enabled=settings.nominatim_enabled, tomtom_api_key=settings.tomtom_traffic_api_key, tomtom_base_url=settings.tomtom_traffic_base_url)
radio_lookup = RadioBrowserLookup()
product_analytics = ProductAnalytics(PRODUCT_DB)
viewer_counts: dict[str, int] = {}
viewer_lock = asyncio.Lock()
_weather_radar_cache: dict = {"expires": 0.0, "payload": None}


class ProductEventIn(BaseModel):
    session_id: str
    event_name: str
    feature: str = ""


_SOURCE_TTLS = {
    "network_rail": 150, "td": 150, "vstp": 900, "bods": 180, "bods_timetable": 86400, "bods_sx": 300,
    "national_highways": 300, "ea_floods": 1200, "aisstream": 150, "rnli": 1800,
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
    if counts["error"] or counts["stale"]: overall = "degraded"
    elif counts["setup"] and not (counts["live"] or counts["ready"]): overall = "setup"
    elif counts["live"] or counts["ready"]: overall = "healthy"
    else: overall = "starting"
    return {"overall": overall, "counts": counts, "sources": sources, "version": "1.2.1"}


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


@asynccontextmanager
async def lifespan(app: FastAPI):
    global trust_collector, td_collector, vstp_collector, bods_collector, bods_sx_collector, roads_collector, floods_collector, ais_collector, rnli_collector, adsb_sar_collector, london_fire_collector, met_office_collector, traffic_flow_service, world_collectors, smart_places
    tasks: list[asyncio.Task] = []

    if settings.demo_mode:
        tasks.append(asyncio.create_task(run_demo(state)))

    if settings.network_rail_enabled:
        trust_collector = TrustCollector(settings, state, STATION_FILE, schedules)
        trust_collector.start()

        if settings.td_enabled:
            td_collector = TDCollector(settings, state, STATION_FILE, SMART_FILE)
            td_collector.start()
        else:
            state.set_source(SourceStatus(name="td", enabled=False, mode="off", message="TD disabled in .env"))

        if settings.vstp_enabled:
            vstp_collector = VSTPCollector(settings, state, schedules)
            vstp_collector.start()
        else:
            state.set_source(SourceStatus(name="vstp", enabled=False, mode="off", message="VSTP disabled in .env"))

        if schedules.ready():
            state.set_source(SourceStatus(
                name="schedule", enabled=True, connected=True, mode="ready",
                message=f"Local SCHEDULE index loaded • {schedules.count():,} schedules",
            ))
        if settings.auto_sync_schedule:
            tasks.append(asyncio.create_task(_sync_schedule()))

        if _mapped_location_count() > 0:
            state.set_source(SourceStatus(
                name="rail_reference", enabled=True, connected=True, mode="ready",
                message=f"{_mapped_location_count():,} mapped STANOX locations loaded; background refresh pending",
            ))
        if settings.auto_sync_reference:
            tasks.append(asyncio.create_task(_sync_reference()))
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

    smart_places = SmartPlacesService(DATA_DIR, schedules, state, bods_collector_getter=lambda: bods_collector)
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

    traffic_flow_service = TrafficFlowService(settings, state)
    traffic_flow_service.set_initial_status()
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

    for collector in (trust_collector, td_collector, vstp_collector, bods_collector, bods_sx_collector, roads_collector, floods_collector, ais_collector, rnli_collector, adsb_sar_collector, london_fire_collector, met_office_collector):
        if collector:
            collector.stop()
    for collector in world_collectors:
        try:
            collector.stop()
        except Exception:
            pass
    for task in tasks:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="UK Activity Radar", version="1.2.1", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def home():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health():
    mode = "live" if settings.network_rail_enabled else ("demo" if settings.demo_mode else "setup")
    return {
        "ok": True,
        "version": "1.2.1",
        "mode": mode,
        "network_rail_credentials_present": settings.credentials_present,
        "mapped_rail_locations": _mapped_location_count(),
        "schedule_count": schedules.count(),
        "td_enabled": settings.td_enabled,
        "vstp_enabled": settings.vstp_enabled,
        "bods_enabled": settings.bods_enabled,
        "bods_api_key_present": bool(settings.bods_api_key),
        "live_bus_count": state.count_type("bus", max_age_minutes=5),
        "bods_sx_enabled": settings.bods_sx_enabled,
        "national_highways_enabled": settings.national_highways_enabled,
        "national_highways_api_key_present": bool(settings.national_highways_api_key),
        "road_event_count": state.count_type("road") + state.count_type("incident"),
        "traffic_flow_enabled": settings.traffic_flow_enabled,
        "traffic_flow_api_key_present": bool(settings.tomtom_traffic_api_key),
        "traffic_flow_ready": settings.traffic_flow_ready,
        "ea_floods_enabled": settings.ea_floods_enabled,
        "flood_event_count": state.count_type("flood"),
        "aisstream_enabled": settings.aisstream_enabled,
        "aisstream_api_key_present": bool(settings.aisstream_api_key),
        "live_vessel_count": state.count_type("vessel", max_age_minutes=10),
        "live_sar_count": state.count_type("sar", max_age_minutes=10),
        "rnli_enabled": settings.rnli_enabled,
        "rnli_launch_count": state.count_type("rescue"),
        "adsb_sar_enabled": settings.adsb_sar_enabled,
        "adsb_sar_count": state.count_type("sar", max_age_minutes=10),
        "london_fire_enabled": settings.london_fire_enabled,
        "met_office_enabled": settings.met_office_enabled,
        "weather_warning_count": state.count_type("weather"),
        "earthquake_count": state.count_type("earthquake"),
        "river_level_count": state.count_type("river"),
        "tide_gauge_count": state.count_type("tide"),
        "wave_buoy_count": state.count_type("wave"),
        "grid_region_count": state.count_type("grid"),
        "air_quality_count": state.count_type("airquality"),
        "bike_point_count": state.count_type("bike"),
        "power_outage_count": state.count_type("outage"),
        "gritter_count": state.count_type("gritter", max_age_minutes=10),
        "interesting_aircraft_count": state.count_type("aircraft", max_age_minutes=10),
        "satellite_count": state.count_type("satellite", max_age_minutes=10),
    }


@app.get("/api/diagnostics")
def diagnostics():
    return {
        "version": "1.2.1",
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


@app.get("/api/events")
def events(type: str | None = None, q: str | None = None):
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
def nearest_rail_place(lat: float, lon: float):
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


@app.get("/api/places/{place_id:path}/board")
def place_board(place_id: str, limit: int = 10):
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


@app.get("/api/environment/measure")
def environment_measure(url: str):
    if smart_places is None:
        raise HTTPException(status_code=503, detail="Smart places are not ready")
    try:
        return smart_places.environment_measure(url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Environment Agency lookup failed: {type(exc).__name__}: {exc}") from exc


@app.get("/api/traffic/status")
def traffic_status():
    if traffic_flow_service is None:
        return {"enabled": False, "configured": False, "provider": "TomTom Traffic Flow"}
    return traffic_flow_service.status_payload()


@app.get("/api/traffic/tile/{z}/{x}/{y}.png")
def traffic_tile(z: int, x: int, y: int, style: str = "relative0"):
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
def traffic_incident_tile(z: int, x: int, y: int):
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
def traffic_segment(lat: float, lon: float, zoom: int = 10):
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

@app.get("/api/weather/radar")
def weather_radar():
    """Return the latest public RainViewer radar tile template.

    No API key is required. Metadata is cached server-side so opening/toggling
    the layer does not repeatedly hit RainViewer.
    """
    now = time.time()
    cached = _weather_radar_cache.get("payload")
    if cached and now < float(_weather_radar_cache.get("expires") or 0):
        return cached
    try:
        response = requests.get(
            "https://api.rainviewer.com/public/weather-maps.json",
            headers={"User-Agent": "UKActivityRadar/1.2.1 weather-radar"},
            timeout=8,
        )
        response.raise_for_status()
        payload = rainviewer_payload(response.json())
    except Exception as exc:
        if cached:
            stale = dict(cached)
            stale["stale"] = True
            stale["warning"] = f"Radar metadata refresh failed: {type(exc).__name__}"
            return stale
        raise HTTPException(status_code=502, detail=f"Weather radar unavailable: {type(exc).__name__}") from exc
    _weather_radar_cache["payload"] = payload
    _weather_radar_cache["expires"] = now + 120
    return payload


@app.get("/api/search")
def search(q: str):
    payload = activity_search.search(q)
    if smart_places is not None:
        try:
            place_matches = smart_places.search_places(q, limit=8)
            existing = payload.get("matches") or []
            payload["matches"] = place_matches + existing
        except Exception:
            pass
    return payload


@app.get("/api/radio/nearby")
def nearby_radio(lat: float, lon: float, limit: int = 5):
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise HTTPException(status_code=400, detail="Invalid coordinates")
    try:
        return radio_lookup.nearby(lat, lon, limit=max(1, min(limit, 8)))
    except Exception as exc:
        return {
            "ok": False,
            "source": "Radio Browser",
            "stations": [],
            "note": f"Nearby public radio lookup unavailable: {type(exc).__name__}",
        }


@app.get("/api/activity/{activity_id:path}/media")
def activity_media(activity_id: str):
    item = state.get(activity_id)
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


@app.get("/api/activity/{activity_id:path}/aircraft")
def activity_aircraft(activity_id: str):
    item = state.get(activity_id)
    if not item or item.type not in {"sar", "aircraft"}:
        raise HTTPException(status_code=404, detail="Aircraft activity not found")
    return aircraft_info.lookup(item.source_id, item.vehicle_ref)


@app.get("/api/events/{event_id}/coverage")
def event_coverage(event_id: str):
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


@app.get("/api/feed-status")
def feed_status():
    return [_source_payload(s) for s in state.sources()]


@app.get("/api/readiness")
def readiness():
    return _readiness_payload()


@app.get("/api/product/plans")
def plans():
    return product_plans()


@app.post("/api/product/event")
def product_event(payload: ProductEventIn):
    accepted = product_analytics.record(payload.session_id, payload.event_name, payload.feature)
    if not accepted:
        raise HTTPException(status_code=400, detail="Unsupported product event")
    return {"ok": True}


@app.get("/api/product/summary")
def product_summary():
    return product_analytics.summary()


@app.get("/api/unmapped")
def unmapped():
    return state.unmapped()


@app.websocket("/ws")
async def websocket_updates(websocket: WebSocket):
    if not same_origin_websocket(websocket.headers.get("origin"), websocket.headers.get("host")):
        await websocket.close(code=1008, reason="WebSocket origin is not allowed")
        return
    await websocket.accept()
    # Start with a broad Britain viewport. Buses are intentionally hidden at
    # national zoom to avoid shipping tens of thousands of GPS points every
    # second. The browser sends its real viewport/zoom immediately after open.
    viewport: dict[str, object] = {
        "bbox": (-8.7, 49.5, 2.1, 59.2),
        "zoom": 6.0,
    }

    watched_id: str | None = None

    async def set_watching(new_id: str | None) -> None:
        nonlocal watched_id
        new_id = (new_id or "").strip() or None
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
                except (TypeError, ValueError):
                    pass
        except (WebSocketDisconnect, RuntimeError):
            return

    receiver = asyncio.create_task(receive_client())
    try:
        while True:
            zoom = float(viewport["zoom"])
            include_buses = zoom >= settings.bus_min_map_zoom
            include_vessels = zoom >= settings.maritime_min_map_zoom
            streamed_vehicles = state.all(
                bbox=viewport["bbox"],
                include_buses=include_buses,
                bus_limit=settings.bus_max_stream,
                include_vessels=include_vessels,
                vessel_limit=settings.maritime_max_stream,
            )
            streamed_events = state.all_events(bbox=viewport["bbox"])
            streamed = [*streamed_vehicles, *streamed_events]
            payload = {
                "activities": [x.model_dump(mode="json") for x in streamed],
                "vehicles": [v.model_dump(mode="json") for v in streamed_vehicles],
                "events": [e.model_dump(mode="json") for e in streamed_events],
                "summary": state.summary(),
                "sources": [_source_payload(s) for s in state.sources()],
                "stream": {
                    "bus_min_zoom": settings.bus_min_map_zoom,
                    "buses_in_memory": state.count_type("bus", max_age_minutes=5),
                    "buses_streamed": sum(v.type == "bus" for v in streamed_vehicles),
                    "bus_limit": settings.bus_max_stream,
                    "maritime_min_zoom": settings.maritime_min_map_zoom,
                    "vessels_in_memory": state.count_type("vessel", max_age_minutes=10),
                    "sar_in_memory": state.count_type("sar", max_age_minutes=10),
                    "vessels_streamed": sum(v.type in {"vessel", "sar"} for v in streamed_vehicles),
                    "maritime_limit": settings.maritime_max_stream,
                    "road_events": sum(e.type in {"road", "incident"} for e in streamed_events),
                    "flood_events": sum(e.type == "flood" for e in streamed_events),
                    "rnli_events": sum(e.type == "rescue" for e in streamed_events),
                    "weather_events": sum(e.type == "weather" for e in streamed_events),
                    "zoom": zoom,
                },
            }
            await websocket.send_json(payload)
            await asyncio.sleep(1.5)
    except (WebSocketDisconnect, asyncio.CancelledError):
        return
    finally:
        await set_watching(None)
        receiver.cancel()
        with suppress(asyncio.CancelledError, WebSocketDisconnect, RuntimeError):
            await receiver
