from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Callable
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ..models import Event, SourceStatus, Vehicle

UA = {"User-Agent": "UKActivityRadar/1.2.1 (free/open public data consumer)"}


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _dt(v):
    if not v:
        return None
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _hash(*parts) -> str:
    return hashlib.sha1("|".join(str(x or "") for x in parts).encode("utf-8")).hexdigest()[:16]


def _pick(row: dict, *names):
    lower = {str(k).lower().replace(" ", "").replace("_", ""): v for k, v in row.items()}
    for name in names:
        key = name.lower().replace(" ", "").replace("_", "")
        if key in lower and lower[key] not in (None, ""):
            return lower[key]
    return None


class SnapshotThread:
    """Small common runner for snapshot-style public feeds."""
    source_name = "source"
    source_label = "Source"
    event_source = "Source"

    def __init__(self, settings, state, poll_seconds: int):
        self.settings = settings
        self.state = state
        self.poll_seconds = poll_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._session = requests.Session()
        self._session.headers.update(UA)
        self._seen = 0

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name=self.source_name, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)

    def fetch_once(self):
        raise NotImplementedError

    def apply(self, rows):
        events = [x for x in rows if isinstance(x, Event)]
        vehicles = [x for x in rows if isinstance(x, Vehicle)]
        if events:
            self.state.replace_events_for_source(self.event_source, events)
        elif self.event_source:
            self.state.replace_events_for_source(self.event_source, [])
        for v in vehicles:
            self.state.upsert(v)
        return events, vehicles

    def message(self, events, vehicles) -> str:
        return f"{len(events)+len(vehicles):,} live records mapped"

    def _run(self):
        self.state.set_source(SourceStatus(name=self.source_name, enabled=True, connected=False, mode="connecting", message=f"Connecting to {self.source_label}…"))
        while not self._stop.is_set():
            try:
                rows = self.fetch_once() or []
                events, vehicles = self.apply(rows)
                count = len(events) + len(vehicles)
                self._seen += count
                self.state.set_source(SourceStatus(
                    name=self.source_name, enabled=True, connected=True,
                    mode="live" if count else "ready",
                    message=self.message(events, vehicles),
                    last_message_at=datetime.now(timezone.utc), messages_seen=self._seen,
                    movements_mapped=count,
                ))
            except Exception as exc:
                self.state.set_source(SourceStatus(name=self.source_name, enabled=True, connected=False, mode="error", message=f"{self.source_label} error: {type(exc).__name__}: {exc}"))
            self._stop.wait(self.poll_seconds)


# ---------- BGS earthquakes ----------

def parse_bgs_georss(xml_text: str) -> list[Event]:
    root = ET.fromstring(xml_text)
    ns = {"geo": "http://www.georss.org/georss", "atom": "http://www.w3.org/2005/Atom"}
    out: list[Event] = []
    now = datetime.now(timezone.utc)
    for item in root.iter():
        if item.tag.split("}")[-1] not in {"item", "entry"}:
            continue
        children = {c.tag.split("}")[-1]: c for c in list(item)}
        title = (children.get("title").text if children.get("title") is not None else "UK earthquake") or "UK earthquake"
        desc = (children.get("description").text if children.get("description") is not None else "") or ""
        point = item.find("geo:point", ns)
        if point is None:
            point = next((c for c in item.iter() if c.tag.split("}")[-1] == "point"), None)
        if point is None or not point.text:
            continue
        parts = point.text.split()
        if len(parts) < 2:
            continue
        lat, lon = _num(parts[0]), _num(parts[1])
        if lat is None or lon is None:
            continue
        text = f"{title} {desc}"
        mm = re.search(r"(?:ML|M|Magnitude)\s*[:=]?\s*(\d+(?:\.\d+)?)", text, re.I)
        mag = _num(mm.group(1)) if mm else None
        link = ""
        le = children.get("link")
        if le is not None:
            link = le.get("href") or (le.text or "")
        pub = children.get("pubDate") or children.get("updated")
        when = _dt(pub.text if pub is not None else None) or now
        location = re.sub(r"\s+", " ", title).strip()
        status = f"Magnitude {mag:.1f}" if mag is not None else "Recorded earthquake"
        sev = "high" if (mag or 0) >= 4 else "medium" if (mag or 0) >= 3 else "low"
        out.append(Event(
            id=f"bgs-quake:{_hash(title,when.isoformat(),lat,lon)}", type="earthquake",
            name=location, subtitle=status, lat=lat, lon=lon, category="Earthquake",
            status=status, severity=sev, reason=re.sub("<[^>]+>", " ", desc)[:800] or None,
            start_time=when, source="British Geological Survey", source_id=_hash(title,when),
            official_url=link or "https://earthquakes.bgs.ac.uk/earthquakes/recent_uk_events.html",
            news_query=f'"{location}" earthquake', social_query=f'"{location}" earthquake',
            interesting=(mag or 0) >= 2.5, last_updated=when,
        ))
    return out


