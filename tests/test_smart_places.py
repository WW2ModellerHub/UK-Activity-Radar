import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from app.collectors.bods import parse_siri_stop_predictions
from app.models import Vehicle
from app.schedule_store import ScheduleStore
from app.smart_places import SmartPlacesService, _ea_readings_url
from app.state import LiveState
from app.timeutils import london_tz


class FakeState:
    def set_source(self, status):
        self.status = status
    def find_train_by_headcode(self, headcode):
        return None
    def all(self, **kwargs):
        return []


def test_nearest_bus_stop_uses_distance_not_database_order(tmp_path: Path):
    store=ScheduleStore(tmp_path/'schedule.sqlite')
    svc=SmartPlacesService(tmp_path,store,FakeState())
    with sqlite3.connect(svc.stop_db) as con:
        con.executescript("""
            CREATE TABLE stops(atco_code TEXT PRIMARY KEY,naptan_code TEXT,name TEXT,indicator TEXT,stop_type TEXT,lat REAL,lon REAL);
            CREATE INDEX idx_stops_lat_lon ON stops(lat,lon);
        """)
        con.executemany("INSERT INTO stops VALUES(?,?,?,?,?,?,?)",[
            ('FAR','far','Far Stop','B','BCT',52.56,-1.39),
            ('NEAR','near','Nearest Stop','A','BCT',52.5505,-1.3805),
        ])
    place=svc.nearest_bus_stop(52.55,-1.38)
    assert place['id']=='bus-stop:NEAR'
    assert place['name']=='Nearest Stop'
    assert place['distance_km'] < 0.1


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


def test_station_board_links_unique_uid_restored_without_date_not_reused_headcode(tmp_path: Path):
    store=ScheduleStore(tmp_path/'schedule.sqlite')
    now=datetime.now(london_tz()).replace(second=0,microsecond=0)
    service_date=now.date().isoformat()
    with store._connect() as con:
        con.execute("INSERT INTO tiplocs(tiploc,stanox,crs,description,lat,lon) VALUES(?,?,?,?,?,?)",('LESTER','12345','LEI','LEICESTER',52.631,-1.125))
        for uid,offset,destination in (('RIGHT1',12,'London St Pancras'),('OTHER1',17,'Sheffield')):
            locations=[
                {'tiploc_code':'LESTER','scheduled_departure':(now+timedelta(minutes=offset)).strftime('%H%M'),'platform':'2'},
                {'tiploc_code':'DEST','scheduled_arrival':(now+timedelta(minutes=offset+50)).strftime('%H%M')},
            ]
            con.execute(
                """INSERT INTO schedules(train_uid,start_date,end_date,days_runs,stp_indicator,atoc_code,signalling_id,origin_name,destination_name,locations_json)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (uid,service_date,service_date,'1111111','P','EM','1F10','Leicester',destination,json.dumps(locations)),
            )
    state=LiveState()
    live=Vehicle(id='nr:live-right',type='train',name='1F10',headcode='1F10',train_uid='RIGHT1',service_date=None,lat=52.631,lon=-1.125,source='Network Rail TRUST')
    state.upsert(live)
    board=SmartPlacesService(tmp_path,store,state)._scheduled_station_calls('LEI',10)
    rows={row['train_uid']:row for row in board['departures']}
    assert rows['RIGHT1']['live_train_id']=='nr:live-right'
    assert 'live_train_id' not in rows['OTHER1']
    assert rows['RIGHT1']['service_date']==service_date


def test_scheduled_train_detail_returns_calling_points_without_live_marker(tmp_path: Path):
    store=ScheduleStore(tmp_path/'schedule.sqlite')
    now=datetime.now(london_tz())
    locations=[
        {'tiploc_code':'NUNEAT','scheduled_departure':'1010'},
        *[
            {'tiploc_code':f'PASS{index:02d}','scheduled_pass':'1015'}
            for index in range(45)
        ],
        {'tiploc_code':'HNK','scheduled_arrival':'1020','scheduled_departure':'1022'},
        {'tiploc_code':'LESTER','scheduled_arrival':'1040'},
    ]
    with store._connect() as con:
        con.executemany(
            "INSERT INTO tiplocs(tiploc,description,lat,lon) VALUES(?,?,?,?)",
            [
                ('NUNEAT','Nuneaton',52.526,-1.464),
                ('WIGSTON','Wigston Junction',52.586,-1.105),
                ('HNK','Hinckley',52.535,-1.371),
                ('LESTER','Leicester',52.631,-1.125),
            ],
        )
        con.execute(
            """INSERT INTO schedules(train_uid,start_date,end_date,source,atoc_code,origin_name,destination_name,locations_json,power_type,booked_speed)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            ('C12345',(now-timedelta(days=1)).date().isoformat(),(now+timedelta(days=1)).date().isoformat(),'SCHEDULE','XC','Birmingham New Street','Edinburgh',json.dumps(locations),'DMU','125'),
        )
    detail=SmartPlacesService(tmp_path,store,FakeState()).scheduled_train_detail('C12345','1L23')
    assert detail['live_marker'] is False
    assert detail['runs'][0]['headcode']=='1L23'
    assert detail['runs'][0]['operator']=='CrossCountry'
    assert detail['runs'][0]['power_type']=='Diesel multiple unit'
    assert detail['runs'][0]['booked_speed_mph']=='125'
    assert detail['runs'][0]['likely_train']['label']=='Class 220/221 Voyager family'
    assert [row['name'] for row in detail['runs'][0]['calling_points']]==['Nuneaton','Hinckley','Leicester']
    assert {row['event'] for row in detail['runs'][0]['calling_points']}=={'Stops'}
    assert detail['runs'][0]['route_path']==[
        [52.526,-1.464],[52.535,-1.371],[52.631,-1.125],
    ]


