from datetime import timezone
from io import BytesIO
from zipfile import ZipFile

from app.collectors.bods import parse_bods_payload, parse_siri_vm

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


def test_parse_zip():
    buf = BytesIO()
    with ZipFile(buf, "w") as z:
        z.writestr("one.xml", SAMPLE)
    rows = parse_bods_payload(buf.getvalue(), "application/zip")
    assert len(rows) == 1
    assert rows[0].id == "bus:ARMD:4407"
