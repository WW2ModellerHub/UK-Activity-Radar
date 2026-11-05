from __future__ import annotations

import json
import math
import os
import sqlite3
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import quote, urlparse, urlunparse

import requests

from .models import SourceStatus
from .bus_timetable import BusTimetableService
from .timeutils import london_tz
from .reference_data import NAPTAN_URL, ensure_naptan_cache
from .train_identity import infer_train_family, is_charter_operator, operator_name, power_type_name

USER_AGENT = "UKActivityRadar/1.4.0 smart-places"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
EA_ROOT = "https://environment.data.gov.uk/flood-monitoring"


def _ea_readings_url(url: str) -> str:
    """Return a canonical EA readings URL for a browser-supplied measure URL."""
    candidate = urlparse(str(url).strip())
    root = urlparse(EA_ROOT)
    if (
        candidate.scheme != root.scheme
        or candidate.hostname != root.hostname
        or candidate.username is not None
        or candidate.password is not None
        or candidate.port not in (None, 443)
        or not candidate.path.startswith(root.path + "/")
    ):
        raise ValueError("Unsupported Environment Agency measure URL")
    clean_path = candidate.path.rstrip("/") + "/readings"
    return urlunparse((root.scheme, root.netloc, clean_path, "", "", ""))


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _text(elem: ET.Element, *names: str) -> str:
    wanted = {n.lower() for n in names}
    for child in elem.iter():
        if _local(child.tag).lower() in wanted and child.text and child.text.strip():
            return child.text.strip()
    return ""


def _lat_lon(elem: ET.Element) -> tuple[float, float] | None:
    try:
        return float(_text(elem, "Latitude")), float(_text(elem, "Longitude"))
    except Exception:
        return None


def _distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2-lat1), math.radians(lon2-lon1)
    a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2*r*math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1-a)))


