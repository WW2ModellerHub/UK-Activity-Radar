from app.search import ActivitySearch


class EmptyState:
    def all_activities(self, **kwargs):
        return []


class Response:
    def raise_for_status(self):
        pass
    def json(self):
        return {
            'results': [{
                'position': {'lat': 52.5, 'lon': -1.4},
                'address': {'freeformAddress': 'M69, Hinckley'},
                'viewport': {
                    'topLeftPoint': {'lat': 52.51, 'lon': -1.41},
                    'btmRightPoint': {'lat': 52.49, 'lon': -1.39},
                },
            }]
        }


class Session:
    def __init__(self): self.calls=[]
    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return Response()


def test_tomtom_search_is_preferred_when_configured():
    search=ActivitySearch(EmptyState(),'https://nominatim.test/search',tomtom_api_key='secret')
    session=Session(); search._session=session
    result=search._geocode('M69 J1')
    assert result['source']=='TomTom Search'
    assert result['lat']==52.5
    assert '/search/2/search/' in session.calls[0][0]
    assert session.calls[0][1]['params']['key']=='secret'
    assert session.calls[0][1]['params']['countrySet']=='GB'
