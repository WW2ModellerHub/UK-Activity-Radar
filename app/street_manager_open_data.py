"""Safe ingestion primitives for DfT Street Manager public open data.

Street Manager's *public* feed is an AWS SNS publisher/subscriber stream.  It
is distinct from the organisation-scoped Reporting API implemented in
``app.collectors.street_manager``.  The official production topics POST SNS
envelopes to a subscriber-owned HTTPS endpoint.

This module:

* allow-lists the three documented production topic ARNs;
* verifies AWS SNS signatures using certificates fetched only from the
  allow-listed regional SNS origin;
* validates SNS subscription-confirmation URLs without fetching them;
* parses additive permit/activity notification objects defensively;
* maps current permit/activity objects to Radar ``Event`` records; and
* stores an ordered SQLite snapshot with tombstones, so stale replay cannot
  resurrect a cancelled work.

Production HTTP wiring must use :func:`verify_and_parse_sns_envelope`, which
binds verification and parsing to the same decoded envelope.  The snapshot
store refuses structurally parsed, unverified notifications by default.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import sqlite3
import threading
from collections import OrderedDict
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qs, urlparse

import requests
from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from .collectors.street_manager import STREET_MANAGER_GUIDANCE_URL, street_manager_position
from .models import Event


ACTIVITY_TOPIC_ARN = "arn:aws:sns:eu-west-2:287813576808:prod-activity-topic"
PERMIT_TOPIC_ARN = "arn:aws:sns:eu-west-2:287813576808:prod-permit-topic"
SECTION_58_TOPIC_ARN = "arn:aws:sns:eu-west-2:287813576808:prod-section-58-topic"
ALLOWED_TOPIC_ARNS = frozenset({ACTIVITY_TOPIC_ARN, PERMIT_TOPIC_ARN, SECTION_58_TOPIC_ARN})

_TOPIC_OBJECT_TYPE = {
    ACTIVITY_TOPIC_ARN: "ACTIVITY",
    PERMIT_TOPIC_ARN: "PERMIT",
    SECTION_58_TOPIC_ARN: "SECTION_58",
}
_SNS_HOST = "sns.eu-west-2.amazonaws.com"
_MAX_ENVELOPE_BYTES = 1_000_000
_MAX_MESSAGE_BYTES = 750_000
_MAX_CERTIFICATE_BYTES = 64 * 1024
_CERTIFICATE_CACHE_SIZE = 8
_CERTIFICATE_FETCH_TIMEOUT = (3.05, 5.0)
_CERTIFICATE_CACHE: OrderedDict[str, x509.Certificate] = OrderedDict()
_CERTIFICATE_CACHE_LOCK = threading.RLock()


class StreetManagerMessageError(ValueError):
    """Raised when an SNS envelope fails structural or routing validation."""


class UnverifiedStreetManagerMessage(PermissionError):
    """Raised when unverified data is offered to the trusted snapshot."""


@dataclass(frozen=True)
class StreetManagerOpenDataMessage:
    envelope: dict[str, Any]
    message_type: str
    message_id: str
    topic_arn: str
    timestamp: datetime | None
    signature_verified: bool = False
    subscribe_url: str | None = None
    event_type: str | None = None
    event_time: datetime | None = None
    object_type: str | None = None
    object_reference: str | None = None
    object_data: dict[str, Any] | None = None
    version: int = 0


def _datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _clean(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text or None


def _humanise(value: Any) -> str | None:
    text = _clean(value)
    if not text:
        return None
    return text.replace("_", " ").capitalize()


def _truthy(value: Any) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes", "y"}


def validate_topic_arn(topic_arn: Any) -> str:
    topic = str(topic_arn or "").strip()
    if topic not in ALLOWED_TOPIC_ARNS:
        raise StreetManagerMessageError("SNS TopicArn is not a documented Street Manager production topic")
    return topic


def validate_subscription_url(url: Any, topic_arn: str) -> str:
    """Validate, but never call, a Street Manager SNS confirmation URL."""

    topic = validate_topic_arn(topic_arn)
    raw = str(url or "").strip()
    parsed = urlparse(raw)
    try:
        port = parsed.port
    except ValueError as exc:
        raise StreetManagerMessageError("invalid Street Manager SNS SubscribeURL port") from exc
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() != _SNS_HOST
        or port not in (None, 443)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise StreetManagerMessageError("invalid Street Manager SNS SubscribeURL origin")
    query = parse_qs(parsed.query, keep_blank_values=True)
    if query.get("Action") != ["ConfirmSubscription"]:
        raise StreetManagerMessageError("SubscribeURL does not request ConfirmSubscription")
    if query.get("TopicArn") != [topic] or not query.get("Token", [""])[0]:
        raise StreetManagerMessageError("SubscribeURL topic/token does not match the SNS envelope")
    return raw


def validate_signing_cert_url(url: Any) -> str:
    """Return an exact SNS certificate URL after strict origin/path checks."""

    raw = str(url or "").strip()
    parsed = urlparse(raw)
    try:
        port = parsed.port
    except ValueError as exc:
        raise StreetManagerMessageError("invalid SNS SigningCertURL port") from exc
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() != _SNS_HOST
        or port not in (None, 443)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not re.fullmatch(
            r"/SimpleNotificationService-[A-Za-z0-9_-]+\.pem",
            parsed.path,
        )
    ):
        raise StreetManagerMessageError("invalid SNS SigningCertURL origin or path")
    return raw


def _json_object(payload: bytes | str | Mapping[str, Any], *, limit: int) -> dict[str, Any]:
    if isinstance(payload, Mapping):
        decoded = dict(payload)
        try:
            encoded = json.dumps(
                decoded,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError, UnicodeEncodeError) as exc:
            raise StreetManagerMessageError(
                "SNS payload contains values that are not valid JSON"
            ) from exc
        if len(encoded) > limit:
            raise StreetManagerMessageError("SNS payload exceeds the safe size limit")
        return decoded
    if isinstance(payload, bytes):
        if len(payload) > limit:
            raise StreetManagerMessageError("SNS payload exceeds the safe size limit")
        try:
            payload = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise StreetManagerMessageError("SNS payload is not UTF-8") from exc
    if not isinstance(payload, str):
        raise StreetManagerMessageError("SNS payload must be a JSON object")
    try:
        payload_size = len(payload.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise StreetManagerMessageError("SNS payload is not valid Unicode") from exc
    if payload_size > limit:
        raise StreetManagerMessageError("SNS payload exceeds the safe size limit")
    try:
        decoded = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise StreetManagerMessageError("SNS payload is not valid JSON") from exc
    if not isinstance(decoded, dict):
        raise StreetManagerMessageError("SNS payload must decode to an object")
    return decoded


def _required_sns_string(envelope: Mapping[str, Any], field: str) -> str:
    value = envelope.get(field)
    if not isinstance(value, str) or not value:
        raise StreetManagerMessageError(f"SNS {field} must be a non-empty string")
    return value


def _sns_canonical_bytes(envelope: Mapping[str, Any]) -> bytes:
    """Build the byte string AWS signs for supported SNS message types."""

    message_type = _required_sns_string(envelope, "Type")
    if message_type == "Notification":
        fields = ["Message", "MessageId"]
        if "Subject" in envelope:
            fields.append("Subject")
        fields.extend(["Timestamp", "TopicArn", "Type"])
    elif message_type == "SubscriptionConfirmation":
        fields = [
            "Message",
            "MessageId",
            "SubscribeURL",
            "Timestamp",
            "Token",
            "TopicArn",
            "Type",
        ]
    else:
        raise StreetManagerMessageError("unsupported SNS message Type")

    canonical: list[str] = []
    for field in fields:
        value = envelope.get(field)
        # Subject is optional, but an explicitly present Subject must still be
        # a string and must occupy its canonical position, even when empty.
        if not isinstance(value, str) or (field != "Subject" and not value):
            raise StreetManagerMessageError(f"SNS {field} must be a non-empty string")
        canonical.extend((field, value))
    try:
        return ("\n".join(canonical) + "\n").encode("utf-8")
    except UnicodeEncodeError as exc:
        raise StreetManagerMessageError("SNS canonical fields are not valid Unicode") from exc


def _certificate_is_current(certificate: x509.Certificate) -> bool:
    now = datetime.now(timezone.utc)
    not_before = getattr(certificate, "not_valid_before_utc", None)
    not_after = getattr(certificate, "not_valid_after_utc", None)
    if not_before is None:
        not_before = certificate.not_valid_before.replace(tzinfo=timezone.utc)
    if not_after is None:
        not_after = certificate.not_valid_after.replace(tzinfo=timezone.utc)
    return not_before <= now <= not_after


def _cached_signing_certificate(cert_url: str) -> x509.Certificate | None:
    with _CERTIFICATE_CACHE_LOCK:
        certificate = _CERTIFICATE_CACHE.get(cert_url)
        if certificate is None:
            return None
        if not _certificate_is_current(certificate):
            del _CERTIFICATE_CACHE[cert_url]
            return None
        _CERTIFICATE_CACHE.move_to_end(cert_url)
        return certificate


def _fetch_signing_certificate(
    cert_url: str,
    *,
    session: requests.Session | None,
) -> x509.Certificate:
    cached = _cached_signing_certificate(cert_url)
    if cached is not None:
        return cached

    requester = session if session is not None else requests
    response: Any = None
    try:
        response = requester.get(
            cert_url,
            timeout=_CERTIFICATE_FETCH_TIMEOUT,
            allow_redirects=False,
            stream=True,
            verify=True,
            headers={"Accept-Encoding": "identity"},
        )
        if (
            getattr(response, "status_code", None) != 200
            or getattr(response, "history", ())
            or getattr(response, "url", None) != cert_url
        ):
            raise StreetManagerMessageError("SNS signing certificate fetch was not a direct success")

        headers = getattr(response, "headers", None)
        if headers is None or not hasattr(headers, "get"):
            raise StreetManagerMessageError("SNS signing certificate response is malformed")
        content_length = headers.get("Content-Length")
        if content_length not in (None, ""):
            try:
                declared_length = int(content_length)
            except (TypeError, ValueError) as exc:
                raise StreetManagerMessageError(
                    "SNS signing certificate response has an invalid length"
                ) from exc
            if declared_length < 0 or declared_length > _MAX_CERTIFICATE_BYTES:
                raise StreetManagerMessageError("SNS signing certificate exceeds the safe size limit")

        iter_content = getattr(response, "iter_content", None)
        if not callable(iter_content):
            raise StreetManagerMessageError("SNS signing certificate response is not streamable")
        certificate_bytes = bytearray()
        for chunk in iter_content(chunk_size=8192):
            if not chunk:
                continue
            if not isinstance(chunk, bytes):
                raise StreetManagerMessageError("SNS signing certificate response is malformed")
            if len(chunk) > _MAX_CERTIFICATE_BYTES - len(certificate_bytes):
                raise StreetManagerMessageError("SNS signing certificate exceeds the safe size limit")
            certificate_bytes.extend(chunk)
        if not certificate_bytes:
            raise StreetManagerMessageError("SNS signing certificate response is empty")
    except StreetManagerMessageError:
        raise
    except Exception as exc:
        raise StreetManagerMessageError("unable to fetch SNS signing certificate") from exc
    finally:
        close = getattr(response, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass

    try:
        certificate = x509.load_pem_x509_certificate(bytes(certificate_bytes))
        public_key = certificate.public_key()
    except Exception as exc:
        raise StreetManagerMessageError("SNS signing certificate is invalid") from exc
    if not _certificate_is_current(certificate):
        raise StreetManagerMessageError("SNS signing certificate is not currently valid")
    if not isinstance(public_key, rsa.RSAPublicKey):
        raise StreetManagerMessageError("SNS signing certificate does not contain an RSA key")

    with _CERTIFICATE_CACHE_LOCK:
        existing = _CERTIFICATE_CACHE.get(cert_url)
        if existing is not None and _certificate_is_current(existing):
            _CERTIFICATE_CACHE.move_to_end(cert_url)
            return existing
        _CERTIFICATE_CACHE[cert_url] = certificate
        _CERTIFICATE_CACHE.move_to_end(cert_url)
        while len(_CERTIFICATE_CACHE) > _CERTIFICATE_CACHE_SIZE:
            _CERTIFICATE_CACHE.popitem(last=False)
    return certificate


def verify_sns_signature(
    payload: bytes | str | Mapping[str, Any],
    *,
    session: requests.Session | None = None,
) -> bool:
    """Verify an official Street Manager SNS envelope cryptographically.

    Validation failures, certificate download failures and signature failures
    are all reported as :class:`StreetManagerMessageError`; callers can reject
    the request without exposing network or cryptography implementation detail.
    """

    envelope = _json_object(payload, limit=_MAX_ENVELOPE_BYTES)
    _verify_sns_envelope(envelope, session=session)
    return True


def _verify_sns_envelope(
    envelope: Mapping[str, Any],
    *,
    session: requests.Session | None,
) -> None:
    message_type = _required_sns_string(envelope, "Type")
    if message_type not in {"Notification", "SubscriptionConfirmation"}:
        raise StreetManagerMessageError("unsupported SNS message Type")

    topic_value = _required_sns_string(envelope, "TopicArn")
    if validate_topic_arn(topic_value) != topic_value:
        raise StreetManagerMessageError("SNS TopicArn must use its exact documented form")
    if message_type == "SubscriptionConfirmation":
        subscribe_url = _required_sns_string(envelope, "SubscribeURL")
        if validate_subscription_url(subscribe_url, topic_value) != subscribe_url:
            raise StreetManagerMessageError("SNS SubscribeURL must use its exact validated form")
        token = _required_sns_string(envelope, "Token")
        query_token = parse_qs(urlparse(subscribe_url).query, keep_blank_values=True).get("Token")
        if query_token != [token]:
            raise StreetManagerMessageError("SNS SubscribeURL token does not match the envelope")

    cert_url_value = _required_sns_string(envelope, "SigningCertURL")
    cert_url = validate_signing_cert_url(cert_url_value)
    if cert_url != cert_url_value:
        raise StreetManagerMessageError("SNS SigningCertURL must use its exact validated form")

    signature_version = _required_sns_string(envelope, "SignatureVersion")
    if signature_version == "1":
        algorithm: hashes.HashAlgorithm = hashes.SHA1()
    elif signature_version == "2":
        algorithm = hashes.SHA256()
    else:
        raise StreetManagerMessageError("unsupported SNS SignatureVersion")

    encoded_signature = _required_sns_string(envelope, "Signature")
    try:
        signature = base64.b64decode(encoded_signature, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise StreetManagerMessageError("SNS Signature is not valid base64") from exc
    if not signature:
        raise StreetManagerMessageError("SNS Signature is empty")

    canonical = _sns_canonical_bytes(envelope)
    certificate = _fetch_signing_certificate(cert_url, session=session)
    public_key = certificate.public_key()
    if not isinstance(public_key, rsa.RSAPublicKey):
        raise StreetManagerMessageError("SNS signing certificate does not contain an RSA key")
    try:
        public_key.verify(signature, canonical, padding.PKCS1v15(), algorithm)
    except InvalidSignature as exc:
        raise StreetManagerMessageError("SNS signature verification failed") from exc
    except Exception as exc:
        raise StreetManagerMessageError("unable to verify SNS signature") from exc


def _parse_sns_envelope(
    envelope: dict[str, Any],
) -> StreetManagerOpenDataMessage:
    message_type = str(envelope.get("Type") or "").strip()
    if message_type not in {"SubscriptionConfirmation", "Notification"}:
        raise StreetManagerMessageError("unsupported SNS message Type")
    topic = validate_topic_arn(envelope.get("TopicArn"))
    message_id = str(envelope.get("MessageId") or "").strip()
    if not message_id:
        raise StreetManagerMessageError("SNS MessageId is required")

    cert_url = envelope.get("SigningCertURL")
    if cert_url:
        validate_signing_cert_url(cert_url)

    if message_type == "SubscriptionConfirmation":
        subscribe_url = validate_subscription_url(envelope.get("SubscribeURL"), topic)
        return StreetManagerOpenDataMessage(
            envelope=envelope,
            message_type=message_type,
            message_id=message_id,
            topic_arn=topic,
            timestamp=_datetime(envelope.get("Timestamp")),
            signature_verified=False,
            subscribe_url=subscribe_url,
        )

    inner = _json_object(envelope.get("Message"), limit=_MAX_MESSAGE_BYTES)
    object_data = inner.get("object_data")
    if not isinstance(object_data, Mapping):
        raise StreetManagerMessageError("Street Manager notification object_data is required")
    object_type = str(inner.get("object_type") or "").strip().upper()
    expected_type = _TOPIC_OBJECT_TYPE[topic]
    if object_type != expected_type:
        raise StreetManagerMessageError("notification object_type does not match TopicArn")
    object_reference = str(inner.get("object_reference") or "").strip()
    if not object_reference:
        raise StreetManagerMessageError("notification object_reference is required")
    try:
        version = int(inner.get("version") or 0)
    except (TypeError, ValueError) as exc:
        raise StreetManagerMessageError("notification version must be an integer") from exc
    if version < 0:
        raise StreetManagerMessageError("notification version cannot be negative")

    return StreetManagerOpenDataMessage(
        envelope=envelope,
        message_type=message_type,
        message_id=message_id,
        topic_arn=topic,
        timestamp=_datetime(envelope.get("Timestamp")),
        signature_verified=False,
        event_type=str(inner.get("event_type") or "").strip().upper().replace("-", "_"),
        event_time=_datetime(inner.get("event_time")),
        object_type=object_type,
        object_reference=object_reference,
        object_data=dict(object_data),
        version=version,
    )


def parse_sns_envelope(
    payload: bytes | str | Mapping[str, Any],
) -> StreetManagerOpenDataMessage:
    """Parse an SNS envelope without granting it verified status.

    Use :func:`verify_and_parse_sns_envelope` for data that will enter the
    trusted snapshot.  This structural parser intentionally has no caller-
    controlled switch that can mark a message as cryptographically verified.
    """

    envelope = _json_object(payload, limit=_MAX_ENVELOPE_BYTES)
    return _parse_sns_envelope(envelope)


def verify_and_parse_sns_envelope(
    payload: bytes | str | Mapping[str, Any],
    *,
    session: requests.Session | None = None,
) -> StreetManagerOpenDataMessage:
    """Atomically verify and parse the same immutable SNS envelope values."""

    envelope = _json_object(payload, limit=_MAX_ENVELOPE_BYTES)
    _verify_sns_envelope(envelope, session=session)
    return replace(_parse_sns_envelope(envelope), signature_verified=True)


_PERMIT_TERMINAL_EVENTS = {
    "WORK_STOP",
    "PERMIT_REFUSED",
    "PERMIT_REVOKED",
    "PERMIT_CANCELLED",
}
_ACTIVITY_TERMINAL_EVENTS = {"ACTIVITY_CANCELLED"}


def is_active_notification(message: StreetManagerOpenDataMessage) -> bool:
    data = message.object_data or {}
    event_type = str(message.event_type or "").upper()
    if message.object_type == "PERMIT":
        status = str(data.get("work_status_ref") or data.get("work_status") or "").strip().casefold()
        permit_status = str(data.get("permit_status") or "").strip().casefold()
        return not (
            event_type in _PERMIT_TERMINAL_EVENTS
            or status in {"completed", "cancelled", "historical", "works completed"}
            or permit_status in {"cancelled", "revoked", "refused", "closed"}
        )
    if message.object_type == "ACTIVITY":
        return event_type not in _ACTIVITY_TERMINAL_EVENTS and not _truthy(data.get("cancelled"))
    return False


def _event_time(date_value: Any, time_value: Any = None) -> datetime | None:
    # Street Manager explicitly distinguishes a null time from midnight.  The
    # Event model can only hold one datetime, so prefer the explicit time when
    # supplied and otherwise retain the published date without inventing a time.
    return _datetime(time_value) or _datetime(date_value)


def open_data_message_to_event(message: StreetManagerOpenDataMessage) -> Event | None:
    """Map an active permit/activity notification to a Radar event."""

    if message.message_type != "Notification" or not is_active_notification(message):
        return None
    data = message.object_data or {}
    if message.object_type == "PERMIT":
        position = street_manager_position(data.get("works_location_coordinates"))
        if not position:
            return None
        street = _clean(data.get("street_name") or data.get("area_name")) or "Local road"
        town = _clean(data.get("town") or data.get("area_name"))
        category = _humanise(data.get("work_category") or data.get("work_category_ref")) or "Roadworks"
        activity = _humanise(data.get("activity_type"))
        management = _humanise(
            data.get("current_traffic_management_type")
            or data.get("traffic_management_type")
            or data.get("traffic_management_type_ref")
        )
        status = _humanise(data.get("work_status") or data.get("work_status_ref")) or "Planned"
        reason = activity or category
        subtitle = " • ".join(part for part in (town, management, status) if part)
        urgency = str(data.get("work_category_ref") or data.get("work_category") or "").casefold()
        severity = "High" if urgency in {"major", "immediate_urgent", "immediate_emergency"} else None
        start = _datetime(data.get("actual_start_date_time")) or _event_time(
            data.get("proposed_start_date"), data.get("proposed_start_time")
        )
        end = _datetime(data.get("actual_end_date_time")) or _event_time(
            data.get("proposed_end_date"), data.get("proposed_end_time")
        )
    elif message.object_type == "ACTIVITY":
        position = street_manager_position(data.get("activity_coordinates"))
        if not position:
            return None
        street = _clean(data.get("street_name") or data.get("activity_location_description")) or "Local road"
        town = _clean(data.get("town") or data.get("area_name"))
        activity = _humanise(data.get("activity_name") or data.get("activity_type")) or "Highway activity"
        category = _humanise(data.get("activity_type")) or "Highway activity"
        management = _humanise(data.get("traffic_management_type"))
        status = "Active"
        reason = activity
        subtitle = " • ".join(part for part in (town, management, status) if part)
        severity = "High" if management in {"Road closure", "Contra flow", "Lane closure"} else None
        start = _event_time(data.get("start_date"), data.get("start_time"))
        end = _event_time(data.get("end_date"), data.get("end_time"))
    else:
        return None

    lat, lon = position
    reference = message.object_reference or "unknown"
    query = " ".join(part for part in (street, town, reason, management) if part)
    return Event(
        id=f"street-manager-open:{reference}",
        type="road",
        name=f"{street} — {reason}"[:220],
        subtitle=subtitle[:350],
        lat=lat,
        lon=lon,
        category=f"Street Manager • {category}",
        status=status,
        severity=severity,
        road=street,
        reason=reason,
        start_time=start,
        end_time=end,
        position_method="static",
        position_label="DfT Street Manager open-data work location (BNG geometry)",
        source="DfT Street Manager Open Data",
        source_id=reference,
        official_url=STREET_MANAGER_GUIDANCE_URL,
        news_query=query,
        social_query=query,
        interesting=severity == "High" or management in {"Road closure", "Contra flow", "Lane closure"},
        last_updated=message.event_time or message.timestamp or datetime.now(timezone.utc),
    )


class StreetManagerSnapshotStore:
    """Durable, replay-resistant current snapshot of verified notifications."""

    def __init__(self, path: str | Path, *, require_signature_verified: bool = True) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.require_signature_verified = require_signature_verified
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
        self._db.execute("PRAGMA busy_timeout=10000")
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS street_manager_snapshot (
                object_reference TEXT PRIMARY KEY,
                object_type TEXT NOT NULL,
                topic_arn TEXT NOT NULL,
                version INTEGER NOT NULL,
                event_time TEXT,
                active INTEGER NOT NULL,
                envelope_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def __enter__(self) -> "StreetManagerSnapshotStore":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    def apply(self, message: StreetManagerOpenDataMessage) -> bool:
        """Apply a verified notification; return ``True`` if state changed."""

        if message.message_type != "Notification" or not message.object_reference:
            raise StreetManagerMessageError("only Notification messages can update the snapshot")
        if self.require_signature_verified and not message.signature_verified:
            raise UnverifiedStreetManagerMessage(
                "SNS cryptographic signature must be verified before updating the snapshot"
            )
        event_time = message.event_time or message.timestamp
        event_time_text = event_time.isoformat() if event_time else ""
        active = 1 if is_active_notification(message) else 0
        serialised = json.dumps(message.envelope, separators=(",", ":"), sort_keys=True)
        now = datetime.now(timezone.utc).isoformat()

        with self._lock:
            existing = self._db.execute(
                "SELECT version, event_time FROM street_manager_snapshot WHERE object_reference=?",
                (message.object_reference,),
            ).fetchone()
            if existing:
                previous_version = int(existing[0])
                previous_time = str(existing[1] or "")
                if message.version < previous_version:
                    return False
                if message.version == previous_version and event_time_text <= previous_time:
                    return False
            self._db.execute(
                """
                INSERT INTO street_manager_snapshot
                    (object_reference, object_type, topic_arn, version, event_time, active, envelope_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(object_reference) DO UPDATE SET
                    object_type=excluded.object_type,
                    topic_arn=excluded.topic_arn,
                    version=excluded.version,
                    event_time=excluded.event_time,
                    active=excluded.active,
                    envelope_json=excluded.envelope_json,
                    updated_at=excluded.updated_at
                """,
                (
                    message.object_reference,
                    message.object_type or "UNKNOWN",
                    message.topic_arn,
                    message.version,
                    event_time_text,
                    active,
                    serialised,
                    now,
                ),
            )
            self._db.commit()
        return True

    def events(self) -> list[Event]:
        """Return all active, mappable objects from the persisted snapshot."""

        with self._lock:
            rows = self._db.execute(
                "SELECT envelope_json FROM street_manager_snapshot WHERE active=1 ORDER BY object_reference"
            ).fetchall()
        events: list[Event] = []
        for (raw,) in rows:
            try:
                message = parse_sns_envelope(raw)
                event = open_data_message_to_event(message)
            except (StreetManagerMessageError, TypeError, ValueError):
                continue
            if event:
                events.append(event)
        return events

    def stats(self) -> dict[str, int]:
        with self._lock:
            total, active = self._db.execute(
                "SELECT COUNT(*), COALESCE(SUM(active), 0) FROM street_manager_snapshot"
            ).fetchone()
        return {"objects": int(total), "active": int(active), "tombstones": int(total) - int(active)}