class BGSEarthquakeCollector(SnapshotThread):
    source_name="bgs_earthquakes"; source_label="BGS recent earthquakes"; event_source="British Geological Survey"
    def __init__(self, settings, state): super().__init__(settings,state,settings.bgs_earthquakes_poll_seconds)
    def fetch_once(self):
        r=self._session.get(self.settings.bgs_earthquakes_url,timeout=20); r.raise_for_status(); return parse_bgs_georss(r.text)
    def message(self,e,v): return f"BGS feed verified • {len(e):,} recent UK earthquakes mapped"


# ---------- Environment Agency tide gauges (ordinary river-level markers suppressed) ----------
class EALevelsCollector(SnapshotThread):
    source_name="ea_levels"; source_label="EA tide gauges"; event_source="Environment Agency tide levels"
    def __init__(self,settings,state):
        super().__init__(settings,state,settings.ea_levels_poll_seconds); self._measures={}; self._stations_loaded=0

    def _load_stations(self):
        params={"_view":"full","_limit":10000}
        r=self._session.get(self.settings.ea_stations_url,params=params,timeout=35); r.raise_for_status()
        items=r.json().get("items",[]); mapping={}
        for s in items:
            lat,lon=_num(s.get("lat")),_num(s.get("long"))
            if lat is None or lon is None: continue
            measures=s.get("measures") or []
            if isinstance(measures,dict): measures=[measures]
            for m in measures:
                if not isinstance(m,dict): continue
                uri=m.get("@id") or m.get("id")
                if not uri: continue
                mapping[str(uri)]={"lat":lat,"lon":lon,"station":s.get("label") or s.get("notation"),"river":s.get("riverName"),"town":s.get("town"),"qualifier":m.get("qualifier"),"unit":m.get("unitName") or m.get("unit"),"parameter":m.get("parameterName") or m.get("parameter"),"stageScale":s.get("stageScale"),"type":str(s.get("type") or "")}
        self._measures=mapping; self._stations_loaded=len(items)

    def fetch_once(self):
        if not self._measures: self._load_stations()
        r=self._session.get(self.settings.ea_readings_url,timeout=45); r.raise_for_status(); items=r.json().get("items",[])
        rows=[]; now=datetime.now(timezone.utc)
        for rd in items:
            meta=self._measures.get(str(rd.get("measure") or "")); val=_num(rd.get("value"))
            if not meta or val is None: continue
            parameter=str(meta.get("parameter") or "").lower(); qualifier=str(meta.get("qualifier") or "")
            # Focus on level/flow-like readings rather than rain/temperature.
            if not any(x in parameter for x in ("level","flow","height")) and not any(x in qualifier.lower() for x in ("stage","tide","flow")): continue
            is_tide="tide" in str(meta.get("type","")).lower() or "tidal" in qualifier.lower()
            if not is_tide:
                continue
            typ="tide"
            unit=str(meta.get("unit") or "m").replace("http://qudt.org/vocab/unit/","")
            station=str(meta.get("station") or "Monitoring station")
            river=meta.get("river"); town=meta.get("town")
            subtitle=" • ".join([x for x in [river,town,f"{val:g} {unit}"] if x])
            when=_dt(rd.get("dateTime")) or now
            rows.append(Event(id=f"ea-{typ}:{_hash(rd.get('measure'))}",type=typ,name=station,subtitle=subtitle,lat=meta['lat'],lon=meta['lon'],category="Tide gauge" if is_tide else "River level",status=f"{val:g} {unit}",reason=f"{qualifier or parameter} • latest Environment Agency reading",source="Environment Agency levels",source_id=str(rd.get("measure") or ""),official_url=str(rd.get("measure") or self.settings.ea_stations_url),interesting=False,last_updated=when))
        rows.sort(key=lambda x:x.last_updated,reverse=True)
        return rows[:self.settings.ea_levels_max_points]
    def message(self,e,v):
        tides=sum(x.type=="tide" for x in e)
        return f"{tides:,} tide-gauge readings • river-level clutter removed • {self._stations_loaded:,} stations indexed"


