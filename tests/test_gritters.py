from types import SimpleNamespace

from app.collectors.live_world import GritterCollector


class Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def test_gritter_collector_discovers_live_layer_and_uses_arcgis_json(monkeypatch):
    settings = SimpleNamespace(gritter_poll_seconds=120, gritter_arcgis_item="item")
    collector = GritterCollector(settings, SimpleNamespace())
    calls = []

    def get(url, params=None, timeout=None):
        calls.append((url, params))
        if "/sharing/rest/content/items/item" in url:
            return Response({"url": "https://arcgis.test/FeatureServer"})
        if url.endswith("/FeatureServer"):
            return Response({"layers": [{"id": 1, "name": "Vehicle Locations"}]})
        if url.endswith("/FeatureServer/1/query"):
            return Response({"features": [{
                "geometry": {"x": -3.2, "y": 55.9},
                "attributes": {"OBJECTID": 7, "Name": "Gritter 7", "Speed": 21},
            }]})
        raise AssertionError(url)

    monkeypatch.setattr(collector._session, "get", get)
    rows = collector.fetch_once()

    assert len(rows) == 1
    assert rows[0].lat == 55.9
    assert rows[0].lon == -3.2
    query = calls[-1]
    assert query[0].endswith("/FeatureServer/1/query")
    assert query[1]["f"] == "json"
    assert query[1]["outSR"] == "4326"
