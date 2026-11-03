from app.tomtom_routing import TomTomRoutingClient


class _Response:
    def raise_for_status(self):
        return None

    def json(self):
        return {"routes": [{
            "summary": {
                "lengthInMeters": 12500,
                "travelTimeInSeconds": 1200,
                "trafficDelayInSeconds": 180,
                "departureTime": "2026-11-01T09:00:00Z",
                "arrivalTime": "2026-11-01T09:20:00Z",
            },
            "legs": [{"points": [{"latitude": 52.4, "longitude": -1.5}, {"latitude": 52.5, "longitude": -1.4}]}],
            "sections": [{
                "sectionType": "TRAFFIC", "startPointIndex": 0, "endPointIndex": 1,
                "simpleCategory": "JAM", "delayInSeconds": 180,
                "effectiveSpeedInKmh": 24, "magnitudeOfDelay": 3,
            }],
            "guidance": {"instructions": [{
                "message": "Turn left onto High Street",
                "street": "High Street",
                "routeOffsetInMeters": 800,
                "travelTimeInSeconds": 90,
                "point": {"latitude": 52.45, "longitude": -1.45},
            }]},
        }]}


def test_tomtom_route_is_normalised_for_existing_journey_ui(monkeypatch):
    client = TomTomRoutingClient("key")
    monkeypatch.setattr(client._session, "get", lambda *args, **kwargs: _Response())
    result = client.plan(52.4, -1.5, 52.5, -1.4, "car", from_name="A", to_name="B")
    assert result["provider"] == "TomTom Routing"
    assert result["itineraries"][0]["traffic_delay_seconds"] == 180
    assert result["itineraries"][0]["traffic_sections"][0]["category"] == "JAM"
    assert result["itineraries"][0]["traffic_sections"][0]["start"] == {"lat": 52.4, "lon": -1.5}
    assert result["itineraries"][0]["legs"][0]["distance_m"] == 12500
    assert result["itineraries"][0]["instructions"][0]["message"] == "Turn left onto High Street"
    assert result["itineraries"][0]["instructions"][0]["point"] == {"lat": 52.45, "lon": -1.45}
