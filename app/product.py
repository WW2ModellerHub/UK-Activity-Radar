from __future__ import annotations

import re
import sqlite3
import threading
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ALLOWED_EVENTS = {
    "app_open",
    "layer_selected",
    "map_mode_selected",
    "search_used",
    "activity_selected",
    "activity_followed",
    "activity_unfollowed",
    "discovery_claimed",
    "discovery_shared",
    "field_guide_opened",
    "missions_opened",
    "discovery_interest_changed",
    "watch_zone_added",
    "watch_zone_removed",
    "nearby_used",
    "radar_plus_opened",
    "pricing_interest",
    "pwa_install_prompted",
    "pwa_installed",
}

ALLOWED_PLANS = {"plus"}

def _clean_token(value: str, max_len: int = 64) -> str:
    value = re.sub(r"[^a-zA-Z0-9._:-]", "", (value or ""))[:max_len]
    return value or "anonymous"


class ProductAnalytics:
    """Tiny privacy-conscious product analytics store for the local prototype.

    Deliberately stores only an anonymous browser session token, a whitelisted
    event name and an optional coarse feature label. It does not store search
    text, coordinates, IP addresses, URLs or activity IDs.
    """

    def __init__(self, path: Path, *, enabled: bool = True) -> None:
        self.path = path
        self.enabled = bool(enabled)
        self._lock = threading.RLock()
        if self.enabled:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS product_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    event_name TEXT NOT NULL,
                    feature TEXT NOT NULL DEFAULT ''
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_product_events_name ON product_events(event_name)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_product_events_session ON product_events(session_id)")
            conn.commit()

    def record(self, session_id: str, event_name: str, feature: str = "") -> bool:
        event_name = (event_name or "").strip().lower()
        if event_name not in ALLOWED_EVENTS:
            return False
        # Treat a valid event as accepted when analytics is disabled so the
        # browser does not retry or surface an error. No directory, database or
        # event row is created in this mode.
        if not self.enabled:
            return True
        session_id = _clean_token(session_id, 80)
        feature = _clean_token(feature.lower(), 80) if feature else ""
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO product_events(created_at,session_id,event_name,feature) VALUES(?,?,?,?)",
                (datetime.now(timezone.utc).isoformat(), session_id, event_name, feature),
            )
            conn.commit()
        return True

    def summary(self) -> dict:
        if not self.enabled:
            return {
                "enabled": False,
                "anonymous_sessions": 0,
                "events": {},
                "pricing_interest": {},
                "note": "Anonymous product analytics are disabled for this deployment.",
            }
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT event_name, feature, session_id FROM product_events").fetchall()
        events = Counter(row["event_name"] for row in rows)
        interest = Counter(
            row["feature"] for row in rows
            if row["event_name"] == "pricing_interest" and row["feature"] in ALLOWED_PLANS
        )
        sessions = {row["session_id"] for row in rows}
        return {
            "enabled": True,
            "anonymous_sessions": len(sessions),
            "events": dict(events),
            "pricing_interest": dict(interest),
            "note": "Prototype aggregate only. No search terms, precise locations or activity IDs are stored.",
        }


def product_plans(*, payments_enabled: bool = False, price_display: str = "£3.99/month") -> dict:
    return {
        "payments_enabled": bool(payments_enabled),
        "currency": "GBP",
        "plans": [
            {
                "id": "free", "name": "Free", "price_display": "£0",
                "status": "current",
                "features": [
                    "Live UK map and core transport layers",
                    "Three-item Cool preview and weekly Field Guide",
                    "Station and stop boards",
                    "Basic My Radar favourites on this device",
                ],
            },
            {
                "id": "plus", "name": "Radar+", "price_display": price_display,
                "status": "available" if payments_enabled else "setup",
                "features": [
                    "Full Cool ranking and advanced filters",
                    "Advanced vehicle identity and live tracking",
                    "Journey planning and official live cameras",
                    "TomTom traffic intelligence and faster tracking",
                    "Unlimited local favourites and watch zones",
                ],
            },
        ],
        "message": (
            "Radar+ checkout is available through Stripe."
            if payments_enabled else
            "Radar+ is ready in the application but checkout needs production Stripe configuration."
        ),
    }
