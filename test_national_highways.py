from app.config import settings
from app.collectors.national_highways import NationalHighwaysCollector
from app.state import LiveState

if not settings.national_highways_api_key:
    raise SystemExit("No NATIONAL_HIGHWAYS_API_KEY found. Run configure_roads.bat first.")

collector = NationalHighwaysCollector(settings, LiveState())
rows = collector.fetch_once()
print(f"SUCCESS: parsed {len(rows):,} current National Highways closure/event records.")
for row in rows[:10]:
    print(f"- {row.type.upper():8} {row.name} @ {row.lat:.5f}, {row.lon:.5f}")
