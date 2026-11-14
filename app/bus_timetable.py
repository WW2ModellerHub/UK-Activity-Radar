from __future__ import annotations

import csv
import io
import os
import sqlite3
import threading
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable

import requests

from .models import SourceStatus
from .timeutils import london_tz

USER_AGENT = "UKActivityRadar/1.4.0 BODS-GTFS"
BODS_GTFS = "https://data.bus-data.dft.gov.uk/timetable/download/gtfs-file/{region}/"
BODS_CACHE_MAX_AGE_SECONDS = 36 * 3600

REGION_URLS = {
    "east_anglia": BODS_GTFS.format(region="east_anglia"),
    "east_midlands": BODS_GTFS.format(region="east_midlands"),
    "london": BODS_GTFS.format(region="london"),
    "north_east": BODS_GTFS.format(region="north_east"),
    "north_west": BODS_GTFS.format(region="north_west"),
    "south_east": BODS_GTFS.format(region="south_east"),
    "south_west": BODS_GTFS.format(region="south_west"),
    "west_midlands": BODS_GTFS.format(region="west_midlands"),
    "yorkshire": BODS_GTFS.format(region="yorkshire"),
    "wales": BODS_GTFS.format(region="wales"),
    "scotland": BODS_GTFS.format(region="scotland"),
}


def _distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    import math
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2-lat1), math.radians(lon2-lon1)
    a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2*r*math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1-a)))


def region_for(lat: float, lon: float) -> str:
    """Choose the official BODS regional GTFS bundle for a UK coordinate.

    These are only coarse download selectors; exact stop matching happens inside
    the GTFS feed by stop code/coordinates, so a boundary error cannot make a
    different nearby stop look like the clicked one.
    """
    if lat >= 55.0:
        return "scotland"
    if lon <= -2.65 and lat <= 53.55:
        return "wales"
    if lat >= 54.45:
        return "north_east" if lon >= -2.1 else "north_west"
    if lat >= 53.25:
        if lon <= -2.0:
            return "north_west"
        if lon >= -0.15:
            return "east_anglia"
        return "yorkshire"
    if lat >= 52.25:
        if lon <= -1.60:
            return "west_midlands"
        if lon >= 0.25:
            return "east_anglia"
        return "east_midlands"
    if lat <= 51.15 and lon <= -1.2:
        return "south_west"
    if lat <= 51.72 and -0.65 <= lon <= 0.35:
        return "london"
    if lon <= -1.45:
        return "south_west"
    return "south_east"


def _gtfs_minutes(value: str | None) -> int | None:
    if not value:
        return None
    try:
        hh, mm, *_ = str(value).strip().split(":")
        return int(hh) * 60 + int(mm)
    except Exception:
        return None


def _service_ids(con: sqlite3.Connection, service_date: date) -> set[str]:
    stamp = service_date.strftime("%Y%m%d")
    weekday = ["monday","tuesday","wednesday","thursday","friday","saturday","sunday"][service_date.weekday()]
    ids: set[str] = set()
    try:
        ids.update(str(r[0]) for r in con.execute(
            f"SELECT service_id FROM calendar WHERE start_date<=? AND end_date>=? AND {weekday}=1",
            (stamp, stamp),
        ).fetchall())
    except sqlite3.Error:
        pass
    try:
        exceptions = con.execute("SELECT service_id,exception_type FROM calendar_dates WHERE date=?", (stamp,)).fetchall()
    except sqlite3.Error:
        exceptions = []
    for sid, exc in exceptions:
        if int(exc or 0) == 1:
            ids.add(str(sid))
        elif int(exc or 0) == 2:
            ids.discard(str(sid))
    return ids


def _service_ids_subset(con: sqlite3.Connection, service_date: date, candidates: set[str]) -> set[str]:
    """Resolve service-day activity without scanning every regional service."""
    if not candidates:
        return set()
    stamp = service_date.strftime("%Y%m%d")
    weekday = ["monday","tuesday","wednesday","thursday","friday","saturday","sunday"][service_date.weekday()]
    values = sorted(candidates)
    placeholders = ",".join("?" for _ in values)
    ids: set[str] = set()
    try:
        ids.update(str(row[0]) for row in con.execute(
            f"SELECT service_id FROM calendar WHERE service_id IN ({placeholders}) AND start_date<=? AND end_date>=? AND {weekday}=1",
            [*values, stamp, stamp],
        ).fetchall())
    except sqlite3.Error:
        pass
    try:
        exceptions = con.execute(
            f"SELECT service_id,exception_type FROM calendar_dates WHERE service_id IN ({placeholders}) AND date=?",
            [*values, stamp],
        ).fetchall()
    except sqlite3.Error:
        exceptions = []
    for service_id, exception_type in exceptions:
        if int(exception_type or 0) == 1:
            ids.add(str(service_id))
        elif int(exception_type or 0) == 2:
            ids.discard(str(service_id))
    return ids


