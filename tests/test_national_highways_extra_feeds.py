from app.collectors.national_highways import (
    parse_road_feature_payload,
    parse_speed_managed_payload,
)


def speed_record(*, status="active", speed="64.3736"):
    return {
        "situationRecord": {
            "sitSpeedManagement": {
                "idG": "speed-40",
                "temporarySpeedLimit": speed,
                "complianceOption": "mandatory",
                "situationRecordVersionTime": "2026-11-01T12:00:00Z",
                "validity": {
                    "validityStatus": status,
                    "validityTimeSpecification": {
                        "overallStartTime": "2026-11-01T11:00:00Z",
                        "overallEndTime": "2027-11-01T11:00:00Z",
                    },
                },
                "generalPublicComment": {"comment": "M6 lane closure and 40 mph speed limit"},
                "locationReference": {
                    "locationDescription": "M6 northbound between J35 and J36",
                    "roadName": "M6",
                    "directionOnLinearSection": "northBound",
                    "posList": "54.198808 -2.723897 54.211303 -2.723502",
                },
            }
        }
    }


def test_active_managed_speed_is_converted_to_real_uk_sign_value():
    rows = parse_speed_managed_payload(speed_record())

    assert len(rows) == 1
    assert rows[0].name == "40 mph temporary limit"
    assert rows[0].category == "Live managed speed"
    assert rows[0].road == "M6"
    assert rows[0].type == "incident"


def test_suspended_or_zero_speed_records_are_not_mapped():
    assert parse_speed_managed_payload(speed_record(status="suspended")) == []
    assert parse_speed_managed_payload(speed_record(speed="0")) == []


def test_roadside_feature_remains_a_separate_opt_in_type():
    payload = {
        "limitOrFeature": {
            "limitOrFeatureRecord": {
                "idG": "phone-1",
                "type": "emergencyTelephone",
                "description": "M25/5161B TEL",
                "validity": {"validityStatus": "active"},
                "limitOrFeatureRecordVersionTime": "2026-10-20T09:03:02Z",
                "locationReference": {
                    "latitude": "51.6768937",
                    "longitude": "-0.4585706",
                    "roadName": "M25",
                    "directionAtPoint": "anticlockwise",
                    "locationDescription": "M25 anti-clockwise between J19 and J18",
                },
            }
        }
    }

    rows = parse_road_feature_payload(payload, "emergencyTelephone")

    assert len(rows) == 1
    assert rows[0].type == "roadfeature"
    assert rows[0].category == "Emergency roadside telephone"
    assert rows[0].road == "M25"
    assert rows[0].interesting is False

