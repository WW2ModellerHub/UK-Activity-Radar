from app.cameras import TrafficCameraService


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _jamcam(camera_id="JamCams_00001.001", *, lat=51.51, lon=-0.12):
    return {
        "id": camera_id,
        "commonName": "A Test Road",
        "lat": lat,
        "lon": lon,
        "additionalProperties": [
            {"key": "available", "value": "true"},
            {"key": "view", "value": "North"},
            {"key": "imageUrl", "value": "https://s3-eu-west-1.amazonaws.com/jamcams.tfl.gov.uk/00001.001.jpg"},
            {"key": "videoUrl", "value": "https://s3-eu-west-1.amazonaws.com/jamcams.tfl.gov.uk/00001.001.mp4"},
        ],
    }


def test_jamcams_are_bounded_and_cached(monkeypatch):
    service = TrafficCameraService(cache_seconds=180)
    calls = []

    def get(*args, **kwargs):
        calls.append((args, kwargs))
        return _Response([_jamcam(), _jamcam("outside", lat=53.0, lon=-2.0)])

    monkeypatch.setattr(service._session, "get", get)
    first = service.for_bounds(west=-0.5, south=51.0, east=0.5, north=52.0)
    second = service.for_bounds(west=-0.5, south=51.0, east=0.5, north=52.0)
    assert [row["id"] for row in first["cameras"]] == ["tfl:JamCams_00001.001"]
    assert first["cameras"][0]["image_url"].endswith("00001.001.jpg")
    assert second["stale"] is False
    assert len(calls) == 1


def test_nearest_camera_obeys_distance_limit(monkeypatch):
    service = TrafficCameraService()
    monkeypatch.setattr(service._session, "get", lambda *a, **k: _Response([_jamcam()]))
    nearby = service.nearest(lat=51.500, lon=-0.120, max_km=5)
    assert nearby["camera"]["distance_km"] < 2
    far_away = service.nearest(lat=55.95, lon=-3.19, max_km=5)
    assert far_away["camera"] is None


def test_untrusted_media_urls_are_rejected():
    row = _jamcam()
    for prop in row["additionalProperties"]:
        if prop["key"] == "imageUrl":
            prop["value"] = "https://example.invalid/not-a-camera.jpg"
        if prop["key"] == "videoUrl":
            prop["value"] = "http://jamcams.tfl.gov.uk/insecure.mp4"
    assert TrafficCameraService._normalise(row) is None


def test_cached_cameras_are_returned_stale_after_provider_failure(monkeypatch):
    service = TrafficCameraService(cache_seconds=30)
    monkeypatch.setattr(service._session, "get", lambda *a, **k: _Response([_jamcam()]))
    service.for_bounds(west=-1, south=50, east=1, north=52)
    service._expires = 0

    def fail(*args, **kwargs):
        raise TimeoutError("offline")

    monkeypatch.setattr(service._session, "get", fail)
    payload = service.for_bounds(west=-1, south=50, east=1, north=52)
    assert payload["stale"] is True
    assert len(payload["cameras"]) == 1
