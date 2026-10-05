import json
from datetime import datetime, timedelta
from pathlib import Path

from app.collectors.bods import parse_siri_stop_predictions
from app.models import Vehicle
from app.schedule_store import ScheduleStore
from app.smart_places import SmartPlacesService
from app.timeutils import london_tz


class FakeState:
    def set_source(self, status):
        self.status = status
    def find_train_by_headcode(self, headcode):
        return None
    def all(self, **kwargs):
        return []


def test_siri_stop_predictions_extracts_expected_time():
    future=(datetime.now().astimezone()+timedelta(minutes=7)).isoformat()
    xml=f'''<Siri><ServiceDelivery><VehicleMonitoringDelivery><VehicleActivity><RecordedAtTime>{datetime.now().astimezone().isoformat()}</RecordedAtTime><MonitoredVehicleJourney><PublishedLineName>158</PublishedLineName><OperatorRef>ARR</OperatorRef><DestinationName>Leicester</DestinationName><VehicleRef>4407</VehicleRef><OnwardCalls><OnwardCall><StopPointRef>2600ABC123</StopPointRef><ExpectedArrivalTime>{future}</ExpectedArrivalTime></OnwardCall></OnwardCalls></MonitoredVehicleJourney></VehicleActivity></VehicleMonitoringDelivery></ServiceDelivery></Siri>'''.encode()
    rows=parse_siri_stop_predictions(xml)
    assert len(rows)==1
    assert rows[0]['stop_ref']=='2600ABC123'
    assert rows[0]['route']=='158'
    assert rows[0]['destination']=='Leicester'
    assert 0 <= rows[0]['minutes'] <= 8