# ---------- Cefas WaveNet ----------
def _dms_to_decimal(text: str, hemi: str|None=None):
    nums=[_num(x) for x in re.findall(r"-?\d+(?:\.\d+)?",text)[:3]]
    nums=[x for x in nums if x is not None]
    if not nums: return None
    val=abs(nums[0])+(nums[1]/60 if len(nums)>1 else 0)+(nums[2]/3600 if len(nums)>2 else 0)
    if str(hemi or "").upper() in {"S","W"} or (nums and nums[0]<0): val=-val
    return val


class WaveNetCollector(SnapshotThread):
    source_name="wavenet"; source_label="Cefas WaveNet"; event_source="Cefas WaveNet"
    def __init__(self,settings,state): super().__init__(settings,state,settings.wavenet_poll_seconds)
    def _site(self,url):
        r=self._session.get(url,timeout=20); r.raise_for_status(); soup=BeautifulSoup(r.text,"html.parser"); text=" ".join(soup.stripped_strings)
        name=(soup.find("h1") or soup.find("h2")); name=" ".join(name.stripped_strings) if name else url.rstrip('/').split('/')[-2]
        # Accept decimal or DMS coordinate presentations.
        m=re.search(r"(?:Located at|Latitude)\s*[:\-]?\s*([0-9°'\"\.\s]+)\s*([NS]).{0,80}?(?:Longitude)?\s*[:\-]?\s*([0-9°'\"\.\s]+)\s*([EW])",text,re.I)
        if not m:
            m=re.search(r"([45]\d(?:\.\d+)?)\s*[,/]\s*(-?\d{1,2}(?:\.\d+)?)",text)
            if not m:return None
            lat,lon=_num(m.group(1)),_num(m.group(2))
        else:
            lat,lon=_dms_to_decimal(m.group(1),m.group(2)),_dms_to_decimal(m.group(3),m.group(4))
        if lat is None or lon is None:return None
        hm=re.search(r"(?:Significant\s+Wave\s+Height|Hs)\s*[:\-]?\s*(\d+(?:\.\d+)?)\s*m",text,re.I)
        tm=re.search(r"(?:Water\s+Temperature|temperature)\s*[:\-]?\s*(\d+(?:\.\d+)?)\s*°?C",text,re.I)
        height=_num(hm.group(1)) if hm else None; temp=_num(tm.group(1)) if tm else None
        bits=[]
        if height is not None:bits.append(f"{height:.1f} m waves")
        if temp is not None:bits.append(f"{temp:.1f} °C water")
        return Event(id=f"wavenet:{_hash(url)}",type="wave",name=name,subtitle=" • ".join(bits) or "Wave buoy telemetry",lat=lat,lon=lon,category="Wave buoy",status=f"{height:.1f} m" if height is not None else "Live telemetry",reason="Cefas WaveNet near-live coastal telemetry",source="Cefas WaveNet",source_id=url,official_url=url,interesting=(height or 0)>=4,last_updated=datetime.now(timezone.utc))
    def fetch_once(self):
        r=self._session.get(self.settings.wavenet_map_url,timeout=25);r.raise_for_status();s=BeautifulSoup(r.text,"html.parser")
        urls=[]
        for a in s.find_all("a",href=True):
            href=str(a.get("href"));
            if "/details/" in href:
                u=urljoin(self.settings.wavenet_map_url,href)
                if u not in urls:urls.append(u)
        out=[]
        for u in urls[:self.settings.wavenet_max_sites]:
            try:
                e=self._site(u)
                if e:out.append(e)
            except Exception:pass
        return out
    def message(self,e,v):return f"WaveNet verified • {len(e):,} near-live wave buoys mapped"


