from __future__ import annotations

import csv
import gzip
import io
import json
import shutil
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from threading import Lock
from typing import Any
import zipfile

import requests

NR_SUPPORTING_URLS = [
    "https://publicdatafeeds.networkrail.co.uk/ntrod/SupportingFileAuthenticate?type={dataset}",
    "https://datafeeds.networkrail.co.uk/ntrod/SupportingFileAuthenticate?type={dataset}",
]
NAPTAN_URL = "https://naptan.api.dft.gov.uk/v1/access-nodes?dataFormat=xml"

# Development geography fallback.  This dataset merges CORPUS/BPLAN/NaPTAN and
# other published rail geography and exposes STANOX/TIPLOC with WGS84 coords.
# It is intentionally isolated here so it can be replaced by an official Rail
# Data Marketplace BPLAN ingest before a commercial release.
PUBLIC_RAIL_GEO_URLS = [
    "https://raw.githubusercontent.com/davwheat/YATIPLOCL/main/tiplocs-merged-stanox.csv",
    "https://raw.githubusercontent.com/davwheat/YATIPLOCL/main/tiplocs-merged.csv",
]

USER_AGENT = "UKActivityRadar/0.4.1"
_NAPTAN_LOCK = Lock()
_PUBLIC_GEO_LOCK = Lock()
_STATION_FILE_LOCK = Lock()


class FeedDownloadError(RuntimeError):
    pass


def _nr_get(url: str, username: str, password: str, *, timeout: int = 180, stream: bool = False) -> requests.Response:
    response = requests.get(
        url,
        auth=(username, password),
        headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
        timeout=timeout,
        allow_redirects=True,
        stream=stream,
    )
    if response.status_code >= 400:
        body = ""
        try:
            body = response.text[:300].replace("\n", " ")
        except Exception:
            pass
        hint = ""
        if response.status_code in {400, 401, 403}:
            hint = " Check that this feed is enabled under My Feeds in your Network Rail Open Data account."
        raise FeedDownloadError(f"HTTP {response.status_code} from Network Rail.{hint} {body}".strip())
    return response


def download_supporting(dataset: str, username: str, password: str) -> bytes:
    if not username or not password:
        raise FeedDownloadError("Network Rail username/password are required")
    errors: list[str] = []
    for template in NR_SUPPORTING_URLS:
        url = template.format(dataset=dataset)
        try:
            r = _nr_get(url, username, password)
            raw = r.content
            if raw[:2] == b"\x1f\x8b":
                raw = gzip.decompress(raw)
            return raw
        except Exception as exc:
            errors.append(f"{url.split('/')[2]}: {exc}")
    raise FeedDownloadError(" | ".join(errors))


def _json_rows(raw: bytes, keys: tuple[str, ...]) -> list[dict[str, Any]]:
    payload = json.loads(raw.decode("utf-8-sig"))
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
    raise ValueError("Unexpected JSON structure")


def _first(row: dict, *names: str) -> str:
    lowered = {str(k).lower(): v for k, v in row.items()}
    for name in names:
        val = row.get(name)
        if val not in (None, ""):
            return str(val).strip()
        val = lowered.get(name.lower())
        if val not in (None, ""):
            return str(val).strip()
    return ""


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _desc_text(elem: ET.Element, *names: str) -> str:
    wanted = {n.lower() for n in names}
    for child in elem.iter():
        local = _local(child.tag).lower()
        if local in wanted and child.text:
            text = child.text.strip()
            if text:
                return text
        for attr, val in child.attrib.items():
            if attr.rsplit("}", 1)[-1].lower() in wanted and str(val).strip():
                return str(val).strip()
    return ""


def _lat_lon(elem: ET.Element) -> tuple[float, float] | None:
    lat = _desc_text(elem, "Latitude")
    lon = _desc_text(elem, "Longitude")
    if not lat or not lon:
        return None
    try:
        return float(lat), float(lon)
    except ValueError:
        return None