def _bearing_to(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1,p2=math.radians(lat1),math.radians(lat2)
    dl=math.radians(lon2-lon1)
    y=math.sin(dl)*math.cos(p2)
    x=math.cos(p1)*math.sin(p2)-math.sin(p1)*math.cos(p2)*math.cos(dl)
    return (math.degrees(math.atan2(y,x))+360)%360

def _angle_diff(a: float, b: float) -> float:
    return abs((a-b+180)%360-180)

def _clean_time(value: str | None) -> str | None:
    if not value:
        return None
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    if len(digits) < 4:
        return None
    hh, mm = int(digits[:2]), int(digits[2:4])
    if hh > 23 or mm > 59:
        return None
    return f"{hh:02d}:{mm:02d}"


def _day_runs(days: str | None, dt: datetime) -> bool:
    if not days:
        return True
    s = str(days).strip()
    if len(s) < 7:
        return True
    return s[dt.weekday()] not in {"0", "N", "n", "-"}


class SmartPlacesService:
    """Live contextual places layered on top of the moving/event radar.

    The service deliberately keeps place metadata separate from live Activity objects:
    railway stations, bus stops and ferry terminals are stable places;
    their boards/context are fetched only when clicked.
    """

    def __init__(
        self,
        data_dir: Path,
        schedules,
        state,
        bods_collector_getter=None,
    ):
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.schedules = schedules
        self.state = state
        self._bods_collector_getter = bods_collector_getter or (lambda: None)
        self.bus_timetables = BusTimetableService(self.data_dir, self.state)
        self.stop_db = self.data_dir / "naptan_stops.sqlite"
        self.ferry_cache = self.data_dir / "ferry_terminals.json"
        self.rain_cache = self.data_dir / "ea_rain_stations.json"
        self.rail_cache = self.data_dir / "naptan_rail.json"
        self.station_cache = self.data_dir / "stations.json"
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": USER_AGENT})
        self._sync_thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._ferries: list[dict] = []
        self._rain: list[dict] = []
        self._rail: list[dict] = []
        self._rail_board_cache: dict[tuple[str, int], tuple[float, dict]] = {}
        self._load_small_caches()

    @staticmethod
    def _rail_places_from_rows(rows: Any, *, source: str) -> list[dict]:
        """Build one passenger-station place per CRS code from cached geography."""
        if not isinstance(rows, dict):
            return []
        by_crs: dict[str, dict] = {}
        for row_key, row in rows.items():
            if not isinstance(row, dict):
                continue
            crs = str(row.get("crs") or row_key or "").strip().upper()
            if len(crs) != 3 or not crs.isalnum():
                continue
            try:
                lat, lon = float(row["lat"]), float(row["lon"])
            except (KeyError, TypeError, ValueError):
                continue
            if not (49.0 <= lat <= 61.8 and -9.9 <= lon <= 3.5):
                continue
            name = str(row.get("name") or crs).strip() or crs
            candidate = {
                "id": f"rail-station:{crs}",
                "kind": "place",
                "place_type": "rail_station",
                "name": name,
                "subtitle": crs,
                "lat": lat,
                "lon": lon,
                "crs": crs,
                "tiploc": row.get("tiploc"),
                "source": source,
            }
            existing = by_crs.get(crs)
            # Prefer a descriptive name over a bare code when duplicate STANOX
            # rows refer to the same passenger station.
            if existing is None or (existing["name"] == crs and name != crs):
                by_crs[crs] = candidate
        return list(by_crs.values())

    def _load_cached_rail_places(self) -> list[dict]:
        """Load NaPTAN, falling back to the already-built TRUST station cache.

        ``stations.json`` is produced from public rail geography before the much
        slower NaPTAN download.  Reusing its CRS-tagged rows makes station search
        available immediately and keeps it available if NaPTAN refresh fails.
        """
        try:
            payload = json.loads(self.rail_cache.read_text(encoding="utf-8"))
            places = self._rail_places_from_rows(
                payload.get("by_crs") or {},
                source="DfT NaPTAN rail geography",
            )
            if places:
                return places
        except Exception:
            pass
        try:
            payload = json.loads(self.station_cache.read_text(encoding="utf-8"))
            return self._rail_places_from_rows(
                payload,
                source="Cached public rail geography",
            )
        except Exception:
            return []

    def _load_small_caches(self) -> None:
        try:
            self._ferries = json.loads(self.ferry_cache.read_text(encoding="utf-8"))
        except Exception:
            self._ferries = []
        try:
            self._rain = json.loads(self.rain_cache.read_text(encoding="utf-8"))
        except Exception:
            self._rain = []
        self._rail = self._load_cached_rail_places()

    def start_background_sync(self) -> None:
        if self._sync_thread and self._sync_thread.is_alive():
            return
        self._sync_thread = threading.Thread(target=self._sync_all, name="smart-places-sync", daemon=True)
        self._sync_thread.start()

    def _sync_all(self) -> None:
        self.state.set_source(SourceStatus(name="smart_places", enabled=True, connected=False, mode="sync", message="Building clickable stations / stops / terminals…"))
        notes: list[str] = []
        try:
            rail = ensure_naptan_cache(self.rail_cache)
            refreshed = self._rail_places_from_rows(
                rail.get("by_crs") or {},
                source="DfT NaPTAN rail geography",
            )
            if refreshed:
                self._rail = refreshed
                notes.append(f"{len(self._rail):,} rail stations")
            elif self._rail:
                notes.append(f"{len(self._rail):,} rail stations from cached geography")
            else:
                notes.append("rail stations unavailable: empty NaPTAN geography")
        except Exception as exc:
            if not self._rail:
                self._rail = self._load_cached_rail_places()
            if self._rail:
                notes.append(f"{len(self._rail):,} rail stations from cached geography (NaPTAN refresh {type(exc).__name__})")
            else:
                notes.append(f"rail stations unavailable: {type(exc).__name__}")
        try:
            if not self.stop_db.exists() or time.time() - self.stop_db.stat().st_mtime > 30*86400:
                count = self._sync_bus_stops()
                notes.append(f"{count:,} NaPTAN bus stops")
            else:
                with sqlite3.connect(self.stop_db) as con:
                    count = int(con.execute("select count(*) from stops").fetchone()[0])
                notes.append(f"{count:,} NaPTAN bus stops cached")
        except Exception as exc:
            notes.append(f"bus stops unavailable: {type(exc).__name__}")
        try:
            self._sync_ferries_if_needed()
            notes.append(f"{len(self._ferries):,} ferry terminals")
        except Exception as exc:
            notes.append(f"ferries unavailable: {type(exc).__name__}")
        self.state.set_source(SourceStatus(name="smart_places", enabled=True, connected=True, mode="ready", message=" • ".join(notes), last_message_at=datetime.now(timezone.utc)))

    def _sync_bus_stops(self) -> int:
        with tempfile.TemporaryDirectory(prefix="ukradar-naptan-stops-") as td:
            xml_path = Path(td) / "naptan.xml"
            with self._session.get(NAPTAN_URL, stream=True, timeout=(15, 240)) as r:
                r.raise_for_status()
                with xml_path.open("wb") as f:
                    for chunk in r.iter_content(1024*1024):
                        if chunk:
                            f.write(chunk)
            tmp_db = self.stop_db.with_suffix(".building.sqlite")
            tmp_db.unlink(missing_ok=True)
            con = sqlite3.connect(tmp_db)
            con.executescript("""
                PRAGMA journal_mode=OFF;
                PRAGMA synchronous=OFF;
                CREATE TABLE stops(
                    atco_code TEXT PRIMARY KEY,
                    naptan_code TEXT,
                    name TEXT,
                    indicator TEXT,
                    stop_type TEXT,
                    lat REAL,
                    lon REAL
                );
                CREATE INDEX idx_stops_lat_lon ON stops(lat,lon);
                CREATE INDEX idx_stops_naptan ON stops(naptan_code);
            """)
            rows = []
            count = 0
            for _, elem in ET.iterparse(xml_path, events=("end",)):
                if _local(elem.tag) != "StopPoint":
                    continue
                point = _lat_lon(elem)
                atco = _text(elem, "AtcoCode")
                if point and atco:
                    stop_type = _text(elem, "StopType")
                    # Keep bus/coach stops. Rail/metro entries are represented separately.
                    if not stop_type or stop_type.upper().startswith(("B", "C")):
                        name = _text(elem, "CommonName", "StopPointName") or atco
                        rows.append((atco, _text(elem, "NaptanCode"), name, _text(elem, "Indicator"), stop_type, point[0], point[1]))
                        count += 1
                        if len(rows) >= 5000:
                            con.executemany("INSERT OR REPLACE INTO stops VALUES(?,?,?,?,?,?,?)", rows)
                            rows.clear()
                elem.clear()
            if rows:
                con.executemany("INSERT OR REPLACE INTO stops VALUES(?,?,?,?,?,?,?)", rows)
            con.commit(); con.close()
            os.replace(tmp_db, self.stop_db)
            return count

    def _sync_ferries_if_needed(self) -> None:
        if self.ferry_cache.exists() and time.time()-self.ferry_cache.stat().st_mtime < 7*86400 and self._ferries:
            return
        query = '[out:json][timeout:35];(node["amenity"="ferry_terminal"](49,-9.5,61.5,3);way["amenity"="ferry_terminal"](49,-9.5,61.5,3);relation["amenity"="ferry_terminal"](49,-9.5,61.5,3););out center tags;'
        r = self._session.post(OVERPASS_URL, data={"data": query}, timeout=(15, 70)); r.raise_for_status()
        out = []
        for row in r.json().get("elements", []):
            tags = row.get("tags") or {}
            lat = row.get("lat") or (row.get("center") or {}).get("lat")
            lon = row.get("lon") or (row.get("center") or {}).get("lon")
            if lat is None or lon is None:
                continue
            name = tags.get("name") or tags.get("name:en") or "Ferry terminal"
            out.append({
                "id": f"ferry:{row.get('type')}:{row.get('id')}", "kind":"place", "place_type":"ferry_terminal",
                "name": name, "lat": float(lat), "lon": float(lon), "operator": tags.get("operator"),
                "website": tags.get("website") or tags.get("contact:website"), "source":"OpenStreetMap / OpenSeaMap context"
            })
        self._ferries = out
        self.ferry_cache.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")

    def _sync_rain_if_needed(self) -> None:
        if self.rain_cache.exists() and time.time()-self.rain_cache.stat().st_mtime < 2*86400 and self._rain:
            return
        r = self._session.get(f"{EA_ROOT}/id/stations", params={"parameter":"rainfall","_view":"full","_limit":10000}, timeout=(10, 45)); r.raise_for_status()
        out = []
        for s in r.json().get("items", []):
            lat, lon = s.get("lat"), s.get("long")
            if lat is None or lon is None:
                continue
            ref = str(s.get("stationReference") or s.get("notation") or "").strip()
            if not ref:
                continue
            out.append({"id":f"rain:{ref}","kind":"place","place_type":"rain_gauge","name":s.get("label") or ref,"lat":float(lat),"lon":float(lon),"station_ref":ref,"river":s.get("riverName"),"town":s.get("town"),"source":"Environment Agency rainfall"})
        self._rain = out
        self.rain_cache.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")

    def rail_places(self, bbox: tuple[float,float,float,float], limit: int=800) -> list[dict]:
        west,south,east,north=bbox
        rows_out: list[dict] = []
        if self.schedules.db_path.exists():
            try:
                with sqlite3.connect(self.schedules.db_path) as con:
                    con.row_factory=sqlite3.Row
                    rows=con.execute("""SELECT tiploc,crs,description,lat,lon FROM tiplocs
                      WHERE crs IS NOT NULL AND crs<>'' AND lat IS NOT NULL AND lon IS NOT NULL
                        AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?
                      GROUP BY crs ORDER BY description LIMIT ?""",(south,north,west,east,limit)).fetchall()
                rows_out=[{"id":f"rail-station:{r['crs']}","kind":"place","place_type":"rail_station","name":r['description'] or r['crs'],"subtitle":r['crs'],"lat":r['lat'],"lon":r['lon'],"crs":r['crs'],"tiploc":r['tiploc'],"source":"Network Rail SCHEDULE + rail geography"} for r in rows]
            except sqlite3.Error:
                rows_out=[]
        # Crucial fallback: station markers and live traini.ac boards must work
        # before the huge Network Rail SCHEDULE index has finished building.
        if not rows_out:
            rows_out=[p for p in self._rail if west <= float(p.get("lon",999)) <= east and south <= float(p.get("lat",999)) <= north]
        return rows_out[:limit]


    def nearest_rail_station(self, lat: float, lon: float, *, tomtom_api_key: str="", tomtom_base_url: str="https://api.tomtom.com") -> dict | None:
        """Resolve the nearest passenger rail station around a map point.

        Prefer local Network Rail/NaPTAN geography. On a brand-new install where
        those caches are still building, a configured TomTom Search key can find
        a nearby station POI; its name is resolved only against the local Network
        Rail schedule index so commercial deployments never call a third-party
        personal-use rail API.
        """
        lat=float(lat); lon=float(lon)
        # A roughly 80 km local window is enough for a useful "nearest station"
        # action while avoiding a whole-country scan of the schedule database.
        pad_lat=0.75; pad_lon=1.2
        local=self.rail_places((lon-pad_lon,lat-pad_lat,lon+pad_lon,lat+pad_lat),1200)
        if local:
            local.sort(key=lambda p:_distance_km(lat,lon,float(p['lat']),float(p['lon'])))
            row=dict(local[0]); row['distance_km']=round(_distance_km(lat,lon,float(row['lat']),float(row['lon'])),1)
            return row

        key=str(tomtom_api_key or '').strip()
        if not key:
            return None
        base=(tomtom_base_url or 'https://api.tomtom.com').rstrip('/')
        try:
            r=self._session.get(
                f"{base}/search/2/search/{quote('train station',safe='')}.json",
                params={"key":key,"countrySet":"GB","limit":8,"idxSet":"POI","lat":lat,"lon":lon,"radius":80000,"language":"en-GB"},
                timeout=(5,10),
            ); r.raise_for_status()
            candidates=r.json().get('results') or []
        except Exception:
            return None
        for item in candidates:
            pos=item.get('position') or {}; poi=item.get('poi') or {}
            name=str(poi.get('name') or (item.get('address') or {}).get('freeformAddress') or '').strip()
            if not name:
                continue
            # TomTom supplies the POI candidate, but the passenger CRS and
            # canonical station coordinates must come from our licensed local
            # Network Rail data.  Do not return a generic POI that cannot open a
            # real board.
            matches = self.search_places(name, limit=8)
            rail_matches = [x for x in matches if x.get("place_type") == "rail_station" and x.get("crs")]
            if not rail_matches:
                continue
            try:
                plat=float(pos.get('lat')); plon=float(pos.get('lon'))
                resolved=min(
                    rail_matches,
                    key=lambda x: _distance_km(plat, plon, float(x['lat']), float(x['lon'])),
                )
                crs=str(resolved.get('crs') or '').strip().upper()
                rlat=float(resolved['lat']); rlon=float(resolved['lon'])
            except (KeyError, TypeError, ValueError):
                continue
            row={
                "id":f"rail-station:{crs}","kind":"place","place_type":"rail_station",
                "name":str(resolved.get('name') or name).strip(),"subtitle":crs,
                "lat":rlat,"lon":rlon,"crs":crs,
                "source":"Network Rail SCHEDULE (TomTom-assisted search)",
                "distance_km":round(_distance_km(lat,lon,rlat,rlon),1),
            }
            with self._lock:
                self._rail=[p for p in self._rail if str(p.get('crs') or '').upper()!=crs]
                self._rail.append(dict(row))
            return row
        return None

    def bus_places(self, bbox: tuple[float,float,float,float], limit: int=1000) -> list[dict]:
        if not self.stop_db.exists():
            return []
        west,south,east,north=bbox
        try:
            with sqlite3.connect(self.stop_db) as con:
                con.row_factory=sqlite3.Row
                rows=con.execute("SELECT * FROM stops WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ? LIMIT ?",(south,north,west,east,limit)).fetchall()
            return [{"id":f"bus-stop:{r['atco_code']}","kind":"place","place_type":"bus_stop","name":r['name'],"subtitle":r['indicator'] or r['naptan_code'] or r['atco_code'],"lat":r['lat'],"lon":r['lon'],"atco_code":r['atco_code'],"naptan_code":r['naptan_code'],"source":"DfT NaPTAN"} for r in rows]
        except sqlite3.Error:
            return []

    def nearest_bus_stop(self, lat: float, lon: float) -> dict | None:
        """Return the nearest cached NaPTAN bus stop to a UK map point."""
        if not self.stop_db.exists():
            return None
        lat=float(lat); lon=float(lon)
        # Expand from a genuinely local search to a rural fallback. The final
        # choice is always based on distance, not database row order.
        for pad in (0.03, 0.08, 0.2, 0.5):
            rows=self.bus_places((lon-pad,lat-pad,lon+pad,lat+pad),3000)
            if not rows:
                continue
            row=min(rows,key=lambda p:_distance_km(lat,lon,float(p['lat']),float(p['lon'])))
            result=dict(row)
            result['distance_km']=round(_distance_km(lat,lon,float(row['lat']),float(row['lon'])),2)
            return result
        return None

    def list_places(self, bbox: tuple[float,float,float,float], zoom: float, mode: str="all") -> list[dict]:
        west,south,east,north=bbox
        def inside(p): return west <= float(p.get("lon",999)) <= east and south <= float(p.get("lat",999)) <= north
        out=[]
        if mode in {"all","rail"} and zoom >= 8:
            out.extend(self.rail_places(bbox, 900 if zoom>=10 else 350))
        if mode in {"all","bus"} and zoom >= 13:
            out.extend(self.bus_places(bbox, 1200))
        if mode in {"all","ferry"} and zoom >= 7:
            out.extend(p for p in self._ferries if inside(p))
        if mode == "gauges" and zoom >= 10:
            out.extend(p for p in self._rain if inside(p))
        return out[:2500]


    def search_places(self, query: str, limit: int = 10) -> list[dict]:
        q=str(query or '').strip()
        if len(q)<2: return []
        out=[]
        if self.schedules.db_path.exists():
            try:
                like=f"%{q.upper()}%"
                with sqlite3.connect(self.schedules.db_path) as con:
                    con.row_factory=sqlite3.Row
                    rows=con.execute("""SELECT tiploc,crs,description,lat,lon FROM tiplocs
                      WHERE lat IS NOT NULL AND lon IS NOT NULL AND crs IS NOT NULL AND crs<>''
                        AND (UPPER(crs)=? OR UPPER(description) LIKE ? OR UPPER(tiploc) LIKE ?)
                      GROUP BY crs ORDER BY CASE WHEN UPPER(crs)=? THEN 0 ELSE 1 END, description LIMIT ?""",
                      (q.upper(),like,like,q.upper(),limit)).fetchall()
                out.extend({"kind":"place","id":f"rail-station:{r['crs']}","place_type":"rail_station","type":"place","name":r['description'] or r['crs'],"subtitle":f"Rail station • {r['crs']}","lat":r['lat'],"lon":r['lon'],"crs":r['crs'],"tiploc":r['tiploc'],"source":"Network Rail SCHEDULE"} for r in rows)
            except sqlite3.Error: pass
        if len(out)<limit:
            needle=q.casefold()
            for p in self._rail:
                if needle in str(p.get('name') or '').casefold() or needle == str(p.get('crs') or '').casefold():
                    row=dict(p); row['type']='place'; row['subtitle']=f"Rail station • {row.get('crs') or ''}"; out.append(row)
                    if len(out)>=limit: break
        if len(out)<limit and self.stop_db.exists() and len(q)>=3:
            try:
                with sqlite3.connect(self.stop_db) as con:
                    con.row_factory=sqlite3.Row
                    rows=con.execute("SELECT * FROM stops WHERE name LIKE ? OR naptan_code LIKE ? OR atco_code LIKE ? LIMIT ?",(f'%{q}%',f'%{q}%',f'%{q}%',max(0,limit-len(out)))).fetchall()
                out.extend({"kind":"place","id":f"bus-stop:{r['atco_code']}","place_type":"bus_stop","type":"place","name":r['name'],"subtitle":f"Bus stop • {r['indicator'] or r['naptan_code'] or r['atco_code']}","lat":r['lat'],"lon":r['lon'],"source":"DfT NaPTAN"} for r in rows)
            except sqlite3.Error: pass
        if len(out)<limit:
            needle=q.casefold()
            for p in [*self._ferries,*self._rain]:
                if needle in str(p.get('name') or '').casefold():
                    row=dict(p); row['type']='place'; row['subtitle']=row.get('subtitle') or row.get('place_type','').replace('_',' '); out.append(row)
                    if len(out)>=limit: break
        return out[:limit]

    def get_place(self, place_id: str) -> dict | None:
        if place_id.startswith("rail-station:"):
            crs=place_id.split(":",1)[1].upper()
            direct=next((p for p in self._rail if str(p.get("crs") or "").upper()==crs),None)
            if direct: return direct
            rows=self.rail_places((-9.9,49,3.5,61.8),5000)
            return next((p for p in rows if str(p.get("crs") or "").upper()==crs),None)
        if place_id.startswith("bus-stop:") and self.stop_db.exists():
            atco=place_id.split(":",1)[1]
            with sqlite3.connect(self.stop_db) as con:
                con.row_factory=sqlite3.Row; r=con.execute("SELECT * FROM stops WHERE atco_code=?",(atco,)).fetchone()
            if r:
                return {"id":place_id,"kind":"place","place_type":"bus_stop","name":r['name'],"subtitle":r['indicator'] or r['naptan_code'] or atco,"lat":r['lat'],"lon":r['lon'],"atco_code":atco,"naptan_code":r['naptan_code'],"source":"DfT NaPTAN"}
        if place_id.startswith("ferry:"):
            return next((p for p in self._ferries if p.get("id")==place_id),None)
        if place_id.startswith("rain:"):
            return next((p for p in self._rain if p.get("id")==place_id),None)
        return None

    def _scheduled_station_calls(self, crs: str, limit: int=12) -> dict:
        now=datetime.now(london_tz())
        today=now.date().isoformat()
        now_min=now.hour*60+now.minute
        with sqlite3.connect(self.schedules.db_path) as con:
            con.row_factory=sqlite3.Row
            tiplocs=[r[0] for r in con.execute("SELECT tiploc FROM tiplocs WHERE crs=?",(crs,)).fetchall()]
            if not tiplocs:
                return {"departures":[],"arrivals":[]}
            rows=[]
            indexed=self.schedules.call_index_ready()
            if indexed:
                placeholders=','.join('?' for _ in tiplocs[:6])
                rows=con.execute(f"""SELECT s.train_uid,s.start_date,s.end_date,s.days_runs,s.stp_indicator,s.atoc_code,s.signalling_id,s.origin_name,s.destination_name,
                           c.tiploc AS call_tiploc,c.scheduled_arrival,c.scheduled_departure,c.public_arrival,c.public_departure,c.platform
                    FROM schedule_calls c JOIN schedules s ON s.id=c.schedule_id
                    WHERE c.tiploc IN ({placeholders}) AND s.start_date<=? AND s.end_date>=? LIMIT 5000""",(*tiplocs[:6],today,today)).fetchall()
            else:
                for tip in tiplocs[:6]:
                    rows.extend(con.execute("""SELECT train_uid,start_date,end_date,days_runs,stp_indicator,atoc_code,signalling_id,origin_name,destination_name,locations_json
                        FROM schedules WHERE start_date<=? AND end_date>=? AND locations_json LIKE ? LIMIT 2200""",(today,today,f'%"tiploc_code":"{tip}"%')).fetchall())
        dep=[]; arr=[]; seen=set()
        for row in rows:
            if not _day_runs(row['days_runs'],now):
                continue
            if indexed:
                locs=[{
                    'tiploc_code':row['call_tiploc'],'scheduled_arrival':row['scheduled_arrival'],
                    'scheduled_departure':row['scheduled_departure'],'public_arrival':row['public_arrival'],
                    'public_departure':row['public_departure'],'platform':row['platform'],
                }]
            else:
                try: locs=json.loads(row['locations_json'] or '[]')
                except Exception: continue
            for loc in locs:
                if str(loc.get('tiploc_code') or '').upper() not in tiplocs:
                    continue
                at=_clean_time(loc.get('public_arrival') or loc.get('scheduled_arrival'))
                dt=_clean_time(loc.get('public_departure') or loc.get('scheduled_departure'))
                common={"headcode":row['signalling_id'],"train_uid":row['train_uid'],"operator":row['atoc_code'],"origin":row['origin_name'],"destination":row['destination_name'],"platform":loc.get('platform')}
                for bucket,clock,kind in ((arr,at,'arrival'),(dep,dt,'departure')):
                    if not clock: continue
                    hh,mm=map(int,clock.split(':')); scheduled_abs=hh*60+mm
                    delta=scheduled_abs-now_min
                    if delta < -10: delta += 1440
                    if delta < -10 or delta > 360: continue
                    key=(row['train_uid'],kind,clock)
                    if key in seen: continue
                    seen.add(key)
                    item={**common,"scheduled":clock,"expected":clock,"minutes":delta,"status":"Scheduled","_scheduled_abs":scheduled_abs}
                    bucket.append(item)
        dep.sort(key=lambda x:x['minutes']); arr.sort(key=lambda x:x['minutes'])
        dep=dep[:limit]; arr=arr[:limit]
        wanted_uids={str(item.get('train_uid') or '').strip() for item in [*dep,*arr]}
        if hasattr(self.state,'trains_by_uids'):
            live_by_uid=self.state.trains_by_uids(wanted_uids)
        else:
            live_by_uid={}
            for uid in wanted_uids:
                fallback_id=self._match_live_train(None,uid)
                if fallback_id and hasattr(self.state,'get'):
                    live_by_uid[uid]=self.state.get(fallback_id)
        for item in [*dep,*arr]:
            live=live_by_uid.get(str(item.get('train_uid') or '').strip())
            if live is not None:
                item['live_train_id']=live.id
                delay=getattr(live,'delay_minutes',None)
                live_status=str(getattr(live,'status',None) or '').strip()
                if delay is not None:
                    expected_abs=(item['_scheduled_abs']+int(delay))%1440
                    item['expected']=f"{expected_abs//60:02d}:{expected_abs%60:02d}"
                    item['delay_minutes']=int(delay)
                    item['status']='On time' if int(delay)==0 else f"{int(delay):+d} min"
                elif live_status:
                    item['status']=live_status
            item.pop('_scheduled_abs',None)
        return {"departures":dep,"arrivals":arr}

    def rail_board(self, crs: str, limit: int=10) -> dict:
        crs=str(crs or '').strip().upper()
        cache_key=(crs,int(limit))
        with self._lock:
            cached=self._rail_board_cache.get(cache_key)
            if cached and time.monotonic()-cached[0] < 15:
                return json.loads(json.dumps(cached[1]))
        local_ready=self.schedules.ready()
        if self.schedules.ready():
            local=self._scheduled_station_calls(crs,limit)
            payload={"mode":"network_rail","source":"Network Rail timetable and live movements","departures":local.get('departures',[]),"arrivals":local.get('arrivals',[]),"recent":[],"note":"Published calling times are combined with live delay and status only when the exact service can be matched."}
            payload['next_live_id']=next((
                x.get('live_train_id')
                for x in [*payload['departures'], *payload['arrivals']]
                if x.get('live_train_id')
            ),None)
            # A small VSTP-only cache is technically "ready" but does not hold
            # the national base timetable.  An empty local board must therefore
            # fall through to the public live board instead of showing a broken
            # blank departures/arrivals panel indefinitely.
            if payload['departures'] or payload['arrivals']:
                with self._lock:
                    self._rail_board_cache[cache_key]=(time.monotonic(),payload)
                return payload

        fallback_reason=(
            "The local Network Rail index has no calls for this station yet."
            if local_ready else
            "The full Network Rail SCHEDULE index is still syncing."
        )
        payload = {
            "mode":"schedule_sync", "source":"Published railway timetable",
            "departures":[], "arrivals":[], "recent":[], "recent_arrivals":[],
            "next_live_id":None,
            "note":fallback_reason + " Refresh shortly; no personal-use or scraped fallback is used on the commercial site.",
        }
        with self._lock:
            self._rail_board_cache[cache_key]=(time.monotonic(),payload)
        return payload

    def train_vehicle_detail(self, vehicle) -> dict:
        """Return commercial-safe rail context from the authoritative live item."""
        exact_id = str(getattr(vehicle, 'id', '') or '').removeprefix('nr:').strip()
        headcode = str(getattr(vehicle, 'headcode', '') or '').strip()
        run = {
            "train_id": exact_id or None,
            "headcode": headcode or None,
            "train_uid": getattr(vehicle, 'train_uid', None),
            "operator": getattr(vehicle, 'operator', None),
            "origin": getattr(vehicle, 'origin', None),
            "destination": getattr(vehicle, 'destination', None),
            "next_location": getattr(vehicle, 'next_location', None),
            "status": getattr(vehicle, 'status', None),
            "delay_minutes": getattr(vehicle, 'delay_minutes', None),
            "position_label": getattr(vehicle, 'position_label', None),
            "service_label": (
                "Railtour / charter special"
                if getattr(vehicle, 'cool_subtype', None) == 'railtour-special'
                else "Special passenger service"
                if headcode.upper().startswith('1Z')
                else "Freight working"
                if getattr(vehicle, 'cool_subtype', None) == 'freight'
                else "Special railway movement"
                if getattr(vehicle, 'cool_candidate', False)
                else None
            ),
        }
        uid = str(getattr(vehicle, 'train_uid', '') or '').strip()
        schedule_verified = False
        if uid and self.schedules.db_path.exists():
            try:
                now = datetime.now(london_tz())
                today = now.date().isoformat()
                with sqlite3.connect(self.schedules.db_path) as con:
                    con.row_factory = sqlite3.Row
                    rows = con.execute(
                        """SELECT * FROM schedules
                           WHERE train_uid=?
                             AND (start_date IS NULL OR start_date='' OR start_date<=?)
                             AND (end_date IS NULL OR end_date='' OR end_date>=?)
                             AND COALESCE(stp_indicator,'')<>'C'
                           ORDER BY CASE source WHEN 'VSTP' THEN 0 ELSE 1 END, id DESC
                           LIMIT 16""",
                        (uid, today, today),
                    ).fetchall()
                    row = next((candidate for candidate in rows if _day_runs(candidate['days_runs'], now)), None)
                    if row:
                        schedule_verified = True
                        locations = json.loads(row['locations_json'] or '[]')
                        codes = sorted({str(item.get('tiploc_code') or '').strip() for item in locations if isinstance(item, dict) and item.get('tiploc_code')})
                        names = {}
                        if codes:
                            placeholders = ','.join('?' for _ in codes)
                            names = {r['tiploc']: (r['description'] or r['tiploc']) for r in con.execute(f"SELECT tiploc,description FROM tiplocs WHERE tiploc IN ({placeholders})", codes)}
                        calls = []
                        for item in locations[:40]:
                            if not isinstance(item, dict):
                                continue
                            code = str(item.get('tiploc_code') or '').strip()
                            clock = _clean_time(item.get('public_departure') or item.get('public_arrival') or item.get('scheduled_departure') or item.get('scheduled_arrival'))
                            if not code:
                                continue
                            calls.append({
                                "name": names.get(code, code),
                                "scheduled": clock,
                                "event": "Calls" if item.get('scheduled_arrival') and item.get('scheduled_departure') else "Passes",
                            })
                        run.update({
                            "origin": row['origin_name'] or run['origin'],
                            "destination": row['destination_name'] or run['destination'],
                            "operator_code": row['atoc_code'],
                            "operator": operator_name(row['atoc_code'], run['operator']),
                            "service_code": row['service_code'],
                            "train_category_code": row['train_category'],
                            "power_type_code": row['power_type'],
                            "power_type": power_type_name(row['power_type']),
                            "timing_load": row['timing_load'],
                            "booked_speed_mph": row['booked_speed'],
                            "operating_characteristics": row['operating_characteristics'],
                            "schedule_source": row['source'],
                            "calling_points": calls,
                        })
                        if headcode.upper().startswith('1Z'):
                            run["service_label"] = (
                                "Railtour / charter special" if is_charter_operator(row['atoc_code'])
                                else "Special passenger service"
                            )
                        likely = infer_train_family(
                            operator_code=row['atoc_code'], power_type=row['power_type'],
                            origin=run['origin'], destination=run['destination'],
                        )
                        if likely:
                            run["likely_train"] = likely
            except (sqlite3.Error, ValueError, TypeError, json.JSONDecodeError):
                pass
        return {
            "available": True, "mode": "network_rail", "source": "Live railway and timetable data",
            "runs": [run],
            "note": (
                "The route and calling points come from today's published timetable. The marker remains at the last confirmed railway report."
                if schedule_verified else
                "No timetable identity could be verified for this live signalling report, so no route has been attached. The marker remains at the last confirmed railway report."
            ),
        }

    def scheduled_train_detail(self, train_uid: str, headcode: str | None = None) -> dict:
        """Return the published route for a board service with no live marker."""
        uid = str(train_uid or '').strip()
        code = str(headcode or '').strip()
        if not uid:
            return {
                "available": False,
                "mode": "published_schedule",
                "source": "Published railway timetable",
                "runs": [],
                "note": "No published train identifier was supplied for this service.",
            }
        vehicle = SimpleNamespace(
            id=f"schedule:{uid}",
            type="train",
            name=code or uid,
            headcode=code or None,
            train_uid=uid,
            operator=None,
            origin=None,
            destination=None,
            next_location=None,
            status="Scheduled",
            delay_minutes=None,
            position_label="Published Network Rail schedule",
            cool_subtype=None,
            cool_candidate=False,
        )
        detail = self.train_vehicle_detail(vehicle)
        detail.update({
            "mode": "published_schedule",
            "source": "Published railway timetable",
            "live_marker": False,
            "note": "Published route only. A matching live position is not available right now.",
        })
        return detail

    def _match_live_train(self, headcode: str | None, train_uid: str | None=None, train_id: str | None=None) -> str | None:
        exact=str(train_id or '').strip()
        if exact and hasattr(self.state,'get') and self.state.get(f'nr:{exact}'):
            return f'nr:{exact}'
        uid=str(train_uid or '').strip()
        if uid:
            for item in self.state.all(include_buses=False,include_vessels=False):
                if getattr(item,'type',None)=='train' and str(getattr(item,'train_uid',None) or '').strip()==uid:
                    return item.id
            return None
        live=self.state.find_train_by_headcode(headcode or '') if hasattr(self.state,'find_train_by_headcode') else None
        return live.id if live else None

    def locate_live_train(self, headcode: str | None, train_uid: str | None=None) -> object | None:
        """Resolve a board service against the current national TRUST state."""
        live_id=self._match_live_train(headcode,train_uid)
        return self.state.get(live_id) if live_id and hasattr(self.state,'get') else None

    def _match_live_bus(self, vehicle_ref: str | None) -> str | None:
        ref=str(vehicle_ref or '').strip()
        if not ref:
            return None
        # GTFS-RT ids are normally bus:{vehicle_ref}, while SIRI-VM ids include
        # the operator (bus:{operator}:{vehicle_ref}). Match the canonical
        # Vehicle.vehicle_ref so both feed shapes focus the correct live bus.
        direct=self.state.get(f"bus:{ref}") if hasattr(self.state,'get') else None
        if direct is not None:
            return direct.id
        for item in self.state.all(include_buses=True,include_vessels=False):
            if item.type=='bus' and str(item.vehicle_ref or '').strip()==ref:
                return item.id
        return None

    def _match_live_bus_trip(self, trip_id: str | None) -> str | None:
        trip=str(trip_id or '').strip()
        if not trip: return None
        for item in self.state.all(include_buses=True,include_vessels=False):
            if getattr(item,'type',None)=='bus' and str(getattr(item,'journey_ref',None) or '').strip()==trip:
                return item.id
        return None

    def bus_board(self, place: dict, limit: int=10) -> dict:
        collector=self._bods_collector_getter(); predictions=[]
        stop_refs=[str(x).strip() for x in (place.get('atco_code'),place.get('naptan_code')) if x]
        if collector is not None and hasattr(collector,'predictions_for_stop'):
            for ref in stop_refs:
                predictions=collector.predictions_for_stop(ref,limit=limit)
                if predictions: break
        for row in predictions:
            live_id=self._match_live_bus(row.get('vehicle_ref'))
            if live_id: row['live_vehicle_id']=live_id
        if predictions:
            return {"mode":"live_predictions","source":"DfT BODS SIRI-VM","rows":predictions,"next_live_id":next((x.get('live_vehicle_id') for x in predictions if x.get('live_vehicle_id')),None),"note":"Stop-specific live predictions from the operator's BODS feed."}

        stop_keys={x.casefold() for x in stop_refs}; targeted=[]
        if stop_keys:
            has_coords=place.get('lat') is not None and place.get('lon') is not None
            plat=float(place['lat']) if has_coords else None; plon=float(place['lon']) if has_coords else None
            for v in self.state.all(include_buses=True,include_vessels=False):
                if getattr(v,'type',None)!='bus' or str(getattr(v,'next_location',None) or '').strip().casefold() not in stop_keys: continue
                km=_distance_km(plat,plon,v.lat,v.lon) if has_coords else None
                targeted.append({"route":v.route,"destination":v.destination,"operator":v.operator,"vehicle_ref":v.vehicle_ref,"live_vehicle_id":v.id,"distance_km":round(km,1) if km is not None else None,"bearing":v.bearing,"speed":v.speed,"speed_unit":v.speed_unit,"status":"Live • reporting this as current/next stop"})
            targeted.sort(key=lambda x:x['distance_km'] if x['distance_km'] is not None else 99999)

        timetable=None
        if place.get('lat') is not None and place.get('lon') is not None:
            timetable=self.bus_timetables.board_for_stop(place,limit)
            if timetable.get('mode')=='published_timetable':
                rows=timetable.get('rows') or []
                for row in rows:
                    live_id=self._match_live_bus_trip(row.get('trip_id'))
                    if live_id:
                        row['live_vehicle_id']=live_id
                        row['status']='Live • ' + str(row.get('status') or 'scheduled')
                next_live=next((x.get('live_vehicle_id') for x in rows if x.get('live_vehicle_id')),None)
                if not next_live and targeted: next_live=targeted[0].get('live_vehicle_id')
                return {**timetable,"source":"DfT BODS GTFS timetable + live BODS","rows":rows,"targeted_live":targeted[:3],"next_live_id":next_live,"note":"Published stop times come from the official BODS regional GTFS timetable. Live GTFS-RT buses are matched by trip ID; buses explicitly reporting this stop are shown separately."}
            if timetable.get('mode')=='timetable_sync':
                if targeted:
                    return {"mode":"live_next_stop","source":"DfT BODS live GPS","rows":targeted[:limit],"next_live_id":targeted[0]['live_vehicle_id'],"retry_seconds":timetable.get('retry_seconds',5),"note":f"{timetable.get('note','Published timetable is preparing')} Live buses explicitly targeting this stop are shown meanwhile."}
                return timetable

        if targeted:
            rows=targeted[:limit]
            return {"mode":"live_next_stop","source":"DfT BODS live GPS","rows":rows,"next_live_id":rows[0]['live_vehicle_id'],"note":"These live buses explicitly report this as their current/next stop. No ETA is invented."}

        note="Nearby buses are deliberately not shown as if they were due. No stop-specific live prediction was published and no live vehicle currently reports this as its next stop."
        if timetable and timetable.get('note'): note += " " + str(timetable['note'])
        return {"mode":"no_due_predictions","source":"DfT BODS","rows":[],"note":note}

    def bus_vehicle_journey(self, vehicle, limit: int = 12) -> dict:
        """Combine one live bus with its official trip and exact stop prediction."""
        payload = self.bus_timetables.journey_for_vehicle(vehicle, limit=limit)
        collector = self._bods_collector_getter()
        stop_ref = str(getattr(vehicle, 'next_location', None) or '').strip()
        if collector is None or not stop_ref or not hasattr(collector, 'predictions_for_stop'):
            return payload
        predictions = collector.predictions_for_stop(stop_ref, limit=25)
        vehicle_ref = str(getattr(vehicle, 'vehicle_ref', None) or '').strip()
        journey_ref = str(getattr(vehicle, 'journey_ref', None) or '').strip()
        exact = next((row for row in predictions if (
            (vehicle_ref and str(row.get('vehicle_ref') or '').strip() == vehicle_ref)
            or (journey_ref and str(row.get('journey_ref') or '').strip() == journey_ref)
        )), None)
        if exact:
            payload = dict(payload)
            payload['live_prediction'] = dict(exact)
            payload['note'] = (
                "The next-stop time is an operator-published live BODS prediction. "
                "Later stops remain published timetable times."
            )
        return payload

    def ferry_board(self, place: dict, limit: int=12) -> dict:
        rows=[]
        plat,plon=float(place['lat']),float(place['lon'])
        for v in self.state.all(include_vessels=True,vessel_limit=None):
            if v.type!='vessel': continue
            km=_distance_km(plat,plon,v.lat,v.lon)
            # A vessel tens of kilometres away is not useful terminal context.
            # Keep a modest approach radius and an intentionally tight harbour
            # radius for vessels that are not demonstrably heading here.
            if km>20: continue
            speed=float(v.speed or 0)
            course=float(v.bearing) if v.bearing is not None else None
            target=_bearing_to(v.lat,v.lon,plat,plon)
            diff=_angle_diff(course,target) if course is not None else None
            inbound=bool(speed>=1.5 and diff is not None and diff<=35)
            if not inbound and km>5: continue
            eta_minutes=round((km/(speed*1.852))*60) if inbound and speed>0 else None
            rows.append({"name":v.name,"distance_km":round(km,1),"speed":v.speed,"speed_unit":v.speed_unit,"bearing":v.bearing,"vehicle_ref":v.vehicle_ref,"live_vehicle_id":v.id,"inbound":inbound,"eta_minutes":eta_minutes,"heading_error":round(diff) if diff is not None else None})
        rows.sort(key=lambda x:(0 if x['inbound'] else 1, x['eta_minutes'] if x['eta_minutes'] is not None else 99999, x['distance_km']))
        inbound=[x for x in rows if x['inbound']]
        shown=(inbound or rows)[:limit]
        mode="inbound_ais" if inbound else ("live_harbour_activity" if shown else "no_nearby_ais")
        note=(
            "AIS suggests these vessels are approaching within 20 km, based on course and speed. This is movement context, not a confirmed ferry timetable."
            if inbound else
            "Only AIS vessels within 5 km of the terminal are shown. This is harbour context, not a confirmed arrival or sailing."
        )
        return {"mode":mode,"source":"AISStream","rows":shown,"next_live_id":shown[0].get('live_vehicle_id') if shown else None,"website":place.get('website'),"operator":place.get('operator'),"note":note}

    def rain_board(self, place: dict) -> dict:
        ref=place.get('station_ref')
        if not ref: return {"rows":[]}
        r=self._session.get(f"{EA_ROOT}/id/stations/{quote(str(ref),safe='')}/readings",params={"latest":"","_view":"full"},timeout=(5,15)); r.raise_for_status()
        rows=[]
        for item in r.json().get('items',[]):
            measure=item.get('measure') or {}
            rows.append({"value":item.get('value'),"dateTime":item.get('dateTime'),"parameter":measure.get('parameterName') or measure.get('parameter'),"qualifier":measure.get('qualifier'),"unit":measure.get('unitName')})
        return {"mode":"live_readings","source":"Environment Agency","rows":rows}

    def environment_measure(self, url: str) -> dict:
        readings_url = _ea_readings_url(url)
        r=self._session.get(readings_url,params={"_sorted":"","_limit":8},timeout=(5,15)); r.raise_for_status()
        items=r.json().get('items',[]) or []
        vals=[x for x in items if isinstance(x.get('value'),(int,float))]
        trend=None
        if len(vals)>=2:
            delta=float(vals[0]['value'])-float(vals[-1]['value'])
            trend='rising' if delta>0.01 else 'falling' if delta<-0.01 else 'steady'
        return {"source":"Environment Agency","trend":trend,"rows":items[:8]}


def _iso_clock(value: Any) -> str | None:
    if not value: return None
    try:
        dt=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        return dt.strftime('%H:%M')
    except Exception:
        return _clean_time(str(value))
