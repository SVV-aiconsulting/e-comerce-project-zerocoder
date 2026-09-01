import json

from django.core.management import call_command

import pytest

from apps.catalog.models import Product
from apps.common.enums import Channel
from apps.customers.services import CustomerService
from apps.intake.enums import InboundEventStatus, OrderDraftStatus
from apps.intake.models import Clarification, InboundEvent, OrderDraft
from apps.intake.services import InboundEventService
from apps.orders.models import Order


@pytest.fixture
def publish_stub(monkeypatch):
    monkeypatch.setattr(InboundEventService, "publish", lambda _event_id: True)


@pytest.mark.django_db
def test_website_assistant_registers_customer_and_enqueues_event(client, publish_stub):
    response = client.post(
        "/store/assistant/messages/",
        data=json.dumps(
            {
                "message": (
                    "Две упаковки креветок, самовывоз. "
                    "Мой номер +7 999 123-45-67, email anna@example.com"
                ),
                "personal_data_consent": True,
            }
        ),
        content_type="application/json",
    )

    assert response.status_code == 202
    event = InboundEvent.objects.select_related("customer").get()
    assert event.channel == Channel.WEBSITE
    assert event.status == InboundEventStatus.QUEUED
    assert event.customer.phone == "79991234567"
    assert event.customer.email == "anna@example.com"
    assert event.customer.personal_data_consent is True
    assert event.raw_payload["source"] == "website_ai_assistant"
    assert response.json()["event_id"] == str(event.public_id)


@pytest.mark.django_db
def test_website_assistant_event_is_session_bound_and_returns_clarification(
    client, publish_stub
):
    response = client.post(
        "/store/assistant/messages/",
        data=json.dumps({"message": "Хочу рыбу"}),
        content_type="application/json",
    )
    event = InboundEvent.objects.get()
    draft = OrderDraft.objects.create(
        channel=Channel.WEBSITE,
        external_user_id=event.external_user_id,
        conversation_key=event.conversation_key,
        status=OrderDraftStatus.NEEDS_CLARIFICATION,
        missing_fields=["items.0.product"],
    )
    event.draft = draft
    event.status = InboundEventStatus.PROCESSED
    event.save(update_fields=["draft", "status", "updated_at"])
    Clarification.objects.create(
        draft=draft,
        field_path="items.0.product",
        question="Какую именно рыбу вы хотите?",
        trigger_event=event,
    )

    status = client.get(f"/store/assistant/events/{event.public_id}/")
    stranger = client.__class__().get(
        f"/store/assistant/events/{event.public_id}/"
    )

    assert status.status_code == 200
    assert status.json()["response"]["message"] == "Какую именно рыбу вы хотите?"
    assert stranger.status_code == 404


@pytest.mark.django_db
def test_website_assistant_links_order_to_existing_customer_by_email(client, publish_stub):
    customer = CustomerService.create_customer(
        name="Анна из CRM",
        email="anna@example.com",
        first_source=Channel.EMAIL,
    )

    response = client.post(
        "/store/assistant/messages/",
        data=json.dumps(
            {
                "message": "Одна упаковка креветок, самовывоз. Email: ANNA@example.com",
                "personal_data_consent": True,
            }
        ),
        content_type="application/json",
    )

    assert response.status_code == 202
    event = InboundEvent.objects.select_related("customer").get()
    assert event.customer == customer
    assert event.external_user_id.startswith("web:")
    assert not event.customer.channel_identities.filter(channel=Channel.WEBSITE).exists()


@pytest.mark.django_db
def test_website_assistant_requires_consent_before_storing_contact(client, publish_stub):
    response = client.post(
        "/store/assistant/messages/",
        data=json.dumps({"message": "Мой телефон +7 999 123-45-67"}),
        content_type="application/json",
    )

    assert response.status_code == 400
    assert InboundEvent.objects.count() == 0


@pytest.mark.django_db
def test_storefront_page_renders_without_catalog(client):
    response = client.get("/")
    assert response.status_code == 200
    content = response.content.decode()
    assert "Каталог морепродуктов" in content
    assert 'data-assistant-dialog' in content
    assert "Заказ обычным сообщением" not in content


@pytest.mark.django_db
def test_storefront_renders_catalog_from_demo_data(client):
    call_command("load_demo_data")

    response = client.get("/")

    assert response.status_code == 200
    content = response.content.decode()
    assert "Каталог морепродуктов" in content
    assert "Лосось" in content
    assert "Икра лососёвая" in content
    assert 'id="catalog"' in content
    assert 'data-add-to-cart' in content
    assert 'href="/static/website/css/storefront.css"' in content
    assert 'src="/static/website/js/storefront.js"' in content
    assert 'src="/static/website/catalog/hero.jpg"' in content
    salmon = Product.objects.get(public_code="DEMO-SALMON")
    assert f'data-product-id="{salmon.id}"' in content
    assert "website/catalog/DEMO-SALMON.jpg" not in content


@pytest.mark.django_db
def test_storefront_shows_admin_catalog_edits(client):
    call_command("load_demo_data")
    Product.objects.filter(public_code="DEMO-SALMON").update(
        name="Сёмга атлантическая",
        description="Карточка обновлена в Django Admin",
    )

    page = client.get("/")
    api = client.get("/api/products/")

    assert "Сёмга атлантическая" in page.content.decode()
    assert "Карточка обновлена в Django Admin" in page.content.decode()
    salmon = next(item for item in api.json() if item["public_code"] == "DEMO-SALMON")
    assert salmon["name"] == "Сёмга атлантическая"
    assert salmon["description"] == "Карточка обновлена в Django Admin"


@pytest.mark.django_db
def test_website_cart_and_checkout_go_through_backend(client):
    call_command("load_demo_data")
    product = Product.objects.get(public_code="DEMO-SALMON")

    added = client.put(
        f"/store/cart/items/{product.id}/",
        data=json.dumps({"quantity": "0.5"}),
        content_type="application/json",
    )
    cart = client.get("/store/cart/")
    created = client.post(
        "/store/orders/",
        data=json.dumps(
            {
                "name": "Анна Покупатель",
                "phone": "+7 999 123-45-67",
                "email": "anna@example.com",
                "receiving_type": "pickup",
                "payment_method": "cash_on_delivery",
                "personal_data_consent": True,
            }
        ),
        content_type="application/json",
    )

    assert added.status_code == 200
    assert added.json()["items"][0]["product"]["id"] == product.id
    assert cart.json()["items"]
    assert created.status_code == 201
    order = Order.objects.get(public_number=created.json()["public_number"])
    assert order.channel == Channel.WEBSITE
    assert order.items.get().product == product
