from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
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


def normalise_preferences(raw: dict) -> dict:
    mode = str(raw.get("mode") or "").lower()
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
        relevant = item.type in {"road", "incident", "weather", "flood"}
        important = any(word in text for word in ("closed", "closure", "delay", "congestion", "incident", "severe", "flood", "warning", "lane"))
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
