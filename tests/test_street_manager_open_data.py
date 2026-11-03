from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID

from app.street_manager_open_data import (
    ACTIVITY_TOPIC_ARN,
    PERMIT_TOPIC_ARN,
    StreetManagerMessageError,
    StreetManagerSnapshotStore,
    UnverifiedStreetManagerMessage,
    open_data_message_to_event,
    parse_sns_envelope,
    verify_and_parse_sns_envelope,
    verify_sns_signature,
)


class _CertificateResponse:
    def __init__(
        self,
        content: bytes,
        url: str,
        *,
        status_code: int = 200,
        history: tuple[object, ...] = (),
        content_length: int | None = None,
    ) -> None:
        self._content = content
        self.url = url
        self.status_code = status_code
        self.history = history
        length = len(content) if content_length is None else content_length
        self.headers = {"Content-Length": str(length)}
        self.closed = False

    def iter_content(self, chunk_size: int):
        for offset in range(0, len(self._content), chunk_size):
            yield self._content[offset : offset + chunk_size]

    def close(self) -> None:
        self.closed = True


class _CertificateSession:
    def __init__(self, response: _CertificateResponse) -> None:
        self.response = response
        self.calls: list[tuple[str, dict[str, object]]] = []

    def get(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


@pytest.fixture(scope="module")
def sns_signing_material():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "SNS verifier test")])
    now = datetime.now(timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .sign(private_key, hashes.SHA256())
    )
    return private_key, certificate.public_bytes(serialization.Encoding.PEM)


def _aws_canonical_bytes(envelope):
    if envelope["Type"] == "Notification":
        fields = ["Message", "MessageId"]
        if "Subject" in envelope:
            fields.append("Subject")
        fields.extend(["Timestamp", "TopicArn", "Type"])
    else:
        fields = [
            "Message",
            "MessageId",
            "SubscribeURL",
            "Timestamp",
            "Token",
            "TopicArn",
            "Type",
        ]
    return "".join(f"{field}\n{envelope[field]}\n" for field in fields).encode("utf-8")


def _sign_envelope(envelope, private_key, *, version="1"):
    signed = dict(envelope)
    signed["SignatureVersion"] = version
    algorithm = hashes.SHA1() if version == "1" else hashes.SHA256()
    signature = private_key.sign(
        _aws_canonical_bytes(signed),
        padding.PKCS1v15(),
        algorithm,
    )
    signed["Signature"] = base64.b64encode(signature).decode("ascii")
    return signed


def _permit_inner(*, event_type="WORK_START", version=1, event_time="2026-10-25T10:00:00Z"):
    return {
        "event_reference": 678,
        "event_type": event_type,
        "object_data": {
            "work_reference_number": "TSR1591199404915",
            "permit_reference_number": "TSR1591199404915-01",
            "promoter_organisation": "Example Utility",
            "highway_authority": "CITY OF WESTMINSTER",
            "works_location_coordinates": "LINESTRING(501251.53 222574.64,501305.92 222506.65)",
            "street_name": "HIGH STREET NORTH",
            "area_name": "LONDON",
            "town": "LONDON",
            "work_category": "Immediate urgent",
            "work_category_ref": "immediate_urgent",
            "traffic_management_type": "Road closure",
            "traffic_management_type_ref": "road_closure",
            "proposed_start_date": "2026-10-25T00:00:00Z",
            "proposed_start_time": None,
            "proposed_end_date": "2026-10-27T00:00:00Z",
            "proposed_end_time": None,
            "actual_start_date_time": "2026-10-25T10:00:00Z",
            "actual_end_date_time": None,
            "work_status": "Works in progress",
            "work_status_ref": "in_progress",
            "activity_type": "Utility repair and maintenance works",
            "permit_status": "granted",
        },
        "event_time": event_time,
        "object_type": "PERMIT",
        "object_reference": "TSR1591199404915-01",
        "version": version,
    }


def _envelope(inner, *, topic=PERMIT_TOPIC_ARN, message_id="message-1"):
    return {
        "Type": "Notification",
        "MessageId": message_id,
        "TopicArn": topic,
        "Message": json.dumps(inner),
        "Timestamp": "2026-10-25T10:00:01Z",
        "SignatureVersion": "1",
        "Signature": "not-verified-by-this-parser",
        "SigningCertURL": (
            "https://sns.eu-west-2.amazonaws.com/"
            "SimpleNotificationService-a86cb10b4e1f29c941702d737128f7b6.pem"
        ),
    }


