from __future__ import annotations

import re
import threading
from datetime import datetime, timedelta, timezone

import requests

from ..models import SourceStatus, Vehicle


# Conservative patterns aimed at UK public-service/SAR aviation. This is not a
# general aircraft tracker; the map should only surface obviously relevant
# callsigns rather than mislabel ordinary flights.
_CALLSIGN_PATTERNS = (
    re.compile(r"^CG\d{2,4}$"),
    re.compile(r"^COASTG?U?ARD"),
    re.compile(r"^BRITISHRESCUE"),
    re.compile(r"^RESCUE"),
    re.compile(r"^SAR\d*"),
    re.compile(r"^HELIMED"),
    re.compile(r"^AIRAMB"),
    # Current HM Coastguard/Bristow operational callsigns are commonly seen
    # as SRD/SRG + digits on ADS-B rather than literal RESCUE callsigns.
    re.compile(r"^SR[DG]\d"),
)

_COASTGUARD_REG = re.compile(r"^G-(?:MCG|RES|UAS)[A-Z]$", re.I)

_INTERESTING_CALLSIGN = re.compile(r"^(?:RRR|RFR|ASCOT|NATO|BAF|VAMP|TARTN|CNV|IAM|MMF|DUKE|JIGSAW|RCH|QID|NOH|RRR)\w*", re.I)

# Common ICAO aircraft designators seen in UK SAR/HEMS/public-service flying.
# ADSB.lol normally supplies the designator in ``t``; keeping a small local map
# makes the card useful immediately, while click-time Airport-Data enrichment can
# provide a fuller/airframe-specific model where available.
_TYPE_NAMES = {
    "S92": "Sikorsky S-92",
    "AW18": "Leonardo AW189",
    "A169": "Leonardo AW169",
    "EC35": "Airbus H135 / EC135",
    "EC45": "Airbus H145 / EC145",
    "AS65": "Airbus AS365 Dauphin",
    "S76": "Sikorsky S-76",
    "B412": "Bell 412",
    "B429": "Bell 429",
    "B407": "Bell 407",
    "EC30": "Airbus H130 / EC130",
    "EC55": "Airbus H155 / EC155",
    "P68": "Vulcanair P.68",
    "DA42": "Diamond DA42",
    "PC12": "Pilatus PC-12",
}


def _type_name(code: str | None) -> str | None:
    value = str(code or "").strip().upper()
    return _TYPE_NAMES.get(value)

def _is_interesting_aircraft(row: dict) -> bool:
    call = str(row.get("flight") or row.get("callsign") or "").strip().upper().replace(" ", "")
    dbflags = _num(row.get("dbFlags")) or 0
    # ADSB.lol/readsb convention: bit 0 marks military. Callsign fallback catches
    # common military/special-flight identities without turning this into a general plane tracker.
    return bool((int(dbflags) & 1) or (call and _INTERESTING_CALLSIGN.search(call)))

def parse_interesting_aircraft_response(payload: dict, *, now: datetime | None = None) -> tuple[list[Vehicle], int]:
    now = now or datetime.now(timezone.utc)
    aircraft = payload.get("ac") or payload.get("aircraft") or []
    out: dict[str, Vehicle] = {}
    total = 0
    if not isinstance(aircraft, list):
        return [], 0
    for row in aircraft:
        if not isinstance(row, dict):
            continue
        total += 1
        callsign = str(row.get("flight") or row.get("callsign") or "").strip()
        reg = str(row.get("r") or row.get("reg") or "").strip() or None
        if _is_public_service(callsign, reg) or not _is_interesting_aircraft(row):
            continue
        lat, lon = _num(row.get("lat")), _num(row.get("lon"))
        if lat is None or lon is None or not (48.0 <= lat <= 62.5 and -13.5 <= lon <= 5.0):
            continue
        seen_pos = _num(row.get("seen_pos")) or 0.0
        updated = now - timedelta(seconds=max(0.0, seen_pos))
        hex_id = str(row.get("hex") or row.get("icao") or callsign or "unknown").strip().lower()
        model = str(row.get("t") or row.get("type") or "").strip() or None
        gs = _num(row.get("gs")); track = _num(row.get("track"))
        altitude = row.get("alt_baro") or row.get("alt_geom")
        altitude_ft = _num(altitude)
        model_name = _type_name(model)
        bits = [x for x in (reg, model_name or model, f"{altitude_ft:.0f} ft" if altitude_ft is not None else None) if x]
        v = Vehicle(
            id=f"aircraft:{hex_id}", type="aircraft", name=callsign or reg or f"Special aircraft {hex_id.upper()}",
            subtitle=" • ".join(bits) or "Military / special-interest aircraft", lat=lat, lon=lon,
            operator="Military / special-interest aircraft", status="Airborne", speed=gs, speed_unit="kt" if gs is not None else None,
            position_method="gps", position_label="ADS-B / MLAT position from ADSB.lol", source="ADSB.lol", source_id=hex_id,
            vehicle_ref=reg, aircraft_type_code=model, aircraft_model=model_name, altitude_ft=altitude_ft,
            bearing=track, interesting=True, last_updated=updated,
        )
        old = out.get(v.id)
        if old is None or v.last_updated >= old.last_updated:
            out[v.id] = v
    return list(out.values()), total


