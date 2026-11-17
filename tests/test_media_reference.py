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
        _page(3, "GWR Class 800 Standard Class Interior.jpg", "Inside a GWR Class 800"),
        _page(4, "GWR Class 800 First Class Half Internal.jpg", "GWR Class 800"),
        _page(2, "GWR 800 004 at Reading.jpg", "GWR Class 800 IET at Reading station"),
    ]})
    result = lookup.lookup(train)
    assert result["ok"] and result["title"] == "GWR 800 004 at Reading"
    assert result["match_kind"] == "type_reference"
    assert result["reference_label"] == "Great Western Railway — example of their trains, not this exact one"


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
    assert result["ok"] and result["reference_label"] == "Stagecoach colours — an example bus, it may show another route"


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
    assert other["ok"] and other["title"] == "A320 at Heathrow"
    assert other["reference_label"] == "Airbus A320 — example of this type, not this exact aircraft"


def test_planes_the_server_does_not_keep_can_still_get_a_photo(monkeypatch):
    calls = []
    monkeypatch.setattr(main.media_lookup, "reference", lambda kind, operator, model, callsign: calls.append((kind, model, callsign)) or {"ok": False})
    monkeypatch.setattr(main, "state", LiveState())
    client = TestClient(main.app)
    assert client.get("/api/media/reference", params={"kind": "aircraft", "model": "Airbus A320", "callsign": "EZY12"}).status_code == 200
    assert calls == [("aircraft", "Airbus A320", "EZY12")]
    assert client.get("/api/media/reference", params={"kind": "ship"}).status_code == 400


def test_a_bus_looks_for_its_own_route_first_then_says_the_route_may_differ():
    from app.media_reference import tidy_title

    bus = Vehicle(id="bus:AMID:1", type="bus", name="Bus 158", route="158", lat=52.4, lon=-1.5, source="BODS", operator="AMID")
    lookup = _lookup({
        "Arriva Midlands bus route 158": [
            _page(1, "Arriva Midlands bus 1580 in Leicester.jpg", "Arriva bus fleet number 1580"),   # 1580 is not route 158
            _page(2, "Arriva Midlands 158 to Nuneaton (geograph 6358807).jpg", "Arriva bus on the 158 at Hinckley"),
        ],
        "Arriva Midlands bus": [_page(3, "An Arriva Midlands bus at Cressage, Shropshire.jpg", "Arriva Midlands bus on route 436")],
    })
    result = lookup.lookup(bus)
    assert result["title"] == "Arriva Midlands 158 to Nuneaton"
    assert result["reference_label"] == "Arriva Midlands bus on route 158 — an example, not this exact bus"
    # No photo of that route: the operator's colours, saying the route may differ.
    other = _lookup({"Arriva Midlands bus": [_page(3, "An Arriva Midlands bus at Cressage, Shropshire.jpg", "Arriva Midlands bus")]}).lookup(
        bus.model_copy(update={"id": "bus:AMID:2", "route": "48"}))
    assert other["title"] == "An Arriva Midlands bus at Cressage, Shropshire"
    assert other["reference_label"] == "Arriva Midlands colours — an example bus, it may show another route"
    assert tidy_title("Scotrail Class 385 train at Gourock - geograph.org.uk - 5247699.jpg") == "Scotrail Class 385 train at Gourock"


def test_a_date_is_not_a_route_and_a_london_bus_is_not_a_midlands_one():
    bus = Vehicle(id="bus:AMID:8", type="bus", name="Bus 8", route="8", lat=52.5, lon=-1.4, source="BODS", operator="AMID")
    lookup = _lookup({
        "Arriva Midlands bus route 8": [
            _page(1, "Arriva London Routemaster bus RML2636, Westminster Bridge, route 159, 8 December 2005.jpg",
                  "Arriva London Routemaster on route 159, 8 December 2005"),
            _page(2, "Arriva Midlands bus 8 December 2019.jpg", "An Arriva Midlands bus photographed on 8 December"),
        ],
        "Arriva Midlands bus": [
            _page(3, "Arriva London bus in Brixton.jpg", "Arriva London double-decker bus"),
            _page(4, "Arriva Midlands Enviro400 in Hinckley.jpg", "Arriva Midlands double-decker bus in Hinckley"),
        ],
    })
    result = lookup.lookup(bus)
    assert result["title"] == "Arriva Midlands Enviro400 in Hinckley"
    assert result["reference_label"] == "Arriva Midlands colours — an example bus, it may show another route"


