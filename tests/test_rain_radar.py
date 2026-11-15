"""Rain radar: the open EUMETNET OPERA composite, cut to the UK and Ireland."""
import io

import h5py
import numpy as np
from fastapi.testclient import TestClient

import app.main as main
from app.rain_radar import BOUNDS, RainRadar, colour, decode_odim, laea_forward, output_grid, reproject

PROJ = "+proj=laea +lat_0=55.0 +lon_0=10.0 +x_0=1950000.0 +y_0=-2100000.0 +units=m +ellps=WGS84"
# Corner coordinates as published in a real OPERA composite file.
WHERE = {"projdef": PROJ, "xsize": 1900, "ysize": 2200, "xscale": 2000.0, "yscale": 2000.0,
         "LL_lon": -10.4345768386404, "LL_lat": 31.7462153182675, "UL_lon": -39.5357864125034, "UL_lat": 67.0228327624372,
         "UR_lon": 57.8119647501499, "UR_lat": 67.6210371071631, "LR_lon": 29.421038635578, "LR_lat": 31.987650276733}


def test_the_projection_puts_the_published_corners_on_the_grid_corners():
    args = (10.0, 55.0, 1950000.0, -2100000.0)
    corners = {name: laea_forward(WHERE[f"{name}_lon"], WHERE[f"{name}_lat"], *args) for name in ("UL", "UR", "LL", "LR")}
    assert np.allclose(corners["UL"], (0, 0), atol=1)
    assert np.allclose(corners["UR"], (3_800_000, 0), atol=1)
    assert np.allclose(corners["LL"], (0, -4_400_000), atol=1)
    assert np.allclose(corners["LR"], (3_800_000, -4_400_000), atol=1)


def _odim(rate: np.ndarray) -> bytes:
    buffer = io.BytesIO()
    with h5py.File(buffer, "w") as handle:
        handle.create_group("what").attrs.update({"date": b"20261115", "time": b"121500", "object": b"COMP"})
        handle.create_group("where").attrs.update({key: (value.encode() if isinstance(value, str) else value) for key, value in WHERE.items()})
        data = handle.create_group("dataset1/data1")
        data.create_dataset("data", data=rate)
        data.create_group("what").attrs.update({"gain": 1.0, "offset": 0.0, "nodata": -9999000.0, "undetect": -8888000.0})
    return buffer.getvalue()


def _rain_at(lon: float, lat: float, value: float) -> np.ndarray:
    """A composite with no rain except one 2 km cell at a place."""
    grid = np.full((2200, 1900), -8888000.0)
    x, y = laea_forward(lon, lat, 10.0, 55.0, 1950000.0, -2100000.0)
    grid[int(-y // 2000), int(x // 2000)] = value
    return grid


def test_rain_lands_in_the_right_place_on_the_map():
    rate, where, taken = decode_odim(_odim(_rain_at(-1.47, 52.54, 6.0)))   # Hinckley
    assert taken.isoformat() == "2026-11-15T12:15:00+00:00"
    out = reproject(rate, where)
    lons, lats = output_grid()
    wet = np.argwhere(out > 1)
    assert len(wet) >= 1
    row, col = wet[len(wet) // 2]
    assert abs(lons[row, col] - -1.47) < 0.05 and abs(lats[row, col] - 52.54) < 0.05
    assert np.nanmax(out[np.isfinite(out) & (out < 1)]) == 0.0   # undetected rain reads as dry, not missing


def test_colours_follow_the_rain_rate_and_dry_is_clear():
    rgba = colour(np.array([[np.nan, 0.0, 0.3, 3.0, 50.0]]))
    assert rgba[0, 0, 3] == 0 and rgba[0, 1, 3] == 0
    assert tuple(rgba[0, 2, :3]) == (154, 206, 255)
    assert tuple(rgba[0, 3, :3]) == (40, 175, 90)
    assert tuple(rgba[0, 4, :3]) == (190, 40, 190)


class _Session:
    def __init__(self, blob):
        self.blob, self.headers, self.calls = blob, {}, []

    def get(self, url, params=None, timeout=None):
        self.calls.append(url)
        blob = self.blob

        class Response:
            content = blob

            def raise_for_status(self):
                return None

            def json(self):
                return {"links": [
                    {"rel": "license", "href": "https://creativecommons.org/licenses/by/4.0/"},
                    {"rel": "items", "href": "https://object-store.example/OPERA/COMP/OPERA@20261115T1215@0@RATE.h5"},
                    {"rel": "items", "href": "https://object-store.example/OPERA/COMP/OPERA@20261115T1200@0@DBZH.h5"},
                ]}
        return Response()


def test_frames_are_fetched_once_and_served_with_their_credit(monkeypatch):
    radar = RainRadar(session=_Session(_odim(_rain_at(-1.47, 52.54, 6.0))))
    assert radar.refresh() == 1 and radar.refresh() == 0       # the same frame is not fetched twice
    assert sum(url.endswith(".h5") for url in radar._session.calls) == 1
    monkeypatch.setattr(main, "rain_radar", radar)
    client = TestClient(main.app)
    index = client.get("/api/weather/radar").json()
    assert index["available"] and index["frames"] == [{"key": "20261115T1215", "time": "2026-11-15T12:15:00Z"}]
    assert index["attribution"] == "Rain radar: EUMETNET OPERA, CC BY 4.0"
    assert index["bounds"] == [[BOUNDS[1], BOUNDS[0]], [BOUNDS[3], BOUNDS[2]]]
    image = client.get("/api/weather/radar/20261115T1215.png")
    assert image.status_code == 200 and image.content.startswith(b"\x89PNG")
    assert client.get("/api/weather/radar/20261115T1230.png").status_code == 404
    assert client.get("/api/weather/radar/..%2Fsecret.png").status_code == 404
