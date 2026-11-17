"""Bus photos are off; bus place names read properly."""
from fastapi.testclient import TestClient

import app.main as main
from app.collectors.bods import _display_place_name
from app.models import Vehicle
from app.state import LiveState


def test_no_bus_photo_is_looked_up_or_offered(monkeypatch):
    state = LiveState()
    state.upsert(Vehicle(id="bus:AMID:3149", type="bus", name="Bus LC14", route="LC14", vehicle_ref="3149",
                         lat=52.54, lon=-1.37, source="BODS", operator="Arriva Midlands"))
    monkeypatch.setattr(main, "state", state)
    asked = []
    monkeypatch.setattr(main.media_lookup, "lookup", lambda item: asked.append(item) or {"ok": True})
    client = TestClient(main.app)
    assert client.get("/api/activity/bus:AMID:3149/media").json() == {"ok": False, "reason": "no_bus_photos"}
    assert client.get("/api/media/reference", params={"kind": "bus", "operator": "AMID"}).status_code == 400
    assert asked == []
    assert not any(kind == "bus" for kind, _, _ in main._common_photo_plans())


def test_bus_feed_place_names_read_properly():
    assert _display_place_name("M_S") == "M&S"            # was "M's"
    assert _display_place_name("M_s") == "M&S"
    assert _display_place_name("B_Q_Hinckley") == "B&Q Hinckley"
    assert _display_place_name("St_Margaret_s") == "St Margaret's"
    assert _display_place_name("Leicester_St_Margaret_s") == "Leicester St Margaret's"
