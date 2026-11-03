from datetime import datetime, timezone

from app.coverage import _rnli_article_matches, coverage_for_event
from app.media import WikimediaLookup
from app.models import Event, Vehicle


def _event():
    return Event(
        id="rnli:test",
        type="rescue",
        name="St Ives Lifeboat Station launch",
        lat=50.21,
        lon=-5.48,
        source="RNLI",
        start_time=datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc),
        official_url="https://rnli.org/what-we-do/lifeboats-and-stations/latest-lifeboat-launches",
    )


def test_rnli_does_not_guess_a_generic_commons_photo():
    event = _event()
    assert WikimediaLookup.query_for(event) == ""
    result = WikimediaLookup("https://example.invalid", enabled=True).lookup(event)
    assert result["ok"] is False
    assert result["reason"] == "event_specific_media_unavailable"
    assert result["search_url"] is None


def test_rnli_coverage_requires_place_rescue_context_and_nearby_date():
    event = _event()
    assert _rnli_article_matches(event, {
        "title": "St Ives RNLI lifeboat launches to rescue crew",
        "seendate": "20261011T080000Z",
    })
    assert not _rnli_article_matches(event, {
        "title": "Another RNLI lifeboat launches to rescue crew",
        "seendate": "20261011T080000Z",
    })
    assert not _rnli_article_matches(event, {
        "title": "St Ives RNLI lifeboat launches to rescue crew",
        "seendate": "20250101T080000Z",
    })


def test_rnli_coverage_omits_generic_news_and_social_searches(monkeypatch):
    monkeypatch.setattr("app.coverage.gdelt_articles", lambda event: [])
    payload = coverage_for_event(_event())
    labels = [row["label"] for row in payload["links"]]
    assert payload["verified_only"] is True
    assert labels == ["Open verified launch location"]
    assert not any("Google" in label or "X" in label or "BBC" in label for label in labels)


def test_aircraft_media_uses_resolved_type_when_registration_is_missing():
    aircraft = Vehicle(
        id="aircraft:43c101", type="aircraft", name="ARMY01",
        lat=52.2, lon=-1.1, source="ADSB.lol",
        aircraft_type_code="AH64", aircraft_model="Boeing AH-64 Apache",
    )
    assert WikimediaLookup.query_for(aircraft) == "Boeing AH-64 Apache aircraft"


def _cool_train(subtype: str, *, name: str = "1Z80", vehicle_ref: str | None = None) -> Vehicle:
    return Vehicle(
        id=f"train:{name}", type="train", name=name,
        lat=52.2, lon=-1.1, source="Network Rail",
        headcode=name, vehicle_ref=vehicle_ref,
        cool_category="rail", cool_subtype=subtype, cool_candidate=True,
    )


def test_cool_train_without_stock_identity_never_gets_a_plausible_reference_photo():
    for subtype in ("railtour-special", "steam", "test-measurement", "freight"):
        assert WikimediaLookup.query_for(_cool_train(subtype)) == ""


def test_train_reference_images_are_limited_to_server_classified_safe_subtypes():
    routine = Vehicle(
        id="train:1a01", type="train", name="1A01", headcode="1A01",
        lat=52.2, lon=-1.1, source="Network Rail",
    )
    unclassified_special = routine.model_copy(update={"cool_subtype": "railtour-special"})

    assert WikimediaLookup.query_for(routine) == ""
    assert WikimediaLookup.query_for(unclassified_special) == ""
    assert WikimediaLookup.query_for(_cool_train("freight")) == ""
    assert WikimediaLookup.query_for(_cool_train("engineering")) == ""

    no_identity = WikimediaLookup("https://example.invalid", enabled=True).lookup(routine)
    assert no_identity["ok"] is False
    assert no_identity["query"] == ""
    assert no_identity["search_url"] is None


def test_train_vehicle_reference_query_behavior_is_preserved():
    train = _cool_train(
        "railtour-special", name="1Z80", vehicle_ref="60163",
    ).model_copy(update={"operator": "LNER"})
    assert WikimediaLookup.query_for(train) == "LNER 60163 train"


class _CommonsResponse:
    def __init__(self, title: str, description: str, licence: str = "CC BY-SA 4.0"):
        self._payload = {
            "query": {
                "pages": [{
                    "pageid": 123,
                    "title": f"File:{title}",
                    "imageinfo": [{
                        "thumburl": "https://upload.wikimedia.org/example.jpg",
                        "url": "https://upload.wikimedia.org/original.jpg",
                        "extmetadata": {
                            "ImageDescription": {"value": description},
                            "Artist": {"value": "Example photographer"},
                            "LicenseShortName": {"value": licence},
                            "LicenseUrl": {"value": "https://creativecommons.org/licenses/by-sa/4.0/"},
                        },
                    }],
                }],
            },
        }

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_train_media_labels_exact_and_reference_images_and_keeps_attribution(monkeypatch):
    lookup = WikimediaLookup("https://example.invalid", enabled=True)
    monkeypatch.setattr(
        lookup._session,
        "get",
        lambda *args, **kwargs: _CommonsResponse(
            "LNER 60163 Tornado",
            "LNER locomotive 60163 working a passenger train",
        ),
    )
    exact = lookup.lookup(_cool_train("railtour-special", vehicle_ref="60163").model_copy(update={"operator": "LNER"}))
    assert exact["ok"] is True
    assert exact["match_kind"] == "exact_vehicle"
    assert exact["artist"] == "Example photographer"
    assert exact["license"] == "CC BY-SA 4.0"
    assert exact["license_url"] == "https://creativecommons.org/licenses/by-sa/4.0/"

def test_train_type_reference_rejects_generic_railway_photos(monkeypatch):
    lookup = WikimediaLookup("https://example.invalid", enabled=True)
    monkeypatch.setattr(
        lookup._session,
        "get",
        lambda *args, **kwargs: _CommonsResponse(
            "British passenger railway train",
            "A routine passenger train in Britain",
        ),
    )
    result = lookup.lookup(_cool_train("railtour-special"))
    assert result["ok"] is False
    assert result["reason"] == "insufficient_identity"


def test_vessel_media_requires_exact_name_or_mmsi_in_metadata(monkeypatch):
    vessel = Vehicle(id="vessel:228798000", type="vessel", name="French warship", vehicle_ref="228798000", lat=49, lon=-3)
    lookup = WikimediaLookup("https://example.invalid", enabled=True)
    monkeypatch.setattr(lookup._session, "get", lambda *args, **kwargs: _CommonsResponse("French naval ship", "A French warship at sea"))
    assert lookup.lookup(vessel)["ok"] is False

    lookup = WikimediaLookup("https://example.invalid", enabled=True)
    monkeypatch.setattr(lookup._session, "get", lambda *args, **kwargs: _CommonsResponse("MMSI 228798000", "Vessel MMSI 228798000"))
    exact = lookup.lookup(vessel)
    assert exact["ok"] is True
    assert exact["match_kind"] == "exact_vessel"


def test_media_rejects_noncommercial_commons_licence(monkeypatch):
    lookup = WikimediaLookup("https://example.invalid", enabled=True)
    monkeypatch.setattr(
        lookup._session,
        "get",
        lambda *args, **kwargs: _CommonsResponse(
            "Locomotive 60163",
            "Locomotive 60163 working a charter train",
            "CC BY-NC 4.0",
        ),
    )
    result = lookup.lookup(_cool_train("railtour-special", vehicle_ref="60163"))
    assert result["ok"] is False
    assert result["reason"] == "no_confident_match"
