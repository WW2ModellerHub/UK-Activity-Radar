from app.weather_radar import rainviewer_payload


def test_rainviewer_payload_uses_latest_past_frame():
    body = {
        'host': 'https://tilecache.rainviewer.com',
        'generated': 123,
        'radar': {'past': [
            {'time': 100, 'path': '/v2/radar/100'},
            {'time': 200, 'path': '/v2/radar/200'},
        ]},
    }
    payload = rainviewer_payload(body)
    assert payload['frame_time'] == 200
    assert payload['max_native_zoom'] == 7
    assert payload['tile_template'].startswith('https://tilecache.rainviewer.com/v2/radar/200/256/')
    assert '{z}' in payload['tile_template']
    assert payload['source'] == 'RainViewer'
