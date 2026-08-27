from decimal import Decimal

import httpx
import pytest

from apps.carts.services import CartService
from apps.common.enums import Channel, PaymentMethod, PaymentStatus, ReceivingType
from apps.delivery.exceptions import DeliveryDataIncompleteError
from apps.delivery.models import (
    DeliveryEnvironment,
    DeliveryOperation,
    DeliveryQuote,
    DeliveryQuoteKind,
    DeliveryQuoteStatus,
    ShipmentStatus,
)
from apps.delivery.shipment_service import (
    YandexShipmentService,
    normalize_yandex_status,
)
from apps.delivery.yandex.client import YandexDeliveryClient, YandexDeliveryConfig
from apps.orders.services import OrderService


def test_normalize_yandex_status_is_conservative():
    assert normalize_yandex_status("CREATED") == ShipmentStatus.CONFIRMED
    assert (
        normalize_yandex_status("DELIVERY_TRANSPORTATION_RECIPIENT")
        == ShipmentStatus.IN_TRANSIT
    )
    assert normalize_yandex_status("DELIVERED") == ShipmentStatus.DELIVERED
    assert normalize_yandex_status("CANCELLED") == ShipmentStatus.CANCELLED


@pytest.mark.django_db
def test_confirm_sync_and_cancel_shipment(
    active_cart,
    product,
    customer,
    delivery_rule,
):
    CartService.set_item_quantity(active_cart, product, Decimal("1"))
    order = OrderService.create_order_from_cart(
        active_cart,
        customer=customer,
        channel=Channel.TELEGRAM,
        receiving_type=ReceivingType.DELIVERY,
        payment_method=PaymentMethod.CARD_PREPAYMENT,
        delivery_address="Москва, Тверская улица, 1",
    )
    quote = DeliveryQuote.objects.create(
        order=order,
        environment=DeliveryEnvironment.TEST,
        kind=DeliveryQuoteKind.OFFER,
        status=DeliveryQuoteStatus.SUCCEEDED,
        request_fingerprint="b" * 64,
        external_offer_id="offer-1",
        destination_address=order.delivery_address,
        amount=Decimal("123.45"),
    )
    with pytest.raises(DeliveryDataIncompleteError, match="webhook оплаты"):
        YandexShipmentService.confirm_quote(quote)

    order.payment_status = PaymentStatus.PAID
    order.save(update_fields=["payment_status", "updated_at"])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/offers/confirm"):
            return httpx.Response(200, json={"request_id": "request-1"})
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "request_id": "request-1",
                    "state": {"status": "DELIVERY_TRANSPORTATION_RECIPIENT"},
                    "sharing_url": "https://dostavka.yandex.ru/route/test",
                },
            )
        return httpx.Response(
            200,
            json={"status": "SUCCESS", "description": "Заказ отменён"},
        )

    api_config = YandexDeliveryConfig(
        enabled=True,
        environment=DeliveryEnvironment.TEST,
        token="test-token",
        station_id="test-station",
        timeout_seconds=5,
    )
    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = YandexDeliveryClient(api_config, http_client=http_client)
        shipment = YandexShipmentService.confirm_quote(quote, client=client)
        assert shipment.external_request_id == "request-1"
        assert shipment.status == ShipmentStatus.CONFIRMED

        shipment = YandexShipmentService.sync(shipment, client=client)
        assert shipment.status == ShipmentStatus.IN_TRANSIT
        assert shipment.tracking_url.endswith("/test")

        shipment = YandexShipmentService.cancel(shipment, client=client)
        assert shipment.status == ShipmentStatus.CANCELLED

    assert list(
        shipment.sync_events.values_list("operation", flat=True).order_by("created_at")
    ) == [
        DeliveryOperation.OFFER_CONFIRM,
        DeliveryOperation.INFO,
        DeliveryOperation.CANCEL,
    ]
