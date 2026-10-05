from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    network_rail_username: str = os.getenv("NETWORK_RAIL_USERNAME", "").strip()
    network_rail_password: str = os.getenv("NETWORK_RAIL_PASSWORD", "").strip()
    network_rail_client_id: str = os.getenv("NETWORK_RAIL_CLIENT_ID", "uk-activity-radar-dev").strip()
    network_rail_host: str = os.getenv("NETWORK_RAIL_HOST", "publicdatafeeds.networkrail.co.uk").strip()
    network_rail_port: int = int(os.getenv("NETWORK_RAIL_PORT", "61618"))

    trust_topic: str = os.getenv("NETWORK_RAIL_TRUST_TOPIC", "/topic/TRAIN_MVT_ALL_TOC").strip()
    td_topic: str = os.getenv("NETWORK_RAIL_TD_TOPIC", "/topic/TD_ALL_SIG_AREA").strip()
    vstp_topic: str = os.getenv("NETWORK_RAIL_VSTP_TOPIC", "/topic/VSTP_ALL").strip()

    network_rail_enabled_flag: bool = _bool("NETWORK_RAIL_ENABLED", True)
    td_enabled: bool = _bool("TD_ENABLED", True)
    vstp_enabled: bool = _bool("VSTP_ENABLED", True)
    demo_mode: bool = _bool("DEMO_MODE", False)

    auto_sync_schedule: bool = _bool("AUTO_SYNC_SCHEDULE", True)
    auto_sync_reference: bool = _bool("AUTO_SYNC_REFERENCE", True)
    refresh_schedule: bool = _bool("REFRESH_SCHEDULE", False)
    refresh_reference_data: bool = _bool("REFRESH_REFERENCE_DATA", False)

    # DfT Bus Open Data Service (BODS). Public live vehicle data is free/open.
    bods_enabled: bool = _bool("BODS_ENABLED", True)
    bods_api_key: str = os.getenv("BODS_API_KEY", "").strip()
    bods_bulk_url: str = os.getenv(
        "BODS_BULK_URL", "https://data.bus-data.dft.gov.uk/avl/download/bulk_archive"
    ).strip()
    # BODS changed enough over earlier builds that old .env files can contain
    # the metadata endpoint (/api/v1/datafeed/) instead of the live GTFS-RT
    # vehicle endpoint. Normalise it here so upgrading the app repairs that
    # stale configuration automatically.
    _bods_api_url_raw: str = os.getenv(
        "BODS_API_URL", "https://data.bus-data.dft.gov.uk/api/v1/gtfsrtdatafeed/"
    ).strip()

    @property
    def bods_api_url(self) -> str:
        raw = (self._bods_api_url_raw or "").rstrip("/") + "/"
        if "/api/v1/datafeed/" in raw and "/gtfsrtdatafeed/" not in raw:
            return "https://data.bus-data.dft.gov.uk/api/v1/gtfsrtdatafeed/"
        return raw
    bods_disruptions_url: str = os.getenv("BODS_DISRUPTIONS_URL", "https://data.bus-data.dft.gov.uk/api/v1/siri-sx").strip()
    bods_cancellations_url: str = os.getenv("BODS_CANCELLATIONS_URL", "https://data.bus-data.dft.gov.uk/api/v1/siri-sx/cancellations").strip()
    bods_sx_enabled: bool = _bool("BODS_SX_ENABLED", True)
    bods_sx_poll_seconds: int = max(30, int(os.getenv("BODS_SX_POLL_SECONDS", "60")))
    bods_poll_seconds: int = max(10, int(os.getenv("BODS_POLL_SECONDS", "30")))
    bods_max_age_seconds: int = max(60, int(os.getenv("BODS_MAX_AGE_SECONDS", "300")))
    bus_min_map_zoom: float = float(os.getenv("BUS_MIN_MAP_ZOOM", "7"))
    bus_max_stream: int = max(100, int(os.getenv("BUS_MAX_STREAM", "2500")))

    # National Highways Road & Lane Closures v2.0. Registration and an API
    # subscription key are free; never expose this key to browser JavaScript.
    national_highways_enabled: bool = _bool("NATIONAL_HIGHWAYS_ENABLED", True)
    national_highways_api_key: str = os.getenv("NATIONAL_HIGHWAYS_API_KEY", "").strip()
    national_highways_base_url: str = os.getenv(
        "NATIONAL_HIGHWAYS_BASE_URL", "https://api.data.nationalhighways.co.uk/roads/v2.0"
    ).rstrip("/")
    national_highways_poll_seconds: int = max(30, int(os.getenv("NATIONAL_HIGHWAYS_POLL_SECONDS", "60")))
    national_highways_include_planned: bool = _bool("NATIONAL_HIGHWAYS_INCLUDE_PLANNED", False)

    # Live traffic-flow overlay. TomTom offers a no-card free allowance for
    # traffic raster/vector tiles. The browser calls our server-side tile proxy
    # so the API key never appears in page source. This is an optional layer;
    # National Highways incidents continue to work independently.
    traffic_flow_enabled: bool = _bool("TRAFFIC_FLOW_ENABLED", True)
    tomtom_traffic_api_key: str = os.getenv("TOMTOM_TRAFFIC_API_KEY", "").strip()
    tomtom_traffic_base_url: str = os.getenv("TOMTOM_TRAFFIC_BASE_URL", "https://api.tomtom.com").rstrip("/")
    traffic_flow_cache_seconds: int = max(15, int(os.getenv("TRAFFIC_FLOW_CACHE_SECONDS", "45")))
    traffic_flow_cache_max_tiles: int = max(64, int(os.getenv("TRAFFIC_FLOW_CACHE_MAX_TILES", "512")))
    traffic_flow_timeout_seconds: int = max(3, int(os.getenv("TRAFFIC_FLOW_TIMEOUT_SECONDS", "12")))

    @property
    def traffic_flow_ready(self) -> bool:
        return self.traffic_flow_enabled and bool(self.tomtom_traffic_api_key)

    # Environment Agency current flood warnings/alerts. No account or key.
    ea_floods_enabled: bool = _bool("EA_FLOODS_ENABLED", True)
    ea_floods_url: str = os.getenv(
        "EA_FLOODS_URL", "https://environment.data.gov.uk/flood-monitoring/id/floods"
    ).strip()
    ea_flood_areas_url: str = os.getenv(
        "EA_FLOOD_AREAS_URL", "https://environment.data.gov.uk/flood-monitoring/id/floodAreas"
    ).rstrip("/")
    ea_floods_poll_seconds: int = max(60, int(os.getenv("EA_FLOODS_POLL_SECONDS", "300")))


    # AISStream live maritime positions. The service is free but requires a
    # free account/API key. Keep the key server-side only.
    aisstream_enabled: bool = _bool("AISSTREAM_ENABLED", True)
    aisstream_api_key: str = os.getenv("AISSTREAM_API_KEY", "").strip()
    aisstream_url: str = os.getenv("AISSTREAM_URL", "wss://stream.aisstream.io/v0/stream").strip()
    maritime_min_map_zoom: float = float(os.getenv("MARITIME_MIN_MAP_ZOOM", "6"))
    maritime_max_stream: int = max(100, int(os.getenv("MARITIME_MAX_STREAM", "1800")))

    @property
    def aisstream_bounding_boxes(self) -> list[list[list[float]]]:
        raw = os.getenv("AISSTREAM_BOUNDING_BOXES", "").strip()
        if raw:
            try:
                import json
                parsed = json.loads(raw)
                if isinstance(parsed, list) and parsed:
                    return parsed
            except Exception:
                pass
        # AISStream bounding boxes are [[north, west], [south, east]].
        # This covers UK/Ireland and surrounding coastal waters.
        return [[[61.5, -13.0], [48.5, 4.0]]]

    @property
    def aisstream_ready(self) -> bool:
        return self.aisstream_enabled and bool(self.aisstream_api_key)


    # Free/open ADSB.lol API for relevant UK SAR / air-ambulance aircraft.
    adsb_sar_enabled: bool = _bool("ADSB_SAR_ENABLED", True)
    adsb_lol_base_url: str = os.getenv("ADSB_LOL_BASE_URL", "https://api.adsb.lol").rstrip("/")
    adsb_sar_poll_seconds: int = max(30, int(os.getenv("ADSB_SAR_POLL_SECONDS", "60")))

    @property
    def adsb_sar_points(self) -> list[tuple[float, float, int]]:
        # Overlapping circles cover the UK and nearby coastal waters.
        return [
            (50.8, -3.5, 220),
            (52.7, -1.5, 220),
            (55.0, -3.0, 230),
            (57.5, -4.5, 230),
        ]

    # Official RNLI latest-launch API. No account/key required.
    rnli_enabled: bool = _bool("RNLI_ENABLED", True)
    rnli_launches_url: str = os.getenv("RNLI_LAUNCHES_URL", "https://services.rnli.org/api/launches").strip()
    rnli_poll_seconds: int = max(60, int(os.getenv("RNLI_POLL_SECONDS", "120")))

    # London Fire Brigade public incident pages — free, no key.
    london_fire_enabled: bool = _bool("LONDON_FIRE_ENABLED", True)
    london_fire_url: str = os.getenv("LONDON_FIRE_URL", "https://www.london-fire.gov.uk/incidents/?initialLoad=True").strip()
    london_fire_poll_seconds: int = max(60, int(os.getenv("LONDON_FIRE_POLL_SECONDS", "180")))


    # Free public mirror of Met Office NSWWS warning polygons. No key. The
    # mirror refreshes hourly; official Met Office links remain authoritative.
    met_office_enabled: bool = _bool("MET_OFFICE_ENABLED", True)
    met_office_arcgis_item: str = os.getenv("MET_OFFICE_ARCGIS_ITEM", "23e51204bd3348f1a37b50786b8abdea").strip()
    met_office_poll_seconds: int = max(300, int(os.getenv("MET_OFFICE_POLL_SECONDS", "900")))

    # Free OpenStreetMap Nominatim fallback for one-off map searches such as
    # "M1 J20" or "A5 Hinckley". The server caches and rate-limits requests.
    nominatim_enabled: bool = _bool("NOMINATIM_ENABLED", True)
    nominatim_url: str = os.getenv("NOMINATIM_URL", "https://nominatim.openstreetmap.org/search").strip()

    # Free Wikimedia Commons image lookup used for optional detail-card photos.
    wikimedia_enabled: bool = _bool("WIKIMEDIA_ENABLED", False)
    wikimedia_api_url: str = os.getenv("WIKIMEDIA_API_URL", "https://commons.wikimedia.org/w/api.php").strip()

    # Free related-coverage discovery. No GDELT key is required. X is linked
    # via its normal public search page rather than a paid API.
    coverage_enabled: bool = _bool("COVERAGE_ENABLED", True)
    gdelt_url: str = os.getenv(
        "GDELT_URL", "https://api.gdeltproject.org/api/v2/doc/doc"
    ).strip()


    # --- v1.2 UK live-world layers: all free/open endpoints where practical ---
    bgs_earthquakes_enabled: bool = _bool("BGS_EARTHQUAKES_ENABLED", True)
    bgs_earthquakes_url: str = os.getenv("BGS_EARTHQUAKES_URL", "https://quakes.bgs.ac.uk/feeds/MhSeismology.xml").strip()
    bgs_earthquakes_poll_seconds: int = max(120, int(os.getenv("BGS_EARTHQUAKES_POLL_SECONDS", "300")))

    ea_levels_enabled: bool = _bool("EA_LEVELS_ENABLED", True)
    ea_stations_url: str = os.getenv("EA_STATIONS_URL", "https://environment.data.gov.uk/flood-monitoring/id/stations").strip()
    ea_readings_url: str = os.getenv("EA_READINGS_URL", "https://environment.data.gov.uk/flood-monitoring/data/readings?latest").strip()
    ea_levels_poll_seconds: int = max(300, int(os.getenv("EA_LEVELS_POLL_SECONDS", "900")))
    ea_levels_max_points: int = max(100, int(os.getenv("EA_LEVELS_MAX_POINTS", "1200")))

    wavenet_enabled: bool = _bool("WAVENET_ENABLED", True)
    wavenet_map_url: str = os.getenv("WAVENET_MAP_URL", "https://wavenet.cefas.co.uk/map").strip()
    wavenet_poll_seconds: int = max(600, int(os.getenv("WAVENET_POLL_SECONDS", "1800")))
    wavenet_max_sites: int = max(10, int(os.getenv("WAVENET_MAX_SITES", "60")))

    carbon_intensity_enabled: bool = _bool("CARBON_INTENSITY_ENABLED", True)
    carbon_intensity_url: str = os.getenv("CARBON_INTENSITY_URL", "https://api.carbonintensity.org.uk/regional").strip()
    carbon_intensity_poll_seconds: int = max(300, int(os.getenv("CARBON_INTENSITY_POLL_SECONDS", "1800")))

    uk_air_enabled: bool = _bool("UK_AIR_ENABLED", True)
    uk_air_base_url: str = os.getenv("UK_AIR_BASE_URL", "https://uk-air.defra.gov.uk/latest/measurement-summary-map").strip()
    uk_air_poll_seconds: int = max(900, int(os.getenv("UK_AIR_POLL_SECONDS", "3600")))

    tfl_live_enabled: bool = _bool("TFL_LIVE_ENABLED", True)
    tfl_api_base: str = os.getenv("TFL_API_BASE", "https://api.tfl.gov.uk").rstrip("/")
    tfl_app_key: str = os.getenv("TFL_APP_KEY", "").strip()
    tfl_poll_seconds: int = max(60, int(os.getenv("TFL_POLL_SECONDS", "120")))

    gbfs_enabled: bool = _bool("GBFS_ENABLED", True)
    gbfs_systems_url: str = os.getenv("GBFS_SYSTEMS_URL", "https://raw.githubusercontent.com/MobilityData/gbfs/master/systems.csv").strip()
    gbfs_poll_seconds: int = max(120, int(os.getenv("GBFS_POLL_SECONDS", "300")))
    gbfs_max_systems: int = max(1, int(os.getenv("GBFS_MAX_SYSTEMS", "12")))
    gbfs_max_points: int = max(100, int(os.getenv("GBFS_MAX_POINTS", "1500")))

    space_weather_enabled: bool = _bool("SPACE_WEATHER_ENABLED", True)
    noaa_kp_url: str = os.getenv("NOAA_KP_URL", "https://services.swpc.noaa.gov/json/planetary_k_index_1m.json").strip()
    noaa_aurora_url: str = os.getenv("NOAA_AURORA_URL", "https://services.swpc.noaa.gov/json/ovation_aurora_latest.json").strip()
    space_weather_poll_seconds: int = max(120, int(os.getenv("SPACE_WEATHER_POLL_SECONDS", "300")))

    iss_enabled: bool = _bool("ISS_ENABLED", True)
    iss_tle_url: str = os.getenv("ISS_TLE_URL", "https://celestrak.org/NORAD/elements/gp.php?CATNR=25544&FORMAT=TLE").strip()
    iss_poll_seconds: int = max(10, int(os.getenv("ISS_POLL_SECONDS", "20")))
    iss_tle_refresh_seconds: int = max(1800, int(os.getenv("ISS_TLE_REFRESH_SECONDS", "21600")))

    power_outages_enabled: bool = _bool("POWER_OUTAGES_ENABLED", True)
    nged_powercuts_url: str = os.getenv("NGED_POWERCUTS_URL", "https://connecteddata.nationalgrid.co.uk/dataset/d6672e1e-c684-4cea-bb78-c7e5248b62a2/resource/a1365982-4e05-463c-8304-8323a2ba0ccd/download/live_detailed_power_cuts.csv").strip()
    ssen_outage_metadata_url: str = os.getenv("SSEN_OUTAGE_METADATA_URL", "https://ckan-prod.sse.datopian.com/dataset/real_time_outage.jsonld").strip()
    power_outages_poll_seconds: int = max(300, int(os.getenv("POWER_OUTAGES_POLL_SECONDS", "900")))

    gritter_enabled: bool = _bool("GRITTER_ENABLED", True)
    gritter_arcgis_item: str = os.getenv("GRITTER_ARCGIS_ITEM", "a256a6b57fa440daa50fc2974d80f219").strip()
    gritter_poll_seconds: int = max(60, int(os.getenv("GRITTER_POLL_SECONDS", "120")))

    interesting_aircraft_enabled: bool = _bool("INTERESTING_AIRCRAFT_ENABLED", True)

    # Optional free/registration-only official traffic feeds. Configure these when approved URLs are supplied.
    traffic_scotland_datex_url: str = os.getenv("TRAFFIC_SCOTLAND_DATEX_URL", "").strip()
    traffic_wales_datex_url: str = os.getenv("TRAFFIC_WALES_DATEX_URL", "").strip()
    ntis_datex_url: str = os.getenv("NTIS_DATEX_URL", "").strip()

    @property
    def credentials_present(self) -> bool:
        return bool(self.network_rail_username and self.network_rail_password)

    @property
    def network_rail_enabled(self) -> bool:
        return self.network_rail_enabled_flag and self.credentials_present

    @property
    def national_highways_ready(self) -> bool:
        return self.national_highways_enabled and bool(self.national_highways_api_key)


settings = Settings()