# ---------- NESO Carbon Intensity ----------
REGIONS={
1:(57.4,-4.0,"North Scotland"),2:(55.8,-3.4,"South Scotland"),3:(54.9,-2.8,"North West England"),4:(54.8,-1.6,"North East England"),5:(53.7,-1.5,"Yorkshire"),6:(53.2,-2.4,"North Wales & Merseyside"),7:(52.7,-1.7,"East Midlands"),8:(52.5,-2.1,"West Midlands"),9:(52.2,-4.0,"South Wales"),10:(52.1,0.8,"East England"),11:(51.7,-1.8,"South West England"),12:(51.5,-0.1,"London"),13:(51.2,-1.3,"South England"),14:(51.0,0.5,"South East England")}

def parse_carbon_regions(payload: dict) -> list[Event]:
    data=payload.get("data") or []
    if isinstance(data,dict):data=[data]
    out=[];now=datetime.now(timezone.utc)
    for block in data:
        rows=block.get("regions") or []
        for r in rows:
            rid=_int(r.get("regionid") or r.get("regionId")); center=REGIONS.get(rid)
            if not center:continue
            intensity=r.get("intensity") or {}; actual=_num(intensity.get("actual")); forecast=_num(intensity.get("forecast")); index=str(intensity.get("index") or "unknown")
            val=actual if actual is not None else forecast
            generation=r.get("generationmix") or []; generation=sorted(generation,key=lambda x:_num(x.get("perc")) or 0,reverse=True)
            mix=", ".join(f"{x.get('fuel')} {(_num(x.get('perc')) or 0):g}%" for x in generation[:3])
            out.append(Event(id=f"carbon:{rid}",type="grid",name=center[2],subtitle=f"{val:g} gCO₂/kWh • {index}" if val is not None else index,lat=center[0],lon=center[1],category="Grid carbon intensity",status=index.title(),reason=(f"Top generation: {mix}" if mix else None),source="NESO Carbon Intensity",source_id=str(rid),official_url="https://carbonintensity.org.uk/",interesting=index.lower() in {"high","very high"},last_updated=now))
    return out


class CarbonIntensityCollector(SnapshotThread):
    source_name="carbon_intensity";source_label="NESO carbon intensity";event_source="NESO Carbon Intensity"
    def __init__(self,s,state):super().__init__(s,state,s.carbon_intensity_poll_seconds)
    def fetch_once(self):
        r=self._session.get(self.settings.carbon_intensity_url,timeout=20);r.raise_for_status();return parse_carbon_regions(r.json())
    def message(self,e,v):return f"NESO regional grid live • {len(e):,} GB regions mapped"


# ---------- DEFRA UK-AIR regional DAQI ----------
AIR_REGIONS={"Scotland":(56.5,-4),"Northern Ireland":(54.7,-6.7),"North East":(54.8,-1.8),"North West":(54,-2.7),"Yorkshire and Humberside":(53.8,-1.5),"West Midlands":(52.5,-2),"East Midlands":(52.8,-1.2),"Wales":(52.3,-3.7),"Eastern":(52.3,0.5),"London":(51.5,-0.1),"South East":(51.2,0.2),"South West":(50.8,-3.2)}

