from __future__ import annotations

import sqlite3
from dataclasses import replace

import pytest

from app.billing import (
    MAX_WEBHOOK_BYTES,
    PLUS_FEATURES,
    BillingAuthenticationError,
    BillingConfiguration,
    BillingConfigurationError,
    BillingPaymentPending,
    BillingStateError,
    BillingStore,
    BillingWebhookError,
    StripeBillingService,
    billing_configuration_errors,
    validate_billing_configuration,
)


PEPPER = "billing-test-pepper-that-is-longer-than-thirty-two-bytes"


class Clock:
    def __init__(self, value: int = 1_800_000_000) -> None:
        self.value = value

    def __call__(self) -> float:
        return float(self.value)

    def advance(self, seconds: int) -> None:
        self.value += seconds


class TokenFactory:
    def __init__(self) -> None:
        self.count = 0

    def __call__(self, _size: int) -> str:
        self.count += 1
        return f"token-{self.count:04d}-" + "x" * 40


class FakeStripeGateway:
    def __init__(self) -> None:
        self.checkout_calls = []
        self.portal_calls = []
        self.sessions = {}
        self.subscriptions = {}
        self.webhook_event = None
        self.signature_error = False

    def create_checkout_session(self, params, *, idempotency_key):
        self.checkout_calls.append((params, idempotency_key))
        session_id = f"cs_test_{len(self.checkout_calls)}"
        return {"id": session_id, "url": f"https://checkout.stripe.com/c/pay/{session_id}"}

    def retrieve_checkout_session(self, session_id, *, expand):
        result = self.sessions[session_id]
        result["_expand_seen"] = list(expand)
        return result

    def retrieve_subscription(self, subscription_id):
        return self.subscriptions[subscription_id]

    def create_customer_portal_session(self, customer_id, *, return_url):
        self.portal_calls.append((customer_id, return_url))
        return {"url": "https://billing.stripe.com/p/session/test"}

    def construct_webhook_event(self, payload, signature, secret):
        if self.signature_error:
            raise ValueError("bad signature")
        assert payload == b"{}"
        assert signature == "t=1,v1=signed"
        assert secret == "whsec_test"
        return self.webhook_event


def config(**changes) -> BillingConfiguration:
    base = BillingConfiguration(
        enabled=True,
        public_base_url="https://radar.example",
        stripe_secret_key="sk_test_secret",
        stripe_webhook_secret="whsec_test",
        stripe_plus_price_id="price_plus",
        token_pepper=PEPPER,
        cookie_secure=True,
        checkout_ttl_seconds=600,
        access_ttl_seconds=3600,
        email_login_ttl_seconds=300,
    )
    return replace(base, **changes)


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def store(tmp_path, clock):
    return BillingStore(
        tmp_path / "billing.sqlite",
        token_pepper=PEPPER,
        clock=clock,
        token_factory=TokenFactory(),
    )


@pytest.fixture
def gateway():
    return FakeStripeGateway()


@pytest.fixture
def service(store, gateway):
    return StripeBillingService(store, config(), gateway=gateway)


def subscription(
    *, status="active", price="price_plus", subscription_id="sub_1", customer_id="cus_1"
):
    return {
        "id": subscription_id,
        "customer": customer_id,
        "status": status,
        "items": {"data": [{"price": {"id": price}}]},
    }


def stripe_event(event_id, event_type, obj, *, created=1_800_000_000, livemode=False):
    return {
        "id": event_id,
        "type": event_type,
        "created": created,
        "livemode": livemode,
        "data": {"object": obj},
    }


def prepare_checkout(service, gateway, *, status="active", price="price_plus"):
    started = service.create_checkout()
    session_id = "cs_test_1"
    gateway.sessions[session_id] = {
        "id": session_id,
        "mode": "subscription",
        "status": "complete",
        "client_reference_id": started.client_reference,
        "customer": "cus_1",
        "customer_details": {"email": "Paid@Example.COM"},
        "subscription": "sub_1",
        "line_items": {"data": [{"price": {"id": price}}]},
    }
    gateway.subscriptions["sub_1"] = subscription(status=status, price=price)
    return started, session_id


def test_configuration_is_noop_when_disabled_and_fails_closed_when_enabled():
    validate_billing_configuration(BillingConfiguration(enabled=False))

    unsafe = BillingConfiguration(enabled=True)
    errors = billing_configuration_errors(unsafe)
    assert any("STRIPE_SECRET_KEY" in item for item in errors)
    assert any("bare HTTPS origin" in item for item in errors)
    with pytest.raises(BillingConfigurationError) as caught:
        validate_billing_configuration(unsafe)
    # Setup errors name variables but never echo credential contents.
    assert "STRIPE_SECRET_KEY" in str(caught.value)


