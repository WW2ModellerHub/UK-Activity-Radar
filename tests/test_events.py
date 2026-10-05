from app.collectors.national_highways import parse_closure_payload
from app.models import Event
from app.state import LiveState


def test_national_highways_datex_like_json_parses_event():
    payload = {
        "payloadPublication": {
            "situation": [{
                "situationRecord": [{
                    "id": "ABC123",
                    "situationRecordCreationTime": "2026-10-04T17:00:00Z",
                    "situationRecordVersionTime": "2026-10-04T17:05:00Z",
                    "roadOrCarriagewayOrLaneManagementType": "laneClosures",
                    "operatorActionStatus": "implemented",
                    "locationReference": {
                        "locationForDisplay": {"latitude": 52.45, "longitude": -1.20},
                        "roadNumber": "M1",
                        "locationDescription": "J20 Lutterworth"
                    },
                    "cause": {"causeType": "accident"}
                }]
            }]
        }
    }
    rows = parse_closure_payload(payload, "unplanned")
    assert len(rows) == 1
    assert rows[0].type == "incident"
    assert rows[0].road == "M1"
    assert rows[0].lat == 52.45
    assert rows[0].lon == -1.20


def test_state_replaces_source_snapshot():
    state = LiveState()
    one = Event(id="a", type="road", name="A", lat=52.0, lon=-1.0, source="National Highways")
    two = Event(id="b", type="flood", name="B", lat=53.0, lon=-2.0, source="Environment Agency")
    state.replace_events_for_source("National Highways", [one])
    state.replace_events_for_source("Environment Agency", [two])
    assert len(state.all_events()) == 2
    state.replace_events_for_source("National Highways", [])
    rows = state.all_events()
    assert len(rows) == 1
    assert rows[0].id == "b"


def test_national_highways_search_query_is_human_readable():
    payload = {
        "payloadPublication": {
            "situation": [{
                "situationRecord": [{
                    "id": "M62TEST",
                    "roadOrCarriagewayOrLaneManagementType": "roadOrCarriagewayOrLaneManagement",
                    "operatorActionStatus": "implemented",
                    "locationReference": {
                        "locationForDisplay": {"latitude": 53.73, "longitude": -1.51},
                        "roadNumber": "M62",
                        "locationDescription": "M62 eastbound within J29 after M1 J42 southbound exit"
                    }
                }]
            }]
        }
    }
    row = parse_closure_payload(payload, "unplanned")[0]
    assert row.news_query == "M62 eastbound within J29 after M1 J42 southbound exit road closure"
    assert "roadOrCarriagewayOrLaneManagement" not in row.news_query
    assert row.official_url.endswith("/current-incidents-disruptions-and-delays/")


def test_national_highways_layers_are_non_overlapping():
    record = {
        "payloadPublication": {"situation": [{"situationRecord": [{
            "id": "X1",
            "roadOrCarriagewayOrLaneManagementType": "laneClosures",
            "locationReference": {"locationForDisplay": {"latitude": 52.5, "longitude": -1.5}, "roadNumber": "M69"},
        }]}]}
    }
    assert parse_closure_payload(record, "planned")[0].type == "road"
    assert parse_closure_payload(record, "unplanned")[0].type == "incident"
