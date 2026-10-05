from __future__ import annotations

import asyncio
import math
import random
from datetime import datetime, timezone

from ..models import SourceStatus, Vehicle
from ..state import LiveState


DEMO = [
    Vehicle(
        id="demo:1A26", type="train", name="1A26 Intercity", subtitle="Manchester → London",
        lat=52.43, lon=-1.50, operator="Demo Rail", route="Manchester → London",
        origin="Manchester", destination="London", status="7 min late", delay_minutes=7,
        speed=112, speed_unit="mph", position_method="demo", position_label="Demo position",
        source="demo", tracking_count=2831, interesting=True,
    ),
    Vehicle(
        id="demo:5Z71", type="train", name="5Z71 Steam Special", subtitle="York → Birmingham",
        lat=53.08, lon=-1.35, operator="Charter", route="York → Birmingham",
        origin="York", destination="Birmingham", status="On time", delay_minutes=0,
        speed=61, speed_unit="mph", position_method="demo", position_label="Demo position",
        source="demo", tracking_count=6842, interesting=True,
    ),
    Vehicle(
        id="demo:bus158", type="bus", name="Arriva 158", subtitle="Nuneaton → Leicester",
        lat=52.55, lon=-1.31, operator="Demo Bus", route="158", origin="Nuneaton",
        destination="Leicester", status="On route", speed=29, speed_unit="mph",
        position_method="demo", position_label="Demo GPS", source="demo", tracking_count=418,
    ),
    Vehicle(
        id="demo:sar936", type="sar", name="Coastguard Rescue 936", subtitle="Search & rescue activity",
        lat=50.42, lon=-4.53, operator="HM Coastguard", status="Airborne", speed=128,
        speed_unit="kt", position_method="demo", position_label="Demo ADS-B style position",
        source="demo", tracking_count=7331, interesting=True,
    ),
    Vehicle(
        id="demo:fire1", type="incident", name="Major fire — Birmingham", subtitle="Public incident demo",
        lat=52.48, lon=-1.90, status="Active", position_method="static",
        position_label="Approximate incident area", source="demo", tracking_count=2014,
    ),
    Vehicle(
        id="demo:road1", type="road", name="M1 J20 disruption", subtitle="Lutterworth area",
        lat=52.45, lon=-1.20, status="Delays", position_method="static",
        position_label="Approximate incident area", source="demo", tracking_count=957,
    ),
]


async def run_demo(state: LiveState) -> None:
    for vehicle in DEMO:
        state.upsert(vehicle)

    state.set_source(SourceStatus(
        name="demo", enabled=True, connected=True, mode="simulation",
        message="Demo data is active so the product works before live credentials are added.",
        last_message_at=datetime.now(timezone.utc), messages_seen=len(DEMO),
    ))

    tick = 0.0
    while True:
        tick += 0.18
        now = datetime.now(timezone.utc)
        for index, original in enumerate(DEMO[:4]):
            current = state.get(original.id)
            if not current:
                continue
            # Smooth, bounded movement around the demo corridor instead of random teleporting.
            amplitude_lat = 0.035 if current.type != "sar" else 0.055
            amplitude_lon = 0.050 if current.type != "sar" else 0.080
            current.lat = original.lat + math.sin(tick + index) * amplitude_lat
            current.lon = original.lon + math.cos(tick * 0.8 + index) * amplitude_lon
            current.tracking_count = max(0, current.tracking_count + random.randint(-8, 14))
            current.last_updated = now
            state.upsert(current)

        source = state.source("demo")
        if source:
            source.last_message_at = now
            source.messages_seen += 1
            state.set_source(source)
        await asyncio.sleep(2)
