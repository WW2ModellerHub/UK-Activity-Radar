from app.aircraft_info import AircraftInfoService


class Response:
    def __init__(self, payload, status=200):
        self._payload=payload
        self.status_code=status
        self.content=b'{}'
    def json(self):
        return self._payload
    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class Session:
    def __init__(self):
        self.calls=[]
        self.headers={}
    def get(self, url, params=None, timeout=None):
        self.calls.append((url,params))
        if 'ac_info' in url:
            return Response({'status':200,'reg':'G-MCGA','model':'Sikorsky S-92A','cn':'920001','country':'United Kingdom','link':'https://airport-data.com/aircraft/G-MCGA'})
        return Response({'status':200,'count':1,'data':[{'image':'https://airport-data.com/images/test.jpg','link':'https://airport-data.com/aircraft/photo/1','photographer':'Test Photographer'}]})


def test_aircraft_info_exact_hex_returns_identity_and_thumbnail():
    svc=AircraftInfoService()
    svc._session=Session()
    out=svc.lookup('407ABC','G-MCGA')
    assert out['ok'] is True
    assert out['registration']=='G-MCGA'
    assert out['model']=='Sikorsky S-92A'
    assert out['photo']['thumbnail_url'].endswith('test.jpg')
    # Cached: no second provider hit.
    svc.lookup('407ABC','G-MCGA')
    assert len(svc._session.calls)==2


def test_aircraft_info_rejects_bad_hex():
    svc=AircraftInfoService()
    out=svc.lookup('not-a-hex','G-TEST')
    assert out['ok'] is False
