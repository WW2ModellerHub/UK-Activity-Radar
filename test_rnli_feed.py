from app.collectors.rnli import RNLILaunchCollector
from app.config import settings
from app.state import LiveState

collector = RNLILaunchCollector(settings, LiveState())
rows = collector.fetch_once()
print(f"SUCCESS: parsed {len(rows):,} recent RNLI launch records with mappable coordinates.")
for row in sorted(rows, key=lambda x: x.last_updated, reverse=True)[:12]:
    print(f"- {row.name} @ {row.lat:.5f}, {row.lon:.5f} • {row.start_time or row.last_updated}")
