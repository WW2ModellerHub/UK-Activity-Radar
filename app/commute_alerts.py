from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import asin, cos, radians, sin, sqrt
from pathlib import Path
from threading import RLock
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from .models import Activity


PUSH_HOST_SUFFIXES = (
    "fcm.googleapis.com",
    "updates.push.services.mozilla.com",
    "push.apple.com",
    "web.push.apple.com",
    "notify.windows.com",
)


def _distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371.0
    p1, p2 = radians(lat1), radians(lat2)
    dp, dl = radians(lat2 - lat1), radians(lon2 - lon1)
    value = sin(dp / 2) ** 2 + cos(p1) * cos(p2) * sin(dl / 2) ** 2
    return 2 * radius * asin(sqrt(value))


def validate_subscription(subscription: dict) -> dict:
    endpoint = str(subscription.get("endpoint") or "").strip()
    keys = subscription.get("keys") if isinstance(subscription.get("keys"), dict) else {}
    parsed = urlparse(endpoint)
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not any(hostname == suffix or hostname.endswith("." + suffix) for suffix in PUSH_HOST_SUFFIXES):
        raise ValueError("Unsupported browser push endpoint")
    p256dh, auth = str(keys.get("p256dh") or ""), str(keys.get("auth") or "")
    if not (40 <= len(p256dh) <= 256 and 8 <= len(auth) <= 128 and len(endpoint) <= 2048):
        raise ValueError("Invalid browser push subscription")
    return {"endpoint": endpoint, "keys": {"p256dh": p256dh, "auth": auth}}


def normalise_spotter(raw) -> dict | None:
    """Where to watch for unusual things, or None when spotting is off."""
    if not isinstance(raw, dict):
        return None
    try:
        lat, lon = float(raw.get("lat")), float(raw.get("lon"))
        radius = float(raw.get("radius_miles") or 10)
    except (TypeError, ValueError):
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    asked = set()
    for kind in raw.get("kinds") or SPOTTER_KINDS:
        asked.update(_LEGACY_SPOTTER_KINDS.get(str(kind).lower(), {str(kind).lower()}))
    kinds = sorted(asked & SPOTTER_KINDS)
    return {"lat": round(lat, 4), "lon": round(lon, 4), "radius_miles": max(2.0, min(radius, 50.0)),
            "kinds": kinds or sorted(SPOTTER_KINDS)}


def normalise_preferences(raw: dict) -> dict:
    preferences = _normalise_preferences(raw)
    preferences["spotter"] = normalise_spotter(raw.get("spotter"))
    preferences["journey"] = normalise_journey(raw.get("journey"))
    preferences["leave_plans"] = normalise_leave_plans(raw.get("leave_plans"))
    return preferences


# ---- Leave now ----------------------------------------------------------
# "Tell me when to leave": for a train someone means to catch, one alert a
# few minutes before they need to set off, moved later if the train is late.
MAX_LEAVE_PLANS = 3
LEAVE_BUFFER_MINUTES = 2
LEAVE_WARNING_MINUTES = 5


def _aware(value) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else None


