from __future__ import annotations

"""Server-owned billing and entitlement primitives.

The browser is never an authority for paid access.  Stripe tells this module
which customer/subscription exists, while :class:`BillingStore` issues opaque
browser tokens and stores only keyed hashes of those tokens.  The module has no
FastAPI dependency on purpose: route code can be thin, and all payment logic is
testable without network access.

This is deliberately a single-process/single-instance SQLite implementation.
It is suitable for the current one-instance Render deployment with a persistent
disk.  Move the tables to a shared transactional database before adding more
application instances.
"""

import hashlib
import hmac
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Protocol
from urllib.parse import urlsplit


PLAN_FREE = "free"
PLAN_PLUS = "plus"
ACTIVE_SUBSCRIPTION_STATUSES = frozenset({"active", "trialing"})

# Keep feature names stable: API routes and the browser can share these values
# without duplicating which plan owns a feature.
PLUS_FEATURES = frozenset(
    {
        "cool_full",
        "advanced_identity",
        "advanced_transport",
        "journey_planner",
        "live_cameras",
        "traffic_pro",
        "fast_updates",
        "live_tracking",
    }
)

MAX_WEBHOOK_BYTES = 256 * 1024


class BillingError(RuntimeError):
    """Base class for safe, expected billing failures."""


class BillingConfigurationError(BillingError):
    pass


class BillingDisabledError(BillingError):
    pass


class BillingStateError(BillingError):
    pass


class BillingAuthenticationError(BillingError):
    pass


class BillingPaymentPending(BillingError):
    pass


class BillingWebhookError(BillingError):
    pass


@dataclass(frozen=True)
class BillingConfiguration:
    enabled: bool = False
    public_base_url: str = ""
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_plus_price_id: str = ""
    token_pepper: str = ""
    cookie_secure: bool = True
    checkout_ttl_seconds: int = 20 * 60
    access_ttl_seconds: int = 90 * 24 * 60 * 60
    email_login_ttl_seconds: int = 15 * 60