def _download_plain(url: str, path: Path, timeout: int = 240) -> None:
    with requests.get(url, headers={"User-Agent": USER_AGENT}, stream=True, timeout=timeout) as response:
        response.raise_for_status()
        tmp = path.with_suffix(path.suffix + ".download")
        with tmp.open("wb") as f:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)
    magic = tmp.read_bytes()[:4]
    try:
        if magic[:2] == b"\x1f\x8b":
            with gzip.open(tmp, "rb") as src, path.open("wb") as dst:
                shutil.copyfileobj(src, dst, length=1024 * 1024)
        elif magic[:2] == b"PK":
            with zipfile.ZipFile(tmp) as zf:
                candidates = [n for n in zf.namelist() if n.lower().endswith((".xml", ".csv", ".txt"))]
                if not candidates:
                    raise ValueError("Downloaded archive did not contain a supported data file")
                with zf.open(candidates[0]) as src, path.open("wb") as dst:
                    shutil.copyfileobj(src, dst, length=1024 * 1024)
        else:
            tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def _parse_naptan_xml(path: Path) -> tuple[dict[str, dict], dict[str, dict]]:
    """Parse current and older NaPTAN rail reference shapes.

    The previous build looked only for a top-level ``RailReference`` element.
    Current NaPTAN exports commonly carry rail identifiers as ``AnnotatedRailRef``
    descendants of a StopPoint, so that returned zero TIPLOC coordinates.
    """
    by_tiploc: dict[str, dict] = {}
    by_crs: dict[str, dict] = {}

    for _, elem in ET.iterparse(path, events=("end",)):
        if _local(elem.tag) != "StopPoint":
            continue

        point = _lat_lon(elem)
        common_name = _desc_text(elem, "CommonName", "StopPointName")
        if point:
            for child in elem.iter():
                if _local(child.tag) not in {"AnnotatedRailRef", "RailReference"}:
                    continue
                tiploc = _desc_text(child, "TiplocRef", "TiplocCode", "TIPLOC").upper()
                crs = _desc_text(child, "CrsRef", "CrsCode", "CRS").upper()
                station_name = _desc_text(child, "StationName", "CommonName") or common_name
                direct_point = _lat_lon(child) or point
                record = {
                    "lat": direct_point[0],
                    "lon": direct_point[1],
                    "name": station_name or tiploc or crs,
                }
                if tiploc:
                    by_tiploc[tiploc] = record
                if crs:
                    by_crs[crs] = record
        elem.clear()

    return by_tiploc, by_crs


def ensure_naptan_cache(cache_file: Path, *, max_age_days: int = 14, force: bool = False) -> dict:
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    with _NAPTAN_LOCK:
        if cache_file.exists() and not force:
            age = time.time() - cache_file.stat().st_mtime
            if age < max_age_days * 86400:
                cached = json.loads(cache_file.read_text(encoding="utf-8"))
                # Do not keep using the broken v0.4 empty cache.
                if cached.get("by_tiploc") or cached.get("by_crs"):
                    return cached

        with tempfile.TemporaryDirectory(prefix="ukradar-naptan-") as tmp_dir:
            xml_path = Path(tmp_dir) / "naptan.xml"
            _download_plain(NAPTAN_URL, xml_path)
            by_tiploc, by_crs = _parse_naptan_xml(xml_path)

        payload = {"by_tiploc": by_tiploc, "by_crs": by_crs}
        tmp = cache_file.with_suffix(cache_file.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(cache_file)
        return payload


def _valid_gb_coordinate(lat: float, lon: float) -> bool:
    return 49.0 <= lat <= 61.6 and -9.8 <= lon <= 3.0


def _parse_public_rail_geo_csv(text: str) -> dict:
    reader = csv.DictReader(io.StringIO(text))
    by_stanox: dict[str, dict] = {}
    by_tiploc: dict[str, dict] = {}
    rows = 0
    usable = 0
    for row in reader:
        rows += 1
        stanox = _first(row, "platform_code", "stanox", "STANOX")
        tiploc = _first(row, "stop_id", "tiploc", "TIPLOC").upper()
        crs = _first(row, "stop_code", "crs", "CRS").upper()
        name = _first(row, "stop_name", "name", "description")
        lat_s = _first(row, "stop_lat", "lat", "latitude")
        lon_s = _first(row, "stop_lon", "lon", "longitude")
        try:
            lat, lon = float(lat_s), float(lon_s)
        except (TypeError, ValueError):
            continue
        if not _valid_gb_coordinate(lat, lon):
            continue
        rec = {
            "name": name or tiploc or crs or (f"STANOX {stanox}" if stanox else "Rail location"),
            "lat": lat,
            "lon": lon,
            "tiploc": tiploc or None,
            "crs": crs or None,
            "source": "Public rail geography fallback",
        }
        if stanox:
            s = stanox.zfill(5) if stanox.isdigit() else stanox
            # First usable record wins to keep deterministic behaviour for
            # duplicate STANOX entries in the merged source.
            by_stanox.setdefault(s, rec)
        if tiploc:
            by_tiploc.setdefault(tiploc, rec)
        usable += 1
    if not by_stanox:
        raise ValueError("Public rail geography CSV contained no usable STANOX coordinates")
    return {"by_stanox": by_stanox, "by_tiploc": by_tiploc, "rows": rows, "usable": usable}


def ensure_public_rail_geo_cache(cache_file: Path, *, max_age_days: int = 14, force: bool = False) -> dict:
    """Download a cleaned public STANOX/TIPLOC geography fallback.

    This fixes the immediate live-map problem while the application remains able
    to ingest official CORPUS/SMART/SCHEDULE data independently.
    """
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    with _PUBLIC_GEO_LOCK:
        if cache_file.exists() and not force:
            age = time.time() - cache_file.stat().st_mtime
            if age < max_age_days * 86400:
                cached = json.loads(cache_file.read_text(encoding="utf-8"))
                if cached.get("by_stanox"):
                    return cached

        errors: list[str] = []
        for url in PUBLIC_RAIL_GEO_URLS:
            try:
                r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=120)
                r.raise_for_status()
                parsed = _parse_public_rail_geo_csv(r.content.decode("utf-8-sig", errors="replace"))
                parsed["download_url"] = url
                tmp = cache_file.with_suffix(cache_file.suffix + ".tmp")
                tmp.write_text(json.dumps(parsed, ensure_ascii=False), encoding="utf-8")
                tmp.replace(cache_file)
                return parsed
            except Exception as exc:
                errors.append(f"{url}: {type(exc).__name__}: {exc}")
        raise FeedDownloadError("Could not download public rail geography: " + " | ".join(errors))


