from __future__ import annotations

import pytest

from app.collectors.street_manager import (
    StreetManagerReportingClient,
    bng_to_wgs84,
    parse_permit_payload,
    street_manager_position,
)


def _permit(reference: str, *, status: str = "in_progress") -> dict:
    return {
        "work_reference_number": reference.rsplit("-", 1)[0],
        "permit_reference_number": reference,
        "works_coordinates": "POINT (530028.746 180380.094)",
        "location_description": "Outside Parliament",
        "street": "Bridge Street",
        "town": "London",
        "work_category": "immediate_urgent",
        "work_category_string": "Immediate urgent",
        "description_of_work": "Emergency utility repair",
        "traffic_management_type": "lane_closure",
        "traffic_management_type_string": "Lane closure",
        "proposed_start_date": "2026-10-25T09:00:00Z",
        "proposed_end_date": "2026-10-25T18:00:00Z",
        "status": "granted",
        "work_status": status,
        "work_status_string": status.replace("_", " ").title(),
        "date_created": "2026-10-24T20:00:00Z",
        "status_changed_date": "2026-10-25T08:30:00Z",
        "is_active_permit": True,
    }


def test_bng_to_wgs84_matches_known_central_london_point():
    lat, lon = bng_to_wgs84(530028.746, 180380.094)
    assert lat == pytest.approx(51.50740, abs=0.00015)
    assert lon == pytest.approx(-0.12780, abs=0.00015)


def test_geometry_accepts_bng_wkt_and_geojson_lon_lat():
    assert street_manager_position("SRID=27700;POINT (530028.746 180380.094)") == pytest.approx(
        (51.50740, -0.12780), abs=0.00015
    )
    assert street_manager_position({"type": "Point", "coordinates": [-2.58, 51.45]}) == (-2.58, 51.45)[::-1]


def test_parse_permits_builds_honest_road_event_and_omits_inactive_rows():
    current = _permit("ABC-01")
    completed = _permit("DEF-01", status="completed")
    rows = parse_permit_payload({"pagination": {"has_next_page": False}, "rows": [current, completed]})

    assert len(rows) == 1
    event = rows[0]
    assert event.id == "street-manager:ABC-01"
    assert event.source == "DfT Street Manager"
    assert event.road == "Bridge Street"
    assert event.reason == "Emergency utility repair"
    assert event.status == "In progress"
    assert event.severity == "High"
    assert event.start_time.isoformat() == "2026-10-25T09:00:00+00:00"
    assert event.interesting is True


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class _Session:
    def __init__(self):
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        offset = kwargs["params"]["offset"]
        if offset == 0:
            return _Response({"pagination": {"has_next_page": True, "total_rows": 2}, "rows": [_permit("AAA-01")]})
        return _Response({"pagination": {"has_next_page": False, "total_rows": 2}, "rows": [_permit("BBB-01")]})


def test_reporting_client_uses_v6_get_token_header_and_paginates():
    session = _Session()
    client = StreetManagerReportingClient("secret-jwt", session=session)

    rows = client.fetch_permit_rows(filters={"work_status": ["planned", "in_progress"]})

    assert [row["permit_reference_number"] for row in rows] == ["AAA-01", "BBB-01"]
    assert len(session.calls) == 2
    first_url, first_call = session.calls[0]
    assert first_url == "https://api.manage-roadworks.service.gov.uk/v6/reporting/permits"
    assert first_call["headers"]["token"] == "secret-jwt"
    assert first_call["params"]["work_status"] == ["planned", "in_progress"]
    assert [call[1]["params"]["offset"] for call in session.calls] == [0, 1]


def test_reporting_client_refuses_missing_jwt():
    with pytest.raises(ValueError, match="JWT"):
        StreetManagerReportingClient(" ")
