from app.collectors.aisstream import parse_ais_message
from app.collectors.rnli import parse_rnli_launches


def test_parse_ais_position_report():
    payload = {
        "MessageType": "PositionReport",
        "MetaData": {
            "MMSI": 235000001,
            "ShipName": "TEST VESSEL",
            "Latitude": 51.10,
            "Longitude": 1.20,
        },
        "Message": {
            "PositionReport": {
                "UserID": 235000001,
                "Sog": 12.4,
                "Cog": 86.7,
                "TrueHeading": 87,
                "Valid": True,
            }
        },
    }
    vehicle = parse_ais_message(payload)
    assert vehicle is not None
    assert vehicle.type == "vessel"
    assert vehicle.name == "TEST VESSEL"
    assert vehicle.vehicle_ref == "235000001"
    assert vehicle.speed == 12.4
    assert vehicle.bearing == 86.7
    assert vehicle.position_method == "gps"


def test_parse_ais_sar_aircraft():
    payload = {
        "MessageType": "StandardSearchAndRescueAircraftReport",
        "MetaData": {
            "MMSI": 111232500,
            "Latitude": 50.20,
            "Longitude": -4.20,
        },
        "Message": {
            "StandardSearchAndRescueAircraftReport": {
                "UserID": 111232500,
                "Sog": 110.0,
                "Cog": 270.0,
            }
        },
    }
    vehicle = parse_ais_message(payload)
    assert vehicle is not None
    assert vehicle.type == "sar"
    assert vehicle.interesting is True


def test_parse_rnli_jsonp_nested_station():
    raw = '''ukActivityRadar({"launches":[{"launchId":123,"launchDateTime":"2026-10-04T17:03:00Z","station":{"stationName":"Blackpool Lifeboat Station","latitude":53.821,"longitude":-3.056,"url":"/find-my-nearest/lifeboat-stations/blackpool-lifeboat-station"}}]});'''
    rows = parse_rnli_launches(raw)
    assert len(rows) == 1
    event = rows[0]
    assert event.type == "rescue"
    assert "Blackpool" in event.name
    assert event.source == "RNLI"
    assert event.lat == 53.821
    assert event.lon == -3.056
    assert event.official_url.startswith("https://rnli.org/")


def test_parse_rnli_xml():
    raw = '''<Launches><Launch><Id>abc</Id><LaunchTime>2026-10-04T15:07:00Z</LaunchTime><Station><Name>St Helier Lifeboat Station</Name><Latitude>49.183</Latitude><Longitude>-2.116</Longitude></Station></Launch></Launches>'''
    rows = parse_rnli_launches(raw)
    assert len(rows) == 1
    assert rows[0].type == "rescue"
