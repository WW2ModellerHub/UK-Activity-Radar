from pathlib import Path

from app.config import settings
from app.reference_data import sync_reference_data
from app.schedule_store import sync_schedule_data

root = Path(__file__).resolve().parent
data = root / "data"
if not settings.credentials_present:
    raise SystemExit("No credentials in .env. Run run.bat once to configure them.")

print("UK Activity Radar v0.4.1 - manual rail-data sync")
print("1/2 Downloading/indexing Network Rail full SCHEDULE...")
try:
    result = sync_schedule_data(settings.network_rail_username, settings.network_rail_password, data, force=True)
    for k, v in result.items():
        print(f"  {k}: {v}")
except Exception as exc:
    print(f"  SCHEDULE ERROR: {type(exc).__name__}: {exc}")

print("\n2/2 Syncing broad rail geography + CORPUS + SMART + NaPTAN...")
try:
    result = sync_reference_data(settings.network_rail_username, settings.network_rail_password, data, force=True)
    for k, v in result.items():
        print(f"  {k}: {v}")
except Exception as exc:
    print(f"  REFERENCE ERROR: {type(exc).__name__}: {exc}")

print("\nFinished. Restart run.bat so live collectors reload the new data.")
