from __future__ import annotations

import hashlib
import threading
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

import requests

from ..error_safety import public_error
from ..models import Event, SourceStatus


def _local(tag: str) -> str:
    return tag.rsplit('}', 1)[-1] if '}' in tag else tag


def _text(node: ET.Element, name: str) -> str | None:
    for el in node.iter():
        if _local(el.tag) == name and el.text and el.text.strip():
            return el.text.strip()
    return None


def _float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _dt(v: str | None) -> datetime | None:
    if not v:
        return None
    try:
        d = datetime.fromisoformat(v.replace('Z', '+00:00'))
        return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
    except ValueError:
        return None


def parse_siri_sx(xml_bytes: bytes, *, cancellation: bool = False) -> list[Event]:
    root = ET.fromstring(xml_bytes)
    out: dict[str, Event] = {}
    for el in root.iter():
        if _local(el.tag) != 'PtSituationElement':
            continue
        situation = _text(el, 'SituationNumber') or hashlib.sha1(ET.tostring(el)).hexdigest()[:16]
        summary = _text(el, 'Summary') or ('Bus cancellation' if cancellation else 'Bus disruption')
        description = _text(el, 'Description') or summary
        progress = _text(el, 'Progress') or 'open'
        start = _dt(_text(el, 'StartTime'))
        end = _dt(_text(el, 'EndTime'))
        line = _text(el, 'LineRef')
        stop_name = _text(el, 'StopPointName')

        lat = lon = None
        # Location is documented under AffectedStopPoint in the supplied BODS
        # SIRI-SX contracts. Use the first usable coordinate pair.
        for loc in el.iter():
            if _local(loc.tag) != 'Location':
                continue
            lon = _float(_text(loc, 'Longitude'))
            lat = _float(_text(loc, 'Latitude'))
            if lat is not None and lon is not None:
                break
        if lat is None or lon is None or not (49 <= lat <= 61.5 and -9.5 <= lon <= 3.5):
            continue

        type_name = 'incident'
        source = 'DfT BODS Cancellations' if cancellation else 'DfT BODS Disruptions'
        name = f"Bus cancellation • {line}" if cancellation and line else summary
        if not cancellation and line and line.lower() not in summary.lower():
            name = f"{summary} • {line}"
        subtitle = stop_name or ('Bus cancellation' if cancellation else 'Bus disruption')
        out[str(situation)] = Event(
            id=f"bods-sx:{'cancel' if cancellation else 'disrupt'}:{situation}",
            type=type_name,
            name=name,
            subtitle=subtitle,
            lat=lat,
            lon=lon,
            category='Bus cancellation' if cancellation else 'Bus disruption',
            status=progress,
            reason=description,
            start_time=start,
            end_time=end,
            position_method='static',
            position_label='Affected stop/location from DfT BODS SIRI-SX',
            source=source,
            source_id=str(situation),
            official_url='https://data.bus-data.dft.gov.uk/',
            news_query=f'"{summary}" bus {line or ""}'.strip(),
            social_query=f'"{summary}" bus {line or ""}'.strip(),
            interesting=True,
            last_updated=datetime.now(timezone.utc),
        )
    return list(out.values())


class BODSSXCollector:
    def __init__(self, settings, state):
        self.settings = settings
        self.state = state
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._session = requests.Session()
        self._session.headers.update({'User-Agent': 'UKActivityRadar/0.8 (BODS SIRI-SX consumer)'})

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name='bods-sx', daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)

    def _get(self, url: str) -> bytes:
        r = self._session.get(url, params={'api_key': self.settings.bods_api_key}, timeout=30)
        if r.status_code == 403:
            raise PermissionError('BODS SIRI-SX returned 403; check the Data Consumer API key.')
        r.raise_for_status()
        return r.content

    def _run(self):
        if not self.settings.bods_api_key:
            self.state.set_source(SourceStatus(name='bods_sx', enabled=True, connected=False, mode='setup', message='Bus disruptions/cancellations need the free BODS Data Consumer API key.'))
            return
        self.state.set_source(SourceStatus(name='bods_sx', enabled=True, connected=False, mode='connecting', message='Loading BODS disruptions and cancellations…'))
        while not self._stop.is_set():
            try:
                disruptions = parse_siri_sx(self._get(self.settings.bods_disruptions_url), cancellation=False)
                cancellations = parse_siri_sx(self._get(self.settings.bods_cancellations_url), cancellation=True)
                self.state.replace_events_for_source('DfT BODS Disruptions', disruptions)
                self.state.replace_events_for_source('DfT BODS Cancellations', cancellations)
                total = len(disruptions) + len(cancellations)
                self.state.set_source(SourceStatus(
                    name='bods_sx', enabled=True, connected=True, mode='live' if total else 'ready',
                    message=f'{len(disruptions):,} bus disruptions • {len(cancellations):,} cancellations mapped',
                    last_message_at=datetime.now(timezone.utc), messages_seen=total, movements_mapped=total,
                ))
            except Exception as exc:
                self.state.set_source(SourceStatus(
                    name='bods_sx', enabled=True, connected=False, mode='error',
                    message=f'BODS disruption feed error: {public_error(exc, self.settings.bods_api_key)}',
                ))
            self._stop.wait(self.settings.bods_sx_poll_seconds)