def _is_public_service(callsign: str | None, registration: str | None) -> bool:
    call = (callsign or "").strip().upper().replace(" ", "")
    reg = (registration or "").strip().upper()
    return bool((call and any(p.search(call) for p in _CALLSIGN_PATTERNS)) or _COASTGUARD_REG.match(reg))


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_adsb_response(payload: dict, *, now: datetime | None = None) -> tuple[list[Vehicle], int]:
    now = now or datetime.now(timezone.utc)
    aircraft = payload.get("ac") or payload.get("aircraft") or []
    out: dict[str, Vehicle] = {}
    total = 0
    if not isinstance(aircraft, list):
        return [], 0
    for row in aircraft:
        if not isinstance(row, dict):
            continue
        total += 1
        callsign = str(row.get("flight") or row.get("callsign") or "").strip()
        reg = str(row.get("r") or row.get("reg") or "").strip() or None
        if not _is_public_service(callsign, reg):
            continue
        lat, lon = _num(row.get("lat")), _num(row.get("lon"))
        if lat is None or lon is None or not (48.0 <= lat <= 62.5 and -13.5 <= lon <= 5.0):
            continue
        seen_pos = _num(row.get("seen_pos")) or 0.0
        updated = now - timedelta(seconds=max(0.0, seen_pos))
        hex_id = str(row.get("hex") or row.get("icao") or callsign or "unknown").strip().lower()
        model = str(row.get("t") or row.get("type") or "").strip() or None
        gs = _num(row.get("gs"))
        track = _num(row.get("track"))
        altitude = row.get("alt_baro") or row.get("alt_geom")
        altitude_ft = _num(altitude)
        model_name = _type_name(model)
        bits = [x for x in (reg, model_name or model, f"{altitude_ft:.0f} ft" if altitude_ft is not None else None) if x]
        subtitle = " • ".join(bits) or "Public-service aircraft"
        is_coastguard = bool(reg and _COASTGUARD_REG.match(reg)) or callsign.upper().replace(" ", "").startswith(("SRD", "SRG", "BRITISHRESCUE", "RESCUE", "CG"))
        display_name = (f"HM Coastguard {reg}" if is_coastguard and reg else (callsign or reg or f"SAR {hex_id.upper()}"))
        v = Vehicle(
            id=f"sar:{hex_id}",
            type="sar",
            name=display_name,
            subtitle=subtitle,
            lat=lat,
            lon=lon,
            operator="HM Coastguard" if is_coastguard else ("UK HEMS / public-service aircraft" if "HELIMED" in callsign.upper() else "Public-service/SAR aircraft"),
            status="Airborne",
            speed=gs,
            speed_unit="kt" if gs is not None else None,
            position_method="gps",
            position_label="ADS-B / MLAT position from ADSB.lol",
            source="ADSB.lol",
            source_id=hex_id,
            vehicle_ref=reg,
            aircraft_type_code=model,
            aircraft_model=model_name,
            altitude_ft=altitude_ft,
            bearing=track,
            interesting=True,
            last_updated=updated,
        )
        old = out.get(v.id)
        if old is None or v.last_updated >= old.last_updated:
            out[v.id] = v
    return list(out.values()), total