def normalise_leave_plans(raw, now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    plans = []
    for row in raw if isinstance(raw, list) else []:
        if not isinstance(row, dict):
            continue
        depart = _aware(row.get("depart"))
        train_id = str(row.get("train_id") or "").strip()
        try:
            minutes = int(row.get("minutes"))
        except (TypeError, ValueError):
            continue
        # Drop plans for trains that have long gone.
        if depart is None or not train_id or len(train_id) > 80 or (now - depart).total_seconds() > 3 * 3600:
            continue
        plans.append({
            "train_id": train_id, "train_name": str(row.get("train_name") or "your train")[:40],
            "station": str(row.get("station") or "the station")[:80],
            "minutes": max(1, min(minutes, 180)), "depart": depart.astimezone(timezone.utc).isoformat(),
        })
    return plans[-MAX_LEAVE_PLANS:]


def leave_alert(plan: dict, status: dict | None, now: datetime | None = None) -> dict | None:
    """The "time to go" alert for one plan, once it is nearly time."""
    now = now or datetime.now(timezone.utc)
    depart = _aware(plan.get("depart"))
    status = status or {}
    if depart is None or status.get("cancelled") or status.get("arrived"):
        return None  # a cancellation is announced by the saved-train alert
    try:
        delay = max(0, int(status.get("delay_minutes") or 0)) if status.get("found") else 0
    except (TypeError, ValueError):
        delay = 0
    expected = depart + timedelta(minutes=delay)
    leave_at = expected - timedelta(minutes=plan["minutes"] + LEAVE_BUFFER_MINUTES)
    if now < leave_at - timedelta(minutes=LEAVE_WARNING_MINUTES) or now >= expected:
        return None
    london = ZoneInfo("Europe/London")
    booked = depart.astimezone(london).strftime("%H:%M")
    remaining = round((leave_at - now).total_seconds() / 60)
    when = "Leave now" if remaining <= 1 else f"Leave in {remaining} min"
    facts = [
        f"Running {delay} min late, now expected {expected.astimezone(london).strftime('%H:%M')}" if delay else
        ("On time" if status.get("found") else "No live report yet, so this is from the timetable"),
        f"Platform {status['platform']}" if status.get("platform") else None,
        f"Allowing {plan['minutes']} min to get there",
    ]
    return {
        "key": f"leave:{plan['train_id']}:{plan['depart']}",
        "title": f"{when} for the {booked} from {plan['station']}",
        "body": ". ".join(x for x in facts if x) + ".",
        "activity_id": plan["train_id"],
        "mode": "leave",
    }


# ---- Journey watch ------------------------------------------------------
# A saved drive: shortly before the usual leaving time, say if a crash,
# closure or serious delay is on the route. Silence means it looks clear.
JOURNEY_LOOKAHEAD_MINUTES = 45
JOURNEY_MAX_POINTS = 80
JOURNEY_REACH_KM = 0.6


def normalise_journey(raw) -> dict | None:
    if not isinstance(raw, dict):
        return None
    points = []
    for row in raw.get("points") or []:
        try:
            lat, lon = float(row[0]), float(row[1])
        except (TypeError, ValueError, IndexError):
            continue
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            points.append([round(lat, 5), round(lon, 5)])
    if len(points) < 2:
        return None
    if len(points) > JOURNEY_MAX_POINTS:
        step = (len(points) - 1) / (JOURNEY_MAX_POINTS - 1)
        points = [points[round(i * step)] for i in range(JOURNEY_MAX_POINTS)]
    try:
        leave = max(0, min(int(raw.get("leave_minutes")), 1439))
    except (TypeError, ValueError):
        return None
    days = sorted({int(d) for d in (raw.get("days") if raw.get("days") is not None else range(5)) if str(d).isdigit() and 0 <= int(d) <= 6})
    return {"name": str(raw.get("name") or "your drive")[:80], "points": points, "leave_minutes": leave, "days": days or [0, 1, 2, 3, 4]}


def journey_due(journey: dict | None, now: datetime | None = None) -> bool:
    """True in the three quarters of an hour before the usual leaving time."""
    if not journey:
        return False
    local = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo("Europe/London"))
    if local.weekday() not in journey["days"]:
        return False
    minutes = local.hour * 60 + local.minute
    return journey["leave_minutes"] - JOURNEY_LOOKAHEAD_MINUTES <= minutes <= journey["leave_minutes"]


def _distance_to_route_km(lat: float, lon: float, points: list) -> float:
    lon_scale, best = 111.32 * cos(radians(lat)), float("inf")
    for (lat1, lon1), (lat2, lon2) in zip(points, points[1:]):
        ax, ay = (lon1 - lon) * lon_scale, (lat1 - lat) * 111.32
        bx, by = (lon2 - lon) * lon_scale, (lat2 - lat) * 111.32
        dx, dy = bx - ax, by - ay
        length = dx * dx + dy * dy
        t = 0.0 if length == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / length))
        best = min(best, sqrt((ax + t * dx) ** 2 + (ay + t * dy) ** 2))
    return best


