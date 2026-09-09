"""One checked preview contract for manual and conversational checkout."""
from datetime import timedelta
from decimal import Decimal
from django.db import transaction, connection
from django.core.exceptions import ValidationError
from django.utils import timezone
from apps.carts.models import Cart, CheckoutPreview
from apps.carts.services import CartService
from apps.common.exceptions import PreviewStaleError
from apps.orders.pricing import PricingService

class CheckoutService:
    @staticmethod
    def signature(cart):
        return [{"product": i.product_id, "quantity": str(i.quantity),
                 "price": str(i.product.base_price), "name": i.product.name,
                 "unit": i.product.unit}
                for i in cart.items.select_related("product").order_by("product_id")]

    @staticmethod
    def terms(cart):
        return {f: str(getattr(cart, f) or "") for f in Cart.CHECKOUT_FIELDS}

    @staticmethod
    def totals(cart, customer, quote=None):
        rows = list(CartService.get_contents(cart))
        if customer is None:
            from apps.orders.pricing import OrderTotals
            amount = PricingService.calculate_items_total(rows)
            result = OrderTotals(amount, Decimal(0), Decimal(0), amount)
        else:
            customer.refresh_from_db()
            result = PricingService.calculate_order_totals(customer=customer,
                cart_items=rows, receiving_type=cart.receiving_type)
        if quote is not None and cart.receiving_type == "delivery":
            result.delivery_cost = Decimal(0) if result.free_delivery else quote.amount
            result.total_amount = PricingService.calculate_total(result.items_total,
                result.discount_amount, result.delivery_cost)
        return {f: str(getattr(result, f).quantize(Decimal("0.01")))
                for f in ("items_total", "discount_amount", "delivery_cost", "total_amount")}

    @classmethod
    @transaction.atomic
    def record(cls, *, cart, customer, result):
        cart = Cart.objects.select_for_update().get(pk=cart.pk)
        if customer is not None:
            customer = type(customer).objects.select_for_update().get(pk=customer.pk)
        if getattr(result, "cart_revision", cart.revision) != cart.revision:
            raise PreviewStaleError()
        current_signature = cls.signature(cart)
        result_signature = getattr(result, "cart_signature", None)
        if result_signature is not None and result_signature != current_signature:
            raise PreviewStaleError()
        quote = result.quote
        actual = cls.totals(cart, customer, quote)
        shown = {k: str(getattr(result.totals, k).quantize(Decimal("0.01"))) for k in actual}
        if actual != shown:
            raise PreviewStaleError()
        expires = timezone.now() + timedelta(minutes=15)
        if quote is not None:
            from django.conf import settings
            expires = min(expires, quote.created_at + timedelta(seconds=settings.YANDEX_DELIVERY_QUOTE_TTL_SECONDS))
            if quote.expires_at:
                expires = min(expires, quote.expires_at)
        return CheckoutPreview.objects.create(cart=cart, cart_revision=cart.revision,
            customer=customer, quote=quote, expires_at=expires,
            snapshot={"items": current_signature, "terms": cls.terms(cart), "totals": shown})

    @classmethod
    @transaction.atomic
    def create_order(cls, *, preview_id, channel, external_user_id, customer, **kwargs):
        from apps.orders.services import OrderService
        from apps.delivery.checkout import CheckoutDeliveryService
        from apps.delivery.models import DeliveryQuote, DeliveryQuoteStatus
        try:
            preview = CheckoutPreview.objects.select_for_update().get(public_id=preview_id,
                cart__channel=channel, cart__external_user_id=external_user_id)
        except (CheckoutPreview.DoesNotExist, ValueError, TypeError, ValidationError):
            raise PreviewStaleError() from None
        cart = Cart.objects.select_for_update().get(pk=preview.cart_id)
        if preview.order_id:
            return preview.order
        if preview.customer_id != customer.pk or cart.status != "active" or preview.expires_at <= timezone.now():
            raise PreviewStaleError()
        customer = type(customer).objects.select_for_update().get(pk=customer.pk)
        quote = None
        if preview.quote_id:
            quote = DeliveryQuote.objects.select_for_update().get(pk=preview.quote_id)
            if (
                quote.cart_id != cart.pk
                or quote.status != DeliveryQuoteStatus.SUCCEEDED
                or quote.order_id is not None
                or quote.amount is None
                or (quote.expires_at and quote.expires_at <= timezone.now())
            ):
                raise PreviewStaleError()
        # Short table read locks prevent concurrent Admin price/rule writes or inserts
        # between revalidation and order creation. No external HTTP under these locks.
        if connection.vendor == "postgresql":
            with connection.cursor() as cursor:
                cursor.execute("LOCK TABLE catalog_product, discounts_discountrule, delivery_deliveryrule IN SHARE MODE")
        if (cart.revision != preview.cart_revision or cls.signature(cart) != preview.snapshot["items"]
                or cls.terms(cart) != preview.snapshot["terms"]
                or cls.totals(cart, customer, quote) != preview.snapshot["totals"]):
            raise PreviewStaleError()
        for key in Cart.CHECKOUT_FIELDS:
            if key in kwargs and str(kwargs[key] or "") != preview.snapshot["terms"][key]:
                raise PreviewStaleError()
        terms = preview.snapshot["terms"]
        allowed = {k:v for k,v in kwargs.items() if k in ("status_source", "is_new_customer")}
        order = OrderService.create_order_from_cart(cart, customer=customer, channel=channel,
            receiving_type=terms["receiving_type"], payment_method=terms["payment_method"],
            delivery_address=terms["delivery_address"], customer_comment=terms["customer_comment"],
            desired_date=cart.desired_date, desired_time_interval=terms["desired_time_interval"],
            customer_phone_snapshot=terms["contact_phone"], customer_email_snapshot=terms["contact_email"],
            delivery_cost_override=Decimal(preview.snapshot["totals"]["delivery_cost"]), **allowed)
        if str(order.total_amount) != preview.snapshot["totals"]["total_amount"]:
            raise PreviewStaleError()
        CheckoutDeliveryService.attach_quote(quote, order)
        preview.order = order
        preview.save(update_fields=["order"])
        return order