def test_a_photo_that_names_the_route_is_used_for_that_route():
    bus = Vehicle(id="bus:AMID:8b", type="bus", name="Bus 8", route="8", lat=52.5, lon=-1.4, source="BODS", operator="AMID")
    lookup = _lookup({"Arriva Midlands bus route 8": [
        _page(5, "Arriva Midlands Streetlite on service 8 to Nuneaton.jpg", "Arriva Midlands bus on service 8"),
    ]})
    result = lookup.lookup(bus)
    assert result["reference_label"] == "Arriva Midlands bus on route 8 — an example, not this exact bus"


def test_a_spare_engine_is_not_a_picture_of_a_train():
    from app.media_reference import shows_the_outside
    assert not shows_the_outside("Direct Rail Services spare class 68 engine")
    assert not shows_the_outside("Class 66 power unit on a pallet")
    assert shows_the_outside("DRS Class 68 68033 at Crewe")


def test_photos_ask_wikimedia_for_a_standard_thumbnail_width():
    from app.media import THUMB_WIDTH
    seen = []

    class Recording(FakeCommons):
        def get(self, url, params=None, timeout=None):
            seen.append(params.get("iiurlwidth"))
            return super().get(url, params, timeout)
    lookup = WikimediaLookup("https://commons.example/api", enabled=True)
    lookup._session = Recording({})
    lookup.reference("train", "Great Western Railway")
    assert THUMB_WIDTH == 500 and seen and set(seen) == {500}


def test_example_photos_survive_a_restart_and_can_be_looked_up_ahead(tmp_path):
    results = {"GWR Class 800": [_page(2, "GWR 800 004 at Reading.jpg", "GWR Class 800 IET at Reading station")]}
    first = _lookup(results)
    first.use_disk_cache(tmp_path / "examples.json")
    assert first.warm([("train", "Great Western Railway", None), ("train", "Some Heritage Railway", None)]) == 1
    # A new server process, with no network at all, still has it.
    restarted = WikimediaLookup("https://commons.example/api", enabled=True)
    restarted._session = None  # any network call would fail
    restarted.use_disk_cache(tmp_path / "examples.json")
    again = restarted.reference("train", "Great Western Railway")
    assert again["ok"] and again["title"] == "GWR 800 004 at Reading"


def test_the_exact_vehicle_and_the_example_are_looked_up_side_by_side():
    import threading
    import time as clock

    both_running = threading.Barrier(2, timeout=2)

    class Slow(FakeCommons):
        def get(self, url, params=None, timeout=None):
            both_running.wait()          # fails unless both searches are in flight together
            clock.sleep(0.05)
            return super().get(url, params, timeout)
    bus = Vehicle(id="bus:SCMN:9", type="bus", name="Bus 50", vehicle_ref="15724", lat=53.4, lon=-2.2, source="BODS", operator="SCMN")
    lookup = WikimediaLookup("https://commons.example/api", enabled=True)
    lookup._session = Slow({"Stagecoach bus": [_page(5, "Stagecoach Manchester Enviro400 MMC.jpg", "Stagecoach double-decker bus in Manchester")]})
    assert lookup.lookup(bus)["ok"]


def test_a_plane_photo_comes_back_in_one_request_with_what_the_phone_knows(monkeypatch):
    calls = []
    monkeypatch.setattr(main.media_lookup, "reference", lambda kind, operator, model, callsign: calls.append((kind, operator, model, callsign)) or {"ok": True})
    monkeypatch.setattr(main, "state", LiveState())
    client = TestClient(main.app)
    answer = client.get("/api/activity/aircraft:40837b/media", params={"kind": "aircraft", "model": "Airbus A321neo", "callsign": "EXS95CX", "operator": "Jet2"})
    assert answer.status_code == 200 and answer.json()["ok"]
    assert calls == [("aircraft", "Jet2", "Airbus A321neo", "EXS95CX")]
    # Without the details, an unknown id is still "not found".
    assert client.get("/api/activity/aircraft:40837b/media").status_code == 404


def test_photos_for_a_list_of_planes_are_found_ahead_and_only_once():
    import threading
    release = threading.Event()

    class Waiting(FakeCommons):
        def get(self, url, params=None, timeout=None):
            release.wait(2)
            return super().get(url, params, timeout)
    lookup = WikimediaLookup("https://commons.example/api", enabled=True)
    lookup._session = Waiting({"Ryanair Boeing 737-800": [_page(7, "EI-DCL Ryanair Boeing 737-8AS.jpg", "Ryanair Boeing 737-800 landing")]})
    plans = [("aircraft", None, "Boeing 737-800", "RYR12AB"), ("aircraft", None, "Boeing 737-800", "RYR12AB"), ("aircraft", None, None, "G-ABCD")]
    assert lookup.prefetch(plans) == 1
    # A tap while that search is still running waits for it rather than searching again.
    answer = {}
    tap = threading.Thread(target=lambda: answer.update(lookup.reference("aircraft", None, "Boeing 737-800", "RYR12AB")))
    tap.start()
    release.set()
    tap.join(3)
    assert answer["ok"] and lookup._session.queries == ["Ryanair Boeing 737-800"]
    assert lookup.prefetch(plans) == 0   # already found


