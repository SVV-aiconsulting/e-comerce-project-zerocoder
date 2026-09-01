import pytest
from rest_framework.test import APIClient

from apps.common.enums import Channel
from apps.intake.enums import InboundEventStatus, OrderDraftStatus
from apps.intake.models import Clarification
from apps.intake.models import InboundEvent
from apps.intake.services import InboundEventService


@pytest.fixture
def api_client(settings, monkeypatch):
    settings.ADAPTER_API_TOKENS = ["test-token"]
    monkeypatch.setattr(InboundEventService, "publish", lambda _event_id: True)
    client = APIClient()
    client.credentials(HTTP_X_ADAPTER_TOKEN="test-token")
    return client


def payload(**overrides):
    data = {
        "channel": Channel.TELEGRAM,
        "external_event_id": "tg-message-100",
        "external_user_id": "12345",
        "conversation_key": "tg-chat-12345",
        "raw_text": "Хочу две упаковки креветок",
        "raw_payload": {"message_id": 100},
    }
    data.update(overrides)
    return data


@pytest.mark.django_db
def test_intake_endpoint_registers_event_and_resolves_customer(api_client, customer):
    response = api_client.post("/api/intake/events/", payload(), format="json")

    assert response.status_code == 202
    assert response.data["duplicate"] is False
    assert response.data["enqueued"] is True
    event = InboundEvent.objects.get()
    assert event.status == InboundEventStatus.QUEUED
    assert event.customer_id == customer.pk
    assert event.raw_payload == {"message_id": 100}


@pytest.mark.django_db
def test_intake_endpoint_is_idempotent(api_client):
    first = api_client.post("/api/intake/events/", payload(), format="json")
    second = api_client.post("/api/intake/events/", payload(raw_text="дубликат"), format="json")

    assert first.status_code == 202
    assert second.status_code == 200
    assert second.data["duplicate"] is True
    assert second.data["enqueued"] is False
    assert second.data["event_id"] == first.data["event_id"]
    assert InboundEvent.objects.count() == 1
    assert InboundEvent.objects.get().raw_text == "Хочу две упаковки креветок"


@pytest.mark.django_db
def test_intake_endpoint_rejects_customer_context_mismatch(
    api_client,
    customer,
):
    response = api_client.post(
        "/api/intake/events/",
        payload(external_user_id="another-user", customer_id=customer.pk),
        format="json",
    )

    assert response.status_code == 409
    assert response.data["error"]["code"] == "customer_context_mismatch"
    assert InboundEvent.objects.count() == 0


@pytest.mark.django_db
def test_intake_endpoint_requires_adapter_token(settings):
    settings.ADAPTER_API_TOKENS = ["test-token"]

    response = APIClient().post("/api/intake/events/", payload(), format="json")

    assert response.status_code == 401
    assert InboundEvent.objects.count() == 0


@pytest.mark.django_db
def test_intake_endpoint_validates_channel(api_client):
    response = api_client.post(
        "/api/intake/events/",
        payload(channel="carrier_pigeon"),
        format="json",
    )

    assert response.status_code == 400
    assert "channel" in response.data["error"]["details"]


@pytest.mark.django_db
def test_intake_detail_requires_matching_channel_identity(api_client, customer):
    created = api_client.post("/api/intake/events/", payload(), format="json")

    mismatch = api_client.get(
        f"/api/intake/events/{created.data['event_id']}/",
        {"channel": Channel.TELEGRAM, "external_user_id": "another-user"},
    )

    assert mismatch.status_code == 404


@pytest.mark.django_db
def test_intake_detail_returns_unified_clarification(api_client, customer):
    created = api_client.post("/api/intake/events/", payload(), format="json")
    event = InboundEvent.objects.get()
    draft = event.draft = customer.order_drafts.create(
        channel=Channel.TELEGRAM,
        external_user_id="12345",
        conversation_key="tg-chat-12345",
        status=OrderDraftStatus.NEEDS_CLARIFICATION,
        missing_fields=["receiving_type"],
    )
    event.status = InboundEventStatus.PROCESSED
    event.save(update_fields=["draft", "status", "updated_at"])
    clarification = Clarification.objects.create(
        draft=draft,
        field_path="receiving_type",
        question="Доставка или самовывоз?",
        trigger_event=event,
    )

    response = api_client.get(
        f"/api/intake/events/{created.data['event_id']}/",
        {"channel": Channel.TELEGRAM, "external_user_id": "12345"},
    )

    assert response.status_code == 200
    assert response.data["complete"] is True
    assert response.data["draft"]["status"] == OrderDraftStatus.NEEDS_CLARIFICATION
    assert response.data["response"] == {
        "id": f"clarification:{clarification.pk}",
        "type": "clarification",
        "message": "Доставка или самовывоз?",
        "action_url": "",
    }