def test_live_train_exact_schedule_id_supplies_route_without_uid(tmp_path: Path):
    store=ScheduleStore(tmp_path/'schedule.sqlite')
    now=datetime.now(london_tz())
    locations=[
        {'tiploc_code':'SHEFFLD','scheduled_departure':'0700'},
        {'tiploc_code':'LESTER','scheduled_arrival':'0800','scheduled_departure':'0802'},
        {'tiploc_code':'STPX','scheduled_arrival':'0910'},
    ]
    with store._connect() as con:
        con.executemany(
            "INSERT INTO tiplocs(tiploc,description,lat,lon) VALUES(?,?,?,?)",
            [
                ('SHEFFLD','Sheffield',53.378,-1.463),
                ('LESTER','Leicester',52.631,-1.125),
                ('STPX','London St Pancras',51.530,-0.125),
            ],
        )
        cursor=con.execute(
            """INSERT INTO schedules(train_uid,start_date,end_date,days_runs,stp_indicator,source,atoc_code,signalling_id,origin_name,destination_name,locations_json)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            ('ROUTE1',(now-timedelta(days=1)).date().isoformat(),(now+timedelta(days=1)).date().isoformat(),'1111111','P','SCHEDULE','EM','1C17','Sheffield','London St Pancras',json.dumps(locations)),
        )
        schedule_id=cursor.lastrowid
    vehicle=Vehicle(
        id='td:LE:1C17',type='train',name='1C17',headcode='1C17',schedule_id=schedule_id,
        service_date=now.date().isoformat(),lat=52.631,lon=-1.125,source='Network Rail TD',
    )

    detail=SmartPlacesService(tmp_path,store,FakeState()).train_vehicle_detail(vehicle)

    run=detail['runs'][0]
    assert run['origin']=='Sheffield'
    assert run['destination']=='London St Pancras'
    assert [row['name'] for row in run['calling_points']]==['Sheffield','Leicester','London St Pancras']
    assert run['route_path']==[[53.378,-1.463],[52.631,-1.125],[51.53,-0.125]]
    assert 'route and calling points' in detail['note']


def test_live_td_train_recovers_full_route_from_headcode_and_reporting_location(tmp_path: Path):
    store=ScheduleStore(tmp_path/'schedule.sqlite')
    now=datetime.now(london_tz()).replace(second=0,microsecond=0)
    locations=[
        {'tiploc_code':'LESTER','scheduled_departure':now.strftime('%H%M')},
        {'tiploc_code':'KNIGHTN','scheduled_pass':(now+timedelta(minutes=3)).strftime('%H%M')},
        {'tiploc_code':'MARKTH','scheduled_arrival':(now+timedelta(minutes=15)).strftime('%H%M')},
    ]
    with store._connect() as con:
        con.executemany(
            "INSERT INTO tiplocs(tiploc,stanox,description,lat,lon) VALUES(?,?,?,?,?)",
            [
                ('LESTER','12345','Leicester',52.631,-1.125),
                ('KNIGHTN','12346','Knighton Junction',None,None),
                ('MARKTH','12347','Market Harborough',52.478,-0.908),
            ],
        )
        con.execute(
            """INSERT INTO schedules(train_uid,start_date,end_date,days_runs,stp_indicator,source,atoc_code,signalling_id,origin_name,destination_name,locations_json)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            ('RECOVER1',(now-timedelta(days=1)).date().isoformat(),(now+timedelta(days=1)).date().isoformat(),'1111111','P','SCHEDULE','EM','1C17','Sheffield','London St Pancras',json.dumps(locations)),
        )
    (tmp_path/'public_rail_geo.json').write_text(json.dumps({
        'by_tiploc': {
            'KNIGHTN': {'name':'Knighton Junction','lat':52.602,'lon':-1.118},
        },
    }),encoding='utf-8')
    vehicle=Vehicle(
        id='td:LE:1C17',type='train',name='1C17',headcode='1C17',
        rail_tiploc='LESTER',rail_stanox='12345',last_updated=now,
        lat=52.631,lon=-1.125,source='Network Rail TD',
    )

    detail=SmartPlacesService(tmp_path,store,FakeState()).train_vehicle_detail(vehicle)

    run=detail['runs'][0]
    assert run['train_uid']=='RECOVER1'
    assert run['route_path']==[
        [52.631,-1.125],[52.602,-1.118],[52.478,-0.908],
    ]
    assert run['route_resolution']=={
        'kind':'published_timing_path',
        'timing_points':3,
        'mapped_timing_points':3,
        'coverage_percent':100,
        'schedule_recovered':True,
    }


