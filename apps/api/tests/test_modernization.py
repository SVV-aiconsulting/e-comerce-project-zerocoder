"""Regression cases from the final review, using a real PostgreSQL test DB."""
import json
import re
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from django.core import mail
from django.test import Client
from django.utils import timezone
from apps.carts.checkout import CheckoutService
from apps.carts.models import Cart, CheckoutPreview
from apps.carts.services import CartService
from apps.common.exceptions import PreviewStaleError, IntakeRateLimited
from apps.customers.models import Customer, WebAccount, OrderAccessGrant, HistoryLinkRequest, LoginCode
from apps.customers.services import CustomerService
from apps.intake.cart_bridge import UnifiedCartBridge
from apps.intake.draft_application import DraftExtractionApplier
from apps.intake.fulfillment import DraftPricingService, DraftOrderConversionService
from apps.intake.services import OrderDraftService, InboundEventService
from apps.intake.tests.test_fulfillment import extraction
from apps.intake.tasks import dispatch_pending_events
from apps.orders.access import OrderAccessService
from apps.orders.models import Order
from apps.orders.services import OrderService
from apps.payments.services import PaymentService
from apps.payments.models import Payment, PaymentState, RefundState

pytestmark = pytest.mark.django_db


def prepared(customer):
    draft, _ = OrderDraftService.get_or_create_active(customer=customer, channel="telegram",
        external_user_id="12345", conversation_key="modernization")
    return DraftPricingService.preview(DraftExtractionApplier.apply(draft, extraction()))


def test_catalog_change_requires_new_confirmation(customer, product):
    draft = prepared(customer)
    assert draft.total_amount == Decimal("200")
    product.base_price = Decimal("150")
    product.save(update_fields=["base_price"])
    with pytest.raises(PreviewStaleError):
        DraftOrderConversionService.convert(OrderDraftService.confirm(draft))
    assert customer.orders.count() == 0


def test_manual_cart_change_invalidates_ai_preview(customer, product):
    draft = prepared(customer)
    revision = draft.revision
    CartService.set_item_quantity(draft.cart, product, Decimal(3))
    updated = UnifiedCartBridge.cart_to_draft(draft)
    assert updated.revision > revision
    assert updated.previewed_revision is None
    assert updated.items.get().requested_quantity == 3


def test_noop_cart_update_preserves_preview(customer, product):
    draft = prepared(customer)
    CartService.set_item_quantity(draft.cart, product, Decimal(2))
    updated = UnifiedCartBridge.cart_to_draft(draft)
    assert updated.previewed_revision == draft.previewed_revision
    order = DraftOrderConversionService.convert(OrderDraftService.confirm(updated))
    assert order.total_amount == 200


def test_expired_preview_cannot_create_order(customer, product):
    draft = prepared(customer)
    CheckoutPreview.objects.filter(pk=draft.checkout_preview_id).update(expires_at=timezone.now()-timedelta(seconds=1))
    with pytest.raises(PreviewStaleError):
        DraftOrderConversionService.convert(OrderDraftService.confirm(draft))


def test_changed_checkout_field_invalidates_snapshot(customer, product):
    draft = prepared(customer)
    cart = draft.cart
    cart.customer_comment = "Позвонить после шести"
    cart.save(update_fields=["customer_comment"])
    with pytest.raises(PreviewStaleError):
        DraftOrderConversionService.convert(OrderDraftService.confirm(draft))


def test_guest_contact_does_not_authorize_history(customer, product, active_cart):
    CartService.set_item_quantity(active_cart, product, Decimal(1))
    order = OrderService.create_order_from_cart(active_cart, customer=customer, channel="telegram",
        receiving_type="pickup", payment_method="cash_on_delivery")
    matched = CustomerService.resolve_website_customer(name="Other", phone=customer.phone, email="other@example.com")
    assert matched.customer.pk == customer.pk
    assert not OrderAccessService.visible(channel="website", external_user_id="web:new").filter(pk=order.pk).exists()
    customer.refresh_from_db()
    assert customer.email == ""


def test_guest_sees_only_session_orders(customer, product):
    cart = CartService.get_or_create_active_cart(channel="website", external_user_id="web:mine", customer=customer)
    CartService.set_item_quantity(cart, product, Decimal(1))
    order = OrderService.create_order_from_cart(cart, customer=customer, channel="website", receiving_type="pickup", payment_method="cash_on_delivery")
    assert OrderAccessService.visible(channel="website", external_user_id="web:mine").filter(pk=order.pk).exists()
    assert not OrderAccessService.visible(channel="website", external_user_id="web:other").exists()


