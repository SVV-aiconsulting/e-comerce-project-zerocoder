from decimal import Decimal
import json

import httpx
import pytest

from apps.carts.services import CartService
from apps.common.enums import Channel, PaymentMethod, ReceivingType
from apps.delivery.models import (
    DeliveryEnvironment,
    DeliveryOperation,
    DeliveryQuoteKind,
    DeliveryQuoteStatus,
)
from apps.delivery.offer_service import YandexDeliveryOfferService, build_offer_payload
from apps.intake.enums import ItemMatchStatus, OrderDraftStatus
from apps.intake.models import OrderDraft, OrderDraftItem
from apps.delivery.yandex.client import YandexDeliveryClient, YandexDeliveryConfig
from apps.orders.services import OrderService


def create_delivery_order(active_cart, product, customer):
    product.delivery_weight_grams = 300
    product.delivery_length_cm = 20
    product.delivery_width_cm = 15
    product.delivery_height_cm = 10
    product.save()
    CartService.set_item_quantity(active_cart, product, Decimal("2"))
    return OrderService.create_order_from_cart(
        active_cart,
        customer=customer,
        channel=Channel.TELEGRAM,
        receiving_type=ReceivingType.DELIVERY,
        payment_method=PaymentMethod.CARD_PREPAYMENT,
        delivery_address="Москва, Тверская улица, 1",
        delivery_cost_override=Decimal("123.45"),
    )


@pytest.mark.django_db
def test_build_offer_payload_uses_order_snapshots(
    active_cart,
    product,
    customer,
    delivery_rule,
    settings,
):
    settings.YANDEX_DELIVERY_VAT_CODE = -1
    settings.YANDEX_DELIVERY_MERCHANT_INN = ""
    order = create_delivery_order(active_cart, product, customer)

    payload, package = build_offer_payload(order, "test-station")

    assert payload["info"]["operator_request_id"] == order.public_number
    assert payload["source"]["platform_station"]["platform_id"] == "test-station"
    assert payload["destination"]["custom_location"]["details"]["full_address"] == (
        order.delivery_address
    )
    assert payload["items"][0]["count"] == 2
    assert payload["items"][0]["billing_details"]["unit_price"] == 10000
    assert payload["items"][0]["place_barcode"] == f"{order.public_number}-1"
    assert payload["places"][0]["barcode"] == f"{order.public_number}-1"
    assert payload["recipient_info"]["phone"] == f"+{customer.phone}"
    assert package["weight_gross"] == 600


@pytest.mark.django_db
def test_create_offers_saves_external_id_price_and_expiry(
    active_cart,
    product,
    customer,
    delivery_rule,
    settings,
):
    settings.YANDEX_DELIVERY_VAT_CODE = -1
    settings.YANDEX_DELIVERY_MERCHANT_INN = ""
    order = create_delivery_order(active_cart, product, customer)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/offers/create")
        return httpx.Response(
            200,
            json={
                "offers": [
                    {
                        "offer_id": "offer-1",
                        "expires_at": "2030-01-01T10:00:00Z",
                        "offer_details": {
                            "delivery_interval": {
                                "min": "2030-01-02T10:00:00Z",
                                "max": "2030-01-02T14:00:00Z",
                                "policy": "time_interval",
                            },
                            "pickup_interval": {},
                            "pricing_total": "456.70 RUB",
                        },
                    }
                ]
            },
        )

    api_config = YandexDeliveryConfig(
        enabled=True,
        environment=DeliveryEnvironment.TEST,
        token="test-token",
        station_id="test-station",
        timeout_seconds=5,
    )
    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        quotes = YandexDeliveryOfferService.create_for_order(
            order,
            client=YandexDeliveryClient(api_config, http_client=http_client),
        )

    assert len(quotes) == 1
    quote = quotes[0]
    assert quote.kind == DeliveryQuoteKind.OFFER
    assert quote.status == DeliveryQuoteStatus.SUCCEEDED
    assert quote.external_offer_id == "offer-1"
    assert quote.amount == Decimal("456.70")
    assert quote.expires_at is not None
    assert quote.sync_events.get().operation == DeliveryOperation.OFFERS_CREATE


@pytest.mark.django_db
def test_create_draft_offer_uses_real_offer_payload_and_earliest_interval(
    customer, product, delivery_rule, settings
):
    settings.YANDEX_DELIVERY_VAT_CODE = -1
    product.delivery_weight_grams = 300
    product.delivery_length_cm = 20
    product.delivery_width_cm = 15
    product.delivery_height_cm = 10
    product.save()
    draft = OrderDraft.objects.create(
        customer=customer,
        channel=Channel.TELEGRAM,
        external_user_id="draft-offer-user",
        conversation_key="draft-offer-conversation",
        status=OrderDraftStatus.READY_FOR_PREVIEW,
        receiving_type="delivery",
        delivery_address="Москва, Тверская улица, 1",
        payment_method=PaymentMethod.CARD_PREPAYMENT,
        contact_phone=customer.phone,
    )
    OrderDraftItem.objects.create(
        draft=draft,
        line_number=1,
        raw_product_name=product.name,
        requested_quantity=Decimal("2"),
        requested_unit=product.unit,
        product=product,
        match_status=ItemMatchStatus.MATCHED,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        barcode = body["places"][0]["barcode"]
        assert body["items"][0]["place_barcode"] == barcode
        assert body["items"][0]["count"] == 2
        return httpx.Response(
            200,
            json={
                "offers": [
                    {
                        "offer_id": "later",
                        "expires_at": "2030-01-01T10:00:00Z",
                        "offer_details": {
                            "pricing_total": "500.00 RUB",
                            "delivery_interval": {
                                "min": "2030-01-03T10:00:00Z",
                                "max": "2030-01-03T14:00:00Z",
                                "policy": "time_interval",
                            },
                        },
                    },
                    {
                        "offer_id": "earlier",
                        "expires_at": "2030-01-01T10:00:00Z",
                        "offer_details": {
                            "pricing_total": "450.00 RUB",
                            "delivery_interval": {
                                "min": "2030-01-02T10:00:00Z",
                                "max": "2030-01-02T14:00:00Z",
                                "policy": "time_interval",
                            },
                        },
                    },
                ]
            },
        )

    api_config = YandexDeliveryConfig(
        enabled=True,
        environment=DeliveryEnvironment.TEST,
        token="test-token",
        station_id="test-station",
        timeout_seconds=5,
    )
    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        quote = YandexDeliveryOfferService.create_for_draft(
            draft,
            client=YandexDeliveryClient(api_config, http_client=http_client),
        )

    assert quote.status == DeliveryQuoteStatus.SUCCEEDED
    assert quote.external_offer_id == "earlier"
    assert quote.amount == Decimal("450.00")
    assert quote.kind == DeliveryQuoteKind.OFFER
