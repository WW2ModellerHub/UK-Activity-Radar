from types import SimpleNamespace
from app.state import LiveState
from app.traffic_flow import TrafficFlowService


class FakeResponse:
    status_code=200
    def raise_for_status(self): pass
    def json(self):
        return {"flowSegmentData":{"currentSpeed":18,"freeFlowSpeed":60,"currentTravelTime":420,"freeFlowTravelTime":120,"confidence":0.91,"roadClosure":False,"frc":"FRC0","coordinates":{"coordinate":[]}}}


class FakeSession:
    def get(self,*args,**kwargs): return FakeResponse()


def test_clickable_traffic_segment_reports_delay_and_congestion():
    settings=SimpleNamespace(traffic_flow_enabled=True,tomtom_traffic_api_key='x',tomtom_traffic_base_url='https://api.tomtom.com',traffic_flow_timeout_seconds=10,traffic_flow_cache_seconds=45,traffic_flow_cache_max_tiles=100)
    svc=TrafficFlowService(settings,LiveState())
    svc._session=FakeSession()
    row=svc.get_segment(52.5,-1.4,10)
    assert row['current_speed_mph']==18
    assert row['free_flow_speed_mph']==60
    assert row['delay_seconds']==300
    assert row['congestion']=='heavy'