def journey_alerts(events, journey: dict | None, now: datetime | None = None) -> list[dict]:
    """Serious things on the route right now, worst first, one alert each per day."""
    if not journey_due(journey, now):
        return []
    local = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo("Europe/London"))
    found = []
    for event in events:
        impact = getattr(event, "impact", None) or {}
        if event.type not in {"road", "incident"} or not impact.get("active") or (impact.get("rank") or 0) < 3:
            continue
        if _distance_to_route_km(event.lat, event.lon, journey["points"]) > JOURNEY_REACH_KM:
            continue
        found.append((impact.get("rank") or 0, {
            "key": "journey:" + road_event_key(event, impact, now),
            "title": f"{impact.get('title') or 'Incident'} on {journey['name']}",
            "body": " · ".join(x for x in (impact.get("where"), impact.get("impact"), impact.get("when")) if x) or "Check before you leave.",
            "activity_id": event.id,
            "mode": "journey",
        }))
    found.sort(key=lambda row: -row[0])
    return [row[1] for row in found]


def journey_bboxes(journey: dict) -> list[tuple[float, float, float, float]]:
    """Small boxes covering the route, for fetching live incidents along it."""
    boxes = []
    points = journey["points"]
    for start in range(0, len(points) - 1, 8):
        chunk = points[start:start + 9]
        lats, lons = [p[0] for p in chunk], [p[1] for p in chunk]
        boxes.append((min(lons) - 0.01, min(lats) - 0.01, max(lons) + 0.01, max(lats) + 0.01))
    return boxes


def _normalise_preferences(raw: dict) -> dict:
    mode = str(raw.get("mode") or "").lower()
    if mode == "trains-only":
        # Alerts for saved trains alone: no place, no hours.
        return {
            "mode": mode, "place_id": "", "place_name": "", "lat": None, "lon": None, "radius_miles": 0.0,
            "start_hour": 0, "end_hour": 24, "activity_types": [],
            "saved_trains": normalise_saved_trains(raw.get("saved_trains")),
        }
    if mode not in {"driving", "train"}:
        raise ValueError("Travel mode must be driving or train")
    lat, lon = float(raw.get("lat")), float(raw.get("lon"))
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("Invalid alert location")
    radius = max(2.0, min(float(raw.get("radius_miles") or 12), 30.0))
    start_hour = max(0, min(int(raw.get("start_hour", 5)), 23))
    end_hour = max(1, min(int(raw.get("end_hour", 11)), 24))
    if end_hour <= start_hour:
        raise ValueError("Alert end time must be after its start time")
    allowed_activity_types = {"train", "bus", "vessel", "aircraft"}
    activity_types = sorted({str(value).lower() for value in (raw.get("activity_types") or []) if str(value).lower() in allowed_activity_types})
    return {
        "mode": mode,
        "place_id": str(raw.get("place_id") or "")[:200],
        "place_name": str(raw.get("place_name") or "Saved place")[:120],
        "lat": lat,
        "lon": lon,
        "radius_miles": radius,
        "start_hour": start_hour,
        "end_hour": end_hour,
        "activity_types": activity_types,
        "saved_trains": normalise_saved_trains(raw.get("saved_trains")),
    }


# Nearby roadworks and incidents are the noisiest alerts: at most this many a
# day per phone, the most serious first.
MAX_PLACE_ROAD_ALERTS_PER_DAY = 3


def road_key_prefix(now: datetime | None = None) -> str:
    return "road:" + (now or datetime.now(timezone.utc)).astimezone(ZoneInfo("Europe/London")).date().isoformat() + ":"


# ---- Spotter alerts -----------------------------------------------------
# "Tell me when something unusual is near me": the same finds Discover lists
# (steam, charters, military and rescue aircraft), within a chosen distance.
# What can be ticked, and which Discover kinds each covers. Ordinary freight
# is left out (it would alert all day), and live rescues always are.
SPOTTER_KIND_SUBTYPES = {
    "steam": {"steam", "railtour-special", "special-passenger"},
    "test": {"test-measurement"},
    "loco": {"locomotive-move", "engineering"},
    "military": {"combat-aircraft", "military-transport", "military-tanker", "military-helicopter", "bomber",
                 "airborne-surveillance", "intelligence-aircraft", "maritime-patrol", "display-aircraft", "jet-trainer"},
}
SPOTTER_KINDS = set(SPOTTER_KIND_SUBTYPES)
_SUBTYPE_KIND = {subtype: kind for kind, subtypes in SPOTTER_KIND_SUBTYPES.items() for subtype in subtypes}
# Saved before the tick boxes: "rail" and "air".
_LEGACY_SPOTTER_KINDS = {"rail": {"steam", "test", "loco"}, "air": {"military"}}
SPOTTER_HOURS = (7, 22)  # local time; nobody wants a Hercules at 3 am