class UKAirCollector(SnapshotThread):
    source_name="uk_air";source_label="DEFRA UK-AIR";event_source="DEFRA UK-AIR"
    def __init__(self,s,state):super().__init__(s,state,s.uk_air_poll_seconds)
    def fetch_once(self):
        out=[];now=datetime.now(timezone.utc)
        for zone in range(1,18):
            try:
                r=self._session.get(self.settings.uk_air_base_url,params={"zone_id":zone},timeout=12);r.raise_for_status();s=BeautifulSoup(r.text,"html.parser");text=" ".join(s.stripped_strings)
                m=re.search(r"Pollution is currently\s*:?\s*([A-Za-z ]+?)\s*\(Index\s*(\d+)\)",text,re.I)
                if not m:continue
                idx=_int(m.group(2)) or 0; status=m.group(1).strip().title()
                heading=next((" ".join(h.stripped_strings) for h in s.find_all(["h1","h2","h3"]) if "pollution" not in " ".join(h.stripped_strings).lower()),"")
                center=next((coords for name,coords in AIR_REGIONS.items() if name.lower() in text.lower()),None)
                name=next((name for name in AIR_REGIONS if name.lower() in text.lower()),heading or f"UK-AIR zone {zone}")
                if not center:continue
                out.append(Event(id=f"ukair:{zone}",type="airquality",name=name,subtitle=f"DAQI {idx} • {status}",lat=center[0],lon=center[1],category="Air quality",status=status,severity=str(idx),reason="Current DEFRA Daily Air Quality Index regional summary",source="DEFRA UK-AIR",source_id=str(zone),official_url=f"{self.settings.uk_air_base_url}?zone_id={zone}",interesting=idx>=7,last_updated=now))
            except Exception:continue
        return out
    def message(self,e,v):return f"DEFRA UK-AIR • {len(e):,} current regional DAQI summaries"


# ---------- TfL bike points ----------
def _nested_latlon(value):
    if isinstance(value, dict):
        lat=_num(value.get("lat") if "lat" in value else value.get("latitude")); lon=_num(value.get("lon") if "lon" in value else value.get("longitude"))
        if lat is not None and lon is not None and -90<=lat<=90 and -180<=lon<=180:
            return lat,lon
        for child in value.values():
            found=_nested_latlon(child)
            if found:return found
    elif isinstance(value,list):
        for child in value:
            found=_nested_latlon(child)
            if found:return found
    return None

class TfLLiveCollector(SnapshotThread):
    source_name="tfl_live";source_label="TfL cycle hire";event_source="TfL cycle hire"
    def __init__(self,s,state):super().__init__(s,state,s.tfl_poll_seconds)
    def _params(self):return {"app_key":self.settings.tfl_app_key} if self.settings.tfl_app_key else {}
    def fetch_once(self):
        out=[];now=datetime.now(timezone.utc)
        r=self._session.get(f"{self.settings.tfl_api_base}/BikePoint/",params=self._params(),timeout=25);r.raise_for_status()
        for p in r.json() if isinstance(r.json(),list) else []:
            lat,lon=_num(p.get("lat")),_num(p.get("lon"));
            if lat is None or lon is None:continue
            props={x.get("key"):x.get("value") for x in p.get("additionalProperties",[]) if isinstance(x,dict)}
            bikes=_int(props.get("NbBikes")) or 0; ebikes=_int(props.get("NbEBikes")) or 0; docks=_int(props.get("NbEmptyDocks")) or 0
            out.append(Event(id=f"tfl-bike:{p.get('id')}",type="bike",name=str(p.get("commonName") or "Cycle hire"),subtitle=f"{bikes} bikes • {ebikes} e-bikes • {docks} empty docks",lat=lat,lon=lon,category="Cycle hire",status=f"{bikes+ebikes} available",source="TfL Cycle Hire",source_id=str(p.get("id")),official_url="https://tfl.gov.uk/modes/cycling/santander-cycles",interesting=False,last_updated=now))
        return out
    def message(self,e,v):
        bikes=sum(x.type=="bike" for x in e)
        return f"TfL live • {bikes:,} cycle points"