def test_a_slow_exact_search_does_not_hold_up_a_ready_example(monkeypatch):
    import time as clock

    import app.media as media
    monkeypatch.setattr(media, "EXACT_WAIT_SECONDS", 0.2)

    class SlowExact(FakeCommons):
        def get(self, url, params=None, timeout=None):
            if "filetype:bitmap" not in params["gsrsearch"]:   # the exact-vehicle search
                clock.sleep(1.0)
            return super().get(url, params, timeout)
    bus = Vehicle(id="bus:SCMN:10", type="bus", name="Bus 50", vehicle_ref="15724", lat=53.4, lon=-2.2, source="BODS", operator="SCMN")
    lookup = WikimediaLookup("https://commons.example/api", enabled=True)
    lookup._session = SlowExact({"Stagecoach bus": [_page(5, "Stagecoach Manchester Enviro400 MMC.jpg", "Stagecoach double-decker bus in Manchester")]})
    started = clock.time()
    assert lookup.lookup(bus)["ok"]
    assert clock.time() - started < 0.8


def test_commons_is_asked_only_for_the_picture_details_used():
    from app.media import META_FIELDS
    seen = []

    class Recording(FakeCommons):
        def get(self, url, params=None, timeout=None):
            seen.append(params.get("iiextmetadatafilter"))
            return super().get(url, params, timeout)
    lookup = WikimediaLookup("https://commons.example/api", enabled=True)
    lookup._session = Recording({})
    lookup.lookup(Vehicle(id="bus:SCMN:11", type="bus", name="Bus 50", vehicle_ref="15724", lat=53.4, lon=-2.2, source="BODS", operator="SCMN"))
    assert seen and set(seen) == {META_FIELDS} and "LicenseShortName" in META_FIELDS


def test_a_hawk_gets_an_example_photo_even_without_a_number_in_its_name():
    from app.media_reference import _model_keys
    assert _model_keys("BAE Systems Hawk") == ("hawk",)
    assert _model_keys("Westland Puma") == ("puma",)
    assert _model_keys("Bell 429") == ("429",)          # a number is enough; "bell" alone is too loose
    lookup = _lookup({"BAE Systems Hawk aircraft": [
        _page(1, "BAE Systems logo.png", "Company logo"),
        _page(2, "RAF Hawk T2 ZK010 at RAF Valley.jpg", "BAE Systems Hawk T2 of the Royal Air Force"),
    ]})
    result = lookup.reference("aircraft", None, "BAE Systems Hawk", "JAVLN14")
    assert result["ok"] and result["title"] == "RAF Hawk T2 ZK010 at RAF Valley"


def test_a_scanned_book_that_mentions_the_fleet_number_is_not_a_photo_of_the_bus():
    bus = Vehicle(id="bus:AMID:3149", type="bus", name="Bus LC14", route="LC14", vehicle_ref="3149",
                  lat=52.54, lon=-1.37, source="BODS", operator="Arriva Midlands")
    pages = {"Arriva Midlands 3149 bus": [
        _page(1, "Geographia (IA A3361092).pdf", "Estrabón Guarino Veronese, 1370-1460. Leaf 3149", licence="Public domain"),
        _page(2, "Arriva Midlands 31490 in Leicester.jpg", "Arriva Midlands bus 31490"),       # another number
        _page(3, "Arriva Midlands 3149 at Hinckley.jpg", "Arriva Midlands Streetlite 3149"),   # no word saying bus
    ]}
    lookup = _lookup(pages)
    lookup.reference = lambda *a, **k: {"ok": False}
    assert lookup.lookup(bus)["ok"] is False
    # A real photo that says bus and carries the number is used.
    pages["Arriva Midlands 3149 bus"].append(_page(4, "Arriva Midlands bus 3149 Hinckley.jpg", "Arriva Midlands bus 3149 on the LC14"))
    found = _lookup(pages)
    found.reference = lambda *a, **k: {"ok": False}
    assert found.lookup(bus)["title"] == "Arriva Midlands bus 3149 Hinckley.jpg"
