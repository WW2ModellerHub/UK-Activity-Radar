from app.collectors.national_highways import parse_vms_payload


def test_vms_payload_becomes_an_explicit_live_sign_event():
    payload = {
        "vmsPublication": {
            "vmsUnit": [{
                "vmsUnitReference": "M1-42A",
                "roadNumber": "M1",
                "directionBound": "northbound",
                "locationDescription": "Junction 21",
                "latitude": 52.6123,
                "longitude": -1.1881,
                "messageText": "QUEUE CAUTION",
                "publicationTime": "2026-11-01T14:00:00Z",
            }]
        }
    }

    rows = parse_vms_payload(payload)

    assert len(rows) == 1
    assert rows[0].id == "nh-vms:M1-42A"
    assert rows[0].category == "Live motorway sign"
    assert rows[0].reason == "QUEUE CAUTION"
    assert rows[0].source == "National Highways VMS"


def test_vms_payload_never_invents_a_message():
    payload = {"vmsUnit": [{"id": "blank", "latitude": 52.0, "longitude": -1.0}]}
    assert parse_vms_payload(payload) == []


def test_vms_ignores_datex_control_metadata_and_joins_display_lines():
    payload = {
        "vmsControllerStatus": {
            "vmsControllerReference": {"idG": "signs/M5-M8444B"},
            "vmsStatus": {
                "vmsMessage": {
                    "messageSetBy": "External Center",
                    "messageIndex": "0",
                    "displayAreaSettings": {
                        "vmsTextDisplay": {
                            "textLine": [
                                {"textLine": {"textLine": "M48 HGVS"}, "lineIndex": "0"},
                                {"textLine": {"textLine": "USE M49/M4"}, "lineIndex": "1"},
                                {"textLine": {"textLine": "[hca1]"}, "lineIndex": "2"},
                            ]
                        }
                    },
                },
                "vmsStatusExtensionG": {
                    "externalIdentifier": "M5/8444B",
                    "latitude": 51.480927,
                    "longitude": -2.702307,
                    "roadName": "M5",
                },
            },
        }
    }

    rows = parse_vms_payload(payload)

    assert len(rows) == 1
    assert rows[0].id == "nh-vms:M5/8444B"
    assert rows[0].name == "M48 HGVS / USE M49/M4"
    assert "External Center" not in rows[0].name
    assert not rows[0].name.startswith("0")
