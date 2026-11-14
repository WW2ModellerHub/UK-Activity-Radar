"""Live incidents (crashes, breakdowns, queues) for the area on screen."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.road_impact import describe
from app.state import LiveState
from app.traffic_incidents import TrafficIncidentService, parse_incidents

NOW = datetime.now(timezone.utc)


def _feature(lon, lat, category, delay=None, length=None, description="Queuing traffic", road="M1", incident_id="i1",
             start=None, validity="present"):
    return {"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[lon - 0.01, lat], [lon, lat], [lon + 0.01, lat]]},
            "properties": {"id": incident_id, "iconCategory": category, "magnitudeOfDelay": 3, "events": [{"description": description, "code": 101}],
                           "startTime": (start or NOW - timedelta(minutes=30)).isoformat(), "endTime": None,
                           "from": "J14 Milton Keynes", "to": "J15 Northampton", "length": length, "delay": delay,
                           "roadNumbers": [road], "timeValidity": validity}}


def test_a_queue_and_a_crash_are_described_for_drivers():
    queue, crash = parse_incidents({"incidents": [
        _feature(-0.8, 52.1, 6, delay=900, length=3200, incident_id="q"),
        _feature(-0.7, 52.2, 1, delay=1500, description="Accident", incident_id="c"),
    ]})
    facts = describe(queue)
    assert queue.type == "incident" and facts["title"] == "Queueing traffic · M1"
    assert facts["where"] == "M1, J14 Milton Keynes to J15 Northampton"
    assert facts["impact"] == "Delays of about 15 min · Queue of 3.2 km"
    assert facts["when"] == "Started 30 min ago" and facts["active"] and facts["worth_showing"]
    assert describe(crash)["what"] == "Crash" and describe(crash)["rank"] >= 3


def test_roadworks_go_on_the_roadworks_layer_and_future_items_are_skipped():
    works, = parse_incidents({"incidents": [_feature(-0.8, 52.1, 9, description="Roadworks", incident_id="w"),
                                            _feature(-0.8, 52.1, 6, incident_id="later", validity="future")]})
    assert works.type == "road" and describe(works)["what"] == "Roadworks"


class FakeSession:
    def __init__(self, features):
        self.features, self.calls = features, []

    def get(self, url, params, timeout):
        self.calls.append(params["bbox"])
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"incidents": self.features})


def _service(features, limit=2000):
    settings = SimpleNamespace(tomtom_traffic_api_key="k", tomtom_traffic_base_url="https://api.tomtom.com")
    state = LiveState()
    session = FakeSession(features)
    return TrafficIncidentService(settings, state, session, daily_limit=limit), state, session


def test_cells_are_fetched_once_cached_and_kept_to_their_own_incidents():
    service, state, session = _service([_feature(-0.8, 52.1, 6, delay=600, incident_id="a"), _feature(-1.2, 52.1, 6, delay=600, incident_id="b")])
    assert service.ensure((-0.9, 52.05, -0.6, 52.3)) == 1  # one half-degree cell
    assert service.ensure((-0.9, 52.05, -0.6, 52.3)) == 0  # cached
    ids = [event.id for event in state.all_events()]
    assert ids == ["tomtom:-2,104:a"]  # "b" lies in another cell and is not duplicated here
    assert len(session.calls) == 1


def test_a_national_view_does_not_fetch_and_the_daily_cap_holds():
    service, _state, session = _service([])
    assert service.ensure((-6.0, 50.0, 2.0, 56.0)) == 0 and session.calls == []
    capped, _s, capped_session = _service([], limit=1)
    capped.ensure((-1.0, 52.0, 0.0, 52.4))  # two cells, one allowed
    assert len(capped_session.calls) == 1