def test_train_route_keeps_every_future_passenger_stop_after_the_current_station(tmp_path: Path):
    store=ScheduleStore(tmp_path/'schedule.sqlite')
    service_date=datetime.now(london_tz()).date().isoformat()
    locations=[{'tiploc_code':f'STOP{index:02d}','scheduled_arrival':f'{10+index//60:02d}{index%60:02d}','scheduled_departure':f'{10+index//60:02d}{index%60:02d}'} for index in range(45)]
    locations.append({'tiploc_code':'LONDON','scheduled_arrival':'1200'})
    with store._connect() as con:
        con.executemany("INSERT INTO tiplocs(tiploc,description) VALUES(?,?)",[(f'STOP{index:02d}',f'Stop {index}') for index in range(45)]+[('LONDON','London St Pancras')])
        con.execute(
            """INSERT INTO schedules(train_uid,start_date,end_date,days_runs,stp_indicator,source,atoc_code,signalling_id,origin_name,destination_name,locations_json)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            ('LONG01',service_date,service_date,'1111111','P','SCHEDULE','EM','1C17','Sheffield','London St Pancras',json.dumps(locations)),
        )
    detail=SmartPlacesService(tmp_path,store,FakeState()).scheduled_train_detail('LONG01','1C17',service_date)
    calls=detail['runs'][0]['calling_points']
    assert len(calls)==46
    assert calls[-1]['name']=='London St Pancras'


def test_live_train_lookup_respects_service_date_for_repeated_uid(tmp_path: Path):
    store=ScheduleStore(tmp_path/'schedule.sqlite')
    state=LiveState()
    first=Vehicle(id='nr:first',type='train',name='1A01',headcode='1A01',train_uid='SAME01',service_date='2026-11-05',lat=52,lon=-1,source='TRUST')
    second=Vehicle(id='nr:second',type='train',name='1A01',headcode='1A01',train_uid='SAME01',service_date='2026-11-06',lat=53,lon=-1,source='TRUST')
    state.upsert(first);state.upsert(second)
    svc=SmartPlacesService(tmp_path,store,state)
    assert svc.locate_live_train('1A01','SAME01','2026-11-05').id=='nr:first'
    assert svc.locate_live_train('1A01','SAME01','2026-11-06').id=='nr:second'


def test_scheduled_train_detail_does_not_reuse_an_expired_route(tmp_path: Path):
    store=ScheduleStore(tmp_path/'schedule.sqlite')
    now=datetime.now(london_tz())
    with store._connect() as con:
        con.execute(
            """INSERT INTO schedules(train_uid,start_date,end_date,source,atoc_code,origin_name,destination_name,locations_json)
               VALUES(?,?,?,?,?,?,?,?)""",
            ('COLD01',(now-timedelta(days=10)).date().isoformat(),(now-timedelta(days=5)).date().isoformat(),'SCHEDULE','XC','Old origin','Old destination','[]'),
        )
    detail=SmartPlacesService(tmp_path,store,FakeState()).scheduled_train_detail('COLD01','1A01')
    run=detail['runs'][0]
    assert run.get('origin') is None
    assert run.get('destination') is None
    assert run.get('calling_points') is None


def test_td_only_train_detail_does_not_guess_schedule_from_reused_headcode(tmp_path: Path):
    store=ScheduleStore(tmp_path/'schedule.sqlite')
    now=datetime.now(london_tz())
    locations=[
        {'tiploc_code':'CHFD','scheduled_departure':(now+timedelta(minutes=2)).strftime('%H%M')},
        {'tiploc_code':'DRBY','scheduled_arrival':(now+timedelta(minutes=34)).strftime('%H%M')},
        {'tiploc_code':'LESTER','scheduled_arrival':(now+timedelta(minutes=60)).strftime('%H%M')},
    ]
    with store._connect() as con:
        con.executemany(
            "INSERT INTO tiplocs(tiploc,description) VALUES(?,?)",
            [('CHFD','Chesterfield'),('DRBY','Derby'),('LESTER','Leicester')],
        )
        con.execute(
            """INSERT INTO schedules(train_uid,start_date,end_date,days_runs,stp_indicator,source,atoc_code,signalling_id,origin_name,destination_name,locations_json)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            ('C2S53',(now-timedelta(days=1)).date().isoformat(),(now+timedelta(days=1)).date().isoformat(),'1111111','P','SCHEDULE','EM','2S53','Sheffield','Leicester',json.dumps(locations)),
        )
    vehicle=Vehicle(
        id='td:DY:2S53',type='train',name='2S53',headcode='2S53',
        lat=53.235,lon=-1.421,status='TD live',source='Network Rail TD',
    )

    detail=SmartPlacesService(tmp_path,store,FakeState()).train_vehicle_detail(vehicle)

    run=detail['runs'][0]
    assert run.get('train_uid') is None
    assert run.get('origin') is None
    assert run.get('destination') is None
    assert run.get('calling_points') is None
    assert 'No timetable identity could be verified' in detail['note']


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