def _expand_legacy(kinds) -> set:
    out = set()
    for kind in kinds or ():
        out.update(_LEGACY_SPOTTER_KINDS.get(str(kind), set()))
    return out


def spotter_alert(item, spotter: dict | None, now: datetime | None = None) -> dict | None:
    if not spotter or not getattr(item, "cool_candidate", False):
        return None
    category = str(getattr(item, "cool_category", "") or "").lower()
    # Live rescues are never something to travel towards.
    if category == "rescue" or getattr(item, "kind", "vehicle") != "vehicle":
        return None
    kind = _SUBTYPE_KIND.get(str(getattr(item, "cool_subtype", "") or ""))
    if kind is None or kind not in set(spotter.get("kinds") or ()) | _expand_legacy(spotter.get("kinds")):
        return None
    local = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo("Europe/London"))
    if not (SPOTTER_HOURS[0] <= local.hour < SPOTTER_HOURS[1]):
        return None
    km = _distance_km(spotter["lat"], spotter["lon"], item.lat, item.lon)
    if km > spotter["radius_miles"] * 1.609344:
        return None
    miles = km / 1.609344
    away = "under a mile away" if miles < 1 else f"about {round(miles)} mile{'s' if round(miles) != 1 else ''} away"
    what = getattr(item, "aircraft_model", None) or getattr(item, "cool_explanation", None) or "Unusual"
    heading = getattr(item, "destination", None)
    return {
        "key": f"spot:{item.id}",
        "title": f"{str(what).rstrip('.')} {away}",
        "body": " · ".join(x for x in (item.name, f"to {heading}" if heading else None, getattr(item, "cool_explanation", None)) if x),
        "activity_id": item.id,
        "mode": "spotter",
    }


def road_event_key(event, impact: dict | None = None, now: datetime | None = None) -> str:
    """One alert per problem per day, however the feed names or splits it.

    Feeds re-issue ids, and list a closure once for each direction; the same
    kind of problem on the same road within about a kilometre is one thing.
    """
    impact = impact or getattr(event, "impact", None) or {}
    day = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo("Europe/London")).date().isoformat()
    road = str(getattr(event, "road", None) or "").strip().upper()
    return f"road:{day}:{impact.get('what') or event.type}:{road}:{round(float(event.lat), 2)},{round(float(event.lon), 2)}"


def alert_for_activity(item: Activity, preferences: dict) -> dict | None:
    if _distance_km(preferences["lat"], preferences["lon"], item.lat, item.lon) > preferences["radius_miles"] * 1.609344:
        return None
    text = " ".join(
        str(value or "")
        for value in (
            item.name,
            item.subtitle,
            item.status,
            getattr(item, "reason", None),
            getattr(item, "severity", None),
            getattr(item, "road", None),
            getattr(item, "route", None),
        )
    ).lower()
    wanted = set(preferences.get("activity_types") or [])
    mapped_type = "aircraft" if item.type == "sar" else item.type
    if item.kind == "vehicle" and mapped_type in wanted:
        reason = getattr(item, "cool_explanation", None) or item.subtitle or item.status or item.name
        return {
            "key": item.id,
            "title": f"{item.name} is near {preferences['place_name']}",
            "body": reason,
            "activity_id": item.id,
            "mode": "my-radar",
        }
    if preferences["mode"] == "driving":
        impact = getattr(item, "impact", None) or {}
        if item.type in {"road", "incident"}:
            # Only things that are happening and get in the way: closures,
            # crashes, lane closures, long delays. Plain roadworks are not news.
            if not impact.get("active") or (impact.get("rank") or 0) < 2:
                return None
            place = preferences["place_name"]
            what = impact.get("what") or "Incident"
            return {
                "key": road_event_key(item, impact),
                "title": f"{what} near {place}",
                "body": " · ".join(x for x in (impact.get("where"), impact.get("impact"), impact.get("when")) if x) or "Check before you set off.",
                "activity_id": item.id,
                "mode": "driving",
            }
        relevant = item.type in {"weather", "flood"}
        important = any(word in text for word in ("severe", "flood", "warning"))
        if not (relevant and important):
            return None
        title = f"Driving near {preferences['place_name']}?"
    else:
        train_problem = item.type == "train" and (
            int(getattr(item, "delay_minutes", 0) or 0) >= 10
            or any(word in text for word in ("cancel", "delayed", "late", "terminated"))
        )
        rail_event = item.kind == "event" and any(
            phrase in text
            for phrase in ("rail replacement", "replacement bus", "train", "railway", "station closed", "rail disruption")
        )
        if not (train_problem or rail_event):
            return None
        title = f"Getting a train near {preferences['place_name']}?"
    reason = getattr(item, "reason", None) or item.subtitle or item.status or item.name
    return {
        "key": item.id,
        "title": title,
        "body": f"Watch out for {item.name}: {reason}",
        "activity_id": item.id,
        "mode": preferences["mode"],
    }


