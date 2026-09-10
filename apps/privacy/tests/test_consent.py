from datetime import date

import pytest
from django.core.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.common.enums import Channel
from apps.privacy.models import (
    ConsentMethod,
    ConsentStatus,
    IdentityType,
    PersonalDataConsentEvent,
    PersonalDataDocumentType,
    PersonalDataDocumentVersion,
)
from apps.privacy.services import ConsentService


def record_consent(identity="tg-1", channel=Channel.TELEGRAM, status=ConsentStatus.GRANTED):
    identity_type = {
        Channel.TELEGRAM: IdentityType.TELEGRAM_USER_ID,
        Channel.VK: IdentityType.VK_USER_ID,
        Channel.MAX: IdentityType.MAX_USER_ID,
        Channel.WEBSITE: IdentityType.WEBSITE_SESSION_ID,
    }[channel]
    return ConsentService.record(
        channel=channel,
        identity_type=identity_type,
        identity_value=identity,
        source="test_explicit_action",
        status=status,
        expression_method=(
            ConsentMethod.WEBSITE_CHECKBOX
            if channel == Channel.WEBSITE
            else ConsentMethod.BOT_BUTTON
        ),
    )


@pytest.mark.django_db
def test_first_grant_records_versions_urls_and_hashes():
    event = record_consent()

    assert event.status == ConsentStatus.GRANTED
    assert event.consent_document.version == "1.1"
    assert event.consent_document.public_path == "/personal-data-consent/v/1.1/"
    assert len(event.consent_document.content_hash) == 64
    assert event.policy_document.public_path == "/privacy-policy/v/1.1/"
    assert len(event.policy_document.content_hash) == 64
    assert ConsentService.has_current_consent(
        channel=Channel.TELEGRAM, identity_value="tg-1"
    )


@pytest.mark.django_db
def test_stable_platform_identity_skips_repeated_consent():
    record_consent(identity="stable-tg")

    assert ConsentService.has_current_consent(
        channel=Channel.TELEGRAM, identity_value="stable-tg"
    )
    assert not ConsentService.has_current_consent(
        channel=Channel.VK, identity_value="stable-tg"
    )


@pytest.mark.django_db
def test_new_website_session_requires_its_own_consent():
    record_consent(identity="session-a", channel=Channel.WEBSITE)

    assert ConsentService.has_current_consent(
        channel=Channel.WEBSITE, identity_value="session-a"
    )
    assert not ConsentService.has_current_consent(
        channel=Channel.WEBSITE, identity_value="session-b"
    )


@pytest.mark.django_db
def test_decline_blocks_identification(settings):
    settings.ADAPTER_API_TOKENS = ["test-token"]
    record_consent(identity="declined-tg", status=ConsentStatus.DECLINED)
    client = APIClient()
    client.credentials(HTTP_X_ADAPTER_TOKEN="test-token")

    response = client.post(
        "/api/identify-customer/",
        {"channel": Channel.TELEGRAM, "external_user_id": "declined-tg"},
        format="json",
    )

    assert response.status_code == 403
    assert response.data["next_action"] == "request_personal_data_consent"


@pytest.mark.django_db
def test_withdrawal_creates_linked_event_and_revokes_current_consent():
    granted = record_consent(identity="withdraw-tg")
    withdrawn = ConsentService.withdraw(
        channel=Channel.TELEGRAM,
        identity_type=IdentityType.TELEGRAM_USER_ID,
        identity_value="withdraw-tg",
        source="test_withdrawal",
        expression_method=ConsentMethod.BOT_COMMAND,
    )

    assert withdrawn.status == ConsentStatus.WITHDRAWN
    assert withdrawn.previous_event == granted
    assert withdrawn.evidence["processing_restricted"] is True
    assert withdrawn.evidence["erasure_review_required"] is True
    assert not ConsentService.has_current_consent(
        channel=Channel.TELEGRAM, identity_value="withdraw-tg"
    )


@pytest.mark.django_db
def test_new_document_versions_require_fresh_consent_and_preserve_history():
    original = record_consent(identity="versioned-tg")
    PersonalDataDocumentVersion.objects.create(
        document_type=PersonalDataDocumentType.POLICY,
        version="2.0",
        effective_from=date(2026, 9, 7),
        content="Новая версия политики",
        public_path="/privacy-policy/v/2.0/",
    )
    PersonalDataDocumentVersion.objects.create(
        document_type=PersonalDataDocumentType.CONSENT,
        version="2.0",
        effective_from=date(2026, 9, 7),
        content="Новая версия согласия",
        public_path="/personal-data-consent/v/2.0/",
    )

    assert not ConsentService.has_current_consent(
        channel=Channel.TELEGRAM, identity_value="versioned-tg"
    )
    renewed = record_consent(identity="versioned-tg")
    superseded = renewed.previous_event
    assert superseded.status == ConsentStatus.SUPERSEDED
    assert superseded.previous_event == original
    assert renewed.consent_document.version == "2.0"