def test_bus_vehicle_journey_uses_only_exact_live_prediction(tmp_path: Path):
    store=ScheduleStore(tmp_path/'schedule.sqlite')
    vehicle=_bus('bus:7',vehicle_ref='vehicle-7',next_location='stop-7')
    vehicle.journey_ref='trip-7'
    collector=type('Collector',(),{
        'predictions_for_stop': lambda self, stop_ref, limit=25: [
            {'stop_ref':stop_ref,'vehicle_ref':'other','journey_ref':'other-trip','expected':'2099-01-01T10:00:00Z'},
            {'stop_ref':stop_ref,'vehicle_ref':'vehicle-7','journey_ref':'trip-7','expected':'2099-01-01T10:06:00Z','delay_minutes':6,'status':'Live • 6 min late'},
        ]
    })()
    svc=SmartPlacesService(tmp_path,store,FakeStateWithGet(),bods_collector_getter=lambda:collector)
    svc.bus_timetables.journey_for_vehicle=lambda _vehicle,limit=12:{'mode':'matched_timetable','stops':[]}

    payload=svc.bus_vehicle_journey(vehicle)

    assert payload['live_prediction']['vehicle_ref']=='vehicle-7'
    assert payload['live_prediction']['delay_minutes']==6
    assert 'operator-published' in payload['note']

class FakeJsonResponse:
    def __init__(self, payload):
        self.payload=payload
    def raise_for_status(self):
        return None
    def json(self):
        return self.payload


def test_rail_board_waits_for_direct_network_rail_index(tmp_path: Path):
    store=ScheduleStore(tmp_path/'schedule.sqlite')
    train=type('Train',(),{'id':'nr:123ABC'})()
    state=FakeStateWithGet({'nr:123ABC':train})
    svc=SmartPlacesService(tmp_path,store,state)
    payload=svc.rail_board('HNK',10)
    assert payload['mode']=='schedule_sync'
    assert payload['source']=='Published railway timetable'
    assert payload['departures']==[]
    assert 'no personal-use or scraped fallback' in payload['note']