def test_local_station_board_finds_upcoming_calls(tmp_path: Path):
    db=tmp_path/'schedule.sqlite'
    store=ScheduleStore(db)
    now=datetime.now(london_tz())
    dep=now+timedelta(minutes=20)
    arr=now+timedelta(minutes=18)
    day='1111111'
    with store._connect() as con:
        con.execute("INSERT INTO tiplocs(tiploc,stanox,crs,description,lat,lon) VALUES(?,?,?,?,?,?)",('HNK','12345','HNK','HINCKLEY',52.535,-1.371))
        locs=[{'tiploc_code':'NUNEAT','scheduled_departure':(now+timedelta(minutes=5)).strftime('%H%M')},{'tiploc_code':'HNK','scheduled_arrival':arr.strftime('%H%M'),'scheduled_departure':dep.strftime('%H%M'),'platform':'1'},{'tiploc_code':'LESTER','scheduled_arrival':(now+timedelta(minutes=40)).strftime('%H%M')}]
        con.execute("""INSERT INTO schedules(train_uid,start_date,end_date,days_runs,stp_indicator,atoc_code,signalling_id,origin_tiploc,destination_tiploc,origin_name,destination_name,locations_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",('C12345',(now-timedelta(days=1)).date().isoformat(),(now+timedelta(days=1)).date().isoformat(),day,'P','EM','1L23','NUNEAT','LESTER','Nuneaton','Leicester',json.dumps(locs,separators=(',',':'))))
    svc=SmartPlacesService(tmp_path,store,FakeState())
    board=svc._scheduled_station_calls('HNK',10)
    assert board['departures']
    assert board['arrivals']
    assert board['departures'][0]['destination']=='Leicester'
    assert board['departures'][0]['platform']=='1'


class FakeStateWithGet(FakeState):
    def __init__(self, items=None):
        self.items = items or {}
    def get(self, item_id):
        return self.items.get(item_id)
    def all(self, **kwargs):
        return list(self.items.values())


def test_rail_places_use_naptan_fallback_when_schedule_empty(tmp_path: Path):
    store=ScheduleStore(tmp_path/'schedule.sqlite')
    svc=SmartPlacesService(tmp_path,store,FakeStateWithGet())
    svc._rail=[{'id':'rail-station:HNK','kind':'place','place_type':'rail_station','name':'Hinckley','subtitle':'HNK','lat':52.535,'lon':-1.371,'crs':'HNK','source':'DfT NaPTAN rail geography'}]
    rows=svc.rail_places((-1.5,52.4,-1.2,52.7),10)
    assert rows and rows[0]['crs']=='HNK'


def test_exact_train_id_match_beats_ambiguous_headcode(tmp_path: Path):
    store=ScheduleStore(tmp_path/'schedule.sqlite')
    item=type('Train',(),{'id':'nr:123ABC'})()
    state=FakeStateWithGet({'nr:123ABC':item})
    svc=SmartPlacesService(tmp_path,store,state)
    assert svc._match_live_train('1A23','C12345','123ABC') == 'nr:123ABC'


def test_bus_stop_without_predictions_does_not_show_nearby_bus(tmp_path: Path):
    store=ScheduleStore(tmp_path/'schedule.sqlite')
    state=FakeStateWithGet()
    svc=SmartPlacesService(tmp_path,store,state,bods_collector_getter=lambda: None)
    payload=svc.bus_board({'atco_code':'2600TEST','naptan_code':'test'},10)
    assert payload['mode']=='no_due_predictions'
    assert payload['rows']==[]
    assert 'Nearby buses are deliberately not shown' in payload['note']


class FakePredictions:
    def __init__(self, rows):
        self.rows=rows
    def predictions_for_stop(self, ref, limit=10):
        return list(self.rows)[:limit] if ref=='2600TEST' else []


def _bus(item_id='bus:ARMD:4407', *, vehicle_ref='4407', next_location=None):
    return Vehicle(
        id=item_id, type='bus', name='Bus 158', subtitle='Nuneaton → Leicester',
        lat=52.54, lon=-1.37, operator='ARMD', route='158', destination='Leicester',
        status='Live GPS', source='DfT Bus Open Data Service', position_method='gps',
        position_label='Live GPS', vehicle_ref=vehicle_ref, next_location=next_location,
    )


def test_bus_prediction_matches_siri_style_vehicle_id(tmp_path: Path):
    store=ScheduleStore(tmp_path/'schedule.sqlite')
    bus=_bus()
    state=FakeStateWithGet({bus.id:bus})
    collector=FakePredictions([{'vehicle_ref':'4407','route':'158','destination':'Leicester','minutes':4,'expected':'16:24'}])
    svc=SmartPlacesService(tmp_path,store,state,bods_collector_getter=lambda: collector)
    payload=svc.bus_board({'atco_code':'2600TEST','naptan_code':'test','lat':52.535,'lon':-1.371},10)
    assert payload['mode']=='live_predictions'
    assert payload['rows'][0]['live_vehicle_id']=='bus:ARMD:4407'
    assert payload['next_live_id']=='bus:ARMD:4407'


def test_bus_next_stop_match_focuses_only_bus_targeting_stop(tmp_path: Path):
    store=ScheduleStore(tmp_path/'schedule.sqlite')
    due=_bus('bus:ARMD:4407',vehicle_ref='4407',next_location='2600TEST')
    other=_bus('bus:ARMD:4410',vehicle_ref='4410',next_location='2600OTHER')
    state=FakeStateWithGet({due.id:due,other.id:other})
    svc=SmartPlacesService(tmp_path,store,state,bods_collector_getter=lambda: None)
    payload=svc.bus_board({'atco_code':'2600TEST','naptan_code':'test','lat':52.535,'lon':-1.371},10)
    assert payload['mode']=='live_next_stop'
    assert [r['live_vehicle_id'] for r in payload['rows']]==['bus:ARMD:4407']
    assert payload['next_live_id']=='bus:ARMD:4407'

class FakeJsonResponse:
    def __init__(self, payload):
        self.payload=payload
    def raise_for_status(self):
        return None
    def json(self):
        return self.payload


class FakeRailSession:
    def get(self, url, params=None, timeout=None):
        if '/api/departures/HNK' in url:
            return FakeJsonResponse({'results':[{
                'departs':'2026-10-05T16:35:00+01:00',
                'expected_departs':'2026-10-05T16:38:00+01:00',
                'train_uid':'C12345','train_id':'123ABC','headcode':'1L23',
                'status':'expected_late','status_text':'Expected 16:38','platform':'1',
                'operator':'East Midlands Railway',
                'origin':{'name':'Birmingham New Street'},'destination':{'name':'Leicester'},
            }]})
        if '/api/stations/HNK/activity' in url:
            return FakeJsonResponse({'results':[{
                'headcode':'1D99','train_id':'999XYZ','event_type':'ARRIVAL',
                'planned':'2026-10-05T16:10:00+01:00','actual':'2026-10-05T16:12:00+01:00',
                'variation_status':'LATE','platform':'2','location':'HINCKLEY',
            }]})
        raise AssertionError(url)


def test_rail_board_works_without_local_schedule_index(tmp_path: Path):
    store=ScheduleStore(tmp_path/'schedule.sqlite')
    train=type('Train',(),{'id':'nr:123ABC'})()
    state=FakeStateWithGet({'nr:123ABC':train})
    svc=SmartPlacesService(tmp_path,store,state)
    svc._session=FakeRailSession()
    payload=svc.rail_board('HNK',10)
    assert payload['departures'][0]['destination']=='Leicester'
    assert payload['departures'][0]['expected']=='16:38'
    assert payload['departures'][0]['live_train_id']=='nr:123ABC'
    assert payload['next_live_id']=='nr:123ABC'
    assert payload['recent_arrivals'][0]['expected']=='16:12'

class FakeNearestSession:
    def get(self, url, params=None, timeout=None):
        if 'api.tomtom.com/search/2/search/' in url:
            return FakeJsonResponse({'results':[{
                'poi':{'name':'Hinckley Train Station'},
                'position':{'lat':52.5350,'lon':-1.3710},
                'address':{'freeformAddress':'Hinckley Station'},
            }]})
        if '/api/stations' in url:
            return FakeJsonResponse({'resolved':{'resolves_to':{'name':'HINCKLEY','crs':'HNK'}},'results':[]})
        raise AssertionError(url)


def test_nearest_station_can_resolve_from_tomtom_before_local_cache(tmp_path: Path):
    store=ScheduleStore(tmp_path/'schedule.sqlite')
    svc=SmartPlacesService(tmp_path,store,FakeStateWithGet())
    svc._session=FakeNearestSession()
    place=svc.nearest_rail_station(52.55,-1.38,tomtom_api_key='test-key')
    assert place['id']=='rail-station:HNK'
    assert place['crs']=='HNK'
    assert place['source']=='TomTom Search + traini.ac station resolver'
    assert svc.get_place('rail-station:HNK')['crs']=='HNK'
