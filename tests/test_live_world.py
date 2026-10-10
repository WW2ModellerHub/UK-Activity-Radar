from types import SimpleNamespace

import requests

from app.collectors.live_world import PowerOutageCollector, parse_bgs_georss, parse_carbon_regions, _parse_outage_csv


def test_bgs_georss_maps_earthquake():
    xml='''<?xml version="1.0"?><rss xmlns:georss="http://www.georss.org/georss"><channel><item><title>2.8 ML LEICESTERSHIRE</title><description>Magnitude 2.8</description><georss:point>52.60 -1.20</georss:point><link>https://example.test/q</link></item></channel></rss>'''
    rows=parse_bgs_georss(xml)
    assert len(rows)==1
    assert rows[0].type=='earthquake'
    assert rows[0].interesting is True
    assert rows[0].lat==52.6


def test_carbon_regions_maps_current_region():
    payload={"data":[{"regions":[{"regionid":7,"shortname":"East Midlands","intensity":{"forecast":153,"index":"moderate"},"generationmix":[{"fuel":"wind","perc":42},{"fuel":"gas","perc":20}]}]}]}
    rows=parse_carbon_regions(payload)
    assert len(rows)==1
    assert rows[0].type=='grid'
    assert '153' in rows[0].subtitle
    assert rows[0].name=='East Midlands'


def test_power_outage_csv_flexible_fields():
    text='Incident ID,Latitude,Longitude,Location,Customers Affected,Status,Reason\nABC,52.5,-1.4,Hinckley,800,Live,Fault\n'
    rows=_parse_outage_csv(text,'Test DNO')
    assert len(rows)==1
    assert rows[0].type=='outage'
    assert rows[0].interesting is True
    assert '800' in rows[0].subtitle


def test_power_outage_snapshot_fails_instead_of_clearing_state_when_all_providers_fail():
    settings = SimpleNamespace(
        power_outages_poll_seconds=900,
        nged_powercuts_url="https://nged.test/outages.csv",
        ssen_outage_metadata_url="https://ssen.test/metadata.json",
    )
    collector = PowerOutageCollector(settings, state=None)

    class FailedSession:
        def get(self, *args, **kwargs):
            raise requests.ConnectionError("offline")

    collector._session = FailedSession()
    try:
        collector.fetch_once()
    except RuntimeError as exc:
        assert "All power-outage providers failed" in str(exc)
    else:
        raise AssertionError("failed providers were incorrectly treated as an empty healthy snapshot")
