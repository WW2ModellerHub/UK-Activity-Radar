from __future__ import annotations

from types import SimpleNamespace

import pytest
import requests

from app.rdm import RailDataMarketplace


class Response:
    def __init__(self, payload, *, status=200):
        self.payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"upstream returned {self.status_code}")

    def json(self):
        return self.payload


class Session:
    def __init__(self, replies):
        self.headers = {}
        self.replies = list(replies)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def settings(**keys):
    values = {f"rdm_{name}_api_key": "" for name in RailDataMarketplace.BASES if name != "rail_replacement"}
    values["raildata_replacement_api_key"] = ""
    values.update(rdm_timeout_seconds=4.0)
    values.update(keys)
    return SimpleNamespace(**values)


def test_station_request_keeps_key_server_side_and_caches():
    session = Session([Response({"name": "Leicester"})])
    client = RailDataMarketplace(
        settings(rdm_stations_api_key="private-station-key"), session=session,
    )

    first = client.station("lei")
    first["name"] = "changed by caller"
    second = client.station("LEI")

    assert second == {"name": "Leicester"}
    assert len(session.calls) == 1
    url, options = session.calls[0]
    assert url.endswith("/stations/LEI")
    assert options["headers"]["x-apikey"] == "private-station-key"
    assert "private-station-key" not in url


def test_last_good_payload_is_used_during_short_outage(monkeypatch):
    session = Session([Response({"ok": True}), requests.Timeout("slow")])
    client = RailDataMarketplace(
        settings(rdm_stations_api_key="secret"), session=session,
    )
    times = iter((10.0, 10.0, 12.0, 12.0))
    monkeypatch.setattr("app.rdm.time.monotonic", lambda: next(times))

    assert client.get("stations", "/stations/LEI", ttl=1, stale_ttl=30) == {"ok": True}
    assert client.get("stations", "/stations/LEI", ttl=1, stale_ttl=30) == {"ok": True}


def test_unknown_paths_and_bad_station_codes_are_rejected_before_network():
    session = Session([])
    client = RailDataMarketplace(
        settings(rdm_stations_api_key="secret"), session=session,
    )

    with pytest.raises(ValueError, match="Unsupported"):
        client.get("stations", "/anything")
    with pytest.raises(ValueError, match="Invalid station CRS"):
        client.station("LEICESTER")
    assert session.calls == []


def test_status_reports_only_configuration_booleans():
    client = RailDataMarketplace(settings(rdm_query_api_key="do-not-return-me"), session=Session([]))
    status = client.status()

    assert status["query"] is True
    assert status["stations"] is False
    assert "do-not-return-me" not in repr(status)


def test_station_disruptions_are_combined_and_filtered_by_crs():
    session = Session([Response([{"id": 1}]), Response([{"id": 2}])])
    client = RailDataMarketplace(
        settings(rdm_disruptions_api_key="disruption-key"), session=session,
    )

    result = client.station_disruptions("lei")

    assert result == {"incidents": [{"id": 1}], "station_messages": [{"id": 2}]}
    assert [call[1]["params"] for call in session.calls] == [
        {"stationCode": "LEI"}, {"stationCode": "LEI"},
    ]


def test_query_historical_and_reference_products_use_their_own_keys():
    query_session = Session([Response({"services": [1]}), Response({"rid": "ok"})])
    query = RailDataMarketplace(settings(rdm_query_api_key="query-key"), session=query_session)
    assert query.query_services("p04025", "20250601") == {"services": [1]}
    assert query.service_by_rid("20250601-P04025") == {"rid": "ok"}
    assert all(call[1]["headers"]["x-apikey"] == "query-key" for call in query_session.calls)

    hsp_session = Session([Response({"history": []})])
    hsp = RailDataMarketplace(settings(rdm_hsp_api_key="hsp-key"), session=hsp_session)
    assert hsp.historical_service({"serviceId": "P04025"}) == {"history": []}
    assert hsp_session.calls[0][1]["json"] == {"serviceId": "P04025"}

    ref_session = Session([Response([{"code": "WC"}])])
    ref = RailDataMarketplace(settings(rdm_performance_reference_api_key="ref-key"), session=ref_session)
    assert ref.performance_reference("routes") == [{"code": "WC"}]
    assert ref_session.calls[0][0].endswith("/referenceData/route")