# ---------- GBFS shared mobility ----------
class GBFSCollector(SnapshotThread):
    source_name="gbfs";source_label="GBFS shared mobility";event_source="GBFS shared mobility"
    def __init__(self,s,state):super().__init__(s,state,s.gbfs_poll_seconds);self._feeds=[]
    def _discover(self):
        r=self._session.get(self.settings.gbfs_systems_url,timeout=25);r.raise_for_status();reader=csv.DictReader(io.StringIO(r.text));feeds=[]
        for row in reader:
            country=(row.get("Country Code") or row.get("country_code") or "").upper()
            if country not in {"GB","UK"}:continue
            url=row.get("Auto-Discovery URL") or row.get("Auto Discovery URL") or row.get("URL")
            if url and "tfl" not in url.lower():feeds.append((row.get("Name") or row.get("System ID") or "GBFS",url))
        self._feeds=feeds[:self.settings.gbfs_max_systems]
    def fetch_once(self):
        if not self._feeds:self._discover()
        out=[];now=datetime.now(timezone.utc)
        for system,url in self._feeds:
            try:
                r=self._session.get(url,timeout=15);r.raise_for_status();p=r.json();data=p.get("data") or {};lang=next(iter(data.values())) if data and all(isinstance(v,dict) for v in data.values()) else data
                feeds=lang.get("feeds",[]) if isinstance(lang,dict) else []
                fmap={f.get("name"):f.get("url") for f in feeds if isinstance(f,dict)}
                info_url=fmap.get("station_information"); status_url=fmap.get("station_status")
                if not info_url or not status_url:continue
                ir=self._session.get(info_url,timeout=15).json();sr=self._session.get(status_url,timeout=15).json()
                idata=ir.get("data") or {};sdata=sr.get("data") or {};stations=idata.get("stations",[]) if isinstance(idata,dict) else [];statuses=sdata.get("stations",[]) if isinstance(sdata,dict) else []
                stat={str(x.get("station_id")):x for x in statuses if isinstance(x,dict)}
                for st in stations:
                    lat,lon=_num(st.get("lat")),_num(st.get("lon")); sid=str(st.get("station_id") or "")
                    if lat is None or lon is None or not (49<=lat<=61 and -9<=lon<=3):continue
                    ss=stat.get(sid,{})
                    avail=_int(ss.get("num_vehicles_available") or ss.get("num_bikes_available")) or 0; docks=_int(ss.get("num_docks_available")) or 0
                    out.append(Event(id=f"gbfs:{_hash(system,sid)}",type="bike",name=str(st.get("name") or system),subtitle=f"{system} • {avail} available • {docks} docks",lat=lat,lon=lon,category="Shared mobility",status=f"{avail} available",source="GBFS shared mobility",source_id=f"{system}:{sid}",official_url=url,interesting=False,last_updated=now))
                    if len(out)>=self.settings.gbfs_max_points:return out
            except Exception:continue
        return out
    def message(self,e,v):return f"GBFS • {len(self._feeds):,} UK systems checked • {len(e):,} live points"


# ---------- NOAA space weather ----------
class SpaceWeatherCollector(SnapshotThread):
    source_name="space_weather";source_label="NOAA space weather";event_source="NOAA Space Weather"
    def __init__(self,s,state):super().__init__(s,state,s.space_weather_poll_seconds)
    def fetch_once(self):
        r=self._session.get(self.settings.noaa_kp_url,timeout=20);r.raise_for_status();p=r.json();rows=p if isinstance(p,list) else p.get("data",[])
        if not rows:return []
        last=rows[-1]; kp=_num(last.get("kp_index") or last.get("Kp") or last.get("kp")); now=datetime.now(timezone.utc)
        if kp is None:return []
        # One regional indicator rather than inventing an aurora footprint when Kp is quiet.
        if kp < 4:return []
        status="Strong geomagnetic activity" if kp>=7 else "Aurora potential" if kp>=5 else "Elevated geomagnetic activity"
        return [Event(id="space:uk-aurora",type="space",name="UK aurora / space-weather watch",subtitle=f"Planetary Kp {kp:.1f}",lat=58.3,lon=-4.2,category="Space weather",status=status,severity=f"Kp {kp:.1f}",reason="Regional indicator only — not an exact aurora position. Check NOAA/Met Office space-weather guidance before travelling.",source="NOAA Space Weather",source_id="kp",official_url="https://www.swpc.noaa.gov/",interesting=kp>=5,last_updated=now)]
    def message(self,e,v):return "NOAA space weather verified • aurora watch active" if e else "NOAA space weather verified • no UK aurora-level trigger right now"