@dataclass(frozen=True)
class StoredSubscription:
    endpoint_hash: str
    subscription: dict
    preferences: dict


class CommuteAlertStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS subscriptions (
              endpoint_hash TEXT PRIMARY KEY,
              device_hash TEXT NOT NULL,
              subscription_json TEXT NOT NULL,
              preferences_json TEXT NOT NULL,
              active INTEGER NOT NULL DEFAULT 1,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS deliveries (
              endpoint_hash TEXT NOT NULL,
              activity_id TEXT NOT NULL,
              delivered_at TEXT NOT NULL,
              PRIMARY KEY(endpoint_hash, activity_id)
            );
            """
        )
        self._connection.commit()

    @staticmethod
    def _hash(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def upsert(self, device_token: str, subscription: dict, preferences: dict) -> None:
        if not (24 <= len(device_token) <= 200):
            raise ValueError("Invalid device token")
        subscription = validate_subscription(subscription)
        preferences = normalise_preferences(preferences)
        endpoint_hash = self._hash(subscription["endpoint"])
        with self._lock:
            self._connection.execute(
                """INSERT INTO subscriptions(endpoint_hash,device_hash,subscription_json,preferences_json,active,updated_at)
                VALUES(?,?,?,?,1,?) ON CONFLICT(endpoint_hash) DO UPDATE SET
                device_hash=excluded.device_hash,subscription_json=excluded.subscription_json,
                preferences_json=excluded.preferences_json,active=1,updated_at=excluded.updated_at""",
                (endpoint_hash, self._hash(device_token), json.dumps(subscription), json.dumps(preferences), datetime.now(timezone.utc).isoformat()),
            )
            self._connection.commit()

    def remove(self, device_token: str) -> None:
        with self._lock:
            self._connection.execute("UPDATE subscriptions SET active=0 WHERE device_hash=?", (self._hash(device_token),))
            self._connection.commit()

    def status(self, device_token: str) -> dict | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT preferences_json,active FROM subscriptions WHERE device_hash=? ORDER BY updated_at DESC LIMIT 1",
                (self._hash(device_token),),
            ).fetchone()
        if row is None:
            return None
        return {"active": bool(row[1]), "preferences": json.loads(row[0])}

    def subscription_for(self, device_token: str) -> StoredSubscription | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT endpoint_hash,subscription_json,preferences_json FROM subscriptions WHERE device_hash=? AND active=1 ORDER BY updated_at DESC LIMIT 1",
                (self._hash(device_token),),
            ).fetchone()
        return StoredSubscription(row[0], json.loads(row[1]), json.loads(row[2])) if row else None

    def active(self) -> list[StoredSubscription]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT endpoint_hash,subscription_json,preferences_json FROM subscriptions WHERE active=1"
            ).fetchall()
        return [StoredSubscription(row[0], json.loads(row[1]), json.loads(row[2])) for row in rows]

    def already_delivered(self, endpoint_hash: str, activity_id: str) -> bool:
        with self._lock:
            return self._connection.execute(
                "SELECT 1 FROM deliveries WHERE endpoint_hash=? AND activity_id=?",
                (endpoint_hash, activity_id),
            ).fetchone() is not None

    def delivered_count(self, endpoint_hash: str, key_prefix: str) -> int:
        with self._lock:
            return self._connection.execute(
                "SELECT COUNT(*) FROM deliveries WHERE endpoint_hash=? AND activity_id LIKE ? ESCAPE '\\'",
                (endpoint_hash, key_prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"),
            ).fetchone()[0]

    def mark_delivered(self, endpoint_hash: str, activity_id: str) -> None:
        with self._lock:
            self._connection.execute(
                "INSERT OR IGNORE INTO deliveries(endpoint_hash,activity_id,delivered_at) VALUES(?,?,?)",
                (endpoint_hash, activity_id, datetime.now(timezone.utc).isoformat()),
            )
            self._connection.commit()

    def deactivate(self, endpoint_hash: str) -> None:
        with self._lock:
            self._connection.execute("UPDATE subscriptions SET active=0 WHERE endpoint_hash=?", (endpoint_hash,))
            self._connection.commit()

    def close(self) -> None:
        with self._lock:
            self._connection.close()


def within_alert_hours(preferences: dict, now: datetime | None = None) -> bool:
    local = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo("Europe/London"))
    return preferences["start_hour"] <= local.hour < preferences["end_hour"]


# ---- Saved trains -------------------------------------------------------
# A saved train alerts when it is cancelled, becomes late (in bands, so a
# train drifting from 6 to 7 minutes late does not alert twice) or is moved
# to another platform.
LATE_BANDS = (5, 15, 30, 60)
MAX_SAVED_TRAINS = 12


def late_band(delay_minutes) -> int:
    try:
        minutes = int(delay_minutes)
    except (TypeError, ValueError):
        return 0
    band = 0
    for threshold in LATE_BANDS:
        if minutes >= threshold:
            band = threshold
    return band


def saved_train_status(vehicle, note: dict | None = None) -> dict:
    """What a traveller needs to know about one saved train, or ``found: False``."""
    if vehicle is None:
        return {"found": False}
    cancelled = bool(getattr(vehicle, "cancelled_at", None)) or bool((note or {}).get("cancelled"))
    platform = getattr(vehicle, "platform", None)
    booked = getattr(vehicle, "booked_platform", None)
    return {
        "found": True,
        "id": vehicle.id,
        "name": vehicle.name,
        "origin": getattr(vehicle, "origin", None),
        "destination": getattr(vehicle, "destination", None),
        "cancelled": cancelled,
        "arrived": bool(getattr(vehicle, "terminated_at", None)),
        "delay_minutes": getattr(vehicle, "delay_minutes", None),
        "late_band": 0 if cancelled else late_band(getattr(vehicle, "delay_minutes", None)),
        "platform": platform,
        "platform_changed": bool(platform and booked and platform != booked),
        "booked_platform": booked,
    }


def saved_train_alerts(status: dict) -> list[dict]:
    """The alerts a status implies, each with a stable de-duplication key."""
    if not status.get("found"):
        return []
    name = status.get("name") or "Your train"
    where = status.get("destination")
    label = f"{name} to {where}" if where else name
    train_id = status["id"]
    alerts = []
    if status.get("cancelled"):
        alerts.append({"key": f"saved-train:{train_id}:cancelled", "title": f"{label} is cancelled",
                       "body": "Check for another service.", "activity_id": train_id, "mode": "saved-train"})
        return alerts
    if status.get("late_band"):
        alerts.append({"key": f"saved-train:{train_id}:late:{status['late_band']}",
                       "title": f"{label} is {int(status['delay_minutes'])} min late",
                       "body": "Tap to see where it is now.", "activity_id": train_id, "mode": "saved-train"})
    if status.get("platform_changed"):
        alerts.append({"key": f"saved-train:{train_id}:platform:{status['platform']}",
                       "title": f"{label} now at platform {status['platform']}",
                       "body": f"It was booked for platform {status['booked_platform']}.",
                       "activity_id": train_id, "mode": "saved-train"})
    return alerts


def normalise_saved_trains(raw) -> list[str]:
    ids = []
    for value in raw or []:
        text = str(value or "").strip()
        if text and len(text) <= 80 and text not in ids:
            ids.append(text)
    return ids[:MAX_SAVED_TRAINS]
