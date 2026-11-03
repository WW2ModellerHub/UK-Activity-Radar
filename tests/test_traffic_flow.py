from types import SimpleNamespace
import threading
import time

from app.state import LiveState
from app.traffic_flow import TrafficFlowService, _tile_for


class DummyResponse:
    status_code = 200
    content = b'PNGDATA'
    headers = {'Content-Type': 'image/png', 'Cache-Control': 'public, max-age=900'}

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
        map_tile_cache_seconds=86400,
        map_tile_cache_max_tiles=128,
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


def test_base_map_provider_no_cache_is_respected():
    service = TrafficFlowService(settings(), LiveState())
    dummy = DummySession()
    dummy.get = lambda *args, **kwargs: SimpleNamespace(
        status_code=200,
        content=b'PNGDATA',
        headers={'Content-Type': 'image/png', 'Cache-Control': 'no-store'},
        raise_for_status=lambda: None,
    )
    service._session = dummy

    first = service.get_map_tile(7, 63, 42)
    second = service.get_map_tile(7, 63, 42)

    assert first.cache_control == 'no-cache'
    assert second.cache_control == 'no-cache'
    assert service.status_payload()['base_map']['cache']['entries'] == 0


def test_commercial_base_map_is_proxied_and_cached():
    service = TrafficFlowService(settings(), LiveState())
    dummy = DummySession()
    service._session = dummy

    first = service.get_map_tile(7, 63, 42)
    second = service.get_map_tile(7, 63, 42)

    assert first.content == second.content == b'PNGDATA'
    assert len(dummy.calls) == 1
    url, kwargs = dummy.calls[0]
    assert '/maps/orbis/display/raster/tile/7/63/42' in url
    assert kwargs['params'] == {'apiVersion': 2, 'style': 'street-light'}
    assert kwargs['headers']['TomTom-Api-Key'] == 'abc'
    assert first.cache_max_age == 900
    assert first.cache_control == 'public, max-age=900'
    assert service.status_payload()['base_map']['cache'] == {
        'entries': 1, 'hits': 1, 'misses': 1, 'limit': 128,
    }


def test_parallel_identical_base_map_requests_share_one_provider_call():
    service = TrafficFlowService(settings(), LiveState())

    class SlowSession(DummySession):
        def __init__(self):
            super().__init__()
            self.lock = threading.Lock()

        def get(self, url, **kwargs):
            with self.lock:
                self.calls.append((url, kwargs))
            time.sleep(0.05)
            return DummyResponse()

    dummy = SlowSession()
    service._session = dummy
    barrier = threading.Barrier(6)
    results = []

    def fetch():
        barrier.wait()
        results.append(service.get_map_tile(7, 63, 42).content)

    threads = [threading.Thread(target=fetch) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    assert results == [b'PNGDATA'] * 6
    assert len(dummy.calls) == 1
    assert service.status_payload()['base_map']['cache'] == {
        'entries': 1, 'hits': 5, 'misses': 1, 'limit': 128,
    }


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