def billing_configuration_errors(config: BillingConfiguration) -> tuple[str, ...]:
    """Return redacted setup errors; never include credential values."""

    if not config.enabled:
        return ()

    errors: list[str] = []
    required = {
        "PUBLIC_BASE_URL": config.public_base_url,
        "STRIPE_SECRET_KEY": config.stripe_secret_key,
        "STRIPE_WEBHOOK_SECRET": config.stripe_webhook_secret,
        "STRIPE_PLUS_PRICE_ID": config.stripe_plus_price_id,
        "BILLING_TOKEN_PEPPER": config.token_pepper,
    }
    for name, value in required.items():
        if not str(value or "").strip():
            errors.append(f"{name} is required when billing is enabled")

    try:
        parsed = urlsplit(config.public_base_url)
    except ValueError:
        parsed = None
    if (
        parsed is None
        or parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        errors.append("PUBLIC_BASE_URL must be a bare HTTPS origin")
    if config.stripe_secret_key and not config.stripe_secret_key.startswith(("sk_test_", "sk_live_")):
        errors.append("STRIPE_SECRET_KEY is not a Stripe secret key")
    if config.stripe_webhook_secret and not config.stripe_webhook_secret.startswith("whsec_"):
        errors.append("STRIPE_WEBHOOK_SECRET is not a Stripe webhook secret")
    if config.stripe_plus_price_id and not config.stripe_plus_price_id.startswith("price_"):
        errors.append("STRIPE_PLUS_PRICE_ID is not a Stripe Price ID")
    if config.token_pepper and len(config.token_pepper.encode("utf-8")) < 32:
        errors.append("BILLING_TOKEN_PEPPER must contain at least 32 bytes")
    if not config.cookie_secure:
        errors.append("secure cookies are required when billing is enabled")
    if config.checkout_ttl_seconds < 60:
        errors.append("checkout state lifetime must be at least 60 seconds")
    if config.access_ttl_seconds < 60:
        errors.append("access token lifetime must be at least 60 seconds")
    if config.email_login_ttl_seconds < 60:
        errors.append("email login lifetime must be at least 60 seconds")
    return tuple(errors)


def validate_billing_configuration(config: BillingConfiguration) -> None:
    """Fail closed when a deployment tries to enable incomplete billing."""

    errors = billing_configuration_errors(config)
    if errors:
        raise BillingConfigurationError("Billing configuration is unsafe: " + "; ".join(errors))


@dataclass(frozen=True)
class AccessContext:
    token_valid: bool = False
    customer_id: str | None = None
    plan: str = PLAN_FREE
    subscription_status: str | None = None
    features: frozenset[str] = frozenset()

    @property
    def plus(self) -> bool:
        return self.plan == PLAN_PLUS and self.subscription_status in ACTIVE_SUBSCRIPTION_STATUSES

    def has(self, feature: str) -> bool:
        return feature in self.features


@dataclass(frozen=True)
class CheckoutState:
    client_reference: str
    plan: str
    price_id: str
    stripe_session_id: str | None
    expires_at: int
    consumed: bool


@dataclass(frozen=True)
class CheckoutStateGrant:
    state_token: str
    client_reference: str
    expires_at: int


@dataclass(frozen=True)
class CheckoutStart:
    checkout_url: str
    state_token: str
    client_reference: str
    expires_at: int


@dataclass(frozen=True)
class CheckoutConfirmation:
    access_token: str
    plan: str
    subscription_status: str
    customer_id: str


@dataclass(frozen=True)
class WebhookResult:
    event_id: str
    event_type: str
    processed: bool
    duplicate: bool = False
    ignored: bool = False
    subscription_id: str | None = None


class BillingStore:
    """Small transactional entitlement store for a single app instance."""

    def __init__(
        self,
        path: Path | str,
        *,
        token_pepper: str | bytes,
        clock: Callable[[], float] = time.time,
        token_factory: Callable[[int], str] = secrets.token_urlsafe,
    ) -> None:
        pepper = token_pepper.encode("utf-8") if isinstance(token_pepper, str) else bytes(token_pepper)
        if len(pepper) < 32:
            raise ValueError("token_pepper must contain at least 32 bytes")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._pepper = pepper
        self._clock = clock
        self._token_factory = token_factory
        self._lock = threading.RLock()
        self._init_db()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=10000")
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS billing_customers (
                    customer_id TEXT PRIMARY KEY,
                    email_normalized TEXT,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_billing_customers_email
                    ON billing_customers(email_normalized);

                CREATE TABLE IF NOT EXISTS billing_subscriptions (
                    subscription_id TEXT PRIMARY KEY,
                    customer_id TEXT NOT NULL,
                    plan TEXT NOT NULL,
                    price_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    last_event_created INTEGER NOT NULL DEFAULT 0,
                    updated_at INTEGER NOT NULL,
                    FOREIGN KEY(customer_id) REFERENCES billing_customers(customer_id)
                );
                CREATE INDEX IF NOT EXISTS idx_billing_subscriptions_customer
                    ON billing_subscriptions(customer_id, status);

                CREATE TABLE IF NOT EXISTS billing_access_tokens (
                    token_hash TEXT PRIMARY KEY,
                    customer_id TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    last_seen_at INTEGER NOT NULL,
                    revoked_at INTEGER,
                    FOREIGN KEY(customer_id) REFERENCES billing_customers(customer_id)
                );
                CREATE INDEX IF NOT EXISTS idx_billing_access_customer
                    ON billing_access_tokens(customer_id, revoked_at, expires_at);

                CREATE TABLE IF NOT EXISTS billing_checkout_states (
                    state_hash TEXT PRIMARY KEY,
                    client_reference TEXT NOT NULL UNIQUE,
                    plan TEXT NOT NULL,
                    price_id TEXT NOT NULL,
                    stripe_session_id TEXT UNIQUE,
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    consumed_at INTEGER
                );

                CREATE TABLE IF NOT EXISTS billing_email_login_tokens (
                    token_hash TEXT PRIMARY KEY,
                    customer_id TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    consumed_at INTEGER,
                    FOREIGN KEY(customer_id) REFERENCES billing_customers(customer_id)
                );

                CREATE TABLE IF NOT EXISTS billing_webhook_events (
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    event_created INTEGER NOT NULL,
                    processed_at INTEGER NOT NULL
                );
                """
            )
            conn.commit()

    def close(self) -> None:
        """Compatibility hook; connections are intentionally short-lived."""

    def _now(self) -> int:
        return int(self._clock())

    def _new_token(self) -> str:
        token = str(self._token_factory(32))
        if len(token) < 24:
            raise RuntimeError("token_factory returned an unsafe token")
        return token

    def _hash_token(self, purpose: str, token: str) -> str:
        if not isinstance(token, str) or not token:
            return ""
        return hmac.new(
            self._pepper,
            f"uk-activity-radar:{purpose}:{token}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    @staticmethod
    def _identifier(value: object, name: str) -> str:
        result = str(value or "").strip()
        if not result or len(result) > 255 or any(ord(ch) < 32 for ch in result):
            raise ValueError(f"invalid {name}")
        return result

    @staticmethod
    def _normalise_email(value: str | None) -> str | None:
        email = str(value or "").strip().casefold()
        if not email:
            return None
        if len(email) > 254 or "@" not in email or email.startswith("@") or email.endswith("@"):
            raise ValueError("invalid email")
        return email

    def _upsert_customer_conn(
        self,
        conn: sqlite3.Connection,
        customer_id: str,
        email: str | None,
        now: int,
    ) -> None:
        normalised = self._normalise_email(email)
        conn.execute(
            """
            INSERT INTO billing_customers(customer_id,email_normalized,created_at,updated_at)
            VALUES(?,?,?,?)
            ON CONFLICT(customer_id) DO UPDATE SET
                email_normalized=COALESCE(excluded.email_normalized,billing_customers.email_normalized),
                updated_at=excluded.updated_at
            """,
            (customer_id, normalised, now, now),
        )

    def upsert_customer(self, customer_id: str, email: str | None = None) -> None:
        customer_id = self._identifier(customer_id, "customer_id")
        now = self._now()
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._upsert_customer_conn(conn, customer_id, email, now)
            conn.commit()

    def _upsert_subscription_conn(
        self,
        conn: sqlite3.Connection,
        *,
        subscription_id: str,
        customer_id: str,
        plan: str,
        price_id: str,
        status: str,
        event_created: int,
        now: int,
    ) -> bool:
        current = conn.execute(
            "SELECT last_event_created FROM billing_subscriptions WHERE subscription_id=?",
            (subscription_id,),
        ).fetchone()
        if current is not None and event_created < int(current["last_event_created"]):
            return False
        conn.execute(
            """
            INSERT INTO billing_subscriptions(
                subscription_id,customer_id,plan,price_id,status,last_event_created,updated_at
            ) VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(subscription_id) DO UPDATE SET
                customer_id=excluded.customer_id,
                plan=excluded.plan,
                price_id=excluded.price_id,
                status=excluded.status,
                last_event_created=excluded.last_event_created,
                updated_at=excluded.updated_at
            """,
            (subscription_id, customer_id, plan, price_id, status, event_created, now),
        )
        return True

    def upsert_subscription(
        self,
        *,
        subscription_id: str,
        customer_id: str,
        plan: str,
        price_id: str,
        status: str,
        email: str | None = None,
        event_created: int | None = None,
    ) -> bool:
        subscription_id = self._identifier(subscription_id, "subscription_id")
        customer_id = self._identifier(customer_id, "customer_id")
        plan = self._identifier(plan, "plan")
        price_id = self._identifier(price_id, "price_id")
        status = self._identifier(status, "status").casefold()
        now = self._now()
        stamp = now if event_created is None else int(event_created)
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._upsert_customer_conn(conn, customer_id, email, now)
            changed = self._upsert_subscription_conn(
                conn,
                subscription_id=subscription_id,
                customer_id=customer_id,
                plan=plan,
                price_id=price_id,
                status=status,
                event_created=stamp,
                now=now,
            )
            conn.commit()
        return changed

    def apply_subscription_webhook(
        self,
        *,
        event_id: str,
        event_type: str,
        event_created: int,
        subscription_id: str,
        customer_id: str,
        plan: str,
        price_id: str,
        status: str,
        email: str | None = None,
    ) -> bool:
        """Atomically apply an ordered subscription event and mark it handled."""

        event_id = self._identifier(event_id, "event_id")
        event_type = self._identifier(event_type, "event_type")
        subscription_id = self._identifier(subscription_id, "subscription_id")
        customer_id = self._identifier(customer_id, "customer_id")
        plan = self._identifier(plan, "plan")
        price_id = self._identifier(price_id, "price_id")
        status = self._identifier(status, "status").casefold()
        event_created = int(event_created)
        now = self._now()
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute(
                "SELECT 1 FROM billing_webhook_events WHERE event_id=?", (event_id,)
            ).fetchone():
                conn.rollback()
                return False
            self._upsert_customer_conn(conn, customer_id, email, now)
            self._upsert_subscription_conn(
                conn,
                subscription_id=subscription_id,
                customer_id=customer_id,
                plan=plan,
                price_id=price_id,
                status=status,
                event_created=event_created,
                now=now,
            )
            conn.execute(
                "INSERT INTO billing_webhook_events(event_id,event_type,event_created,processed_at) VALUES(?,?,?,?)",
                (event_id, event_type, event_created, now),
            )
            conn.commit()
            return True

    def mark_webhook_processed(self, event_id: str, event_type: str, event_created: int) -> bool:
        event_id = self._identifier(event_id, "event_id")
        event_type = self._identifier(event_type, "event_type")
        with self._lock, self._connect() as conn:
            try:
                conn.execute(
                    "INSERT INTO billing_webhook_events(event_id,event_type,event_created,processed_at) VALUES(?,?,?,?)",
                    (event_id, event_type, int(event_created), self._now()),
                )
                conn.commit()
                return True
            except sqlite3.IntegrityError:
                return False

    def webhook_processed(self, event_id: str) -> bool:
        with self._connect() as conn:
            return conn.execute(
                "SELECT 1 FROM billing_webhook_events WHERE event_id=?", (str(event_id),)
            ).fetchone() is not None

    def issue_access_token(self, customer_id: str, *, ttl_seconds: int = 90 * 24 * 60 * 60) -> str:
        customer_id = self._identifier(customer_id, "customer_id")
        if ttl_seconds < 60:
            raise ValueError("access token lifetime must be at least 60 seconds")
        raw = self._new_token()
        digest = self._hash_token("access", raw)
        now = self._now()
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if not conn.execute(
                "SELECT 1 FROM billing_customers WHERE customer_id=?", (customer_id,)
            ).fetchone():
                raise BillingStateError("customer does not exist")
            conn.execute(
                """
                INSERT INTO billing_access_tokens(
                    token_hash,customer_id,created_at,expires_at,last_seen_at,revoked_at
                ) VALUES(?,?,?,?,?,NULL)
                """,
                (digest, customer_id, now, now + int(ttl_seconds), now),
            )
            conn.commit()
        return raw

    def access_context(self, raw_token: str | None) -> AccessContext:
        if not raw_token:
            return AccessContext()
        digest = self._hash_token("access", raw_token)
        now = self._now()
        with self._lock, self._connect() as conn:
            row = conn.execute(
                """
                SELECT customer_id,last_seen_at FROM billing_access_tokens
                WHERE token_hash=? AND revoked_at IS NULL AND expires_at>?
                """,
                (digest, now),
            ).fetchone()
            if row is None:
                return AccessContext()
            customer_id = str(row["customer_id"])
            subscription = conn.execute(
                """
                SELECT plan,status FROM billing_subscriptions
                WHERE customer_id=? AND plan=? AND status IN ('active','trialing')
                ORDER BY updated_at DESC LIMIT 1
                """,
                (customer_id, PLAN_PLUS),
            ).fetchone()
            # Avoid turning every API request into a database write.
            if now - int(row["last_seen_at"]) >= 3600:
                conn.execute(
                    "UPDATE billing_access_tokens SET last_seen_at=? WHERE token_hash=?", (now, digest)
                )
                conn.commit()
        if subscription is None:
            return AccessContext(token_valid=True, customer_id=customer_id)
        status = str(subscription["status"])
        return AccessContext(
            token_valid=True,
            customer_id=customer_id,
            plan=PLAN_PLUS,
            subscription_status=status,
            features=PLUS_FEATURES,
        )

    def revoke_access_token(self, raw_token: str | None) -> bool:
        if not raw_token:
            return False
        digest = self._hash_token("access", raw_token)
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE billing_access_tokens SET revoked_at=?
                WHERE token_hash=? AND revoked_at IS NULL
                """,
                (self._now(), digest),
            )
            conn.commit()
            return cursor.rowcount == 1

    def revoke_customer_tokens(self, customer_id: str) -> int:
        customer_id = self._identifier(customer_id, "customer_id")
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE billing_access_tokens SET revoked_at=?
                WHERE customer_id=? AND revoked_at IS NULL
                """,
                (self._now(), customer_id),
            )
            conn.commit()
            return int(cursor.rowcount)

    def create_checkout_state(
        self,
        *,
        plan: str,
        price_id: str,
        ttl_seconds: int = 20 * 60,
    ) -> CheckoutStateGrant:
        plan = self._identifier(plan, "plan")
        price_id = self._identifier(price_id, "price_id")
        if ttl_seconds < 60:
            raise ValueError("checkout state lifetime must be at least 60 seconds")
        raw = self._new_token()
        digest = self._hash_token("checkout", raw)
        client_reference = "radar_" + self._new_token()[:48]
        now = self._now()
        expires = now + int(ttl_seconds)
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO billing_checkout_states(
                    state_hash,client_reference,plan,price_id,stripe_session_id,
                    created_at,expires_at,consumed_at
                ) VALUES(?,?,?,?,NULL,?,?,NULL)
                """,
                (digest, client_reference, plan, price_id, now, expires),
            )
            conn.commit()
        return CheckoutStateGrant(raw, client_reference, expires)

    def bind_checkout_session(self, state_token: str, stripe_session_id: str) -> None:
        digest = self._hash_token("checkout", state_token)
        stripe_session_id = self._identifier(stripe_session_id, "stripe_session_id")
        now = self._now()
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM billing_checkout_states WHERE state_hash=?", (digest,)
            ).fetchone()
            if row is None or int(row["expires_at"]) <= now or row["consumed_at"] is not None:
                raise BillingStateError("checkout state is invalid or expired")
            existing = row["stripe_session_id"]
            if existing and existing != stripe_session_id:
                raise BillingStateError("checkout state is already bound")
            try:
                conn.execute(
                    "UPDATE billing_checkout_states SET stripe_session_id=? WHERE state_hash=?",
                    (stripe_session_id, digest),
                )
                conn.commit()
            except sqlite3.IntegrityError as exc:
                raise BillingStateError("checkout session is already bound") from exc

    def inspect_checkout_state(
        self,
        state_token: str,
        *,
        stripe_session_id: str,
        client_reference: str,
    ) -> CheckoutState:
        digest = self._hash_token("checkout", state_token)
        now = self._now()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM billing_checkout_states WHERE state_hash=?", (digest,)
            ).fetchone()
        if row is None or int(row["expires_at"]) <= now or row["consumed_at"] is not None:
            raise BillingStateError("checkout state is invalid, expired, or already used")
        if row["stripe_session_id"] != stripe_session_id:
            raise BillingStateError("checkout session does not match this browser")
        if row["client_reference"] != client_reference:
            raise BillingStateError("checkout client reference does not match")
        return CheckoutState(
            client_reference=str(row["client_reference"]),
            plan=str(row["plan"]),
            price_id=str(row["price_id"]),
            stripe_session_id=str(row["stripe_session_id"]),
            expires_at=int(row["expires_at"]),
            consumed=False,
        )

    def finalise_checkout(
        self,
        *,
        state_token: str,
        stripe_session_id: str,
        client_reference: str,
        customer_id: str,
        email: str | None,
        subscription_id: str,
        plan: str,
        price_id: str,
        status: str,
        access_ttl_seconds: int,
    ) -> str:
        """Consume checkout state and issue access atomically."""

        digest = self._hash_token("checkout", state_token)
        customer_id = self._identifier(customer_id, "customer_id")
        subscription_id = self._identifier(subscription_id, "subscription_id")
        stripe_session_id = self._identifier(stripe_session_id, "stripe_session_id")
        client_reference = self._identifier(client_reference, "client_reference")
        plan = self._identifier(plan, "plan")
        price_id = self._identifier(price_id, "price_id")
        status = self._identifier(status, "status").casefold()
        if status not in ACTIVE_SUBSCRIPTION_STATUSES:
            raise BillingPaymentPending("subscription is not active")
        if access_ttl_seconds < 60:
            raise ValueError("access token lifetime must be at least 60 seconds")
        raw_access = self._new_token()
        access_hash = self._hash_token("access", raw_access)
        now = self._now()
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM billing_checkout_states WHERE state_hash=?", (digest,)
            ).fetchone()
            if row is None or int(row["expires_at"]) <= now or row["consumed_at"] is not None:
                raise BillingStateError("checkout state is invalid, expired, or already used")
            expected = (
                row["stripe_session_id"], row["client_reference"], row["plan"], row["price_id"]
            )
            actual = (stripe_session_id, client_reference, plan, price_id)
            if expected != actual:
                raise BillingStateError("checkout confirmation does not match its state")
            self._upsert_customer_conn(conn, customer_id, email, now)
            self._upsert_subscription_conn(
                conn,
                subscription_id=subscription_id,
                customer_id=customer_id,
                plan=plan,
                price_id=price_id,
                status=status,
                event_created=now,
                now=now,
            )
            conn.execute(
                "UPDATE billing_checkout_states SET consumed_at=? WHERE state_hash=?",
                (now, digest),
            )
            conn.execute(
                """
                INSERT INTO billing_access_tokens(
                    token_hash,customer_id,created_at,expires_at,last_seen_at,revoked_at
                ) VALUES(?,?,?,?,?,NULL)
                """,
                (access_hash, customer_id, now, now + int(access_ttl_seconds), now),
            )
            conn.commit()
        return raw_access

    def active_customer_for_email(self, email: str) -> str | None:
        normalised = self._normalise_email(email)
        if normalised is None:
            return None
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT c.customer_id
                FROM billing_customers c
                JOIN billing_subscriptions s ON s.customer_id=c.customer_id
                WHERE c.email_normalized=? AND s.plan=? AND s.status IN ('active','trialing')
                ORDER BY s.updated_at DESC LIMIT 1
                """,
                (normalised, PLAN_PLUS),
            ).fetchone()
        return str(row["customer_id"]) if row is not None else None

    def create_email_login_token(self, email: str, *, ttl_seconds: int = 15 * 60) -> str | None:
        """Create a token only for an entitled email; callers must respond generically."""

        if ttl_seconds < 60:
            raise ValueError("email login lifetime must be at least 60 seconds")
        customer_id = self.active_customer_for_email(email)
        if customer_id is None:
            return None
        raw = self._new_token()
        digest = self._hash_token("email-login", raw)
        now = self._now()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO billing_email_login_tokens(
                    token_hash,customer_id,created_at,expires_at,consumed_at
                ) VALUES(?,?,?,?,NULL)
                """,
                (digest, customer_id, now, now + int(ttl_seconds)),
            )
            conn.commit()
        return raw

    def consume_email_login_token(self, raw_token: str) -> str | None:
        digest = self._hash_token("email-login", raw_token)
        now = self._now()
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT customer_id FROM billing_email_login_tokens
                WHERE token_hash=? AND consumed_at IS NULL AND expires_at>?
                """,
                (digest, now),
            ).fetchone()
            if row is None:
                conn.rollback()
                return None
            conn.execute(
                "UPDATE billing_email_login_tokens SET consumed_at=? WHERE token_hash=?",
                (now, digest),
            )
            conn.commit()
            return str(row["customer_id"])


class StripeGateway(Protocol):
    def create_checkout_session(self, params: Mapping[str, Any], *, idempotency_key: str) -> Any: ...

    def retrieve_checkout_session(self, session_id: str, *, expand: tuple[str, ...]) -> Any: ...

    def retrieve_subscription(self, subscription_id: str) -> Any: ...

    def create_customer_portal_session(self, customer_id: str, *, return_url: str) -> Any: ...

    def construct_webhook_event(self, payload: bytes, signature: str, secret: str) -> Any: ...


class StripeSdkGateway:
    """Thin adapter around the pinned Stripe SDK; replace with a fake in tests."""

    def __init__(self, secret_key: str) -> None:
        try:
            import stripe
        except ImportError as exc:  # pragma: no cover - deployment configuration
            raise BillingConfigurationError("The pinned Stripe SDK is not installed") from exc
        stripe.api_key = secret_key
        self._stripe = stripe

    def create_checkout_session(self, params: Mapping[str, Any], *, idempotency_key: str) -> Any:
        return self._stripe.checkout.Session.create(
            **dict(params), idempotency_key=idempotency_key
        )

    def retrieve_checkout_session(self, session_id: str, *, expand: tuple[str, ...]) -> Any:
        return self._stripe.checkout.Session.retrieve(session_id, expand=list(expand))

    def retrieve_subscription(self, subscription_id: str) -> Any:
        return self._stripe.Subscription.retrieve(subscription_id)

    def create_customer_portal_session(self, customer_id: str, *, return_url: str) -> Any:
        return self._stripe.billing_portal.Session.create(
            customer=customer_id, return_url=return_url
        )

    def construct_webhook_event(self, payload: bytes, signature: str, secret: str) -> Any:
        return self._stripe.Webhook.construct_event(payload, signature, secret)


def _plain(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    converter = getattr(value, "to_dict", None)
    if callable(converter):
        try:
            return _plain(converter(for_json=True))
        except TypeError:
            return _plain(converter())
    raise BillingWebhookError("Stripe returned an unsupported object")


def _nested(mapping: Mapping[str, Any], *path: str) -> Any:
    value: Any = mapping
    for key in path:
        if not isinstance(value, Mapping):
            return None
        value = value.get(key)
    return value


def _resource_id(value: Any) -> str:
    if isinstance(value, Mapping):
        return str(value.get("id") or "").strip()
    return str(value or "").strip()


def _subscription_price_id(subscription: Mapping[str, Any]) -> str:
    rows = _nested(subscription, "items", "data")
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            price = row.get("price")
            price_id = _resource_id(price)
            if price_id:
                return price_id
    return ""


def _checkout_price_id(session: Mapping[str, Any]) -> str:
    rows = _nested(session, "line_items", "data")
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, Mapping):
                price_id = _resource_id(row.get("price"))
                if price_id:
                    return price_id
    return ""


class StripeBillingService:
    """Hosted Checkout/Portal orchestration with no implicit network in tests."""

    SUBSCRIPTION_EVENTS = frozenset(
        {
            "customer.subscription.created",
            "customer.subscription.updated",
            "customer.subscription.deleted",
            "customer.subscription.paused",
            "customer.subscription.resumed",
        }
    )

    def __init__(
        self,
        store: BillingStore,
        config: BillingConfiguration,
        *,
        gateway: StripeGateway | None = None,
    ) -> None:
        validate_billing_configuration(config)
        self.store = store
        self.config = config
        self.gateway = gateway
        if config.enabled and gateway is None:
            self.gateway = StripeSdkGateway(config.stripe_secret_key)

    def _require_enabled(self) -> StripeGateway:
        if not self.config.enabled or self.gateway is None:
            raise BillingDisabledError("Billing is not enabled")
        return self.gateway

    @property
    def base_url(self) -> str:
        return self.config.public_base_url.rstrip("/")

    def _plan_for_price(self, price_id: str) -> str:
        return PLAN_PLUS if hmac.compare_digest(price_id, self.config.stripe_plus_price_id) else "unknown"

    @staticmethod
    def _hosted_url(value: object, *, expected_host: str | None = None) -> str:
        url = str(value or "").strip()
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise BillingStateError("Stripe did not return a safe hosted URL")
        if expected_host and not (
            parsed.hostname == expected_host or parsed.hostname.endswith("." + expected_host)
        ):
            raise BillingStateError("Stripe returned an unexpected hosted URL")
        return url

    def create_checkout(self, plan: str = PLAN_PLUS) -> CheckoutStart:
        gateway = self._require_enabled()
        if plan != PLAN_PLUS:
            raise BillingStateError("Unknown paid plan")
        grant = self.store.create_checkout_state(
            plan=PLAN_PLUS,
            price_id=self.config.stripe_plus_price_id,
            ttl_seconds=self.config.checkout_ttl_seconds,
        )
        params = {
            "mode": "subscription",
            "line_items": [{"price": self.config.stripe_plus_price_id, "quantity": 1}],
            "success_url": self.base_url + "/billing/success?session_id={CHECKOUT_SESSION_ID}",
            "cancel_url": self.base_url + "/?billing=cancelled",
            "client_reference_id": grant.client_reference,
            "metadata": {"radar_plan": PLAN_PLUS, "radar_checkout_ref": grant.client_reference},
            "subscription_data": {
                "metadata": {"radar_plan": PLAN_PLUS, "radar_checkout_ref": grant.client_reference}
            },
        }
        session = _plain(
            gateway.create_checkout_session(
                params, idempotency_key="radar-checkout-" + grant.client_reference
            )
        )
        if not isinstance(session, Mapping):
            raise BillingStateError("Stripe returned an invalid Checkout Session")
        session_id = str(session.get("id") or "").strip()
        if not session_id:
            raise BillingStateError("Stripe Checkout Session has no ID")
        checkout_url = self._hosted_url(session.get("url"), expected_host="stripe.com")
        self.store.bind_checkout_session(grant.state_token, session_id)
        return CheckoutStart(
            checkout_url=checkout_url,
            state_token=grant.state_token,
            client_reference=grant.client_reference,
            expires_at=grant.expires_at,
        )

    def _current_subscription(self, raw: Any) -> Mapping[str, Any]:
        gateway = self._require_enabled()
        value = _plain(raw)
        subscription_id = _resource_id(value)
        if not subscription_id:
            raise BillingStateError("Checkout has no subscription")
        # Always retrieve the current object; a Checkout snapshot can become
        # stale if cancellation/payment events race the browser redirect.
        current = _plain(gateway.retrieve_subscription(subscription_id))
        if not isinstance(current, Mapping) or str(current.get("id") or "") != subscription_id:
            raise BillingStateError("Stripe returned an invalid subscription")
        return current

    def confirm_checkout(self, session_id: str, state_token: str) -> CheckoutConfirmation:
        gateway = self._require_enabled()
        session_id = BillingStore._identifier(session_id, "session_id")
        session = _plain(
            gateway.retrieve_checkout_session(
                session_id,
                expand=("subscription", "line_items.data.price", "customer"),
            )
        )
        if not isinstance(session, Mapping) or str(session.get("id") or "") != session_id:
            raise BillingStateError("Stripe returned an invalid Checkout Session")
        if session.get("mode") != "subscription" or session.get("status") != "complete":
            raise BillingPaymentPending("Checkout is not complete")
        client_reference = str(session.get("client_reference_id") or "").strip()
        state = self.store.inspect_checkout_state(
            state_token,
            stripe_session_id=session_id,
            client_reference=client_reference,
        )
        subscription = self._current_subscription(session.get("subscription"))
        subscription_id = str(subscription.get("id") or "").strip()
        customer_id = _resource_id(subscription.get("customer")) or _resource_id(session.get("customer"))
        status = str(subscription.get("status") or "").casefold()
        price_id = _subscription_price_id(subscription) or _checkout_price_id(session)
        plan = self._plan_for_price(price_id)
        if state.plan != PLAN_PLUS or plan != state.plan or price_id != state.price_id:
            raise BillingStateError("Checkout price does not match the requested plan")
        if not customer_id or not subscription_id:
            raise BillingStateError("Checkout is missing its customer or subscription")
        if status not in ACTIVE_SUBSCRIPTION_STATUSES:
            raise BillingPaymentPending("Subscription payment is not active yet")
        email = _nested(session, "customer_details", "email") or _nested(session, "customer", "email")
        access_token = self.store.finalise_checkout(
            state_token=state_token,
            stripe_session_id=session_id,
            client_reference=client_reference,
            customer_id=customer_id,
            email=str(email) if email else None,
            subscription_id=subscription_id,
            plan=plan,
            price_id=price_id,
            status=status,
            access_ttl_seconds=self.config.access_ttl_seconds,
        )
        return CheckoutConfirmation(
            access_token=access_token,
            plan=plan,
            subscription_status=status,
            customer_id=customer_id,
        )

    def create_portal(self, access_token: str) -> str:
        gateway = self._require_enabled()
        context = self.store.access_context(access_token)
        if not context.token_valid or not context.customer_id:
            raise BillingAuthenticationError("A valid subscriber session is required")
        portal = _plain(
            gateway.create_customer_portal_session(
                context.customer_id,
                return_url=self.base_url + "/?billing=portal-return",
            )
        )
        if not isinstance(portal, Mapping):
            raise BillingStateError("Stripe returned an invalid Portal Session")
        return self._hosted_url(portal.get("url"), expected_host="stripe.com")

    def _validate_event_mode(self, event: Mapping[str, Any]) -> None:
        live_key = self.config.stripe_secret_key.startswith("sk_live_")
        livemode = event.get("livemode")
        if isinstance(livemode, bool) and livemode != live_key:
            raise BillingWebhookError("Stripe event mode does not match the configured key")

    def handle_webhook(self, payload: bytes, signature: str) -> WebhookResult:
        gateway = self._require_enabled()
        if not isinstance(payload, bytes) or not payload or len(payload) > MAX_WEBHOOK_BYTES:
            raise BillingWebhookError("Webhook body is empty or too large")
        if not signature:
            raise BillingWebhookError("Stripe-Signature is required")
        try:
            event = _plain(
                gateway.construct_webhook_event(
                    payload, signature, self.config.stripe_webhook_secret
                )
            )
        except BillingError:
            raise
        except Exception as exc:
            raise BillingWebhookError("Stripe webhook signature verification failed") from exc
        if not isinstance(event, Mapping):
            raise BillingWebhookError("Stripe returned an invalid webhook event")
        self._validate_event_mode(event)
        event_id = str(event.get("id") or "").strip()
        event_type = str(event.get("type") or "").strip()
        event_created = int(event.get("created") or 0)
        if not event_id or not event_type or event_created <= 0:
            raise BillingWebhookError("Stripe event metadata is incomplete")
        if self.store.webhook_processed(event_id):
            return WebhookResult(event_id, event_type, processed=False, duplicate=True)

        obj = _nested(event, "data", "object")
        if not isinstance(obj, Mapping):
            raise BillingWebhookError("Stripe event object is missing")

        if event_type in self.SUBSCRIPTION_EVENTS:
            subscription = obj
            subscription_id = str(subscription.get("id") or "").strip()
            customer_id = _resource_id(subscription.get("customer"))
            price_id = _subscription_price_id(subscription)
            status = str(subscription.get("status") or "").casefold()
            if event_type == "customer.subscription.deleted":
                status = "canceled"
            elif event_type == "customer.subscription.paused":
                status = "paused"
            if not all((subscription_id, customer_id, price_id, status)):
                raise BillingWebhookError("Stripe subscription event is incomplete")
            applied = self.store.apply_subscription_webhook(
                event_id=event_id,
                event_type=event_type,
                event_created=event_created,
                subscription_id=subscription_id,
                customer_id=customer_id,
                plan=self._plan_for_price(price_id),
                price_id=price_id,
                status=status,
            )
            return WebhookResult(
                event_id,
                event_type,
                processed=applied,
                duplicate=not applied,
                subscription_id=subscription_id,
            )

        if event_type in {"checkout.session.completed", "checkout.session.async_payment_succeeded"}:
            session = obj
            subscription = self._current_subscription(session.get("subscription"))
            subscription_id = str(subscription.get("id") or "").strip()
            customer_id = _resource_id(subscription.get("customer")) or _resource_id(session.get("customer"))
            price_id = _subscription_price_id(subscription)
            status = str(subscription.get("status") or "").casefold()
            email = _nested(session, "customer_details", "email") or _nested(session, "customer", "email")
            if not all((subscription_id, customer_id, price_id, status)):
                raise BillingWebhookError("Stripe Checkout event is incomplete")
            applied = self.store.apply_subscription_webhook(
                event_id=event_id,
                event_type=event_type,
                event_created=event_created,
                subscription_id=subscription_id,
                customer_id=customer_id,
                plan=self._plan_for_price(price_id),
                price_id=price_id,
                status=status,
                email=str(email) if email else None,
            )
            return WebhookResult(
                event_id,
                event_type,
                processed=applied,
                duplicate=not applied,
                subscription_id=subscription_id,
            )

        marked = self.store.mark_webhook_processed(event_id, event_type, event_created)
        return WebhookResult(
            event_id,
            event_type,
            processed=marked,
            duplicate=not marked,
            ignored=True,
        )
