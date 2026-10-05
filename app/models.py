from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

ActivityType = Literal["train", "bus", "vessel", "sar", "aircraft", "rescue", "road", "incident", "flood", "weather", "earthquake", "river", "tide", "wave", "grid", "airquality", "bike", "space", "satellite", "outage", "gritter", "camera", "other"]
PositionMethod = Literal["gps", "network_report", "estimated", "static", "demo"]


class Vehicle(BaseModel):
    kind: Literal["vehicle"] = "vehicle"
    id: str
    type: ActivityType
    name: str
    subtitle: str = ""
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    operator: str | None = None
    route: str | None = None
    origin: str | None = None
    destination: str | None = None
    headcode: str | None = None
    train_uid: str | None = None
    vehicle_ref: str | None = None
    journey_ref: str | None = None
    aircraft_type_code: str | None = None
    aircraft_model: str | None = None
    altitude_ft: float | None = None
    bearing: float | None = None
    occupancy: str | None = None
    next_location: str | None = None
    status: str = "Unknown"
    speed: float | None = None
    speed_unit: str | None = None
    delay_minutes: int | None = None
    position_method: PositionMethod = "estimated"
    position_label: str = "Estimated"
    source: str = "unknown"
    source_id: str | None = None
    tracking_count: int = 0
    interesting: bool = False
    last_updated: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Optional next-report metadata retained for context. Rail markers are not
    # geometrically interpolated between TRUST points because a straight line is
    # not a railway route; they remain at the last confirmed mapped report.
    reported_lat: float | None = None
    reported_lon: float | None = None
    next_lat: float | None = None
    next_lon: float | None = None
    estimate_started_at: datetime | None = None
    estimate_due_at: datetime | None = None


class Event(BaseModel):
    kind: Literal["event"] = "event"
    id: str
    type: ActivityType
    name: str
    subtitle: str = ""
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    category: str = "Event"
    status: str = "Active"
    severity: str | None = None
    road: str | None = None
    direction: str | None = None
    lanes_closed: str | None = None
    reason: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    position_method: PositionMethod = "static"
    position_label: str = "Official event location"
    source: str = "unknown"
    source_id: str | None = None
    official_url: str | None = None
    news_query: str | None = None
    social_query: str | None = None
    tracking_count: int = 0
    interesting: bool = True
    last_updated: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class SourceStatus(BaseModel):
    name: str
    enabled: bool = False
    connected: bool = False
    mode: str = "off"
    message: str = "Not started"
    last_message_at: datetime | None = None
    messages_seen: int = 0
    movements_mapped: int = 0
    movements_unmapped: int = 0