@pytest.mark.parametrize(
    "change",
    [
        {"public_base_url": "http://radar.example"},
        {"public_base_url": "https://user:pass@radar.example"},
        {"public_base_url": "https://radar.example/app"},
        {"stripe_secret_key": "pk_test_public"},
        {"stripe_webhook_secret": "secret"},
        {"stripe_plus_price_id": "prod_not_a_price"},
        {"token_pepper": "short"},
        {"cookie_secure": False},
    ],
)
def test_configuration_rejects_unsafe_values(change):
    with pytest.raises(BillingConfigurationError):
        validate_billing_configuration(config(**change))


def test_access_tokens_are_opaque_hmac_hashes_and_never_stored_raw(store):
    store.upsert_customer("cus_1", "paid@example.com")
    store.upsert_subscription(
        subscription_id="sub_1",
        customer_id="cus_1",
        plan="plus",
        price_id="price_plus",
        status="active",
    )
    token = store.issue_access_token("cus_1", ttl_seconds=3600)

    context = store.access_context(token)
    assert context.plus is True
    assert context.features == PLUS_FEATURES
    database_bytes = store.path.read_bytes()
    assert token.encode() not in database_bytes
    with sqlite3.connect(store.path) as conn:
        digest, = conn.execute("SELECT token_hash FROM billing_access_tokens").fetchone()
    assert len(digest) == 64
    assert digest != token


def test_inactive_subscription_retains_login_but_removes_paid_features(store):
    store.upsert_customer("cus_1", "paid@example.com")
    store.upsert_subscription(
        subscription_id="sub_1",
        customer_id="cus_1",
        plan="plus",
        price_id="price_plus",
        status="active",
    )
    token = store.issue_access_token("cus_1")
    store.upsert_subscription(
        subscription_id="sub_1",
        customer_id="cus_1",
        plan="plus",
        price_id="price_plus",
        status="canceled",
    )

    context = store.access_context(token)
    assert context.token_valid is True
    assert context.plus is False
    assert context.features == frozenset()


def test_logout_and_customer_wide_revocation(store):
    store.upsert_customer("cus_1")
    first = store.issue_access_token("cus_1")
    second = store.issue_access_token("cus_1")
    assert store.revoke_access_token(first) is True
    assert store.revoke_access_token(first) is False
    assert store.access_context(first).token_valid is False
    assert store.revoke_customer_tokens("cus_1") == 1
    assert store.access_context(second).token_valid is False


def test_access_token_expiry_is_enforced(store, clock):
    store.upsert_customer("cus_1")
    token = store.issue_access_token("cus_1", ttl_seconds=60)
    clock.advance(61)
    assert store.access_context(token).token_valid is False


def test_checkout_creation_uses_only_server_price_and_binds_state(service, gateway, store):
    started = service.create_checkout("plus")
    params, idempotency_key = gateway.checkout_calls[0]

    assert params["mode"] == "subscription"
    assert params["line_items"] == [{"price": "price_plus", "quantity": 1}]
    assert params["client_reference_id"] == started.client_reference
    assert params["success_url"].endswith("session_id={CHECKOUT_SESSION_ID}")
    assert idempotency_key.endswith(started.client_reference)
    inspected = store.inspect_checkout_state(
        started.state_token,
        stripe_session_id="cs_test_1",
        client_reference=started.client_reference,
    )
    assert inspected.price_id == "price_plus"
    assert started.state_token.encode() not in store.path.read_bytes()


def test_checkout_rejects_unknown_plan(service):
    with pytest.raises(BillingStateError):
        service.create_checkout("attacker-selected-plan")


def test_checkout_confirmation_is_bound_to_state_session_and_reference(service, gateway, store):
    started, session_id = prepare_checkout(service, gateway)
    confirmation = service.confirm_checkout(session_id, started.state_token)

    assert confirmation.plan == "plus"
    assert confirmation.subscription_status == "active"
    assert store.access_context(confirmation.access_token).plus is True
    assert store.active_customer_for_email("paid@example.com") == "cus_1"
    assert "subscription" in gateway.sessions[session_id]["_expand_seen"]
    with pytest.raises(BillingStateError):
        service.confirm_checkout(session_id, started.state_token)


def test_copied_or_wrong_checkout_state_cannot_claim_subscription(service, gateway, store):
    legitimate, session_id = prepare_checkout(service, gateway)
    attacker = store.create_checkout_state(plan="plus", price_id="price_plus")
    store.bind_checkout_session(attacker.state_token, "cs_attacker")

    with pytest.raises(BillingStateError):
        service.confirm_checkout(session_id, attacker.state_token)
    # The legitimate state remains usable after the attack fails.
    assert service.confirm_checkout(session_id, legitimate.state_token).plan == "plus"