@pytest.mark.django_db
def test_registry_and_published_documents_are_immutable():
    event = record_consent()
    event.source = "changed"
    with pytest.raises(ValidationError):
        event.save()

    document = event.consent_document
    document.content = "changed"
    with pytest.raises(ValidationError):
        document.save()


@pytest.mark.django_db
def test_public_documents_and_website_withdrawal(client):
    assert client.get("/privacy-policy/").status_code == 200
    assert client.get("/personal-data-consent/").status_code == 200

    session = client.session
    session["website_external_user_id"] = "site-session"
    session.save()
    record_consent(identity="site-session", channel=Channel.WEBSITE)
    response = client.post("/personal-data-withdrawal/")

    assert response.status_code == 200
    assert b"withdrawn" not in response.content.lower()
    assert PersonalDataConsentEvent.objects.filter(
        channel=Channel.WEBSITE,
        identity_value="site-session",
        status=ConsentStatus.WITHDRAWN,
    ).exists()


@pytest.mark.django_db
def test_website_consent_is_an_interactive_session_action(client):
    initial = client.get("/personal-data-consent/actions/")
    assert initial.status_code == 200
    assert initial.json()["granted"] is False

    granted = client.post(
        "/personal-data-consent/actions/",
        data={"accepted": True},
    )
    assert granted.status_code == 200
    assert client.get("/personal-data-consent/actions/").json()["granted"] is True


@pytest.mark.django_db
def test_bot_consent_api_grant_reuse_and_withdrawal(settings):
    settings.ADAPTER_API_TOKENS = ["test-token"]
    settings.PUBLIC_SITE_URL = "https://shop.example.test"
    client = APIClient()
    client.credentials(HTTP_X_ADAPTER_TOKEN="test-token")
    params = {"channel": Channel.VK, "external_user_id": "vk-consent-user"}

    initial = client.get("/api/privacy/consent/", params)
    granted = client.post(
        "/api/privacy/consent/",
        {**params, "action": ConsentStatus.GRANTED, "source": "vk_bot_button"},
        format="json",
    )
    reused = client.get("/api/privacy/consent/", params)
    withdrawn = client.post(
        "/api/privacy/consent/",
        {**params, "action": ConsentStatus.WITHDRAWN, "source": "vk_bot_command"},
        format="json",
    )
    after_withdrawal = client.get("/api/privacy/consent/", params)

    assert initial.data["granted"] is False
    assert initial.data["policy_url"] == "https://shop.example.test/privacy-policy/"
    assert initial.data["consent_url"] == "https://shop.example.test/personal-data-consent/"
    assert granted.status_code == 200
    assert reused.data["granted"] is True
    assert withdrawn.data["status"] == ConsentStatus.WITHDRAWN
    assert after_withdrawal.data["granted"] is False


@pytest.mark.django_db
def test_regrant_after_customer_erasure_clears_orphan_channel_state(settings, monkeypatch):
    from apps.customers.services import CustomerService

    settings.ADAPTER_API_TOKENS = ["test-token"]
    calls = []
    monkeypatch.setattr(
        CustomerService,
        "erase_transient_channel_state",
        lambda **kwargs: calls.append(kwargs),
    )
    client = APIClient()
    client.credentials(HTTP_X_ADAPTER_TOKEN="test-token")
    payload = {"channel": Channel.TELEGRAM, "external_user_id": "tg-erasure"}

    client.post(
        "/api/privacy/consent/",
        {**payload, "action": ConsentStatus.GRANTED, "source": "telegram_bot_button"},
        format="json",
    )
    client.post(
        "/api/privacy/consent/",
        {**payload, "action": ConsentStatus.WITHDRAWN, "source": "customer_card_erased"},
        format="json",
    )
    response = client.post(
        "/api/privacy/consent/",
        {**payload, "action": ConsentStatus.GRANTED, "source": "telegram_bot_button"},
        format="json",
    )

    assert response.status_code == 200
    assert calls == [payload]