# ---------- ISS live orbital position from CelesTrak TLE ----------
class ISSCollector(SnapshotThread):
    source_name="iss";source_label="ISS orbital tracker";event_source=""
    def __init__(self,s,state):
        super().__init__(s,state,s.iss_poll_seconds);self._sat=None;self._tle_at=0.0
    def _refresh(self):
        from sgp4.api import Satrec
        r=self._session.get(self.settings.iss_tle_url,timeout=20);r.raise_for_status();lines=[x.strip() for x in r.text.splitlines() if x.strip()]
        l1=next(x for x in lines if x.startswith("1 "));l2=next(x for x in lines if x.startswith("2 "));self._sat=Satrec.twoline2rv(l1,l2);self._tle_at=time.time()
    @staticmethod
    def _gmst(jd):
        T=(jd-2451545.0)/36525.0; deg=280.46061837+360.98564736629*(jd-2451545.0)+0.000387933*T*T-T*T*T/38710000.0;return math.radians(deg%360)
    def fetch_once(self):
        from sgp4.api import jday
        if self._sat is None or time.time()-self._tle_at>self.settings.iss_tle_refresh_seconds:self._refresh()
        now=datetime.now(timezone.utc);jd,fr=jday(now.year,now.month,now.day,now.hour,now.minute,now.second+now.microsecond/1e6);e,r,_v=self._sat.sgp4(jd,fr)
        if e!=0:raise RuntimeError(f"SGP4 propagation error {e}")
        x,y,z=r;theta=self._gmst(jd+fr);xe=x*math.cos(theta)+y*math.sin(theta);ye=-x*math.sin(theta)+y*math.cos(theta);rr=math.sqrt(x*x+y*y+z*z);lat=math.degrees(math.asin(z/rr));lon=((math.degrees(math.atan2(ye,xe))+540)%360)-180;alt=rr-6371.0
        # Keep it useful around UK/Europe; otherwise no off-screen marker, but status still proves propagation.
        if not (30<=lat<=75 and -35<=lon<=35):return []
        return [Vehicle(id="satellite:iss",type="satellite",name="International Space Station",subtitle=f"~{alt:.0f} km altitude • CelesTrak TLE",lat=lat,lon=lon,status="Orbiting",position_method="estimated",position_label="SGP4 orbital position from CelesTrak elements",source="CelesTrak",source_id="25544",interesting=True,last_updated=now)]
    def message(self,e,v):return "ISS orbital elements live • currently over Europe/nearby" if v else "ISS orbital elements live • ISS currently outside the UK/Europe map window"


# ---------- Power outages ----------
def _parse_outage_csv(text: str, source: str) -> list[Event]:
    rows=list(csv.DictReader(io.StringIO(text)));out=[];now=datetime.now(timezone.utc)
    for row in rows:
        lat=_num(_pick(row,"latitude","lat","y"));lon=_num(_pick(row,"longitude","lon","lng","long","x"))
        if lat is None or lon is None or not (49<=lat<=61 and -9<=lon<=3):continue
        place=str(_pick(row,"location","area","town","postcode","postcodearea") or "Power outage")
        customers=_int(_pick(row,"customersoff","customersaffected","numberofcustomers","customer_count")); status=str(_pick(row,"status","state") or "Live outage")
        reason=str(_pick(row,"reason","cause","description") or "")
        ident=str(_pick(row,"incidentid","reference","id") or _hash(source,place,lat,lon))
        subtitle=" • ".join([x for x in [f"{customers:,} customers" if customers is not None else None,status] if x])
        out.append(Event(id=f"outage:{_hash(source,ident)}",type="outage",name=place,subtitle=subtitle,lat=lat,lon=lon,category="Power outage",status=status,reason=reason[:900] or None,source=source,source_id=ident,official_url="https://www.powercut105.com/",interesting=(customers or 0)>=500,last_updated=now))
    return out


