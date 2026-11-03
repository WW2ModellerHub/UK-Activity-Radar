from __future__ import annotations

import csv
import re
from pathlib import Path
from threading import Lock


SOURCE_NAME = "UK Train Dataset"
SOURCE_URL = "https://github.com/jamie-lord/uk-train-dataset"


def _canonical_number(value: str | None) -> str:
    """Normalise formatting without turning a partial identifier into a match."""
    return re.sub(r"[\s-]+", "", str(value or "").strip().upper())


class UKTrainReference:
    """Small, read-only exact-number index over the bundled MIT dataset.

    TRUST headcodes and train UIDs are services, not rolling-stock numbers.  This
    class therefore refuses fuzzy, class-only, or substring matches: a result is
    returned only when the live feed supplies the complete vehicle number.
    """

    def __init__(self, csv_path: Path):
        self.csv_path = Path(csv_path)
        self._rows: dict[str, dict[str, str]] | None = None
        self._lock = Lock()

    def _load(self) -> dict[str, dict[str, str]]:
        if self._rows is not None:
            return self._rows
        with self._lock:
            if self._rows is not None:
                return self._rows
            rows: dict[str, dict[str, str]] = {}
            if self.csv_path.exists():
                with self.csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
                    for raw in csv.DictReader(handle):
                        number = _canonical_number(raw.get("Number"))
                        if number:
                            rows.setdefault(number, {str(k): str(v or "").strip() for k, v in raw.items()})
            self._rows = rows
            return rows

    @property
    def record_count(self) -> int:
        return len(self._load())

    def lookup(self, vehicle_number: str | None) -> dict:
        supplied = str(vehicle_number or "").strip()
        key = _canonical_number(supplied)
        if not key:
            return {
                "matched": False,
                "reason": "The live feed has not supplied an exact locomotive or unit number.",
                "source": SOURCE_NAME,
                "source_url": SOURCE_URL,
            }
        row = self._load().get(key)
        if row is None:
            return {
                "matched": False,
                "number": supplied,
                "reason": "No exact rolling-stock number match was found; no class has been guessed.",
                "source": SOURCE_NAME,
                "source_url": SOURCE_URL,
            }
        fields = {
            "number": row.get("Number"),
            "train_class": row.get("Class"),
            "owner": row.get("Owner"),
            "operator": row.get("Operator"),
            "pool_code": row.get("PoolCode"),
            "livery": row.get("Livery"),
            "depot": row.get("Depot"),
            "formation": row.get("Formation"),
            "name": row.get("Name"),
            "preserved_at": row.get("PreservedAt"),
            "stored_at": row.get("StoredAt"),
            "year_built": row.get("YearBuilt"),
            "works_number": row.get("WorksNumber"),
        }
        return {
            "matched": True,
            **{name: value for name, value in fields.items() if value},
            "match_basis": "exact_vehicle_number",
            "source": SOURCE_NAME,
            "source_url": SOURCE_URL,
            "caveat": "Reference data may be incomplete or out of date; this is not a live allocation source.",
        }
