from decimal import Decimal

import httpx
import pytest

from apps.carts.services import CartService
from apps.common.enums import Channel, PaymentMethod, ReceivingType
from apps.delivery.exceptions import DeliveryDataIncompleteError
from apps.delivery.models import (
    DeliveryEnvironment,
    DeliveryOperation,
    DeliveryQuote,
    DeliveryQuoteKind,
    DeliveryQuoteStatus,
    Shipment,
)
from apps.delivery.quote_service import (
    DeliveryLine,
    DeliveryPackageService,
    YandexDeliveryQuoteService,
    build_pricing_payload,
)
from apps.delivery.yandex.client import YandexDeliveryClient, YandexDeliveryConfig
from apps.orders.services import OrderService
from apps.intake.enums import ItemMatchStatus, OrderDraftStatus
from apps.intake.fulfillment import DraftOrderConversionService, DraftPricingService
from apps.intake.models import OrderDraft, OrderDraftItem
from apps.intake.services import OrderDraftService


def test_package_requires_catalog_dimensions(product):
    with pytest.raises(DeliveryDataIncompleteError, match="Тестовый товар"):
        DeliveryPackageService.build(
            [DeliveryLine(product=product, quantity=Decimal("1"))]
        )


def test_package_aggregates_weight_and_stacks_height(product):
    product.delivery_weight_grams = 250
    product.delivery_length_cm = 20
    product.delivery_width_cm = 10
    product.delivery_height_cm = 5

    package = DeliveryPackageService.build(
        [DeliveryLine(product=product, quantity=Decimal("2.5"))]
    )

    assert package.weight_gross == 625
    assert package.length_cm == 20
    assert package.width_cm == 10
    assert package.height_cm == 13


def test_pricing_payload_maps_card_on_delivery(product):
    product.delivery_weight_grams = 100
    product.delivery_length_cm = 10
    product.delivery_width_cm = 10
    product.delivery_height_cm = 10
    package = DeliveryPackageService.build(
        [DeliveryLine(product=product, quantity=Decimal("1"))]
    )

    payload = build_pricing_payload(
        station_id="station",
        destination_address=" Москва, Тверская, 1 ",
        package=package,
        items_total=Decimal("123.45"),
        payment_method=PaymentMethod.CARD_ON_DELIVERY,
    )

    assert payload["destination"]["address"] == "Москва, Тверская, 1"
    assert payload["payment_method"] == "card_on_receipt"
    assert payload["client_price"] == 12345
    assert payload["total_assessed_price"] == 12345


def test_pricing_payload_supports_pickup_point(product):
    product.delivery_weight_grams = 100
    product.delivery_length_cm = 10
    product.delivery_width_cm = 10
    product.delivery_height_cm = 10
    package = DeliveryPackageService.build(
        [DeliveryLine(product=product, quantity=Decimal("1"))]
    )

    payload = build_pricing_payload(
        station_id="source-station",
        destination_station_id="pickup-station",
        package=package,
        items_total=Decimal("100.00"),
        payment_method=PaymentMethod.CARD_PREPAYMENT,
        last_mile_policy="self_pickup",
    )

    assert payload["destination"] == {"platform_station_id": "pickup-station"}
    assert payload["tariff"] == "self_pickup"


@pytest.mark.django_db
def test_quote_order_saves_result_and_sync_audit(
    active_cart,
    product,
    customer,
    delivery_rule,
):
    product.delivery_weight_grams = 100
    product.delivery_length_cm = 10
    product.delivery_width_cm = 10
    product.delivery_height_cm = 10
    product.save()
    CartService.set_item_quantity(active_cart, product, Decimal("1"))
    order = OrderService.create_order_from_cart(
        active_cart,
        customer=customer,
        channel=Channel.TELEGRAM,
        receiving_type=ReceivingType.DELIVERY,
        payment_method=PaymentMethod.CARD_PREPAYMENT,
        delivery_address="Москва, Тверская улица, 1",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"pricing_total": "321.50 RUB", "delivery_days": 2},
        )

    api_config = YandexDeliveryConfig(
        enabled=True,
        environment=DeliveryEnvironment.TEST,
        token="test-token",
        station_id="test-station",
        timeout_seconds=5,
    )
    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        quote = YandexDeliveryQuoteService.quote_order(
            order,
            client=YandexDeliveryClient(api_config, http_client=http_client),
        )

    assert quote.status == DeliveryQuoteStatus.SUCCEEDED
    assert quote.amount == Decimal("321.50")
    assert quote.currency == "RUB"
    assert quote.delivery_days == 2
    assert quote.order == order
    event = quote.sync_events.get()
    assert event.operation == DeliveryOperation.PRICING
    assert event.succeeded is True
    assert event.http_status == 200


@pytest.mark.django_db
def test_ai_preview_uses_yandex_quote_and_conversion_keeps_it(
    customer,
    product,
    delivery_rule,
    settings,
    monkeypatch,
):
    settings.YANDEX_DELIVERY_ENABLED = True
    draft = OrderDraft.objects.create(
        customer=customer,
        channel=Channel.TELEGRAM,
        external_user_id="delivery-user",
        conversation_key="delivery-conversation",
        status=OrderDraftStatus.READY_FOR_PREVIEW,
        receiving_type=ReceivingType.DELIVERY,
        delivery_address="Москва, Тверская улица, 1",
        payment_method=PaymentMethod.CARD_PREPAYMENT,
        contact_phone=customer.phone,
    )
    OrderDraftItem.objects.create(
        draft=draft,
        line_number=1,
        raw_product_name=product.name,
        requested_quantity=Decimal("1"),
        requested_unit=product.unit,
        product=product,
        match_status=ItemMatchStatus.MATCHED,
    )

    def fake_quote(_draft):
        return DeliveryQuote.objects.create(
            order_draft=_draft,
            environment=DeliveryEnvironment.TEST,
            kind=DeliveryQuoteKind.PRELIMINARY,
            status=DeliveryQuoteStatus.SUCCEEDED,
            request_fingerprint="a" * 64,
            destination_address=_draft.delivery_address,
            amount=Decimal("123.45"),
            currency="RUB",
            delivery_days=2,
        )

    monkeypatch.setattr(YandexDeliveryQuoteService, "quote_draft", fake_quote)

    preview = DraftPricingService.preview(draft)
    assert preview.delivery_cost == Decimal("123.45")
    assert preview.total_amount == Decimal("223.45")

    confirmed = OrderDraftService.confirm(preview)
    order = DraftOrderConversionService.convert(confirmed)

    assert order.delivery_cost == Decimal("123.45")
    assert order.total_amount == Decimal("223.45")
    shipment = Shipment.objects.get(order=order)
    assert shipment.amount == Decimal("123.45")
    assert shipment.quote.status == DeliveryQuoteStatus.SELECTED