class ADSBLolSARCollector:
    """Low-rate UK SAR/special-aircraft collector.

    ADSB.lol's public point API can legitimately return HTTP 429 when several
    large point queries are fired back-to-back. Older Radar builds queried all
    four UK coverage circles every minute, creating a burst. This collector
    intentionally polls ONE region per interval, rotates around the country,
    caches the other recent regions, and honours Retry-After on 429 responses.
    """

    def __init__(self, settings, state):
        self.settings = settings
        self.state = state
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "UKActivityRadar/1.2.1 (open ADS-B consumer)"})
        self._seen = 0
        self._point_index = 0
        self._region_cache: dict[int, tuple[list[Vehicle], list[Vehicle], int, datetime]] = {}

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="adsb-sar", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)

    def _fetch_region(self, index: int) -> tuple[list[Vehicle], list[Vehicle], int]:
        lat, lon, radius = self.settings.adsb_sar_points[index]
        url = f"{self.settings.adsb_lol_base_url}/v2/point/{lat}/{lon}/{radius}"
        r = self._session.get(url, timeout=20)
        r.raise_for_status()
        payload = r.json()
        rows, count = parse_adsb_response(payload)
        special, _ = parse_interesting_aircraft_response(payload)
        if not self.settings.interesting_aircraft_enabled:
            special = []
        return rows, special, count

    def _cached_snapshot(self, *, max_age_minutes: int = 10) -> tuple[list[Vehicle], list[Vehicle], int]:
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=max_age_minutes)
        combined: dict[str, Vehicle] = {}
        interesting: dict[str, Vehicle] = {}
        checked = 0
        expired: list[int] = []
        for idx, (rows, special, total, fetched_at) in self._region_cache.items():
            if fetched_at < cutoff:
                expired.append(idx)
                continue
            checked += total
            for v in rows:
                old = combined.get(v.id)
                if old is None or v.last_updated >= old.last_updated:
                    combined[v.id] = v
            for v in special:
                old = interesting.get(v.id)
                if old is None or v.last_updated >= old.last_updated:
                    interesting[v.id] = v
        for idx in expired:
            self._region_cache.pop(idx, None)
        return list(combined.values()), list(interesting.values()), checked

    def fetch_once(self) -> tuple[list[Vehicle], list[Vehicle], int]:
        """Diagnostic full-UK fetch with deliberate spacing between regions.

        Normal background operation uses one region per cycle; this method is
        retained for manual tests and spaces calls so it does not recreate the
        old four-request burst.
        """
        combined: dict[str, Vehicle] = {}
        interesting: dict[str, Vehicle] = {}
        total = 0
        for index in range(len(self.settings.adsb_sar_points)):
            rows, special, count = self._fetch_region(index)
            total += count
            for v in rows:
                old = combined.get(v.id)
                if old is None or v.last_updated >= old.last_updated:
                    combined[v.id] = v
            for v in special:
                old = interesting.get(v.id)
                if old is None or v.last_updated >= old.last_updated:
                    interesting[v.id] = v
            if index + 1 < len(self.settings.adsb_sar_points):
                # ADS-B public APIs commonly protect against bursts. Keep the
                # diagnostic path polite too.
                self._stop.wait(1.25)
        return list(combined.values()), list(interesting.values()), total

    @staticmethod
    def _retry_after_seconds(exc: requests.HTTPError, default: int) -> int:
        response = getattr(exc, "response", None)
        raw = response.headers.get("Retry-After") if response is not None else None
        try:
            return max(30, min(900, int(float(raw))))
        except (TypeError, ValueError):
            return max(90, min(300, default * 2))

    def _run(self) -> None:
        point_count = max(1, len(self.settings.adsb_sar_points))
        self.state.set_source(SourceStatus(
            name="adsb_sar", enabled=True, connected=False, mode="connecting",
            message=f"Connecting to ADSB.lol • rotating {point_count} UK coverage regions…",
        ))
        while not self._stop.is_set():
            index = self._point_index % point_count
            try:
                rows, special, total = self._fetch_region(index)
                fetched_at = datetime.now(timezone.utc)
                self._region_cache[index] = (rows, special, total, fetched_at)
                self._point_index = (index + 1) % point_count
                vehicles, all_special, checked = self._cached_snapshot()
                for v in [*rows, *special]:
                    self.state.upsert(v)
                self._seen += total
                cycle_minutes = max(1, round(point_count * self.settings.adsb_sar_poll_seconds / 60))
                self.state.set_source(SourceStatus(
                    name="adsb_sar", enabled=True, connected=True,
                    mode="ready" if not vehicles and not all_special else "live",
                    message=(
                        f"ADSB.lol verified • region {index + 1}/{point_count} refreshed • "
                        f"{checked:,} recent aircraft records checked • "
                        f"{len(vehicles):,} SAR/HEMS + {len(all_special):,} military/special visible • "
                        f"full UK rotation ≈ {cycle_minutes} min"
                    ),
                    last_message_at=fetched_at,
                    messages_seen=self._seen,
                    movements_mapped=len(vehicles) + len(all_special),
                ))
            except requests.HTTPError as exc:
                status = getattr(exc, "response", None)
                code = getattr(status, "status_code", None)
                if code == 429:
                    delay = self._retry_after_seconds(exc, self.settings.adsb_sar_poll_seconds)
                    vehicles, special, _ = self._cached_snapshot()
                    self.state.set_source(SourceStatus(
                        name="adsb_sar", enabled=True, connected=True, mode="waiting",
                        message=(
                            f"ADSB.lol rate limit reached • retaining {len(vehicles) + len(special)} last verified "
                            f"SAR/special positions • retrying region {index + 1}/{point_count} in {delay}s"
                        ),
                        last_message_at=datetime.now(timezone.utc),
                        messages_seen=self._seen,
                        movements_mapped=len(vehicles) + len(special),
                    ))
                    self._stop.wait(delay)
                    continue
                self.state.set_source(SourceStatus(
                    name="adsb_sar", enabled=True, connected=False, mode="error",
                    message=f"ADSB SAR HTTP error: {code or 'unknown'} • {str(exc)[:180]}",
                ))
            except Exception as exc:
                self.state.set_source(SourceStatus(
                    name="adsb_sar", enabled=True, connected=False, mode="error",
                    message=f"ADSB SAR feed error: {type(exc).__name__}: {exc}",
                ))
            self._stop.wait(self.settings.adsb_sar_poll_seconds)
