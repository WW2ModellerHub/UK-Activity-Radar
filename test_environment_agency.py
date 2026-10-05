from pathlib import Path
from app.config import settings
from app.collectors.environment_agency import EnvironmentAgencyFloodCollector
from app.state import LiveState

collector = EnvironmentAgencyFloodCollector(settings, LiveState(), Path(__file__).parent / "data")
rows = collector.fetch_once()
print(f"SUCCESS: mapped {len(rows):,} current Environment Agency flood warnings/alerts.")
for row in rows[:10]:
    print(f"- {row.status}: {row.name} @ {row.lat:.5f}, {row.lon:.5f}")