class PowerOutageCollector(SnapshotThread):
    source_name="power_outages";source_label="UK power outages";event_source="UK power outages"
    def __init__(self,s,state):super().__init__(s,state,s.power_outages_poll_seconds)
    def fetch_once(self):
        out=[]; successful_sources=0; errors=[]
        try:
            r=self._session.get(self.settings.nged_powercuts_url,timeout=25);r.raise_for_status();out.extend(_parse_outage_csv(r.text,"National Grid Electricity Distribution"));successful_sources+=1
        except Exception as exc:errors.append(f"NGED {type(exc).__name__}")
        try:
            m=self._session.get(self.settings.ssen_outage_metadata_url,timeout=20);m.raise_for_status();raw=json.dumps(m.json());urls=re.findall(r'https?://[^"\\\s]+',raw)
            url=next((u.rstrip('.,') for u in urls if any(x in u.lower() for x in ('.csv','download'))),None)
            if url:
                rr=self._session.get(url,timeout=25);rr.raise_for_status();out.extend(_parse_outage_csv(rr.text,"SSEN Distribution"));successful_sources+=1
            else:
                errors.append("SSEN download URL missing")
        except Exception as exc:errors.append(f"SSEN {type(exc).__name__}")
        if successful_sources == 0:
            raise RuntimeError("All power-outage providers failed: " + "; ".join(errors))
        # normalize to one replace source while preserving provider in text
        for e in out:
            e.reason=(f"Provider: {e.source}. " + (e.reason or "")).strip(); e.source="UK power outages"
        return out
    def message(self,e,v):return f"UK outage data • {len(e):,} geocoded live power cuts from open DNO feeds"


# ---------- Traffic Scotland gritters ----------
class GritterCollector(SnapshotThread):
    source_name="gritters";source_label="Traffic Scotland gritters";event_source=""
    def __init__(self,s,state):super().__init__(s,state,s.gritter_poll_seconds)
    def fetch_once(self):
        now=datetime.now(timezone.utc)
        if now.month not in {11,12,1,2,3}:return []
        meta=f"https://www.arcgis.com/sharing/rest/content/items/{self.settings.gritter_arcgis_item}?f=json";r=self._session.get(meta,timeout=15);r.raise_for_status();p=r.json();url=p.get("url")
        if not url:
            d=self._session.get(f"https://www.arcgis.com/sharing/rest/content/items/{self.settings.gritter_arcgis_item}/data?f=json",timeout=15).json();raw=json.dumps(d);m=re.search(r'https?://[^" ]+/FeatureServer',raw);url=m.group(0) if m else None
        if not url:return []
        q=f"{url.rstrip('/')}/0/query";rr=self._session.get(q,params={"where":"1=1","outFields":"*","f":"geojson"},timeout=25);rr.raise_for_status();features=rr.json().get("features",[]);out=[]
        for f in features:
            geom=f.get("geometry") or {};coords=geom.get("coordinates") or []
            if len(coords)<2:continue
            lon,lat=_num(coords[0]),_num(coords[1]);props=f.get("properties") or {}
            if lat is None or lon is None:continue
            name=str(_pick(props,"name","vehicle","call_sign","fleetno","registration") or "Traffic Scotland gritter")
            ident=str(_pick(props,"objectid","id","vehicleid","registration") or _hash(lat,lon,name))
            speed=_num(_pick(props,"speed","velocity"));heading=_num(_pick(props,"heading","bearing"))
            out.append(Vehicle(id=f"gritter:{ident}",type="gritter",name=name,subtitle="Live winter-service vehicle",lat=lat,lon=lon,operator="Traffic Scotland",status="Active",speed=speed,speed_unit="mph" if speed is not None else None,bearing=heading,position_method="gps",position_label="Traffic Scotland near-real-time vehicle position",source="Traffic Scotland Gritters",source_id=ident,interesting=True,last_updated=now))
        return out
    def message(self,e,v):
        if datetime.now(timezone.utc).month not in {11,12,1,2,3}:return "Seasonal tracker ready • official live gritters operate Nov–Mar"
        return f"Traffic Scotland • {len(v):,} live winter-service vehicles"
