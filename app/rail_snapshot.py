from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from .models import Vehicle


class RailPositionSnapshot:
    """Durable, short-lived warm start for Network Rail event streams."""

    def __init__(self, path: Path, *, max_age_minutes: int = 45, max_rows: int = 5000) -> None:
        self.path = Path(path)
        self.max_age = timedelta(minutes=max(1, int(max_age_minutes)))
        self.max_rows = max(1, int(max_rows))

    def load(self, *, now: datetime | None = None) -> list[Vehicle]:
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        cutoff = current - self.max_age
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return []
        rows = payload.get("trains") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            return []
        restored: list[Vehicle] = []
        for raw in rows[: self.max_rows]:
            try:
                vehicle = Vehicle.model_validate(raw)
            except (TypeError, ValueError):
                continue
            updated = vehicle.last_updated.astimezone(timezone.utc)
            if (
                vehicle.type != "train"
                or not str(vehicle.source or "").startswith("Network Rail")
                or updated < cutoff
                or updated > current + timedelta(minutes=5)
            ):
                continue
            restored.append(vehicle)
        restored.sort(key=lambda row: row.last_updated, reverse=True)
        return restored

    def save(self, vehicles: Iterable[Vehicle], *, now: datetime | None = None) -> int:
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        cutoff = current - self.max_age
        rows = [
            vehicle for vehicle in vehicles
            if vehicle.type == "train"
            and str(vehicle.source or "").startswith("Network Rail")
            and vehicle.last_updated.astimezone(timezone.utc) >= cutoff
        ]
        rows.sort(key=lambda row: row.last_updated, reverse=True)
        rows = rows[: self.max_rows]
        payload = {
            "saved_at": current.isoformat(),
            "trains": [row.model_dump(mode="json", exclude_none=True) for row in rows],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        os.replace(temporary, self.path)
        return len(rows)
