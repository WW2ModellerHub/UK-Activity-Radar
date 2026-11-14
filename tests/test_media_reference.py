"""Trains, buses and planes get an example photo in their operator's colours
when no photo of the exact vehicle exists, and never another firm's."""
from fastapi.testclient import TestClient

import app.main as main
from app.media import WikimediaLookup
from app.media_reference import airline_for, bus_group, reference_for
from app.models import Vehicle
from app.state import LiveState


def _page(pageid, title, description="", licence="CC BY-SA 4.0", width=1600, height=1000):
    return {
        "pageid": pageid, "title": f"File:{title}",
        "imageinfo": [{
            "url": f"https://upload.wikimedia.org/{pageid}.jpg", "thumburl": f"https://upload.wikimedia.org/thumb/{pageid}.jpg",
            "width": width, "height": height,
            "extmetadata": {"LicenseShortName": {"value": licence}, "ImageDescription": {"value": description},
                            "Artist": {"value": "A Photographer"}},
        }],
    }


class FakeCommons:
    def __init__(self, results):
        self.results = results
        self.queries = []

    def get(self, url, params=None, timeout=None):
        query = params["gsrsearch"].replace(" filetype:bitmap", "")
        self.queries.append(query)
        pages = self.results.get(query, [])

        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {"query": {"pages": pages}}
        return Response()


def _lookup(results):
    lookup = WikimediaLookup("https://commons.example/api", enabled=True)
    lookup._session = FakeCommons(results)
    return lookup


def test_a_routine_train_shows_its_operators_train_with_a_clear_label():
    train = Vehicle(id="nr:1", type="train", name="1A23", lat=51.5, lon=-1.0, source="Network Rail",
                    operator="Great Western Railway")
    lookup = _lookup({"GWR Class 800": [
        _page(1, "Southern 377 at Brighton.jpg", "Southern Class 377"),
        _page(2, "GWR 800 004 at Reading.jpg", "GWR Class 800 IET at Reading station"),
    ]})
    result = lookup.lookup(train)
    assert result["ok"] and result["title"] == "GWR 800 004 at Reading.jpg"
    assert result["match_kind"] == "type_reference"
    assert result["reference_label"] == "Example of a Great Western Railway train — not this exact one"


def test_a_photo_of_another_operator_or_with_a_non_commercial_licence_is_never_shown():
    bus = Vehicle(id="bus:AMID:1", type="bus", name="Bus 7", lat=52.4, lon=-1.5, source="BODS", operator="AMID")
    lookup = _lookup({"Arriva bus": [
        _page(1, "Stagecoach bus in Leicester.jpg", "A Stagecoach double-decker"),
        _page(2, "Arriva Midlands bus.jpg", "Arriva Midlands Enviro400 bus", licence="CC BY-NC 2.0"),
    ]})
    assert lookup.lookup(bus)["ok"] is False


def test_a_bus_shows_its_groups_colours():
    bus = Vehicle(id="bus:SCMN:1", type="bus", name="Bus 50", lat=53.4, lon=-2.2, source="BODS", operator="SCMN")
    lookup = _lookup({"Stagecoach bus": [_page(5, "Stagecoach Manchester Enviro400 MMC.jpg", "Stagecoach double-decker bus in Manchester")]})
    result = lookup.lookup(bus)
    assert result["ok"] and result["reference_label"] == "Example of a Stagecoach bus — not this exact one"


def test_an_unknown_operator_gets_no_guess():
    assert bus_group("ZZZZ") is None
    assert reference_for("train", "Some Heritage Railway") is None
    assert reference_for("bus", "Unknown operator") is None


def test_steam_railtours_never_get_an_everyday_train_photo():
    lookup = _lookup({"GWR Class 800": [_page(2, "GWR 800 004.jpg", "GWR Class 800")]})
    steam = Vehicle(id="nr:2", type="train", name="1Z80", lat=51.5, lon=-1.0, source="Network Rail",
                    operator="Great Western Railway", cool_subtype="steam", cool_candidate=True)
    assert lookup.lookup(steam)["ok"] is False
    assert lookup._session.queries == []


def test_an_airliner_shows_the_type_in_the_airlines_colours_then_falls_back_to_the_type():
    assert airline_for("RYR12AB") == "Ryanair" and airline_for("G-ABCD") is None
    lookup = _lookup({
        "Ryanair Boeing 737-800": [_page(7, "EI-DCL Ryanair Boeing 737-8AS.jpg", "Ryanair Boeing 737-800 landing")],
        "Airbus A320 aircraft": [_page(8, "A320 at Heathrow.jpg", "Airbus A320 landing")],
    })
    ryanair = lookup.reference("aircraft", None, "Boeing 737-800", "RYR12AB")
    assert ryanair["ok"] and ryanair["reference_label"].startswith("Ryanair Boeing 737-800")
    other = lookup.reference("aircraft", None, "Airbus A320", "EZY12")
    assert other["ok"] and other["title"] == "A320 at Heathrow.jpg"
    assert other["reference_label"] == "Airbus A320 — example of this type, not this exact aircraft"


def test_planes_the_server_does_not_keep_can_still_get_a_photo(monkeypatch):
    calls = []
    monkeypatch.setattr(main.media_lookup, "reference", lambda kind, operator, model, callsign: calls.append((kind, model, callsign)) or {"ok": False})
    monkeypatch.setattr(main, "state", LiveState())
    client = TestClient(main.app)
    assert client.get("/api/media/reference", params={"kind": "aircraft", "model": "Airbus A320", "callsign": "EZY12"}).status_code == 200
    assert calls == [("aircraft", "Airbus A320", "EZY12")]
    assert client.get("/api/media/reference", params={"kind": "ship"}).status_code == 400
