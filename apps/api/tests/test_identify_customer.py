import pytest
from rest_framework.test import APIClient

from apps.common.enums import Channel
from apps.customers.models import CustomerChannelIdentity, CustomerIdentityConflict


@pytest.mark.django_db
def test_identify_customer_requires_phone_for_unknown_identity(settings):
    settings.ADAPTER_API_TOKENS = ["test-token"]
    client = APIClient()
    client.credentials(HTTP_X_ADAPTER_TOKEN="test-token")

    response = client.post(
        "/api/identify-customer/",
        {"channel": Channel.TELEGRAM, "external_user_id": "tg-unknown"},
        format="json",
    )

    assert response.status_code == 200
    assert response.data["status"] == "registration_required"
    assert response.data["registration_required"] is True
    assert response.data["next_action"] == "request_phone"


@pytest.mark.django_db
def test_identify_customer_creates_new_customer_with_phone(settings):
    settings.ADAPTER_API_TOKENS = ["test-token"]
    client = APIClient()
    client.credentials(HTTP_X_ADAPTER_TOKEN="test-token")

    response = client.post(
        "/api/identify-customer/",
        {
            "channel": Channel.TELEGRAM,
            "external_user_id": "tg-777",
            "phone": "+7 (912) 345-67-81",
            "display_name": "Новый клиент",
            "username": "new_customer",
            "phone_verification_source": "platform_contact",
        },
        format="json",
    )

    assert response.status_code == 200
    assert response.data["status"] == "identified"
    assert response.data["is_new_customer"] is True
    assert response.data["customer_id"] is not None
    assert response.data["customer_public_code"]
    assert response.data["phone"] == "79123456781"
    assert response.data["display_name"] == "Новый клиент"
    assert response.data["channel"] == Channel.TELEGRAM


@pytest.mark.django_db
def test_identify_customer_creates_second_channel_card_on_phone_conflict(customer, settings):
    settings.ADAPTER_API_TOKENS = ["test-token"]
    client = APIClient()
    client.credentials(HTTP_X_ADAPTER_TOKEN="test-token")

    response = client.post(
        "/api/identify-customer/",
        {
            "channel": Channel.VK,
            "external_user_id": "vk-777",
            "phone": customer.phone,
            "username": "vk_user",
        },
        format="json",
    )

    assert response.status_code == 200
    assert response.data["status"] == "identified"
    assert response.data["is_new_customer"] is True
    assert response.data["customer_id"] != customer.pk
    assert CustomerChannelIdentity.objects.filter(
        customer_id=response.data["customer_id"],
        channel=Channel.VK,
        external_user_id="vk-777",
    ).exists()
    assert CustomerIdentityConflict.objects.filter(
        source_customer_id=response.data["customer_id"],
        matched_customer=customer,
        contact_value=customer.phone,
    ).exists()


@pytest.mark.django_db
def test_identify_customer_without_token_returns_401(settings):
    settings.ADAPTER_API_TOKENS = ["test-token"]
    client = APIClient()
    response = client.post(
        "/api/identify-customer/",
        {"channel": Channel.TELEGRAM, "external_user_id": "tg-unknown"},
        format="json",
    )
    assert response.status_code == 401


@pytest.mark.django_db
def test_identify_customer_without_configured_tokens_returns_403(settings):
    settings.ADAPTER_API_TOKENS = []
    client = APIClient()
    response = client.post(
        "/api/identify-customer/",
        {"channel": Channel.TELEGRAM, "external_user_id": "tg-unknown"},
        format="json",
    )
    assert response.status_code == 403
