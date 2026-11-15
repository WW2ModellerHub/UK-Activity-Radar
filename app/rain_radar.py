"""Rain radar for the UK and Ireland from the EUMETNET OPERA composite.

OPERA joins the national weather radars of Europe (including the Met
Office's and Met Éireann's) into one rain-rate picture every 15 minutes, on
a 2 km grid. EUMETNET publishes the composites openly under CC BY 4.0, which
allows commercial use with attribution, through the Open Radar Data API.

This keeps the last two hours of frames. Each is cut to the UK and Ireland,
reprojected from the composite's Lambert equal-area grid to the map's Web
Mercator, coloured by rain rate and kept as a transparent PNG for the map
to lay over the base map.
"""
from __future__ import annotations

import io
import logging
import math
import threading
import time
from datetime import datetime, timedelta, timezone

import numpy as np
import requests

log = logging.getLogger(__name__)

API = "https://api.meteogate.eu/eu-eumetnet-weather-radar/collections/observations/locations/0-20010-0-OPERA"
ATTRIBUTION = "Rain radar: EUMETNET OPERA, CC BY 4.0"
LICENCE_URL = "https://creativecommons.org/licenses/by/4.0/"
SOURCE_URL = "https://www.eumetnet.eu/observations/weather-radar-network/"

# The UK and Ireland with a little sea around them (west, south, east, north).
BOUNDS = (-11.5, 49.4, 2.6, 61.2)
WIDTH = 900          # pixels; about 1.7 km each, near the composite's 2 km
KEEP_FRAMES = 9      # two hours at 15-minute steps
POLL_SECONDS = 300

# Rain rate (mm/h) -> colour, the usual radar scale from drizzle to downpour.
SCALE = [
    (0.1, (154, 206, 255, 150)),
    (0.5, (78, 157, 247, 175)),
    (1.0, (31, 105, 222, 195)),
    (2.0, (40, 175, 90, 205)),
    (4.0, (245, 212, 40, 215)),
    (8.0, (245, 140, 30, 225)),
    (16.0, (220, 40, 40, 230)),
    (32.0, (190, 40, 190, 235)),
]

# GRS80/WGS84 ellipsoid, for the Lambert azimuthal equal-area projection.
_A = 6378137.0
_E2 = 0.0066943799901413165
_E = math.sqrt(_E2)


def _q(sin_phi):
    return (1 - _E2) * (sin_phi / (1 - _E2 * sin_phi ** 2)
                        - (1 / (2 * _E)) * np.log((1 - _E * sin_phi) / (1 + _E * sin_phi)))


def laea_forward(lon, lat, lon0: float, lat0: float, x0: float = 0.0, y0: float = 0.0):
    """Ellipsoidal Lambert azimuthal equal-area (Snyder 1987, eq. 24-27)."""
    lon = np.radians(np.asarray(lon, dtype=float))
    lat = np.radians(np.asarray(lat, dtype=float))
    lam0, phi1 = math.radians(lon0), math.radians(lat0)
    qp = float(_q(np.float64(1.0)))
    rq = _A * math.sqrt(qp / 2)
    beta1 = math.asin(float(_q(np.float64(math.sin(phi1)))) / qp)
    m1 = math.cos(phi1) / math.sqrt(1 - _E2 * math.sin(phi1) ** 2)
    d = _A * m1 / (rq * math.cos(beta1))
    beta = np.arcsin(np.clip(_q(np.sin(lat)) / qp, -1, 1))
    dl = lon - lam0
    b = rq * np.sqrt(2 / (1 + math.sin(beta1) * np.sin(beta) + math.cos(beta1) * np.cos(beta) * np.cos(dl)))
    x = b * d * np.cos(beta) * np.sin(dl)
    y = (b / d) * (math.cos(beta1) * np.sin(beta) - math.sin(beta1) * np.cos(beta) * np.cos(dl))
    return x + x0, y + y0


def _projdef(text: str) -> dict:
    parts = {}
    for token in str(text).split():
        if token.startswith("+") and "=" in token:
            key, value = token[1:].split("=", 1)
            parts[key] = value
    return parts


def _mercator_y(lat):
    return np.log(np.tan(np.pi / 4 + np.radians(lat) / 2))


def output_grid(bounds=BOUNDS, width: int = WIDTH):
    """Lon/lat of each output pixel centre, evenly spaced in Web Mercator."""
    west, south, east, north = bounds
    y_top, y_bottom = _mercator_y(north), _mercator_y(south)
    height = int(round(width * (y_top - y_bottom) / math.radians(east - west)))
    lons = west + (np.arange(width) + 0.5) * (east - west) / width
    ys = y_top - (np.arange(height) + 0.5) * (y_top - y_bottom) / height
    lats = np.degrees(2 * np.arctan(np.exp(ys)) - np.pi / 2)
    return np.meshgrid(lons, lats)


def colour(rate: np.ndarray) -> np.ndarray:
    """RGBA image for a rain-rate grid; no rain and no data are transparent."""
    out = np.zeros(rate.shape + (4,), dtype=np.uint8)
    valid = np.isfinite(rate)
    for threshold, rgba in SCALE:
        out[valid & (rate >= threshold)] = rgba
    return out