def test_permit_notification_maps_official_open_data_fields_to_event():
    message = parse_sns_envelope(_envelope(_permit_inner()))
    event = open_data_message_to_event(message)

    assert message.signature_verified is False
    assert event is not None
    assert event.id == "street-manager-open:TSR1591199404915-01"
    assert event.source == "DfT Street Manager Open Data"
    assert event.road == "HIGH STREET NORTH"
    assert event.reason == "Utility repair and maintenance works"
    assert event.status == "Works in progress"
    assert event.severity == "High"
    assert event.start_time.isoformat() == "2026-10-25T10:00:00+00:00"
    assert 49 < event.lat < 61.5
    assert -9.5 < event.lon < 3.5


def test_activity_notification_maps_to_road_event():
    inner = {
        "event_reference": 529771,
        "event_type": "ACTIVITY_CREATED",
        "object_data": {
            "activity_reference_number": "ARN-5990-85775436",
            "street_name": "Fake Street",
            "town": "London",
            "area_name": "MARYLEBONE HIGH STREET",
            "activity_coordinates": "POINT(501251.53 222574.64)",
            "activity_name": "London Marathon",
            "activity_type": "event",
            "start_date": "2026-10-25T00:00:00Z",
            "start_time": "2026-10-25T14:30:00Z",
            "end_date": "2026-10-25T00:00:00Z",
            "end_time": "2026-10-25T18:00:00Z",
            "traffic_management_type": "road_closure",
            "traffic_management_required": "Yes",
            "cancelled": "No",
        },
        "event_time": "2026-10-25T12:00:00Z",
        "object_type": "ACTIVITY",
        "object_reference": "ARN-5990-85775436",
        "version": 1,
    }
    event = open_data_message_to_event(
        parse_sns_envelope(_envelope(inner, topic=ACTIVITY_TOPIC_ARN))
    )

    assert event is not None
    assert event.name == "Fake Street — London marathon"
    assert event.reason == "London marathon"
    assert event.severity == "High"
    assert event.start_time.isoformat() == "2026-10-25T14:30:00+00:00"


def test_topic_and_object_type_must_match():
    with pytest.raises(StreetManagerMessageError, match="object_type"):
        parse_sns_envelope(_envelope(_permit_inner(), topic=ACTIVITY_TOPIC_ARN))

    bad = _envelope(_permit_inner())
    bad["TopicArn"] = "arn:aws:sns:eu-west-2:000000000000:fake"
    with pytest.raises(StreetManagerMessageError, match="TopicArn"):
        parse_sns_envelope(bad)


def test_subscription_confirmation_url_is_strictly_allowlisted():
    query = urlencode({
        "Action": "ConfirmSubscription",
        "TopicArn": PERMIT_TOPIC_ARN,
        "Token": "opaque-token",
    })
    envelope = {
        "Type": "SubscriptionConfirmation",
        "MessageId": "confirmation-1",
        "TopicArn": PERMIT_TOPIC_ARN,
        "Message": "confirm",
        "Timestamp": "2026-10-25T10:00:00Z",
        "SubscribeURL": f"https://sns.eu-west-2.amazonaws.com/?{query}",
    }
    parsed = parse_sns_envelope(envelope)
    assert parsed.subscribe_url == envelope["SubscribeURL"]

    envelope["SubscribeURL"] = f"https://evil.example/?{query}"
    with pytest.raises(StreetManagerMessageError, match="origin"):
        parse_sns_envelope(envelope)


def test_signature_verifier_accepts_v1_notification_and_rejects_tampering(
    sns_signing_material,
):
    private_key, certificate_pem = sns_signing_material
    cert_url = (
        "https://sns.eu-west-2.amazonaws.com/"
        "SimpleNotificationService-test-notification.pem"
    )
    envelope = _envelope(_permit_inner())
    envelope["SigningCertURL"] = cert_url
    signed = _sign_envelope(envelope, private_key, version="1")
    session = _CertificateSession(_CertificateResponse(certificate_pem, cert_url))

    assert verify_sns_signature(signed, session=session) is True
    trusted_message = verify_and_parse_sns_envelope(signed, session=session)
    assert trusted_message.signature_verified is True
    assert trusted_message.object_reference == "TSR1591199404915-01"
    assert len(session.calls) == 1
    requested_url, options = session.calls[0]
    assert requested_url == cert_url
    assert options["allow_redirects"] is False
    assert options["stream"] is True
    assert options["verify"] is True

    tampered = dict(signed)
    tampered["Message"] = tampered["Message"] + " "
    with pytest.raises(StreetManagerMessageError, match="signature verification failed"):
        verify_and_parse_sns_envelope(tampered, session=session)

    # The successfully loaded certificate is reused, including for rejection.
    assert len(session.calls) == 1