def _read_csv(zf: zipfile.ZipFile, name: str) -> Iterable[dict[str, str]]:
    with zf.open(name) as raw:
        with io.TextIOWrapper(raw, encoding="utf-8-sig", errors="replace", newline="") as text:
            yield from csv.DictReader(text)


class BusTimetableService:
    """Lazy regional BODS GTFS cache used by clicked bus stops.

    The live BODS feed is excellent for GPS but many operators do not publish a
    stop-specific ETA in SIRI-VM. BODS also publishes official timetable GTFS.
    The first click in a region starts a background download/index; after that,
    stop cards show the published times and match live GTFS-RT vehicles by trip_id.
    """

    def __init__(self, data_dir: Path, state) -> None:
        self.data_dir = data_dir / "bus_timetables"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.state = state
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": USER_AGENT})
        self._lock = threading.RLock()
        self._building: set[str] = set()
        self._indexing: set[str] = set()
        self._progress: dict[str, str] = {}
        self._journey_cache: dict[tuple[str, str, str, str], tuple[float, dict]] = {}
        # Upgrade previously downloaded regional databases without making the
        # first user click pay the CREATE INDEX cost. Fresh downloads already
        # receive these indexes inside _index_zip.
        for database in self.data_dir.glob("*.sqlite"):
            region = database.stem
            if region in REGION_URLS and not self.needs_refresh(region):
                threading.Thread(
                    target=self._ensure_query_indexes,
                    args=(region,),
                    name=f"bods-index-{region}",
                    daemon=True,
                ).start()

    def _db(self, region: str) -> Path:
        return self.data_dir / f"{region}.sqlite"

    def _ensure_query_indexes(self, region: str) -> None:
        with self._lock:
            if region in self._indexing:
                return
            self._indexing.add(region)
        try:
            with sqlite3.connect(self._db(region), timeout=30) as con:
                con.executescript("""
                    CREATE INDEX IF NOT EXISTS idx_trips_route ON trips(route_id);
                    CREATE INDEX IF NOT EXISTS idx_routes_short_name ON routes(short_name);
                """)
                con.commit()
        except sqlite3.Error:
            # A stale/incomplete cache will be replaced by the normal atomic
            # regional refresh; it must not make the web process fail startup.
            pass
        finally:
            with self._lock:
                self._indexing.discard(region)

    def ready(self, region: str) -> bool:
        db = self._db(region)
        if not db.exists():
            return False
        try:
            with sqlite3.connect(db) as con:
                n = int(con.execute("SELECT COUNT(*) FROM stop_times").fetchone()[0])
            return n > 0
        except sqlite3.Error:
            return False

    def needs_refresh(self, region: str) -> bool:
        db = self._db(region)
        if not db.exists():
            return True
        try:
            return max(0.0, datetime.now().timestamp() - db.stat().st_mtime) >= BODS_CACHE_MAX_AGE_SECONDS
        except OSError:
            return True

    def ensure_region(self, region: str) -> None:
        if region not in REGION_URLS or (self.ready(region) and not self.needs_refresh(region)):
            return
        with self._lock:
            if region in self._building:
                return
            self._building.add(region)
        threading.Thread(target=self._build_region, args=(region,), name=f"bods-gtfs-{region}", daemon=True).start()

    def _status(self, region: str, message: str, *, mode: str = "sync", connected: bool = False) -> None:
        self._progress[region] = message
        self.state.set_source(SourceStatus(
            name="bods_timetable", enabled=True, connected=connected, mode=mode,
            message=message, last_message_at=datetime.now(tz=london_tz()),
        ))

    def _build_region(self, region: str) -> None:
        tmp_zip = self.data_dir / f"{region}.download.zip"
        tmp_db = self.data_dir / f"{region}.building.sqlite"
        try:
            self._status(region, f"BODS timetable • downloading {region.replace('_',' ')} GTFS…")
            with self._session.get(REGION_URLS[region], stream=True, timeout=(20, 300)) as r:
                r.raise_for_status()
                total = int(r.headers.get("content-length") or 0)
                got = 0
                with tmp_zip.open("wb") as f:
                    for chunk in r.iter_content(1024*1024):
                        if not chunk:
                            continue
                        f.write(chunk)
                        got += len(chunk)
                        if got and got % (10*1024*1024) < len(chunk):
                            if total:
                                self._status(region, f"BODS timetable • {region.replace('_',' ')} {got/1048576:.0f}/{total/1048576:.0f} MB")
                            else:
                                self._status(region, f"BODS timetable • {region.replace('_',' ')} {got/1048576:.0f} MB")
            self._index_zip(region, tmp_zip, tmp_db)
        except Exception as exc:
            self._status(region, f"BODS timetable error ({region.replace('_',' ')}): {type(exc).__name__}: {exc}", mode="error", connected=False)
        finally:
            with self._lock:
                self._building.discard(region)
            tmp_db.unlink(missing_ok=True)
            tmp_zip.unlink(missing_ok=True)

    def _index_zip(self, region: str, zip_path: Path, tmp_db: Path | None = None) -> int:
        tmp_db = tmp_db or self.data_dir / f"{region}.building.sqlite"
        self._status(region, f"BODS timetable • indexing {region.replace('_',' ')}…")
        tmp_db.unlink(missing_ok=True)
        con = sqlite3.connect(tmp_db)
        try:
            con.executescript("""
                PRAGMA journal_mode=OFF;
                PRAGMA synchronous=OFF;
                PRAGMA temp_store=MEMORY;
                CREATE TABLE stops(stop_id TEXT PRIMARY KEY,stop_code TEXT,name TEXT,lat REAL,lon REAL);
                CREATE TABLE agencies(agency_id TEXT PRIMARY KEY,name TEXT);
                CREATE TABLE routes(route_id TEXT PRIMARY KEY,agency_id TEXT,short_name TEXT,long_name TEXT);
                CREATE TABLE trips(trip_id TEXT PRIMARY KEY,route_id TEXT,service_id TEXT,headsign TEXT,direction_id TEXT);
                CREATE TABLE calendar(service_id TEXT PRIMARY KEY,monday INTEGER,tuesday INTEGER,wednesday INTEGER,thursday INTEGER,friday INTEGER,saturday INTEGER,sunday INTEGER,start_date TEXT,end_date TEXT);
                CREATE TABLE calendar_dates(service_id TEXT,date TEXT,exception_type INTEGER);
                CREATE TABLE stop_times(stop_id TEXT,trip_id TEXT,arrival_time TEXT,departure_time TEXT,stop_sequence INTEGER);
                CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT);
            """)
            with zipfile.ZipFile(zip_path) as zf:
                names = set(zf.namelist())
                required = {"stops.txt","routes.txt","trips.txt","stop_times.txt"}
                if not required.issubset(names):
                    raise RuntimeError(f"GTFS bundle is missing {sorted(required-names)}")
                con.executemany("INSERT OR REPLACE INTO stops VALUES(?,?,?,?,?)", (
                    (r.get("stop_id"), r.get("stop_code"), r.get("stop_name"), float(r.get("stop_lat") or 0), float(r.get("stop_lon") or 0))
                    for r in _read_csv(zf,"stops.txt") if r.get("stop_id")
                ))
                if "agency.txt" in names:
                    con.executemany("INSERT OR REPLACE INTO agencies VALUES(?,?)", (
                        (r.get("agency_id"),r.get("agency_name")) for r in _read_csv(zf,"agency.txt") if r.get("agency_id")
                    ))
                con.executemany("INSERT OR REPLACE INTO routes VALUES(?,?,?,?)", (
                    (r.get("route_id"),r.get("agency_id"),r.get("route_short_name"),r.get("route_long_name"))
                    for r in _read_csv(zf,"routes.txt") if r.get("route_id")
                ))
                con.executemany("INSERT OR REPLACE INTO trips VALUES(?,?,?,?,?)", (
                    (r.get("trip_id"),r.get("route_id"),r.get("service_id"),r.get("trip_headsign"),r.get("direction_id"))
                    for r in _read_csv(zf,"trips.txt") if r.get("trip_id")
                ))
                if "calendar.txt" in names:
                    con.executemany("INSERT OR REPLACE INTO calendar VALUES(?,?,?,?,?,?,?,?,?,?)", (
                        (r.get("service_id"),int(r.get("monday") or 0),int(r.get("tuesday") or 0),int(r.get("wednesday") or 0),int(r.get("thursday") or 0),int(r.get("friday") or 0),int(r.get("saturday") or 0),int(r.get("sunday") or 0),r.get("start_date"),r.get("end_date"))
                        for r in _read_csv(zf,"calendar.txt") if r.get("service_id")
                    ))
                if "calendar_dates.txt" in names:
                    con.executemany("INSERT INTO calendar_dates VALUES(?,?,?)", (
                        (r.get("service_id"),r.get("date"),int(r.get("exception_type") or 0))
                        for r in _read_csv(zf,"calendar_dates.txt") if r.get("service_id")
                    ))
                batch=[]
                count=0
                for r in _read_csv(zf,"stop_times.txt"):
                    if not r.get("stop_id") or not r.get("trip_id"):
                        continue
                    batch.append((r.get("stop_id"),r.get("trip_id"),r.get("arrival_time"),r.get("departure_time"),int(r.get("stop_sequence") or 0)))
                    count += 1
                    if len(batch)>=20000:
                        con.executemany("INSERT INTO stop_times VALUES(?,?,?,?,?)",batch)
                        batch.clear()
                        if count % 200000 < 20000:
                            self._status(region, f"BODS timetable • indexing {region.replace('_',' ')} • {count:,} stop calls")
                if batch:
                    con.executemany("INSERT INTO stop_times VALUES(?,?,?,?,?)",batch)
            con.executescript("""
                CREATE INDEX idx_stops_code ON stops(stop_code);
                CREATE INDEX idx_stops_latlon ON stops(lat,lon);
                CREATE INDEX idx_stop_times_stop ON stop_times(stop_id);
                CREATE INDEX idx_stop_times_trip ON stop_times(trip_id);
                CREATE INDEX idx_trips_service ON trips(service_id);
                CREATE INDEX idx_trips_route ON trips(route_id);
                CREATE INDEX idx_routes_short_name ON routes(short_name);
                CREATE INDEX idx_calendar_dates ON calendar_dates(date,service_id);
            """)
            con.execute("INSERT OR REPLACE INTO meta VALUES('built_at',?)",(datetime.now(tz=london_tz()).isoformat(),))
            con.commit()
        finally:
            con.close()
        os.replace(tmp_db,self._db(region))
        self._status(region, f"BODS timetable ready • {region.replace('_',' ')} • {count:,} stop calls", mode="ready", connected=True)
        return count

    def _find_stop_ids(self, con: sqlite3.Connection, place: dict) -> list[str]:
        refs=[str(place.get(k) or "").strip() for k in ("atco_code","naptan_code")]
        refs=[x for x in refs if x]
        out=[]
        for ref in refs:
            rows=con.execute("SELECT stop_id FROM stops WHERE stop_id=? OR stop_code=? LIMIT 8",(ref,ref)).fetchall()
            out.extend(str(r[0]) for r in rows)
        if out:
            return list(dict.fromkeys(out))
        try:
            lat,lon=float(place["lat"]),float(place["lon"])
        except Exception:
            return []
        candidates=con.execute(
            "SELECT stop_id,lat,lon FROM stops WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ? LIMIT 200",
            (lat-.01,lat+.01,lon-.015,lon+.015),
        ).fetchall()
        ranked=sorted(((_distance_km(lat,lon,float(r[1]),float(r[2])), str(r[0])) for r in candidates), key=lambda x:x[0])
        return [sid for km,sid in ranked[:4] if km<=0.35]

    def board_for_stop(self, place: dict, limit: int = 12) -> dict:
        lat,lon=float(place["lat"]),float(place["lon"])
        region=region_for(lat,lon)
        if not self.ready(region):
            self.ensure_region(region)
            progress=self._progress.get(region) or f"BODS timetable for {region.replace('_',' ')} is preparing."
            return {"mode":"timetable_sync","source":"DfT BODS GTFS timetable","rows":[],"region":region,"note":progress,"retry_seconds":5}
        refreshing = self.needs_refresh(region)
        if refreshing:
            # Keep the last complete timetable serving while its atomic
            # replacement downloads and indexes in the background.
            self.ensure_region(region)
        db=self._db(region)
        now=datetime.now(tz=london_tz())
        rows=[]
        with sqlite3.connect(db) as con:
            con.row_factory=sqlite3.Row
            stop_ids=self._find_stop_ids(con,place)
            if not stop_ids:
                return {"mode":"no_timetable_stop","source":"DfT BODS GTFS timetable","rows":[],"region":region,"note":"The regional BODS timetable is ready but this NaPTAN stop could not be matched to a GTFS stop."}
            for service_date in (now.date()-timedelta(days=1), now.date()):
                active=_service_ids(con,service_date)
                if not active:
                    continue
                active_list=list(active)
                for start in range(0,len(active_list),700):
                    ids=active_list[start:start+700]
                    q_stop=','.join('?' for _ in stop_ids)
                    q_service=','.join('?' for _ in ids)
                    sql=f"""SELECT st.stop_id,st.trip_id,st.arrival_time,st.departure_time,st.stop_sequence,
                             t.route_id,t.service_id,t.headsign,r.short_name,r.long_name,a.name AS agency_name
                             FROM stop_times st JOIN trips t ON t.trip_id=st.trip_id
                             LEFT JOIN routes r ON r.route_id=t.route_id
                             LEFT JOIN agencies a ON a.agency_id=r.agency_id
                             WHERE st.stop_id IN ({q_stop}) AND t.service_id IN ({q_service})"""
                    for r in con.execute(sql,[*stop_ids,*ids]).fetchall():
                        clock=r['departure_time'] or r['arrival_time']
                        mins=_gtfs_minutes(clock)
                        if mins is None:
                            continue
                        due=datetime.combine(service_date, datetime.min.time(), tzinfo=london_tz())+timedelta(minutes=mins)
                        delta=(due-now).total_seconds()/60
                        if delta < -2 or delta > 240:
                            continue
                        rows.append({
                            "scheduled":due.isoformat(),"clock":due.strftime("%H:%M"),"minutes":max(0,int(delta)),
                            "route":r['short_name'] or r['route_id'],"route_long_name":r['long_name'],
                            "destination":r['headsign'],"operator":r['agency_name'],"trip_id":r['trip_id'],
                            "stop_id":r['stop_id'],"status":"Scheduled",
                        })
        rows.sort(key=lambda x:x['scheduled'])
        dedup=[]
        seen=set()
        for row in rows:
            key=(row['trip_id'],row['stop_id'],row['scheduled'])
            if key in seen:
                continue
            seen.add(key)
            dedup.append(row)
        return {
            "mode":"published_timetable","source":"DfT BODS GTFS timetable","rows":dedup[:limit],"region":region,
            "note":"Published stop times from the official BODS regional GTFS timetable. Live BODS vehicle positions are matched by GTFS trip ID where available."
                   + (" A newer regional timetable is refreshing in the background." if refreshing else ""),
        }

    def journey_for_vehicle(self, vehicle, limit: int = 12) -> dict:
        """Return an honest timetable spine for one live BODS vehicle.

        BODS AVL identifies a GTFS trip on many vehicles but does not guarantee
        an ETA at every stop.  This method joins that trip to the official BODS
        timetable and marks the next reported stop; it never turns scheduled
        times into invented live predictions.
        """
        trip_id = str(getattr(vehicle, "journey_ref", None) or "").strip()
        if not trip_id:
            return {
                "mode": "identity_unavailable", "source": "DfT BODS",
                "stops": [], "note": "The bus operator has not linked this live vehicle to a published journey, so its next stop cannot be identified reliably.",
            }
        try:
            lat, lon = float(vehicle.lat), float(vehicle.lon)
        except (TypeError, ValueError, AttributeError):
            return {"mode": "location_unavailable", "source": "DfT BODS", "stops": []}
        region = region_for(lat, lon)
        cache_key = (
            region, trip_id, str(getattr(vehicle, "route", None) or "").strip(),
            str(getattr(vehicle, "destination", None) or "").strip(),
        )
        with self._lock:
            cached = self._journey_cache.get(cache_key)
            if cached and datetime.now().timestamp() - cached[0] < 30:
                return dict(cached[1])
        if not self.ready(region):
            self.ensure_region(region)
            return {
                "mode": "timetable_sync", "source": "DfT BODS GTFS timetable", "region": region,
                "stops": [], "retry_seconds": 5,
                "note": self._progress.get(region) or f"BODS timetable for {region.replace('_', ' ')} is preparing.",
            }
        if self.needs_refresh(region):
            self.ensure_region(region)

        now = datetime.now(tz=london_tz())
        match_method = "exact_trip"
        with sqlite3.connect(self._db(region)) as con:
            con.row_factory = sqlite3.Row
            trip = con.execute(
                """SELECT t.trip_id,t.service_id,t.headsign,t.direction_id,t.route_id,
                          r.short_name,r.long_name,a.name AS agency_name
                   FROM trips t LEFT JOIN routes r ON r.route_id=t.route_id
                   LEFT JOIN agencies a ON a.agency_id=r.agency_id
                   WHERE t.trip_id=? LIMIT 1""",
                (trip_id,),
            ).fetchone()
            if trip is None:
                # SIRI publishers often use an operational journey reference
                # that is different from the BODS GTFS trip_id. Match within
                # the same official timetable by route, service day, time and
                # proximity instead of giving up or pretending the nearest bus
                # stop is necessarily next.
                route = str(getattr(vehicle, "route", None) or "").strip()
                destination = str(getattr(vehicle, "destination", None) or "").strip().casefold()
                candidates = con.execute(
                    """SELECT t.trip_id,t.service_id,t.headsign,t.direction_id,t.route_id,
                              r.short_name,r.long_name,a.name AS agency_name
                       FROM trips t JOIN routes r ON r.route_id=t.route_id
                       LEFT JOIN agencies a ON a.agency_id=r.agency_id
                       WHERE r.short_name=? OR r.route_id=? LIMIT 240""",
                    (route, route),
                ).fetchall() if route else []
                candidate_services = {str(row["service_id"]) for row in candidates}
                active_by_date = {
                    now.date() - timedelta(days=1): _service_ids_subset(con, now.date() - timedelta(days=1), candidate_services),
                    now.date(): _service_ids_subset(con, now.date(), candidate_services),
                }
                candidates = [row for row in candidates if any(str(row["service_id"]) in ids for ids in active_by_date.values())]
                stop_rows: dict[str, list[sqlite3.Row]] = {}
                if candidates:
                    ids = [str(row["trip_id"]) for row in candidates]
                    placeholders = ",".join("?" for _ in ids)
                    for row in con.execute(
                        f"""SELECT st.trip_id,st.arrival_time,st.departure_time,st.stop_sequence,
                                   s.stop_id,s.stop_code,s.name,s.lat,s.lon
                            FROM stop_times st LEFT JOIN stops s ON s.stop_id=st.stop_id
                            WHERE st.trip_id IN ({placeholders}) ORDER BY st.trip_id,st.stop_sequence""",
                        ids,
                    ).fetchall():
                        stop_rows.setdefault(str(row["trip_id"]), []).append(row)
                best: tuple[float, sqlite3.Row] | None = None
                for row in candidates:
                    raw = stop_rows.get(str(row["trip_id"]), [])
                    if not raw:
                        continue
                    spatial = min((_distance_km(lat, lon, float(s["lat"]), float(s["lon"])) for s in raw if s["lat"] is not None and s["lon"] is not None), default=999.0)
                    temporal = 999.0
                    for service_date, active in active_by_date.items():
                        if str(row["service_id"]) not in active:
                            continue
                        for stop in raw:
                            minutes = _gtfs_minutes(stop["departure_time"] or stop["arrival_time"])
                            if minutes is None:
                                continue
                            due = datetime.combine(service_date, datetime.min.time(), tzinfo=london_tz()) + timedelta(minutes=minutes)
                            temporal = min(temporal, abs((due-now).total_seconds())/60)
                    headsign = str(row["headsign"] or "").casefold()
                    destination_penalty = 0 if not destination or destination in headsign or headsign in destination else 12
                    score = spatial + temporal/12 + destination_penalty
                    if spatial <= 20 and temporal <= 180 and (best is None or score < best[0]):
                        best = (score, row)
                if best is not None:
                    trip = best[1]
                    match_method = "route_time_position"
                else:
                    payload = {
                        "mode": "trip_not_in_cache", "source": "DfT BODS GTFS timetable", "region": region,
                        "stops": [], "note": "The operator has not supplied a journey identifier that matches the current regional timetable, so a next stop cannot be stated safely.",
                    }
                    with self._lock:
                        self._journey_cache[cache_key] = (datetime.now().timestamp(), payload)
                    return payload
            raw_stops = con.execute(
                """SELECT st.stop_id,st.arrival_time,st.departure_time,st.stop_sequence,
                          s.stop_code,s.name,s.lat,s.lon
                   FROM stop_times st LEFT JOIN stops s ON s.stop_id=st.stop_id
                   WHERE st.trip_id=? ORDER BY st.stop_sequence""",
                (str(trip["trip_id"]),),
            ).fetchall()

            candidates: list[tuple[float, date, list[dict]]] = []
            for service_date in (now.date() - timedelta(days=1), now.date()):
                if str(trip["service_id"]) not in _service_ids(con, service_date):
                    continue
                timeline: list[dict] = []
                for row in raw_stops:
                    minutes = _gtfs_minutes(row["departure_time"] or row["arrival_time"])
                    if minutes is None:
                        continue
                    due = datetime.combine(service_date, datetime.min.time(), tzinfo=london_tz()) + timedelta(minutes=minutes)
                    timeline.append({
                        "stop_id": row["stop_id"], "stop_code": row["stop_code"],
                        "name": row["name"] or row["stop_id"], "sequence": row["stop_sequence"],
                        "scheduled": due.isoformat(), "clock": due.strftime("%H:%M"),
                        "lat": row["lat"], "lon": row["lon"],
                    })
                if timeline:
                    distance = min(abs((datetime.fromisoformat(row["scheduled"]) - now).total_seconds()) for row in timeline)
                    candidates.append((distance, service_date, timeline))

        if not candidates:
            return {
                "mode": "service_not_running", "source": "DfT BODS GTFS timetable", "region": region,
                "stops": [], "note": "The matched timetable trip is not scheduled on the current service day.",
            }
        _, service_date, timeline = min(candidates, key=lambda item: item[0])
        reported_stop = str(getattr(vehicle, "next_location", None) or "").strip().casefold()
        next_index = None
        next_from_report = False
        if reported_stop:
            next_index = next((index for index, row in enumerate(timeline) if reported_stop in {
                str(row.get("stop_id") or "").casefold(), str(row.get("stop_code") or "").casefold()
            }), None)
            next_from_report = next_index is not None
        if next_index is None:
            next_index = next((index for index, row in enumerate(timeline)
                               if datetime.fromisoformat(row["scheduled"]) >= now - timedelta(minutes=3)), len(timeline) - 1)
        # The whole journey: the app opens it at the stop before the bus and
        # keeps earlier stops behind a button. Very long trips are trimmed to
        # 20 stops before the bus and enough after it.
        start = max(0, next_index - 20)
        shown = timeline[start:next_index + max(12, min(60, int(limit)))]
        for index, row in enumerate(shown, start=start):
            row["state"] = "next" if index == next_index else ("passed" if index < next_index else "upcoming")
        payload = {
            "mode": "matched_timetable", "source": "DfT BODS GTFS timetable + live BODS GPS",
            "region": region, "service_date": service_date.isoformat(),
            "route": trip["short_name"] or trip["route_id"], "route_long_name": trip["long_name"],
            "destination": trip["headsign"], "operator": trip["agency_name"],
            "trip_id": trip["trip_id"], "next_stop_id": timeline[next_index]["stop_id"], "stops": shown,
            "match_method": match_method,
            # True when the operator's GPS feed named the next stop, so the
            # timetable time there says how late the bus is.
            "next_from_report": next_from_report,
            "note": ("Stop order and times are the official timetable. The live feed's journey reference matched this trip exactly."
                     if match_method == "exact_trip" else
                     "The operator uses a different live journey reference. This official timetable trip is the best route/time/position match; times remain scheduled, not live predictions."),
        }
        with self._lock:
            self._journey_cache[cache_key] = (datetime.now().timestamp(), payload)
        return payload
