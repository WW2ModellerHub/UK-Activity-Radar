from pathlib import Path

from app.config import settings
from app.reference_data import sync_reference_data

root = Path(__file__).resolve().parent
if not settings.credentials_present:
    raise SystemExit("Add Network Rail credentials to .env first (run run.bat once).")

print("Syncing broad rail geography + CORPUS + SMART + NaPTAN reference data...")
result = sync_reference_data(
    settings.network_rail_username,
    settings.network_rail_password,
    root / "data",
    force=True,
)
for key, value in result.items():
    print(f"{key}: {value}")