def test_signature_verifier_v2_canonicalises_optional_subject_exactly(
    sns_signing_material,
):
    private_key, certificate_pem = sns_signing_material
    cert_url = (
        "https://sns.eu-west-2.amazonaws.com/"
        "SimpleNotificationService-test-subject.pem"
    )
    envelope = _envelope(_permit_inner(), message_id="message-with-subject")
    envelope["SigningCertURL"] = cert_url
    envelope["Subject"] = "Planned works\nCafé"
    signed = _sign_envelope(envelope, private_key, version="2")
    session = _CertificateSession(_CertificateResponse(certificate_pem, cert_url))

    # JSON decoding must restore escapes before canonical UTF-8 signing bytes
    # are built; the verifier must not sign the JSON representation itself.
    encoded_payload = json.dumps(signed, ensure_ascii=True).encode("utf-8")
    assert verify_sns_signature(encoded_payload, session=session) is True

    subject_removed = dict(signed)
    subject_removed.pop("Subject")
    with pytest.raises(StreetManagerMessageError, match="signature verification failed"):
        verify_sns_signature(subject_removed, session=session)

    empty_subject = _envelope(_permit_inner(), message_id="message-empty-subject")
    empty_subject["SigningCertURL"] = cert_url
    empty_subject["Subject"] = ""
    empty_subject_signed = _sign_envelope(empty_subject, private_key, version="2")
    assert verify_sns_signature(empty_subject_signed, session=session) is True


def test_signature_verifier_accepts_v2_subscription_confirmation_canonical_form(
    sns_signing_material,
):
    private_key, certificate_pem = sns_signing_material
    cert_url = (
        "https://sns.eu-west-2.amazonaws.com/"
        "SimpleNotificationService-test-confirmation.pem"
    )
    token = "opaque-token"
    query = urlencode({
        "Action": "ConfirmSubscription",
        "TopicArn": PERMIT_TOPIC_ARN,
        "Token": token,
    })
    envelope = {
        "Type": "SubscriptionConfirmation",
        "MessageId": "confirmation-signed-1",
        "Token": token,
        "TopicArn": PERMIT_TOPIC_ARN,
        "Message": "Please confirm this Street Manager subscription.\n",
        "SubscribeURL": f"https://sns.eu-west-2.amazonaws.com/?{query}",
        "Timestamp": "2026-10-25T10:00:00Z",
        "SigningCertURL": cert_url,
    }
    signed = _sign_envelope(envelope, private_key, version="2")
    session = _CertificateSession(_CertificateResponse(certificate_pem, cert_url))

    assert verify_sns_signature(signed, session=session) is True

    tampered = dict(signed)
    tampered["Token"] = "replacement-token"
    replacement_query = urlencode({
        "Action": "ConfirmSubscription",
        "TopicArn": PERMIT_TOPIC_ARN,
        "Token": tampered["Token"],
    })
    tampered["SubscribeURL"] = f"https://sns.eu-west-2.amazonaws.com/?{replacement_query}"
    with pytest.raises(StreetManagerMessageError, match="signature verification failed"):
        verify_sns_signature(tampered, session=session)


def test_signature_verifier_rejects_redirects_oversize_and_untrusted_paths(
    sns_signing_material,
):
    private_key, certificate_pem = sns_signing_material
    redirect_url = (
        "https://sns.eu-west-2.amazonaws.com/"
        "SimpleNotificationService-test-redirect.pem"
    )
    redirect_envelope = _envelope(_permit_inner(), message_id="redirect")
    redirect_envelope["SigningCertURL"] = redirect_url
    redirect_signed = _sign_envelope(redirect_envelope, private_key)
    redirect_session = _CertificateSession(
        _CertificateResponse(
            certificate_pem,
            "https://evil.example/certificate.pem",
            status_code=302,
            history=(object(),),
        )
    )
    with pytest.raises(StreetManagerMessageError, match="direct success"):
        verify_sns_signature(redirect_signed, session=redirect_session)

    oversize_url = (
        "https://sns.eu-west-2.amazonaws.com/"
        "SimpleNotificationService-test-oversize.pem"
    )
    oversize_envelope = _envelope(_permit_inner(), message_id="oversize")
    oversize_envelope["SigningCertURL"] = oversize_url
    oversize_signed = _sign_envelope(oversize_envelope, private_key)
    oversize_session = _CertificateSession(
        _CertificateResponse(certificate_pem, oversize_url, content_length=64 * 1024 + 1)
    )
    with pytest.raises(StreetManagerMessageError, match="safe size limit"):
        verify_sns_signature(oversize_signed, session=oversize_session)

    invalid_envelope = _envelope(_permit_inner(), message_id="invalid-path")
    invalid_envelope["SigningCertURL"] = (
        "https://sns.eu-west-2.amazonaws.com/nested/"
        "SimpleNotificationService-attacker.pem"
    )
    invalid_signed = _sign_envelope(invalid_envelope, private_key)
    unused_session = _CertificateSession(_CertificateResponse(certificate_pem, redirect_url))
    with pytest.raises(StreetManagerMessageError, match="origin or path"):
        verify_sns_signature(invalid_signed, session=unused_session)
    assert unused_session.calls == []


