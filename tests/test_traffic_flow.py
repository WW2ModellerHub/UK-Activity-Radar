from types import SimpleNamespace

from app.state import LiveState
from app.traffic_flow import TrafficFlowService, _tile_for


class DummyResponse:
    status_code = 200
    content = b'PNGDATA'
    headers = {'Content-Type': 'image/png'}

    def raise_for_status(self):
        return None


class DummySession:
    def __init__(self):
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return DummyResponse()


def settings(key='abc'):
    return SimpleNamespace(
        traffic_flow_enabled=True,
        tomtom_traffic_api_key=key,
        tomtom_traffic_base_url='https://api.tomtom.com',
        traffic_flow_cache_seconds=45,
        traffic_flow_cache_max_tiles=64,
        traffic_flow_timeout_seconds=12,
    )


def test_tile_for_uk_is_valid():
    x, y = _tile_for(52.5, -1.5, 7)
    assert 0 <= x < 128
    assert 0 <= y < 128


def test_missing_key_sets_setup_status():
    state = LiveState()
    service = TrafficFlowService(settings(''), state)
    service.set_initial_status()
    status = state.source('traffic_flow')
    assert status is not None
    assert status.mode == 'setup'
    assert not status.connected


def test_live_tile_is_proxied_and_cached():
    state = LiveState()
    service = TrafficFlowService(settings(), state)
    dummy = DummySession()
    service._session = dummy
    first = service.get_tile(7, 63, 42, 'relative0')
    second = service.get_tile(7, 63, 42, 'relative0')
    assert first.content == b'PNGDATA'
    assert second.content == b'PNGDATA'
    assert len(dummy.calls) == 1
    url, kwargs = dummy.calls[0]
    assert '/maps/orbis/traffic/flow/raster/tile/7/63/42' in url
    assert kwargs['params']['apiVersion'] == 2
    assert kwargs['params']['style'] == 'light'
    assert kwargs['headers']['TomTom-Api-Key'] == 'abc'
    status = state.source('traffic_flow')
    assert status is not None
    assert status.mode == 'live'
    assert status.connected


def test_delay_style_is_allowed():
    state = LiveState()
    service = TrafficFlowService(settings(), state)
    dummy = DummySession()
    service._session = dummy
    service.get_tile(8, 127, 85, 'relative-delay')
    assert '/maps/orbis/traffic/flow/raster/tile/8/127/85' in dummy.calls[0][0]
    assert dummy.calls[0][1]['params']['style'] == 'light'


def test_incident_tile_is_proxied_and_cached():
    state = LiveState()
    service = TrafficFlowService(settings(), state)
    dummy = DummySession()
    service._session = dummy
    first = service.get_incident_tile(8, 127, 85)
    second = service.get_incident_tile(8, 127, 85)
    assert first.content == b'PNGDATA'
    assert second.content == b'PNGDATA'
    assert len(dummy.calls) == 1
    url, kwargs = dummy.calls[0]
    assert '/maps/orbis/traffic/incidents/raster/tile/8/127/85' in url
    assert kwargs['params']['apiVersion'] == 2
    assert kwargs['params']['style'] == 'light'
    assert kwargs['headers']['TomTom-Api-Key'] == 'abc'
