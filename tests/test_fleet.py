"""Bus fleet history: recording what each bus works and the fleet pages."""
from __future__ import annotations

from datetime import date, datetime, timezone

from fastapi.testclient import TestClient

import app.main as main
from app.fleet_pages import operator_label, vehicle_path, vehicle_slug
from app.history import HistoryStore
from app.models import Vehicle
from app.state import LiveState


def _bus(ref: str, route: str, operator: str = "ARMD", destination: str = "Leicester") -> Vehicle:
    return Vehicle(id=f"bus:{operator}:{ref}", type="bus", name=f"Bus {route}", route=route, vehicle_ref=ref,
                   operator=operator, destination=destination, lat=52.54, lon=-1.37, source="BODS")


def test_each_bus_route_is_recorded_once_per_day_with_first_and_last_seen():
    store = HistoryStore(":memory:")
    day = date.today().isoformat()
    morning = datetime(2026, 11, 22, 8, 0, tzinfo=timezone.utc)
    evening = datetime(2026, 11, 22, 18, 30, tzinfo=timezone.utc)
    store.record_buses([_bus("4407", "158"), _bus("4408", "48")], day=day, now=morning)
    store.record_buses([_bus("4407", "158"), _bus("4407", "X6", destination="Coventry")], day=day, now=evening)

    fleet = store.bus_fleet("ARMD")
    assert [v["vehicle"] for v in fleet] == ["4407", "4408"]
    assert fleet[0]["routes"] == ["158", "X6"]
    work = store.bus_vehicle("ARMD", "4407")[0]["work"]
    assert {(w["route"], w["first"], w["last"]) for w in work} == {("158", "08:00", "18:30"), ("X6", "18:30", "18:30")}
    assert store.bus_operators()[0] == {"operator": "ARMD", "vehicles": 2, "routes": 3}


def test_operator_names_are_learned_then_preferred():
    store = HistoryStore(":memory:")
    assert operator_label("NATX") == "National Express"
    assert operator_label("ZZZZ") == "ZZZZ"
    store.learn_operator_name("ARMD", "Arriva Midlands")
    store.learn_operator_name("XXXX", "XXXX")          # a code is not a name
    assert store.operator_names() == {"ARMD": "Arriva Midlands"}
    assert operator_label("ARMD", store.operator_names()) == "Arriva Midlands"


def test_fleet_pages_list_buses_and_show_one_bus_history(monkeypatch):
    store = HistoryStore(":memory:")
    store.record_buses([_bus("YX13 ABC", "158"), _bus("4408", "48")])
    state = LiveState()
    state.upsert(_bus("YX13 ABC", "158"))
    monkeypatch.setattr(main, "history_store", store)
    monkeypatch.setattr(main, "state", state)
    main._seo_page_cache.clear()
    client = TestClient(main.app)

    assert "/fleet/armd" in client.get("/fleet").text
    fleet = client.get("/fleet/armd")
    assert fleet.status_code == 200 and "YX13 ABC" in fleet.text and "on the road" in fleet.text
    assert vehicle_slug("YX13 ABC") == "yx13-abc" and vehicle_path("ARMD", "YX13 ABC") == "/fleet/armd/yx13-abc"
    bus = client.get("/fleet/armd/yx13-abc")
    assert bus.status_code == 200 and "Bus YX13 ABC" in bus.text and "see it on the live map" in bus.text
    assert client.get("/fleet/armd/nope").status_code == 404
    assert client.get("/fleet/zzzz").status_code == 404