def test_signature_verifier_rejects_malformed_envelopes_before_certificate_fetch(
    sns_signing_material,
):
    private_key, certificate_pem = sns_signing_material
    cert_url = (
        "https://sns.eu-west-2.amazonaws.com/"
        "SimpleNotificationService-test-prefetch-validation.pem"
    )
    envelope = _envelope(_permit_inner(), message_id="prefetch-validation")
    envelope["SigningCertURL"] = cert_url
    signed = _sign_envelope(envelope, private_key)

    bad_version = dict(signed)
    bad_version["SignatureVersion"] = "3"
    bad_base64 = dict(signed)
    bad_base64["Signature"] = "not base64!"
    missing_timestamp = dict(signed)
    missing_timestamp.pop("Timestamp")
    hostile_topic = dict(signed)
    hostile_topic["TopicArn"] = "arn:aws:sns:eu-west-2:000000000000:fake"
    oversized_mapping = dict(signed)
    oversized_mapping["Subject"] = "x" * 1_000_001

    for candidate, error in (
        (bad_version, "SignatureVersion"),
        (bad_base64, "base64"),
        (missing_timestamp, "Timestamp"),
        (hostile_topic, "TopicArn"),
        (oversized_mapping, "safe size limit"),
    ):
        session = _CertificateSession(_CertificateResponse(certificate_pem, cert_url))
        with pytest.raises(StreetManagerMessageError, match=error):
            verify_sns_signature(candidate, session=session)
        assert session.calls == []


def test_mapping_valued_inner_message_obeys_its_own_size_limit():
    small = _envelope(_permit_inner(), message_id="mapping-inner")
    small["Message"] = _permit_inner()
    assert parse_sns_envelope(small).object_reference == "TSR1591199404915-01"

    oversized_inner = _permit_inner()
    oversized_inner["padding"] = "x" * 800_000
    oversized = _envelope(oversized_inner, message_id="oversized-mapping-inner")
    oversized["Message"] = oversized_inner
    with pytest.raises(StreetManagerMessageError, match="safe size limit"):
        parse_sns_envelope(oversized)


def test_snapshot_requires_verification_and_tombstones_block_stale_replay(
    sns_signing_material,
):
    private_key, certificate_pem = sns_signing_material
    cert_url = (
        "https://sns.eu-west-2.amazonaws.com/"
        "SimpleNotificationService-test-snapshot.pem"
    )
    session = _CertificateSession(_CertificateResponse(certificate_pem, cert_url))

    def trusted_message(envelope):
        envelope["SigningCertURL"] = cert_url
        return verify_and_parse_sns_envelope(
            _sign_envelope(envelope, private_key),
            session=session,
        )

    unverified = parse_sns_envelope(_envelope(_permit_inner()))
    with pytest.raises(TypeError):
        parse_sns_envelope(_envelope(_permit_inner()), signature_verified=True)

    with StreetManagerSnapshotStore(":memory:") as store:
        with pytest.raises(UnverifiedStreetManagerMessage):
            store.apply(unverified)

        first = trusted_message(_envelope(_permit_inner()))
        assert store.apply(first) is True
        assert len(store.events()) == 1

        stopped = trusted_message(
            _envelope(
                _permit_inner(event_type="WORK_STOP", version=2, event_time="2026-10-25T11:00:00Z"),
                message_id="message-2",
            )
        )
        assert store.apply(stopped) is True
        assert store.events() == []
        assert store.stats() == {"objects": 1, "active": 0, "tombstones": 1}

        # Replaying the older start cannot resurrect the stopped work.
        assert store.apply(first) is False
        assert store.events() == []
    assert len(session.calls) == 1
