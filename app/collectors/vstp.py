from __future__ import annotations

import json
from datetime import datetime, timezone

from ..config import Settings
from ..models import SourceStatus
from ..schedule_store import ScheduleStore
from ..state import LiveState
from .stomp_client import StompFeed


class VSTPCollector(StompFeed):
    def __init__(self, settings: Settings, state: LiveState, schedules: ScheduleStore) -> None:
        super().__init__(settings, state, name="vstp", topic=settings.vstp_topic)
        self.schedules = schedules

    def handle_message(self, payload: str) -> None:
        try:
            decoded = json.loads(payload)
        except json.JSONDecodeError:
            return
        messages = decoded if isinstance(decoded, list) else [decoded]
        status = self.state.source(self.name) or SourceStatus(name=self.name, enabled=True, connected=True, mode="live")
        stored = 0
        for wrapper in messages:
            status.messages_seen += 1
            if not isinstance(wrapper, dict):
                continue
            body = wrapper.get("VSTPCIFMsgV1") or wrapper.get("VSTP") or wrapper
            if not isinstance(body, dict):
                continue
            schedule = body.get("schedule") or body.get("JsonScheduleV1")
            if not isinstance(schedule, dict):
                continue
            transaction = str(schedule.get("transaction_type") or "Create").lower()
            if transaction != "delete":
                self.schedules.upsert_vstp(schedule)
                stored += 1
        status.last_message_at = datetime.now(timezone.utc)
        status.connected = True
        status.message = f"VSTP live • {status.messages_seen:,} messages • {stored:,} schedules updated in latest batch"
        self.state.set_source(status)