def test_checkout_requires_current_active_subscription_and_expected_price(service, gateway):
    started, session_id = prepare_checkout(service, gateway, status="incomplete")
    with pytest.raises(BillingPaymentPending):
        service.confirm_checkout(session_id, started.state_token)

    gateway.subscriptions["sub_1"] = subscription(status="active", price="price_other")
    with pytest.raises(BillingStateError):
        service.confirm_checkout(session_id, started.state_token)


def test_email_login_tokens_are_one_time_short_lived_and_entitlement_only(store, clock):
    assert store.create_email_login_token("nobody@example.com") is None
    store.upsert_customer("cus_1", "Paid@Example.com")
    store.upsert_subscription(
        subscription_id="sub_1",
        customer_id="cus_1",
        plan="plus",
        price_id="price_plus",
        status="trialing",
    )
    token = store.create_email_login_token(" paid@example.COM ", ttl_seconds=60)
    assert token
    assert token.encode() not in store.path.read_bytes()
    assert store.consume_email_login_token(token) == "cus_1"
    assert store.consume_email_login_token(token) is None

    expired = store.create_email_login_token("paid@example.com", ttl_seconds=60)
    clock.advance(61)
    assert store.consume_email_login_token(expired) is None


def test_customer_portal_requires_valid_browser_token(service, store, gateway):
    with pytest.raises(BillingAuthenticationError):
        service.create_portal("not-a-valid-token")

    store.upsert_customer("cus_1")
    token = store.issue_access_token("cus_1")
    url = service.create_portal(token)
    assert url.startswith("https://billing.stripe.com/")
    assert gateway.portal_calls == [("cus_1", "https://radar.example/?billing=portal-return")]


def test_webhook_signature_failure_and_size_limit_fail_before_mutation(service, gateway, store):
    gateway.signature_error = True
    with pytest.raises(BillingWebhookError):
        service.handle_webhook(b"{}", "t=1,v1=signed")
    assert store.webhook_processed("evt_1") is False

    with pytest.raises(BillingWebhookError):
        service.handle_webhook(b"x" * (MAX_WEBHOOK_BYTES + 1), "t=1,v1=signed")


def test_subscription_webhooks_are_idempotent_ordered_and_revoke_access(service, gateway, store):
    active = subscription(status="active")
    gateway.webhook_event = stripe_event(
        "evt_active", "customer.subscription.updated", active, created=200
    )
    result = service.handle_webhook(b"{}", "t=1,v1=signed")
    assert result.processed is True
    token = store.issue_access_token("cus_1")
    assert store.access_context(token).plus is True

    duplicate = service.handle_webhook(b"{}", "t=1,v1=signed")
    assert duplicate.duplicate is True

    # A late older event is recorded but cannot roll current entitlement back.
    old_canceled = subscription(status="canceled")
    gateway.webhook_event = stripe_event(
        "evt_old", "customer.subscription.deleted", old_canceled, created=100
    )
    service.handle_webhook(b"{}", "t=1,v1=signed")
    assert store.access_context(token).plus is True

    gateway.webhook_event = stripe_event(
        "evt_delete", "customer.subscription.deleted", old_canceled, created=300
    )
    service.handle_webhook(b"{}", "t=1,v1=signed")
    context = store.access_context(token)
    assert context.token_valid is True
    assert context.plus is False


def test_checkout_webhook_retrieves_subscription_and_enables_email_recovery(
    service, gateway, store
):
    gateway.subscriptions["sub_1"] = subscription(status="trialing")
    gateway.webhook_event = stripe_event(
        "evt_checkout",
        "checkout.session.completed",
        {
            "id": "cs_1",
            "subscription": "sub_1",
            "customer": "cus_1",
            "customer_details": {"email": "buyer@example.com"},
        },
    )
    result = service.handle_webhook(b"{}", "t=1,v1=signed")
    assert result.subscription_id == "sub_1"
    assert store.active_customer_for_email("buyer@example.com") == "cus_1"


def test_unknown_webhook_is_acknowledged_idempotently(service, gateway):
    gateway.webhook_event = stripe_event("evt_other", "customer.updated", {"id": "cus_1"})
    first = service.handle_webhook(b"{}", "t=1,v1=signed")
    second = service.handle_webhook(b"{}", "t=1,v1=signed")
    assert first.ignored is True and first.processed is True
    assert second.duplicate is True


def test_webhook_rejects_test_live_mode_mismatch(service, gateway):
    gateway.webhook_event = stripe_event(
        "evt_live", "customer.subscription.updated", subscription(), livemode=True
    )
    with pytest.raises(BillingWebhookError):
        service.handle_webhook(b"{}", "t=1,v1=signed")
