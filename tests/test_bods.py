from datetime import datetime, timedelta, timezone
from io import BytesIO
from zipfile import ZipFile

from app.collectors.bods import parse_bods_payload, parse_siri_stop_predictions, parse_siri_vm

SAMPLE = b'''<?xml version="1.0" encoding="UTF-8"?>
<Siri xmlns="http://www.siri.org.uk/siri" version="2.0">
  <ServiceDelivery>
    <VehicleMonitoringDelivery>
      <VehicleActivity>
        <RecordedAtTime>2026-10-04T18:30:10Z</RecordedAtTime>
        <ItemIdentifier>item-1</ItemIdentifier>
        <MonitoredVehicleJourney>
          <Bearing>135</Bearing>
          <VehicleJourneyRef>journey-447183</VehicleJourneyRef>
          <OriginName>Nuneaton</OriginName>
          <DestinationName>Leicester</DestinationName>
          <PublishedLineName>158</PublishedLineName>
          <OperatorRef>ARMD</OperatorRef>
          <VehicleLocation><Longitude>-1.37284</Longitude><Latitude>52.54129</Latitude></VehicleLocation>
          <Velocity>12.5</Velocity>
          <MonitoredCall><StopPointRef>2600NEXT</StopPointRef></MonitoredCall>
          <VehicleRef>4407</VehicleRef>
        </MonitoredVehicleJourney>
      </VehicleActivity>
    </VehicleMonitoringDelivery>
  </ServiceDelivery>
</Siri>'''


def test_parse_siri_vm():
    rows = parse_siri_vm(SAMPLE)
    assert len(rows) == 1
    v = rows[0]
    assert v.type == "bus"
    assert v.position_method == "gps"
    assert v.route == "158"
    assert v.operator == "ARMD"
    assert v.vehicle_ref == "4407"
    assert v.origin == "Nuneaton"
    assert v.destination == "Leicester"
    assert v.next_location == "2600NEXT"
    assert round(v.lat, 5) == 52.54129
    assert round(v.lon, 5) == -1.37284
    assert v.last_updated.tzinfo == timezone.utc


def test_siri_machine_place_names_and_dated_trip_ref_are_humanised():
    payload = SAMPLE.replace(
        b"<VehicleJourneyRef>journey-447183</VehicleJourneyRef>",
        b"<DatedVehicleJourneyRef>dated-trip-158</DatedVehicleJourneyRef>",
    ).replace(b"<OriginName>Nuneaton</OriginName>", b"<OriginName>Bus_Station</OriginName>").replace(
        b"<DestinationName>Leicester</DestinationName>",
        b"<DestinationName>St_Margaret_s_Bus_Station</DestinationName>",
    )
    vehicle = parse_siri_vm(payload)[0]
    assert vehicle.origin == "Bus Station"
    assert vehicle.destination == "St Margaret's Bus Station"
    assert vehicle.journey_ref == "dated-trip-158"


def test_dated_trip_reference_wins_when_publisher_also_sends_an_operational_reference():
    payload = SAMPLE.replace(
        b"<VehicleJourneyRef>journey-447183</VehicleJourneyRef>",
        b"<VehicleJourneyRef>operator-only-ref</VehicleJourneyRef><DatedVehicleJourneyRef>gtfs-trip-158</DatedVehicleJourneyRef>",
    )
    assert parse_siri_vm(payload)[0].journey_ref == "gtfs-trip-158"


def test_parse_zip():
    buf = BytesIO()
    with ZipFile(buf, "w") as z:
        z.writestr("one.xml", SAMPLE)
    rows = parse_bods_payload(buf.getvalue(), "application/zip")
    assert len(rows) == 1
    assert rows[0].id == "bus:ARMD:4407"


def test_stop_prediction_keeps_scheduled_time_delay_and_confidence():
    now = datetime.now(timezone.utc).replace(microsecond=0)
    aimed = now + timedelta(minutes=12)
    expected = aimed + timedelta(minutes=6)
    payload = f'''<Siri xmlns="http://www.siri.org.uk/siri"><ServiceDelivery>
      <VehicleMonitoringDelivery><VehicleActivity><MonitoredVehicleJourney>
        <PublishedLineName>7</PublishedLineName><DestinationName>City Centre</DestinationName>
        <OperatorRef>TEST</OperatorRef><VehicleJourneyRef>trip-7</VehicleJourneyRef>
        <VehicleRef>bus-7</VehicleRef><MonitoredCall><StopPointRef>stop-7</StopPointRef>
        <AimedDepartureTime>{aimed.isoformat()}</AimedDepartureTime>
        <ExpectedDepartureTime>{expected.isoformat()}</ExpectedDepartureTime>
        </MonitoredCall></MonitoredVehicleJourney></VehicleActivity></VehicleMonitoringDelivery>
      </ServiceDelivery></Siri>'''.encode()

    rows = parse_siri_stop_predictions(payload)

    assert len(rows) == 1
    assert rows[0]["aimed"] == aimed.isoformat()
    assert rows[0]["expected"] == expected.isoformat()
    assert rows[0]["delay_minutes"] == 6
    assert rows[0]["prediction_confidence"] == "operator_live"
    assert rows[0]["status"] == "Live • 6 min late"
