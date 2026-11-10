from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, TypeAlias

from pydantic import BaseModel, Field

ActivityType = Literal["train", "bus", "vessel", "sar", "aircraft", "rescue", "road", "roadfeature", "incident", "flood", "weather", "earthquake", "river", "tide", "wave", "grid", "airquality", "bike", "space", "satellite", "outage", "gritter", "camera", "other"]
PositionMethod = Literal["gps", "network_report", "estimated", "static", "demo"]
CoolCategory = Literal["air", "rail", "sea", "rescue"]
IdentityConfidence = Literal["exact", "type", "unresolved"]


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
    service_date: str | None = None
    schedule_id: int | None = None
    rail_event_type: str | None = None
    rail_tiploc: str | None = None
    rail_stanox: str | None = None
    train_category: str | None = None
    power_type: str | None = None
    timing_load: str | None = None
    booked_speed: str | None = None
    operating_characteristics: str | None = None
    vehicle_ref: str | None = None
    journey_ref: str | None = None
    aircraft_type_code: str | None = None
    aircraft_model: str | None = None
    identity_label: str | None = None
    identity_confidence: IdentityConfidence = "unresolved"
    identity_explanation: str | None = None
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
    # Server-owned Cool classification. These fields are deliberately additive:
    # older clients may keep using ``interesting`` while newer clients can use a
    # single, explainable priority score instead of duplicating keyword rules.
    cool_category: CoolCategory | None = None
    cool_subtype: str | None = None
    cool_score: int = Field(default=0, ge=0, le=100)
    cool_explanation: str | None = None
    cool_badge: str | None = None
    cool_theme: str | None = None
    cool_icon: str | None = None
    cool_candidate: bool = False
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
    cool_category: CoolCategory | None = None
    cool_subtype: str | None = None
    cool_score: int = Field(default=0, ge=0, le=100)
    cool_explanation: str | None = None
    cool_badge: str | None = None
    cool_theme: str | None = None
    cool_icon: str | None = None
    cool_candidate: bool = False
    last_updated: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


Activity: TypeAlias = Vehicle | Event


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
