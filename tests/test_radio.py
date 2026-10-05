from app.radio import RadioBrowserLookup


def test_radio_normalise_and_distance(monkeypatch):
    lookup = RadioBrowserLookup(cache_seconds=999)
    rows = [
        {"stationuuid":"a","name":"Near FM","url_resolved":"https://example.com/near","geo_lat":52.64,"geo_long":-1.14,"clickcount":20,"codec":"MP3","bitrate":128},
        {"stationuuid":"b","name":"Far FM","url_resolved":"https://example.com/far","geo_lat":55.95,"geo_long":-3.19,"clickcount":99,"codec":"AAC","bitrate":64},
    ]
    monkeypatch.setattr(lookup, "_fetch", lambda: rows)
    result = lookup.nearby(52.6369, -1.1398, limit=2)
    assert result["ok"] is True
    assert result["stations"][0]["name"] == "Near FM"
    assert result["stations"][0]["distance_km"] < 2
    assert result["stations"][0]["stream_url"].startswith("https://")


def test_specialist_audio_is_prioritised(monkeypatch):
    lookup = RadioBrowserLookup(cache_seconds=999)
    rows = [
        {"stationuuid":"g","name":"Very Near FM","url_resolved":"https://example.com/general","geo_lat":52.64,"geo_long":-1.14,"clickcount":100,"tags":"pop"},
        {"stationuuid":"a","name":"Midlands Aviation Radio","url_resolved":"https://example.com/air","geo_lat":52.90,"geo_long":-1.20,"clickcount":2,"tags":"aviation,airport"},
    ]
    monkeypatch.setattr(lookup, "_fetch", lambda: rows)
    result = lookup.nearby(52.6369, -1.1398, limit=2)
    assert result["stations"][0]["specialism"] == "aviation"
    assert result["specialist_count"] == 1

