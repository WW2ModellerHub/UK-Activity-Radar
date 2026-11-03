"""Safe, stateless collection metadata for live map discoveries.

The browser owns a visitor's collection.  This module only defines the stable
catalogue and validates that a claimed activity is a current, real feed record.
It deliberately returns no coordinates: a collection card is a memory of a
map discovery, not an instruction to approach a moving vehicle or incident.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

from .cool_classifier import classify_activity
from .models import Activity


DiscoveryCategory = Literal["air", "rail", "sea"]


@dataclass(frozen=True, slots=True)
class Collectible:
    id: str
    key: str
    category: DiscoveryCategory
    subtype: str
    title: str
    explanation: str
    icon: str
    badge: str
    safety_note: str


_MAP_ONLY = "Discover from the live map only; never approach moving vehicles or restricted sites."
_RAIL_SAFETY = "Discover from the live map or a lawful public location; never enter railway property."
_SEA_SAFETY = "Discover from the live map; never enter restricted ports or interfere with vessel operations."


def _collectible(
    category: DiscoveryCategory,
    subtype: str,
    title: str,
    explanation: str,
    icon: str,
    badge: str,
    safety_note: str,
) -> Collectible:
    key = f"{category}:{subtype}"
    return Collectible(
        id=key,
        key=key,
        category=category,
        subtype=subtype,
        title=title,
        explanation=explanation,
        icon=icon,
        badge=badge,
        safety_note=safety_note,
    )


# These subtype names come directly from cool_classifier.py.  Rescue/SAR and
# unresolved identities are intentionally absent: active emergencies must
# never become a collect-and-chase mechanic, and an unidentified target is not
# a trustworthy collectible.
COLLECTIBLES: tuple[Collectible, ...] = (
    _collectible("air", "display-aircraft", "Display Aircraft", "An identified display or aerobatic aircraft.", "▲", "DISPLAY", _MAP_ONLY),
    _collectible("air", "bomber", "Military Bomber", "An identified military bomber aircraft.", "▲", "BOMBER", _MAP_ONLY),
    _collectible("air", "combat-aircraft", "Fast Jet", "An identified high-performance combat aircraft.", "▲", "FAST JET", _MAP_ONLY),
    _collectible("air", "airborne-surveillance", "Airborne Surveillance", "An identified airborne early-warning or surveillance aircraft.", "▲", "AWACS", _MAP_ONLY),
    _collectible("air", "military-tanker", "Aerial Tanker", "An identified military aerial-refuelling aircraft.", "▲", "TANKER", _MAP_ONLY),
    _collectible("air", "maritime-patrol", "Maritime Patrol Aircraft", "An identified maritime-patrol aircraft.", "▲", "MARITIME PATROL", _MAP_ONLY),
    _collectible("air", "intelligence-aircraft", "Intelligence Aircraft", "An identified intelligence or electronic-surveillance aircraft.", "▲", "INTELLIGENCE", _MAP_ONLY),
    _collectible("air", "military-helicopter", "Military Helicopter", "An identified operational military helicopter.", "▲", "MIL HELI", _MAP_ONLY),
    _collectible("air", "military-transport", "Military Airlifter", "An identified military heavy-lift or transport aircraft.", "▲", "AIRLIFT", _MAP_ONLY),
    _collectible("rail", "steam", "Steam Train", "A steam-hauled railway movement.", "♨", "STEAM", _RAIL_SAFETY),
    _collectible("rail", "railtour-special", "Railtour", "A charter or special passenger movement.", "◆", "RAILTOUR", _RAIL_SAFETY),
    _collectible("rail", "special-passenger", "Special Passenger Train", "A special passenger working; not necessarily a railtour.", "◆", "SPECIAL", _RAIL_SAFETY),
    _collectible("rail", "test-measurement", "Measurement Train", "A railway test or measurement movement.", "◇", "TEST TRAIN", _RAIL_SAFETY),
    _collectible("rail", "engineering", "Engineering Train", "A specialist railway engineering movement.", "⚒", "ENGINEERING", _RAIL_SAFETY),
    _collectible("rail", "freight", "Freight Train", "A freight or non-passenger railway movement.", "▰", "FREIGHT", _RAIL_SAFETY),
    _collectible("rail", "locomotive-move", "Locomotive Move", "A special light-locomotive movement.", "◆", "LOCO MOVE", _RAIL_SAFETY),
    _collectible("sea", "naval", "Naval Vessel", "An identified military or naval vessel.", "⚓", "NAVAL", _SEA_SAFETY),
    _collectible("sea", "patrol-vessel", "Patrol Vessel", "An identified patrol vessel.", "⚓", "PATROL", _SEA_SAFETY),
    _collectible("sea", "cruise", "Cruise Ship", "A cruise or notable passenger ship.", "▤", "CRUISE", _SEA_SAFETY),
    _collectible("sea", "specialist-vessel", "Specialist Vessel", "A research, survey or other specialist vessel.", "⌁", "SPECIALIST", _SEA_SAFETY),
)

_BY_KEY = {row.key: row for row in COLLECTIBLES}

_EXPECTED_TYPES: dict[DiscoveryCategory, str] = {
    "air": "aircraft",
    "rail": "train",
    "sea": "vessel",
}

_FRESH_SECONDS: dict[DiscoveryCategory, int] = {
    "air": 10 * 60,
    "rail": 45 * 60,
    "sea": 15 * 60,
}

_MEDIUM_CONFIDENCE = {
    "rail:engineering",
    "rail:freight",
    "rail:locomotive-move",
    "sea:specialist-vessel",
}

_SYNTHETIC_PREFIXES = ("demo:", "fixture:", "replay:", "sample:", "mock:", "test:")
_SYNTHETIC_SOURCE_PREFIXES = ("demo", "fixture", "replay", "sample", "mock", "synthetic", "simulation", "recorded")
_UNSAFE_TYPES = {"sar", "rescue", "incident"}


@dataclass(frozen=True, slots=True)
class DiscoveryClaimError(Exception):
    code: str
    message: str
    status_code: int

    def __str__(self) -> str:
        return self.message


def catalogue_payload() -> dict:
    return {
        "version": 1,
        "count": len(COLLECTIBLES),
        "collectibles": [asdict(row) for row in COLLECTIBLES],
    }


_MISSION_SAFETY = (
    "Map discovery only. Never travel towards an emergency, moving vehicle, "
    "restricted site or unsafe location."
)

_MISSION_DEFINITIONS = (
    {
        "id": "britain-in-motion",
        "title": "Britain in Motion",
        "description": "Discover one air, one rail and one sea collectible on the live map.",
        "icon": "✦",
        "targets": [row.key for row in COLLECTIBLES],
        "categories": ["air", "rail", "sea"],
        "total": 3,
        "distinct": "category",
    },
    {
        "id": "air-observer",
        "title": "Air Observer",
        "description": "Discover two different identified aircraft types.",
        "icon": "▲",
        "targets": [row.key for row in COLLECTIBLES if row.category == "air"],
        "categories": ["air"],
        "total": 2,
        "distinct": "collectible",
    },
    {
        "id": "rail-explorer",
        "title": "Rail Explorer",
        "description": "Discover two different special railway movements.",
        "icon": "◆",
        "targets": [row.key for row in COLLECTIBLES if row.category == "rail"],
        "categories": ["rail"],
        "total": 2,
        "distinct": "collectible",
    },
    {
        "id": "harbour-watch",
        "title": "Harbour Watch",
        "description": "Discover two different notable vessel types on the map.",
        "icon": "⚓",
        "targets": [row.key for row in COLLECTIBLES if row.category == "sea"],
        "categories": ["sea"],
        "total": 2,
        "distinct": "collectible",
    },
    {
        "id": "specialist-machines",
        "title": "Specialist Machines",
        "description": "Discover two machines built for an unusual specialist role.",
        "icon": "◇",
        "targets": [
            "air:airborne-surveillance", "air:maritime-patrol", "air:intelligence-aircraft",
            "rail:test-measurement", "rail:engineering", "sea:specialist-vessel",
        ],
        "categories": ["air", "rail", "sea"],
        "total": 2,
        "distinct": "collectible",
    },
    {
        "id": "heritage-and-specials",
        "title": "Heritage & Specials",
        "description": "Discover two different heritage or special railway movements.",
        "icon": "♨",
        "targets": ["rail:steam", "rail:railtour-special", "rail:locomotive-move"],
        "categories": ["rail"],
        "total": 2,
        "distinct": "collectible",
    },
    {
        "id": "big-movers",
        "title": "Big Movers",
        "description": "Discover two large-scale transport or service machines.",
        "icon": "▰",
        "targets": ["air:military-transport", "air:military-tanker", "rail:freight", "sea:cruise", "sea:naval"],
        "categories": ["air", "rail", "sea"],
        "total": 2,
        "distinct": "collectible",
    },
)


def missions_payload(*, now: datetime | None = None) -> dict:
    current = _utc(now or datetime.now(timezone.utc))
    starts = (current - timedelta(days=current.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    ends = starts + timedelta(days=7)
    iso = starts.isocalendar()
    ends_at = ends.isoformat()
    missions = [
        {
            **definition,
            "targets": list(definition["targets"]),
            "categories": list(definition["categories"]),
            "safety_note": _MISSION_SAFETY,
            "ends_at": ends_at,
        }
        for definition in _MISSION_DEFINITIONS
    ]
    return {
        "week": f"{iso.year}-W{iso.week:02d}",
        "starts_at": starts.isoformat(),
        "ends_at": ends_at,
        "missions": missions,
    }


def claim_activity(activity: Activity | None, *, now: datetime | None = None) -> dict:
    """Validate and describe one current live activity without storing a claim."""
    if activity is None:
        raise DiscoveryClaimError("activity_not_found", "That live activity is no longer available.", 404)

    if _synthetic(activity):
        raise DiscoveryClaimError(
            "activity_not_live",
            "Demo, fixture, test and replay records cannot be added to a live collection.",
            422,
        )

    current = _utc(now or datetime.now(timezone.utc))
    classification = classify_activity(activity, now=current)
    category = str(classification.category or "").strip().lower()
    subtype = str(classification.subtype or "").strip().lower()
    if _unsafe(activity, category):
        raise DiscoveryClaimError(
            "unsafe_activity",
            "Emergency and rescue activity cannot be collected or turned into a mission.",
            403,
        )

    key = f"{category}:{subtype}"
    collectible = _BY_KEY.get(key)
    if collectible is None or getattr(activity, "kind", "") != "vehicle":
        raise DiscoveryClaimError(
            "not_collectible",
            "This activity does not have a trustworthy collectible classification.",
            422,
        )
    if getattr(activity, "type", "") != _EXPECTED_TYPES[collectible.category]:
        raise DiscoveryClaimError(
            "not_collectible",
            "This activity type does not match its collectible classification.",
            422,
        )

    observed = getattr(activity, "last_updated", None)
    if not isinstance(observed, datetime):
        raise DiscoveryClaimError("activity_stale", "This activity has no current observation time.", 409)
    observed = _utc(observed)
    raw_age = int((current - observed).total_seconds())
    if raw_age < -300:
        raise DiscoveryClaimError("activity_not_live", "This activity has an invalid future timestamp.", 422)
    age_seconds = max(0, raw_age)
    fresh_for = _FRESH_SECONDS[collectible.category]
    if age_seconds > fresh_for:
        raise DiscoveryClaimError(
            "activity_stale",
            "That activity is no longer fresh enough to add to a live collection.",
            409,
        )

    # The Field Guide is fed by Cool-mode activity.  Keep this server-side
    # guard even though the catalogue currently omits every force-excluded
    # subtype, so a future catalogue edit cannot create an impossible entry.
    if not classification.candidate:
        raise DiscoveryClaimError(
            "not_collectible",
            "This activity does not meet the Cool priority threshold.",
            422,
        )

    identity_level, identity_note = _identity(activity, collectible)
    discovered_at = current.isoformat()
    return {
        "id": collectible.id,
        "key": collectible.key,
        "activity_id": str(activity.id),
        "category": collectible.category,
        "subtype": collectible.subtype,
        "title": collectible.title,
        "explanation": str(classification.explanation or collectible.explanation),
        "icon": collectible.icon,
        "confidence": "medium" if collectible.key in _MEDIUM_CONFIDENCE else "high",
        "freshness": "live",
        "age_seconds": age_seconds,
        "fresh_for_seconds": fresh_for,
        "observed_at": observed.isoformat(),
        "discovered_at": discovered_at,
        "broad_region": _broad_region(float(activity.lat), float(activity.lon)),
        "source": _safe_source(getattr(activity, "source", "")),
        "identity_level": identity_level,
        "identity_note": identity_note,
    }


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _unsafe(activity: Activity, classified_category: str = "") -> bool:
    return (
        getattr(activity, "type", "") in _UNSAFE_TYPES
        or classified_category == "rescue"
    )


def _synthetic(activity: Activity) -> bool:
    if getattr(activity, "position_method", "") == "demo":
        return True
    for value in (getattr(activity, "id", ""), getattr(activity, "source_id", "")):
        if str(value or "").strip().lower().startswith(_SYNTHETIC_PREFIXES):
            return True
    source = str(getattr(activity, "source", "") or "").strip().lower()
    return any(source == prefix or source.startswith(f"{prefix} ") or source.startswith(f"{prefix}:")
               for prefix in _SYNTHETIC_SOURCE_PREFIXES)


def _identity(activity: Activity, collectible: Collectible) -> tuple[str, str]:
    vehicle_ref = str(getattr(activity, "vehicle_ref", "") or "").strip()
    source_id = str(getattr(activity, "source_id", "") or "").strip()
    if collectible.category == "rail":
        if vehicle_ref:
            return "exact", "The live provider supplied a vehicle identifier for this movement."
        if collectible.subtype in {"railtour-special", "special-passenger"}:
            return "type", "The special-movement or headcode classification does not confirm its locomotive or formation."
        return "type", "The movement type is classified, but its exact locomotive or formation is not confirmed."
    if collectible.category == "air" and (vehicle_ref or source_id):
        return "exact", "The live provider supplied an individual aircraft identifier."
    if collectible.category == "sea" and (vehicle_ref or source_id):
        return "exact", "The live provider supplied an individual vessel identifier."
    return "type", "The activity type is classified, but the individual vehicle is not confirmed."


def _broad_region(lat: float, lon: float) -> str | None:
    """Return deliberately coarse coordinate bands, never a chaseable place."""
    if not (49.0 <= lat <= 61.5 and -9.5 <= lon <= 3.0):
        return None
    if lon <= -5.25 and 53.8 <= lat <= 55.6:
        return "Northern Ireland area"
    if lat >= 55.5:
        return "Northern UK"
    if lat >= 53.3:
        return "Northern England and Wales"
    if lat >= 51.8:
        return "Central UK"
    return "Southern UK"


def _safe_source(value: object) -> str:
    source = " ".join(str(value or "live provider").split())
    return source[:80]