def build_stations_from_corpus(corpus_rows: list[dict], naptan: dict) -> dict[str, dict]:
    by_tiploc = naptan.get("by_tiploc", {})
    by_crs = naptan.get("by_crs", {})
    result: dict[str, dict] = {}
    for row in corpus_rows:
        stanox = _first(row, "STANOX", "NLCStanoxCode")
        if not stanox:
            continue
        stanox = stanox.zfill(5)
        tiploc = _first(row, "TIPLOC", "NLCTiplocCode").upper()
        crs = _first(row, "3ALPHA", "NLCCrsCode").upper()
        desc = _first(row, "NLCDESC", "NLCDescription", "NLCDESC16")
        point = by_tiploc.get(tiploc) or by_crs.get(crs)
        if not point:
            continue
        result[stanox] = {
            "name": desc or point.get("name") or tiploc or crs or f"STANOX {stanox}",
            "lat": float(point["lat"]),
            "lon": float(point["lon"]),
            "tiploc": tiploc or None,
            "crs": crs or None,
            "source": "CORPUS + NaPTAN",
        }
    return result


def merge_station_file(station_file: Path, additions: dict[str, dict]) -> int:
    # Schedule and reference bootstrap run concurrently at startup. Serialize
    # their read/merge/write cycle and publish atomically.
    with _STATION_FILE_LOCK:
        current: dict[str, dict] = {}
        if station_file.exists():
            try:
                loaded = json.loads(station_file.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    current = loaded
            except Exception:
                pass
        current.update(additions)
        station_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = station_file.with_suffix(station_file.suffix + ".tmp")
        tmp.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(station_file)
        return len(current)


def sync_public_geography(data_dir: Path, *, force: bool = False) -> dict[str, Any]:
    """Populate stations.json from broad public rail geography and return stats."""
    data_dir.mkdir(parents=True, exist_ok=True)
    geo = ensure_public_rail_geo_cache(data_dir / "public_rail_geo.json", force=force)
    stations = geo.get("by_stanox", {})
    total = merge_station_file(data_dir / "stations.json", stations)
    return {"stanox": len(stations), "total": total, "rows": int(geo.get("rows") or 0)}


def sync_reference_data(username: str, password: str, data_dir: Path, *, force: bool = False) -> dict:
    """Build rail geography independently from each available source.

    Order/precedence:
      1. public cleaned rail geography provides broad STANOX coverage immediately;
      2. NaPTAN + CORPUS can override passenger-station points with official data;
      3. SMART feeds the TD berth resolver.

    A failure in any one source no longer leaves the entire map empty.
    """
    data_dir.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {"rail_geo": None, "corpus": None, "smart": None, "naptan": None}

    station_file = data_dir / "stations.json"

    # Broad geography first so TRUST can start mapping while other datasets sync.
    try:
        geo = ensure_public_rail_geo_cache(data_dir / "public_rail_geo.json", force=force)
        stations = geo.get("by_stanox", {})
        total = merge_station_file(station_file, stations)
        result["rail_geo"] = f"ready ({len(stations):,} STANOX coordinates; {total:,} total mapped)"
    except Exception as exc:
        result["rail_geo"] = f"error: {type(exc).__name__}: {exc}"

    naptan_cache = data_dir / "naptan_rail.json"
    try:
        naptan = ensure_naptan_cache(naptan_cache, force=force)
        result["naptan"] = f"ready ({len(naptan.get('by_tiploc', {})):,} TIPLOC; {len(naptan.get('by_crs', {})):,} CRS coordinates)"
    except Exception as exc:
        naptan = {"by_tiploc": {}, "by_crs": {}}
        result["naptan"] = f"error: {type(exc).__name__}: {exc}"

    try:
        corpus_rows = _json_rows(download_supporting("CORPUS", username, password), ("TIPLOCDATA", "locations", "data"))
        (data_dir / "corpus.json").write_text(json.dumps(corpus_rows, ensure_ascii=False), encoding="utf-8")
        stations = build_stations_from_corpus(corpus_rows, naptan)
        total = merge_station_file(station_file, stations)
        result["corpus"] = f"ready ({len(corpus_rows):,} rows; {len(stations):,} NaPTAN-geocoded; {total:,} total mapped STANOX)"
    except Exception as exc:
        result["corpus"] = f"error: {type(exc).__name__}: {exc}"

    try:
        smart_rows = _json_rows(download_supporting("SMART", username, password), ("BERTHDATA", "SMART", "data", "rows"))
        (data_dir / "smart.json").write_text(json.dumps(smart_rows, ensure_ascii=False), encoding="utf-8")
        result["smart"] = f"ready ({len(smart_rows):,} berth-step rows)"
    except Exception as exc:
        result["smart"] = f"error: {type(exc).__name__}: {exc}"

    return result