def request_code(client, email="buyer@example.com"):
    response = client.post("/store/auth/code/request/", data=json.dumps({"identifier":email}), content_type="application/json")
    assert response.status_code == 202
    code = re.search(r"\b[0-9]{6}\b", mail.outbox[-1].body).group()
    return response.json()["challenge_id"], code


def verify(client, challenge, code):
    return client.post("/store/auth/code/verify/", data=json.dumps({"challenge_id":challenge,"code":code}),content_type="application/json")


def test_code_registration_and_reuse_rejected(client, settings):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    challenge, code = request_code(client)
    stored = LoginCode.objects.get(public_id=challenge)
    assert stored.code_hash != code
    assert verify(client, challenge, code).status_code == 200
    account = WebAccount.objects.get()
    assert not account.user.is_staff and not account.user.has_usable_password()
    assert account.customer_id is not None
    assert Customer.objects.filter(
        pk=account.customer_id,
        email="buyer@example.com",
        email_verified_at__isnull=False,
    ).exists()
    assert not Order.objects.exists()
    assert client.get("/store/account/").json()["authenticated"]
    assert verify(client, challenge, code).status_code == 400


def test_code_bound_to_browser_and_attempt_limit(client, settings):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    challenge, code = request_code(client)
    assert verify(Client(), challenge, code).status_code == 400
    for _ in range(5):
        wrong = "000000" if code != "000000" else "111111"
        assert verify(client, challenge, wrong).status_code == 400
    assert verify(client, challenge, code).status_code == 400


def test_expired_login_code(client, settings):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    challenge, code = request_code(client)
    LoginCode.objects.filter(public_id=challenge).update(expires_at=timezone.now()-timedelta(seconds=1))
    assert verify(client, challenge, code).status_code == 400


def test_send_throttled_and_unknown_phone_generic(client, settings):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    request_code(client)
    r = client.post("/store/auth/code/request/", data=json.dumps({"identifier":"buyer@example.com"}),content_type="application/json")
    assert r.status_code == 429 and r["Retry-After"] == "60"
    r = client.post("/store/auth/code/request/", data=json.dumps({"identifier":"79991234500"}),content_type="application/json")
    assert r.status_code == 202 and len(mail.outbox) == 1


def test_verified_email_links_snapshot_not_entire_customer(client, settings, customer, product):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    orders = []
    for n,email in enumerate(["buyer@example.com", "different@example.com"]):
        cart = CartService.get_or_create_active_cart(channel="telegram", external_user_id=f"old{n}", customer=customer)
        CartService.set_item_quantity(cart, product, Decimal(1))
        orders.append(OrderService.create_order_from_cart(cart, customer=customer, channel="telegram", receiving_type="pickup", payment_method="cash_on_delivery", customer_email_snapshot=email))
    challenge, code = request_code(client)
    assert verify(client, challenge, code).status_code == 200
    assert OrderAccessGrant.objects.filter(order=orders[0]).exists()
    assert not OrderAccessGrant.objects.filter(order=orders[1]).exists()
    client.post("/store/auth/logout/", data="{}",content_type="application/json")
    assert not client.get("/store/account/orders/").json()["orders"]


def test_stale_processing_is_republished():
    event = InboundEventService.register(channel="telegram", external_user_id="stale", external_event_id="stale", conversation_key="stale", raw_text="hello").event
    event.status = "processing"
    event.started_at = timezone.now()-timedelta(hours=2)
    event.save(update_fields=["status","started_at"])
    with patch.object(InboundEventService, "publish", return_value=True) as publish:
        assert dispatch_pending_events.run()["selected"] == 1
        publish.assert_called_once_with(event.pk)


def test_paid_state_never_regresses(customer, product, active_cart):
    CartService.set_item_quantity(active_cart, product, Decimal(1))
    order = OrderService.create_order_from_cart(active_cart, customer=customer, channel="telegram", receiving_type="pickup", payment_method="cash_on_delivery")
    order.payment_status = "paid"
    order.save(update_fields=["payment_status"])
    PaymentService._sync_order_payment_status(order, "pending")
    order.refresh_from_db()
    assert order.payment_status == "paid"


def test_limiter_rejects_second_pending_but_allows_idempotent_retry(settings):
    settings.INTAKE_ADMISSION_ENABLED = True
    data = dict(channel="website", external_user_id="web:limit", external_event_id="1", conversation_key="limit", raw_text="Hello")
    first = InboundEventService.register(**data)
    assert InboundEventService.register(**data).event.pk == first.event.pk
    with pytest.raises(IntakeRateLimited):
        InboundEventService.register(**{**data,"external_event_id":"2"})


