from __future__ import annotations

import hashlib
import re
import threading
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

from ..models import Event, SourceStatus
from ..timeutils import london_tz

LFB_INCIDENTS = "https://www.london-fire.gov.uk/incidents/"

# Borough-centre fallback coordinates. Incident cards are public but do not
# consistently expose machine-readable lat/lon; we deliberately label these as
# borough-level approximate locations rather than pretending they are exact.
BOROUGH_CENTRES = {
    "barking and dagenham": (51.5607, 0.1557), "barnet": (51.6252, -0.1517),
    "bexley": (51.4549, 0.1505), "brent": (51.5588, -0.2817), "bromley": (51.4039, 0.0198),
    "camden": (51.5455, -0.1628), "croydon": (51.3714, -0.0977), "ealing": (51.5130, -0.3089),
    "enfield": (51.6538, -0.0799), "greenwich": (51.4892, 0.0648), "hackney": (51.5450, -0.0553),
    "hammersmith and fulham": (51.4927, -0.2339), "haringey": (51.5906, -0.1110),
    "harrow": (51.5898, -0.3346), "havering": (51.5812, 0.1837), "hillingdon": (51.5441, -0.4760),
    "hounslow": (51.4746, -0.3680), "islington": (51.5416, -0.1022),
    "kensington and chelsea": (51.5020, -0.1947), "kingston upon thames": (51.4085, -0.3064),
    "lambeth": (51.4607, -0.1163), "lewisham": (51.4452, -0.0209), "merton": (51.4098, -0.2108),
    "newham": (51.5255, 0.0352), "redbridge": (51.5590, 0.0741), "richmond upon thames": (51.4479, -0.3260),
    "southwark": (51.5035, -0.0804), "sutton": (51.3618, -0.1945), "tower hamlets": (51.5099, -0.0059),
    "waltham forest": (51.5908, -0.0134), "wandsworth": (51.4567, -0.1910), "westminster": (51.4975, -0.1357),
    "city of london": (51.5155, -0.0922),
}


def _borough_from_text(text: str) -> str | None:
    lower = text.lower()
    for borough in BOROUGH_CENTRES:
        if borough in lower:
            return borough
    return None


def _parse_dt(text: str) -> datetime | None:
    # LFB pages typically use dd/mm/yyyy h:mm AM/PM.
    london = london_tz()
    for m in re.finditer(r"(\d{1,2}/\d{1,2}/\d{4})\s+(\d{1,2}:\d{2})\s*(AM|PM)", text, re.I):
        try:
            return datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3).upper()}", "%d/%m/%Y %I:%M %p").replace(tzinfo=london).astimezone(timezone.utc)
        except ValueError:
            pass
    return None


def parse_lfb_incidents(html: str) -> list[Event]:
    soup = BeautifulSoup(html, "html.parser")
    events: dict[str, Event] = {}
    now = datetime.now(timezone.utc)
    for a in soup.find_all("a", href=True):
        href = str(a.get("href") or "")
        if "/incidents/" not in href or href.rstrip("/") == "/incidents":
            continue
        title = " ".join(a.stripped_strings).strip()
        if not title or len(title) < 5:
            continue
        # Pull context from the nearest useful parent; this usually includes the
        # incident date, borough and short appliance summary.
        parent = a
        for _ in range(4):
            if parent.parent is None:
                break
            parent = parent.parent
            text = " ".join(parent.stripped_strings)
            if len(text) > len(title) + 20:
                break
        context = " ".join(parent.stripped_strings)
        borough = _borough_from_text(context) or _borough_from_text(title)
        if not borough:
            continue
        lat, lon = BOROUGH_CENTRES[borough]
        started = _parse_dt(context) or now
        absolute = href if href.startswith("http") else "https://www.london-fire.gov.uk" + (href if href.startswith("/") else "/" + href)
        source_id = hashlib.sha1(absolute.encode()).hexdigest()[:16]
        category = "Fire / rescue incident"
        lower = title.lower()
        if "road traffic" in lower or "collision" in lower:
            category = "Road traffic collision"
        elif "rescue" in lower:
            category = "Rescue"
        summary = re.sub(r"\s+", " ", context).strip()
        if len(summary) > 260:
            summary = summary[:257] + "…"
        events[source_id] = Event(
            id=f"lfb:{source_id}", type="incident", name=title, subtitle=f"London Fire Brigade • {borough.title()}",
            lat=lat, lon=lon, category=category, status="Published", reason=summary,
            start_time=started, position_method="static", position_label=f"Approximate borough location ({borough.title()})",
            source="London Fire Brigade", source_id=source_id, official_url=absolute,
            news_query=f'"{title}" London fire', social_query=f'"{title}" LFB',
            interesting=True, last_updated=started,
        )
    return list(events.values())[:30]


class LondonFireCollector:
    def __init__(self, settings, state):
        self.settings = settings
        self.state = state
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "UKActivityRadar/0.8 (public incident consumer)"})

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="london-fire", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)

    def fetch_once(self) -> list[Event]:
        r = self._session.get(self.settings.london_fire_url, timeout=25)
        r.raise_for_status()
        return parse_lfb_incidents(r.text)

    def _run(self):
        self.state.set_source(SourceStatus(name="london_fire", enabled=True, connected=False, mode="connecting", message="Loading London Fire Brigade public incidents…"))
        while not self._stop.is_set():
            try:
                events = self.fetch_once()
                self.state.replace_events_for_source("London Fire Brigade", events)
                self.state.set_source(SourceStatus(
                    name="london_fire", enabled=True, connected=True, mode="live" if events else "ready",
                    message=f"{len(events):,} recent public LFB incidents mapped at borough level" if events else "LFB page responded but no current incident cards were parsed",
                    last_message_at=datetime.now(timezone.utc), messages_seen=len(events), movements_mapped=len(events),
                ))
            except Exception as exc:
                self.state.set_source(SourceStatus(name="london_fire", enabled=True, connected=False, mode="error", message=f"London Fire incident error: {type(exc).__name__}: {exc}"))
            self._stop.wait(self.settings.london_fire_poll_seconds)