def test_ready_but_incomplete_schedule_reports_sync_instead_of_unlicensed_fallback(tmp_path: Path):
    store=ScheduleStore(tmp_path/'schedule.sqlite')
    store.ready=lambda: True
    svc=SmartPlacesService(tmp_path,store,FakeStateWithGet())
    payload=svc.rail_board('HNK',10)
    assert payload['mode']=='schedule_sync'
    assert payload['departures']==[]
    assert 'no calls for this station' in payload['note']


def _vessel(item_id: str, *, lat: float, lon: float=-1.0, bearing: float=90, speed: float=8):
    return Vehicle(
        id=item_id, type='vessel', name=item_id, subtitle='Live AIS vessel',
        lat=lat, lon=lon, operator='AIS', status='Under way', source='AISStream',
        position_method='gps', position_label='Live AIS', vehicle_ref=item_id[-3:],
        speed=speed, speed_unit='kt', bearing=bearing,
    )


def test_ferry_board_excludes_unrelated_vessels_tens_of_kilometres_away(tmp_path: Path):
    store=ScheduleStore(tmp_path/'schedule.sqlite')
    near=_vessel('vessel:near',lat=52.03,bearing=90)
    inbound=_vessel('vessel:inbound',lat=52.14,bearing=180)
    far=_vessel('vessel:far',lat=52.315,bearing=180)
    svc=SmartPlacesService(tmp_path,store,FakeStateWithGet({v.id:v for v in (near,inbound,far)}))
    payload=svc.ferry_board({'lat':52.0,'lon':-1.0},10)
    ids={row['live_vehicle_id'] for row in payload['rows']}
    assert 'vessel:inbound' in ids
    assert 'vessel:far' not in ids
    assert all(row['distance_km'] <= 20 for row in payload['rows'])


def test_ferry_board_nearby_fallback_is_limited_to_five_kilometres(tmp_path: Path):
    store=ScheduleStore(tmp_path/'schedule.sqlite')
    near=_vessel('vessel:near',lat=52.03,bearing=90)
    passing=_vessel('vessel:passing',lat=52.09,bearing=90)
    svc=SmartPlacesService(tmp_path,store,FakeStateWithGet({v.id:v for v in (near,passing)}))
    payload=svc.ferry_board({'lat':52.0,'lon':-1.0},10)
    assert payload['mode']=='live_harbour_activity'
    assert [row['live_vehicle_id'] for row in payload['rows']]==['vessel:near']
    assert 'within 5 km' in payload['note']

class FakeNearestSession:
    def get(self, url, params=None, timeout=None):
        if 'api.tomtom.com/search/2/search/' in url:
            return FakeJsonResponse({'results':[{
                'poi':{'name':'Hinckley Train Station'},
                'position':{'lat':52.5350,'lon':-1.3710},
                'address':{'freeformAddress':'Hinckley Station'},
            }]})
        raise AssertionError(url)


def test_nearest_station_can_resolve_from_tomtom_before_local_cache(tmp_path: Path):
    store=ScheduleStore(tmp_path/'schedule.sqlite')
    with store._connect() as con:
        con.execute(
            "INSERT INTO tiplocs(tiploc,stanox,crs,description,lat,lon) VALUES(?,?,?,?,?,?)",
            ('HNK','12345','HNK','HINCKLEY TRAIN STATION',52.535,-1.371),
        )
    svc=SmartPlacesService(tmp_path,store,FakeStateWithGet())
    svc._session=FakeNearestSession()
    place=svc.nearest_rail_station(52.55,-1.38,tomtom_api_key='test-key')
    assert place['id']=='rail-station:HNK'
    assert place['crs']=='HNK'
    assert place['source']=='Network Rail SCHEDULE + rail geography'
    assert svc.get_place('rail-station:HNK')['crs']=='HNK'


def test_environment_measure_url_is_restricted_to_ea_host():
    url = "https://environment.data.gov.uk/flood-monitoring/id/measures/example"
    assert _ea_readings_url(url) == url + "/readings"


def test_environment_measure_url_rejects_external_userinfo_host():
    malicious = "https://environment.data.gov.uk@attacker.example/flood-monitoring/id/measures/x"
    try:
        _ea_readings_url(malicious)
    except ValueError as exc:
        assert str(exc) == "Unsupported Environment Agency measure URL"
    else:
        raise AssertionError("external Environment Agency URL was accepted")
