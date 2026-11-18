from app.planespotters import PlanespottersPhotoLookup


def test_planespotters_photo_is_normalised_and_credited():
    lookup = PlanespottersPhotoLookup()

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"photos": [{
                "id": "123", "photographer": "A Spotter",
                "link": "https://www.planespotters.net/photo/123/example?utm_source=api",
                "thumbnail_large": {"src": "https://t.plnspttrs.net/123.jpg", "size": {"width": 420, "height": 280}},
            }]}

    class Session:
        def get(self, url, timeout):
            assert url.endswith("/40621D") and timeout == 5
            return Response()

    lookup._session = Session()
    result = lookup.lookup("40621d")
    assert result["ok"] is True
    assert result["match_kind"] == "exact_airframe"
    assert result["artist"] == "© A Spotter"
    assert result["thumbnail_width"] == 420
    assert result["source"] == "Planespotters.net"


def test_planespotters_rejects_non_icao_identifiers_without_a_request():
    lookup = PlanespottersPhotoLookup()
    assert lookup.lookup("EZY123")["ok"] is False