def test_quantity_does_not_use_phone_or_address():
    from apps.assistant.tools import AssistantToolExecutor
    assert not AssistantToolExecutor._message_has_quantity("Мой телефон +7 999 123-45-67")
    assert not AssistantToolExecutor._message_has_quantity("улица Ленина 15")
    assert AssistantToolExecutor._message_has_quantity("Добавьте 2 кг лосося")
    assert not AssistantToolExecutor._message_has_quantity("Добавьте 3 кг лосося", 2)
    assert AssistantToolExecutor._message_has_quantity("Добавьте два кг лосося", 2)


def test_channel_identity_does_not_open_other_channel_orders(customer, product):
    from apps.customers.models import CustomerChannelIdentity

    CustomerChannelIdentity.objects.create(
        customer=customer,
        channel="telegram",
        external_user_id="tg-owner",
    )
    cart = CartService.get_or_create_active_cart(
        channel="website", external_user_id="web:owner", customer=customer
    )
    CartService.set_item_quantity(cart, product, Decimal(1))
    order = OrderService.create_order_from_cart(
        cart,
        customer=customer,
        channel="website",
        receiving_type="pickup",
        payment_method="cash_on_delivery",
    )
    assert not OrderAccessService.visible(
        channel="telegram", external_user_id="tg-owner"
    ).filter(pk=order.pk).exists()


def test_verified_email_change_requires_code(client, settings):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    challenge, code = request_code(client, "old@example.com")
    assert verify(client, challenge, code).status_code == 200
    response = client.post(
        "/store/auth/code/request/",
        data=json.dumps({"identifier": "new@example.com", "purpose": "email_change"}),
        content_type="application/json",
    )
    change_challenge = response.json()["challenge_id"]
    change_code = re.search(r"\b[0-9]{6}\b", mail.outbox[-1].body).group()
    assert WebAccount.objects.get().email == "old@example.com"
    assert verify(client, change_challenge, change_code).status_code == 200
    assert WebAccount.objects.get().email == "new@example.com"


def test_paid_order_cannot_be_cancelled(customer, product, active_cart):
    CartService.set_item_quantity(active_cart, product, Decimal(1))
    order = OrderService.create_order_from_cart(
        active_cart,
        customer=customer,
        channel="telegram",
        receiving_type="pickup",
        payment_method="cash_on_delivery",
    )
    order.payment_status = "paid"
    order.save(update_fields=["payment_status"])
    with pytest.raises(ValueError, match="Оплаченный заказ"):
        OrderService.change_status(order, "cancelled")


def test_partial_then_remaining_full_refund(customer, product, active_cart):
    CartService.set_item_quantity(active_cart, product, Decimal(1))
    order = OrderService.create_order_from_cart(
        active_cart,
        customer=customer,
        channel="telegram",
        receiving_type="pickup",
        payment_method="card_prepayment",
    )
    receipt = {
        "customer": {"email": "buyer@example.com"},
        "items": [{
            "description": "Позиция",
            "quantity": "2",
            "amount": {"value": "50.00", "currency": "RUB"},
            "measure": "piece",
        }],
    }
    payment = Payment.objects.create(
        order=order,
        amount=Decimal("100.00"),
        description="Оплата",
        external_id="pay-refund",
        state=PaymentState.SUCCEEDED,
        receipt_data=receipt,
    )

    class RefundClient:
        calls = 0

        def create_refund(self, payload, *, idempotence_key):
            self.calls += 1
            return {
                "id": f"refund-{self.calls}",
                "payment_id": "pay-refund",
                "status": "succeeded",
                "amount": payload["amount"],
            }

        def get_refund(self, external_id):
            raise AssertionError("completed refund must not be sent again")

    client_api = RefundClient()
    partial = PaymentService.create_refund(
        payment,
        lines={"0": "1"},
        operation_id="7402f130-f624-4f47-bfa6-f198ef86de6a",
        client=client_api,
    )
    remaining = PaymentService.create_refund(payment, client=client_api)
    replay = PaymentService.create_refund(payment, client=client_api)
    assert partial.state == remaining.state == RefundState.SUCCEEDED
    assert partial.amount == remaining.amount == Decimal("50.00")
    assert replay.pk == remaining.pk
    assert client_api.calls == 2
