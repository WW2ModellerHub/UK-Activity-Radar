"""Read-only rail smoke check against the same public endpoints as the map."""
from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from datetime import datetime, timezone
from urllib.request import urlopen

from websockets.asyncio.client import connect


async def audit(base: str, stations: list[str]) -> dict:
    socket_url = base.replace('https://', 'wss://').replace('http://', 'ws://') + '/ws'
    async with connect(socket_url, origin=base, open_timeout=20, max_size=8_000_000) as socket:
        await socket.send(json.dumps({'action': 'viewport', 'bbox': [-8.7, 49.5, 2.1, 59.2], 'zoom': 14, 'types': ['train']}))
        snapshot = json.loads(await asyncio.wait_for(socket.recv(), timeout=30))
    trains = [r for r in snapshot.get('activities', []) if r.get('type') == 'train']
    now = datetime.now(timezone.utc)
    ages = [(now - datetime.fromisoformat(r['last_updated'].replace('Z', '+00:00'))).total_seconds() for r in trains]
    report = {
        'checked_at': now.isoformat(),
        'trains': len(trains),
        'with_uid': sum(bool(r.get('train_uid')) for r in trains),
        'with_service_date': sum(bool(r.get('service_date')) for r in trains),
        'reports_under_5_minutes': sum(age < 300 for age in ages),
        'sources': dict(Counter(r.get('source') for r in trains)),
        'boards': {},
    }
    for crs in stations:
        def fetch():
            with urlopen(f'{base}/api/places/rail-station:{crs}/board?limit=10', timeout=30) as response:
                return json.load(response)
        payload = await asyncio.to_thread(fetch)
        departures = payload.get('board', {}).get('departures', [])
        report['boards'][crs] = []
        for row in departures:
            same_code = [r for r in trains if r.get('headcode') == row.get('headcode')]
            report['boards'][crs].append({
                'headcode': row.get('headcode'), 'uid': row.get('train_uid'),
                'scheduled': row.get('scheduled'), 'expected': row.get('expected'),
                'status': row.get('status'), 'live_id': row.get('live_train_id'),
                'map_candidates': [{k: r.get(k) for k in ('id', 'train_uid', 'service_date', 'rail_tiploc', 'rail_event_type', 'lat', 'lon', 'last_updated', 'source')} for r in same_code],
            })
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', default='https://uk-activity-radar.onrender.com')
    parser.add_argument('--stations', nargs='+', default=['LEI', 'BHM', 'STP'])
    args = parser.parse_args()
    print(json.dumps(asyncio.run(audit(args.base.rstrip('/'), args.stations)), indent=2))