def reproject(data: np.ndarray, where: dict, bounds=BOUNDS, width: int = WIDTH) -> np.ndarray:
    """Nearest-neighbour sample of the composite onto the Mercator output grid."""
    proj = _projdef(where["projdef"])
    lon0, lat0 = float(proj.get("lon_0", 0)), float(proj.get("lat_0", 0))
    x0, y0 = float(proj.get("x_0", 0)), float(proj.get("y_0", 0))
    xscale, yscale = float(where["xscale"]), float(where["yscale"])
    ul_x, ul_y = laea_forward(float(where["UL_lon"]), float(where["UL_lat"]), lon0, lat0, x0, y0)
    lons, lats = output_grid(bounds, width)
    x, y = laea_forward(lons, lats, lon0, lat0, x0, y0)
    col = np.floor((x - ul_x) / xscale).astype(int)
    row = np.floor((ul_y - y) / yscale).astype(int)
    inside = (col >= 0) & (col < data.shape[1]) & (row >= 0) & (row < data.shape[0])
    out = np.full(lons.shape, np.nan)
    out[inside] = data[row[inside], col[inside]]
    return out


def decode_odim(blob: bytes) -> tuple[np.ndarray, dict, datetime]:
    """Rain rate (mm/h, NaN where there is no data or no rain) from ODIM HDF5."""
    import h5py

    with h5py.File(io.BytesIO(blob), "r") as handle:
        what = handle["dataset1/data1/what"].attrs
        raw = handle["dataset1/data1/data"][()].astype(float)
        where = {key: (value.decode() if isinstance(value, bytes) else value) for key, value in handle["where"].attrs.items()}
        top = handle["what"].attrs
        stamp = f"{_text(top['date'])}{_text(top['time'])}"
        gain, offset = float(what.get("gain", 1.0)), float(what.get("offset", 0.0))
        nodata, undetect = what.get("nodata"), what.get("undetect")
    rate = raw * gain + offset
    if nodata is not None:
        rate[raw == float(nodata)] = np.nan
    if undetect is not None:
        rate[raw == float(undetect)] = 0.0
    taken = datetime.strptime(stamp[:12], "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
    return rate, where, taken


def _text(value) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def to_png(rgba: np.ndarray) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buffer, "PNG", optimize=True)
    return buffer.getvalue()


class RainRadar:
    """Keeps the latest frames, refreshed in the background."""

    def __init__(self, session: requests.Session | None = None) -> None:
        self._session = session or requests.Session()
        self._session.headers.update({"User-Agent": "UKActivityRadar/1.5 (rain radar)"})
        self._lock = threading.Lock()
        self.frames: dict[str, dict] = {}   # time key -> {"time", "png"}
        self.last_error: str | None = None
        self.checked_at: float | None = None

    def links(self, now: datetime | None = None) -> list[tuple[datetime, str]]:
        now = now or datetime.now(timezone.utc)
        start = now - timedelta(minutes=15 * KEEP_FRAMES + 20)
        response = self._session.get(API, params={
            "datetime": f"{start:%Y-%m-%dT%H:%MZ}/{now:%Y-%m-%dT%H:%MZ}",
            "f": "CoverageJSON", "standard_name": "RATE", "format": "ODIM",
        }, timeout=(5, 20))
        response.raise_for_status()
        found = []
        for link in response.json().get("links") or []:
            href = str(link.get("href") or "")
            if link.get("rel") != "items" or not href.startswith("https://") or not href.endswith("@RATE.h5"):
                continue
            try:
                stamp = href.rsplit("/", 1)[-1].split("@")[1]
                found.append((datetime.strptime(stamp, "%Y%m%dT%H%M").replace(tzinfo=timezone.utc), href))
            except (IndexError, ValueError):
                continue
        return sorted(found)[-KEEP_FRAMES:]

    def refresh(self) -> int:
        """Fetch any frames not yet held; returns how many were added."""
        self.checked_at = time.time()
        try:
            wanted = self.links()
        except (requests.RequestException, ValueError) as exc:
            self.last_error = f"radar list unavailable: {type(exc).__name__}"
            return 0
        added = 0
        for taken, href in wanted:
            key = taken.strftime("%Y%m%dT%H%M")
            if key in self.frames:
                continue
            try:
                response = self._session.get(href, timeout=(5, 40))
                response.raise_for_status()
                rate, where, stamped = decode_odim(response.content)
                png = to_png(colour(reproject(rate, where)))
            except Exception as exc:  # one bad frame must not stop the others
                self.last_error = f"radar frame {key} failed: {type(exc).__name__}"
                log.warning(self.last_error)
                continue
            with self._lock:
                self.frames[key] = {"time": stamped.isoformat().replace("+00:00", "Z"), "png": png}
            added += 1
        keep = {taken.strftime("%Y%m%dT%H%M") for taken, _ in wanted}
        with self._lock:
            for key in [k for k in self.frames if k not in keep and len(self.frames) > KEEP_FRAMES]:
                self.frames.pop(key, None)
            for key in sorted(self.frames)[:-KEEP_FRAMES]:
                self.frames.pop(key, None)
        if added:
            self.last_error = None
        return added

    def index(self) -> dict:
        with self._lock:
            keys = sorted(self.frames)
            frames = [{"key": key, "time": self.frames[key]["time"]} for key in keys]
        west, south, east, north = BOUNDS
        return {
            "available": bool(frames), "frames": frames,
            "bounds": [[south, west], [north, east]],
            "attribution": ATTRIBUTION, "licence_url": LICENCE_URL, "source_url": SOURCE_URL,
            "scale": [{"mm_per_hour": t, "colour": f"rgba({r},{g},{b},{a / 255:.2f})"} for t, (r, g, b, a) in SCALE],
            "error": None if frames else self.last_error,
        }

    def png(self, key: str) -> bytes | None:
        with self._lock:
            frame = self.frames.get(key)
            return frame["png"] if frame else None
