"""Explainable, server-side classification for the Cool experience.

Collectors should describe what their source actually reported.  This module is
the single place that turns those descriptions into product categories and a
priority score.  In particular, a source merely marking an aircraft as military
is not enough to make it a Best Spot candidate: an identified combat, support,
display or rescue role is required.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from .models import Activity, CoolCategory


COOL_CANDIDATE_THRESHOLD = 45


@dataclass(frozen=True, slots=True)
class CoolClassification:
    category: CoolCategory | None = None
    subtype: str | None = None
    interest_score: int = 0
    explanation: str | None = None
    badge: str | None = None
    theme: str | None = None
    icon: str | None = None
    candidate: bool = False


_COMBAT_AIRCRAFT = re.compile(
    r"\b(?:EUROFIGHTER|TYPHOON|EUFI|F[- ]?35|LIGHTNING\s*II|F[- ]?15|F[- ]?16|"
    r"F/?A[- ]?18|F[- ]?18|RAFALE|TORNADO|GRIPEN|MIRAGE|MIG[- ]?\d+|SU[- ]?\d+)\b"
)
_BOMBER = re.compile(r"\b(?:B[- ]?(?:1B?|2A?|52H?)|BOMBER|SPIRIT|LANCER)\b")
_DISPLAY = re.compile(r"\b(?:RED\s*ARROWS?|REDARROW|AEROBATIC|DISPLAY\s+(?:TEAM|FLIGHT|AIRCRAFT))\b")
_SURVEILLANCE = re.compile(r"\b(?:AWACS|SENTRY|E[- ]?3[ADTF]?|E3(?:CF|TF)|AIRBORNE\s+(?:EARLY\s+WARNING|SURVEILLANCE))\b")
_TANKER = re.compile(r"\b(?:TANKER|VOYAGER|A330\s*MRTT|MRTT|KC[- ]?(?:10|46|135)|K35R|QID\w*|TARTN\w*)\b")
_MARITIME_PATROL = re.compile(r"\b(?:POSEIDON|P[- ]?8A?|MARITIME\s+PATROL)\b")
_INTELLIGENCE = re.compile(
    r"\b(?:RIVET\s+JOINT|RC[- ]?135|R135|SHADOW\s*R1|SENTINEL\s*R1|INTELLIGENCE|"
    r"ELECTRONIC\s+SURVEILLANCE)\b"
)
_HEAVY_LIFT = re.compile(r"\b(?:A400M?|ATLAS|C[- ]?17|GLOBEMASTER|C[- ]?130|C30J|HERCULES)\b")
_COMBAT_HELICOPTER = re.compile(
    r"\b(?:APACHE|CHINOOK|LYNX|WILDCAT|MERLIN|GAZELLE|PUMA|"
    r"AH[- ]?64|CH[- ]?47|H47|H64|LYNX\s*AH\.?\s*7)\b"
)
_TRAINER = re.compile(
    r"\b(?:TRAINER|TRAINING\s+(?:AIRCRAFT|FLIGHT)|HAWK|TUTOR|PREFECT|TEXAN|JUNO|JUPITER|"
    r"GROB|T[- ]?6|PC[- ]?21|PHENOM)\b"
)
_MILITARY_IDENTITY = re.compile(r"\b(?:RRR|RFR|ASCOT|RCH|CNV|BAF|IAM|MMF|VAMP|OBI|VYT)\w*\b")

_STEAM = re.compile(
    r"\b(?:STEAM(?:[- ]HAULED)?|PACIFIC|TORNADO|FLYING\s+SCOTSMAN|"
    r"60103|60163|70000|46100|45596|34067)\b"
)
_RAILTOUR = re.compile(r"\b(?:RAIL\s*TOUR|CHARTER|HERITAGE|SPECIAL\s+PASSENGER)\b")
_TEST_TRAIN = re.compile(r"\b(?:TEST\s+TRAIN|MEASUREMENT\s+TRAIN|NEW\s+MEASUREMENT\s+TRAIN|NMT)\b")
_ENGINEERING_TRAIN = re.compile(r"\b(?:ENGINEERING\s+TRAIN|TAMPER|RAIL\s+GRINDER|BALLAST\s+TRAIN)\b")
_FREIGHT = re.compile(r"\b(?:FREIGHT|CARGO|DB\s+CARGO|FREIGHTLINER|GBRF|COLAS\s+RAIL)\b")

_NAVAL = re.compile(r"\b(?:HMS|RFA|WARSHIP|ROYAL\s+NAVY|NAVAL|FRIGATE|DESTROYER|AIRCRAFT\s+CARRIER)\b")
_LIFEBOAT = re.compile(r"\b(?:RNLI|LIFEBOAT)\b")
_MARITIME_RESCUE = re.compile(r"\b(?:COASTGUARD|SEARCH\s+AND\s+RESCUE|RESCUE\s+VESSEL)\b")
_PATROL = re.compile(r"\bPATROL\s+(?:BOAT|VESSEL|SHIP)\b")
_CRUISE = re.compile(r"\b(?:QUEEN\s+MARY|CUNARD|CRUISE|PRINCESS\s+CRUISES?|CELEBRITY|AIDA\w*|MSC\s+)\b")
_SPECIALIST_VESSEL = re.compile(r"\b(?:RESEARCH|SURVEY|OCEANOGRAPH|EXPLORER|CABLE\s+(?:SHIP|LAYER)|OFFSHORE)\b")
_TUG = re.compile(r"\b(?:TUG|TOWING|UNDER\s+TOW)\b")

_COASTGUARD_AIR = re.compile(r"\b(?:COASTGUARD|BRITISHRESCUE|RESCUE\w*|SR[DG]\d+|G-MCG[A-Z])\b")
_HEMS = re.compile(r"\b(?:HELIMED\w*|AIRAMB\w*|AIR\s+AMBULANCE|HEMS)\b")


def _searchable(activity: Activity) -> str:
    """Return source data useful for identity matching, excluding Cool output."""
    fields = (
        "name", "subtitle", "operator", "route", "origin", "destination",
        "headcode", "train_uid", "vehicle_ref", "aircraft_type_code",
        "aircraft_model", "status", "source", "source_id", "category",
        "severity", "road", "reason",
    )
    return " ".join(str(value) for field in fields if (value := getattr(activity, field, None))).upper()


def _age_minutes(activity: Activity, now: datetime) -> float:
    updated = getattr(activity, "last_updated", None)
    if not isinstance(updated, datetime):
        return 10_000.0
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    return max(0.0, (now - updated.astimezone(timezone.utc)).total_seconds() / 60.0)


def _classified(
    *,
    category: CoolCategory,
    subtype: str,
    score: int,
    explanation: str,
    badge: str,
    theme: str,
    icon: str,
    activity: Activity,
    now: datetime,
    force_exclude: bool = False,
) -> CoolClassification:
    age = _age_minutes(activity, now)
    kind = getattr(activity, "kind", "vehicle")

    # Rescue/event sources often expose a history as well as a live snapshot.
    # Historical records remain honestly classified, but must not win "now".
    stale = age > 60
    if kind == "event" and category == "rescue" and age > 360:
        score = min(score, COOL_CANDIDATE_THRESHOLD - 1)
        stale = True
        explanation = f"{explanation} Historical report; excluded from Best Spot now."
    elif stale:
        score = min(score, COOL_CANDIDATE_THRESHOLD - 1)
        explanation = f"{explanation} Position is stale; excluded from Best Spot now."

    score = max(0, min(100, int(score)))
    candidate = score >= COOL_CANDIDATE_THRESHOLD and not stale and not force_exclude
    return CoolClassification(
        category=category,
        subtype=subtype,
        interest_score=score,
        explanation=explanation,
        badge=badge,
        theme=theme,
        icon=icon,
        candidate=candidate,
    )


def classify_activity(activity: Activity, *, now: datetime | None = None) -> CoolClassification:
    """Classify one live activity without mutating it."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    text = _searchable(activity)
    activity_type = activity.type
    headcode = str(getattr(activity, "headcode", "") or "").upper().replace(" ", "")

    if activity_type == "sar":
        source = str(getattr(activity, "source", "") or "").upper()
        if "AIS" in source:
            return _classified(category="rescue", subtype="maritime-sar", score=90,
                               explanation="Search and rescue at sea.", badge="MARITIME SAR",
                               theme="rescue", icon="✚", activity=activity, now=now)
        if _COASTGUARD_AIR.search(text):
            return _classified(category="rescue", subtype="coastguard-aircraft", score=98,
                               explanation="Coastguard rescue helicopter.", badge="COASTGUARD",
                               theme="rescue", icon="✚", activity=activity, now=now)
        if _HEMS.search(text):
            return _classified(category="rescue", subtype="air-ambulance", score=93,
                               explanation="Air ambulance.", badge="HEMS",
                               theme="rescue", icon="✚", activity=activity, now=now)
        return _classified(category="rescue", subtype="sar-aircraft", score=88,
                           explanation="Search and rescue aircraft.", badge="SAR AIR",
                           theme="rescue", icon="✚", activity=activity, now=now)

    if activity_type == "aircraft":
        if _DISPLAY.search(text):
            return _classified(category="air", subtype="display-aircraft", score=98,
                               explanation="Display or aerobatic aircraft.", badge="DISPLAY",
                               theme="air", icon="▲", activity=activity, now=now)
        if _BOMBER.search(text):
            return _classified(category="air", subtype="bomber", score=100,
                               explanation="Military bomber.", badge="BOMBER",
                               theme="air", icon="▲", activity=activity, now=now)
        if _COMBAT_AIRCRAFT.search(text):
            return _classified(category="air", subtype="combat-aircraft", score=100,
                               explanation="Military fast jet.", badge="FAST JET",
                               theme="air", icon="▲", activity=activity, now=now)
        if _SURVEILLANCE.search(text):
            return _classified(category="air", subtype="airborne-surveillance", score=97,
                               explanation="Airborne early-warning aircraft.", badge="AWACS",
                               theme="air", icon="▲", activity=activity, now=now)
        if _TANKER.search(text):
            return _classified(category="air", subtype="military-tanker", score=94,
                               explanation="Air-to-air refuelling tanker.", badge="TANKER",
                               theme="air", icon="▲", activity=activity, now=now)
        if _MARITIME_PATROL.search(text):
            return _classified(category="air", subtype="maritime-patrol", score=93,
                               explanation="Maritime patrol aircraft.", badge="MARITIME PATROL",
                               theme="air", icon="▲", activity=activity, now=now)
        if _INTELLIGENCE.search(text):
            return _classified(category="air", subtype="intelligence-aircraft", score=92,
                               explanation="Military surveillance aircraft.", badge="INTELLIGENCE",
                               theme="air", icon="▲", activity=activity, now=now)
        if _COMBAT_HELICOPTER.search(text):
            return _classified(category="air", subtype="military-helicopter", score=89,
                               explanation="Military helicopter.", badge="MIL HELI",
                               theme="air", icon="▲", activity=activity, now=now)
        if _HEAVY_LIFT.search(text):
            return _classified(category="air", subtype="military-transport", score=84,
                               explanation="Military transport aircraft.", badge="AIRLIFT",
                               theme="air", icon="▲", activity=activity, now=now)
        if _TRAINER.search(text):
            return _classified(category="air", subtype="military-trainer", score=18,
                               explanation="Military training aircraft.", badge="TRAINER",
                               theme="air", icon="▲", activity=activity, now=now, force_exclude=True)
        if _MILITARY_IDENTITY.search(text) or bool(getattr(activity, "interesting", False)):
            return _classified(category="air", subtype="military-unresolved", score=40,
                               explanation="Military aircraft.",
                               badge="MIL / SPECIAL", theme="air", icon="▲", activity=activity, now=now,
                               force_exclude=True)

    if activity_type == "train":
        if _STEAM.search(text):
            return _classified(category="rail", subtype="steam", score=100,
                               explanation="Steam locomotive.", badge="STEAM", theme="steam", icon="♨",
                               activity=activity, now=now)
        if _RAILTOUR.search(text):
            return _classified(category="rail", subtype="railtour-special", score=95,
                               explanation="Charter train.", badge="RAILTOUR",
                               theme="rail", icon="◆", activity=activity, now=now)
        if headcode.startswith("1Z"):
            return _classified(category="rail", subtype="special-passenger", score=72,
                               explanation="Special passenger train.",
                               badge="SPECIAL", theme="rail", icon="◆", activity=activity, now=now)
        if _TEST_TRAIN.search(text) or headcode.startswith(("1Q", "3Q")):
            return _classified(category="rail", subtype="test-measurement", score=82,
                               explanation="Test or track-recording train.", badge="TEST TRAIN",
                               theme="rail", icon="◇", activity=activity, now=now)
        if _ENGINEERING_TRAIN.search(text):
            return _classified(category="rail", subtype="engineering", score=76,
                               explanation="Engineering train.", badge="ENGINEERING",
                               theme="rail", icon="⚒", activity=activity, now=now)
        if _FREIGHT.search(text) or (headcode and headcode[0] in {"4", "6", "7"}):
            return _classified(category="rail", subtype="freight", score=62,
                               explanation="Freight train.", badge="FREIGHT",
                               theme="freight", icon="▰", activity=activity, now=now)
        if headcode.startswith("0Z"):
            return _classified(category="rail", subtype="locomotive-move", score=70,
                               explanation="Locomotive on its own.", badge="LOCO MOVE",
                               theme="rail", icon="◆", activity=activity, now=now)

    if activity_type == "vessel":
        if _NAVAL.search(text):
            return _classified(category="sea", subtype="naval", score=98,
                               explanation="Naval ship.", badge="NAVAL", theme="naval", icon="⚓",
                               activity=activity, now=now)
        if _LIFEBOAT.search(text):
            return _classified(category="rescue", subtype="lifeboat", score=97,
                               explanation="RNLI lifeboat.", badge="LIFEBOAT",
                               theme="rescue", icon="◆", activity=activity, now=now)
        if _MARITIME_RESCUE.search(text):
            return _classified(category="rescue", subtype="rescue-vessel", score=90,
                               explanation="Coastguard vessel.", badge="RESCUE",
                               theme="rescue", icon="⚓", activity=activity, now=now)
        if _PATROL.search(text):
            return _classified(category="sea", subtype="patrol-vessel", score=82,
                               explanation="Patrol vessel.", badge="PATROL", theme="sea", icon="⚓",
                               activity=activity, now=now)
        if _CRUISE.search(text):
            return _classified(category="sea", subtype="cruise", score=74,
                               explanation="Cruise ship.", badge="CRUISE", theme="sea", icon="▤",
                               activity=activity, now=now)
        if _SPECIALIST_VESSEL.search(text):
            return _classified(category="sea", subtype="specialist-vessel", score=70,
                               explanation="Research vessel.", badge="SPECIALIST",
                               theme="sea", icon="⌁", activity=activity, now=now)
        if _TUG.search(text):
            return _classified(category="sea", subtype="tug", score=42,
                               explanation="Tug.", badge="TUG",
                               theme="sea", icon="⚓", activity=activity, now=now, force_exclude=True)

    if activity_type == "rescue":
        if _LIFEBOAT.search(text):
            return _classified(category="rescue", subtype="rnli-launch", score=96,
                               explanation="RNLI lifeboat launch.", badge="RNLI LAUNCH",
                               theme="rescue", icon="◆", activity=activity, now=now)
        return _classified(category="rescue", subtype="emergency-response", score=76,
                           explanation="Emergency response.", badge="RESCUE",
                           theme="rescue", icon="✚", activity=activity, now=now)

    return CoolClassification()


def apply_cool_classification(activity: Activity, *, now: datetime | None = None) -> Activity:
    """Populate the additive API fields on an activity and return it."""
    result = classify_activity(activity, now=now)
    activity.cool_category = result.category
    activity.cool_subtype = result.subtype
    activity.cool_score = result.interest_score
    activity.cool_explanation = result.explanation
    activity.cool_badge = result.badge
    activity.cool_theme = result.theme
    activity.cool_icon = result.icon
    activity.cool_candidate = result.candidate
    return activity
