"""Сессия сайта и JSON-адаптер витрины без публикации adapter token."""
import json
import uuid
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import ensure_csrf_cookie

from apps.api.exceptions import _map_shop_error
from apps.api.helpers import get_active_cart
from apps.api.serializers.cart import CartSerializer
from apps.api.serializers.orders import OrderSerializer
from apps.carts.services import CartService
from apps.catalog.services import CatalogService
from apps.common.enums import Channel, PaymentMethod, ReceivingType, StatusChangeSource
from apps.common.exceptions import ShopError
from apps.customers.models import Customer
from apps.customers.services import CustomerService
from apps.customers.validators import normalize_email, normalize_phone, validate_phone
from apps.orders.pricing import OrderTotals, PricingService
from apps.orders.services import OrderService
from apps.payments.exceptions import PaymentError
from apps.payments.services import PaymentService

SESSION_USER_KEY = "website_external_user_id"
SESSION_CUSTOMER_KEY = "website_customer_id"


def get_or_create_website_user_id(request) -> str:
    """Стабильный ID браузерной сессии для корзины канала website."""
    user_id = request.session.get(SESSION_USER_KEY)
    if not user_id:
        user_id = f"web:{uuid.uuid4()}"
        request.session[SESSION_USER_KEY] = user_id
        request.session.modified = True
    return user_id


def website_cart(request, customer=None):
    return get_active_cart(
        channel=Channel.WEBSITE,
        external_user_id=get_or_create_website_user_id(request),
        customer=customer,
    )


def cart_payload(request, customer=None) -> dict:
    return CartSerializer(
        website_cart(request, customer=customer),
        context={"request": request},
    ).data


def json_error(message: str, *, code: str = "validation_error", status: int = 400, details=None):
    return JsonResponse(
        {"error": {"code": code, "message": message, "details": details or {}}},
        status=status,
    )


def parse_json(request) -> dict:
    if not request.body:
        return {}
    try:
        payload = json.loads(request.body.decode())
    except json.JSONDecodeError as exc:
        raise ValueError("Некорректный JSON") from exc
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise ValueError("Ожидался объект JSON")
    return payload


def session_customer(request) -> Customer | None:
    return Customer.objects.filter(pk=request.session.get(SESSION_CUSTOMER_KEY)).first()


def identify_from_payload(request, payload: dict):
    name = str(payload.get("name") or "").strip()
    phone = str(payload.get("phone") or "").strip()
    email = str(payload.get("email") or "").strip()
    if phone:
        phone = normalize_phone(phone)
        validate_phone(phone)
    if email:
        email = normalize_email(email)
    if not phone and not email:
        raise ValueError("Укажите телефон или email для связи.")
    identity = CustomerService.resolve_website_customer(
        name=name or "Покупатель",
        phone=phone,
        email=email,
        external_user_id=get_or_create_website_user_id(request),
    )
    customer = identity.customer
    if customer is None:
        raise ValueError("Не удалось идентифицировать клиента.")
    if payload.get("personal_data_consent") and not customer.personal_data_consent:
        customer.personal_data_consent = True
        customer.save(update_fields=["personal_data_consent", "updated_at"])
    request.session[SESSION_CUSTOMER_KEY] = customer.pk
    return identity


class WebsiteApiView(View):
    def dispatch(self, request, *args, **kwargs):
        try:
            get_or_create_website_user_id(request)
            return super().dispatch(request, *args, **kwargs)
        except ShopError as exc:
            response = _map_shop_error(exc)
            return JsonResponse(response.data, status=response.status_code)
        except ValidationError as exc:
            message = exc.messages[0] if getattr(exc, "messages", None) else str(exc)
            return json_error(message)
        except ValueError as exc:
            return json_error(str(exc))


@method_decorator(ensure_csrf_cookie, name="dispatch")
class WebsiteCartView(WebsiteApiView):
    def get(self, request):
        return JsonResponse(cart_payload(request, customer=session_customer(request)))


class WebsiteCartItemView(WebsiteApiView):
    def put(self, request, product_id: int):
        payload = parse_json(request)
        try:
            quantity = Decimal(str(payload.get("quantity", "0")))
        except (InvalidOperation, TypeError):
            return json_error("Укажите корректное количество.")
        product = CatalogService.get_product(product_id=product_id)
        if product is None or not product.is_active:
            return json_error("Товар недоступен.", code="product_unavailable", status=422)
        customer = session_customer(request)
        cart = website_cart(request, customer=customer)
        if quantity <= 0:
            CartService.remove_item(cart, product)
        else:
            CartService.set_item_quantity(cart, product, quantity)
        return JsonResponse(cart_payload(request, customer=customer))

    def delete(self, request, product_id: int):
        product = CatalogService.get_product(product_id=product_id)
        if product is None:
            return json_error("Товар не найден.", code="product_not_found", status=404)
        customer = session_customer(request)
        CartService.remove_item(website_cart(request, customer=customer), product)
        return JsonResponse(cart_payload(request, customer=customer))


class WebsiteCartClearView(WebsiteApiView):
    def delete(self, request):
        customer = session_customer(request)
        CartService.clear(website_cart(request, customer=customer))
        return JsonResponse(cart_payload(request, customer=customer))


class WebsiteCheckoutPreviewView(WebsiteApiView):
    def post(self, request):
        payload = parse_json(request)
        receiving_type = payload.get("receiving_type") or ReceivingType.DELIVERY
        if receiving_type not in ReceivingType.values:
            return json_error("Некорректный способ получения.")
        customer = session_customer(request)
        if payload.get("phone") or payload.get("email"):
            customer = identify_from_payload(request, payload).customer
        cart = website_cart(request, customer=customer)
        items = list(CartService.get_contents(cart))
        if customer is not None:
            CartService.validate_cart_for_order(cart)
            totals = PricingService.calculate_order_totals(
                customer=customer,
                cart_items=items,
                receiving_type=receiving_type,
            )
        else:
            items_total = PricingService.calculate_items_total(items)
            delivery_cost = (
                Decimal("0")
                if receiving_type == ReceivingType.PICKUP
                else PricingService.calculate_delivery(items_total)
            )
            totals = OrderTotals(
                items_total=items_total,
                discount_amount=Decimal("0.00"),
                delivery_cost=delivery_cost,
                total_amount=PricingService.calculate_total(
                    items_total, Decimal("0"), delivery_cost
                ),
                free_delivery=False,
            )
        return JsonResponse(
            {
                "items_total": str(totals.items_total),
                "discount_amount": str(totals.discount_amount),
                "delivery_cost": str(totals.delivery_cost),
                "total_amount": str(totals.total_amount),
                "free_delivery": totals.free_delivery,
            }
        )


class WebsiteCreateOrderView(WebsiteApiView):
    def post(self, request):
        payload = parse_json(request)
        if not payload.get("personal_data_consent"):
            return json_error("Нужно согласие на обработку данных.")
        receiving_type = payload.get("receiving_type") or ReceivingType.DELIVERY
        payment_method = payload.get("payment_method") or PaymentMethod.CASH_ON_DELIVERY
        if receiving_type not in ReceivingType.values:
            return json_error("Некорректный способ получения.")
        if payment_method not in PaymentMethod.values:
            return json_error("Некорректный способ оплаты.")
        if receiving_type == ReceivingType.DELIVERY and not str(
            payload.get("delivery_address") or ""
        ).strip():
            return json_error("Для доставки укажите адрес.")
        identity = identify_from_payload(request, payload)
        customer = identity.customer
        cart = website_cart(request, customer=customer)
        order = OrderService.create_order_from_cart(
            cart,
            customer=customer,
            channel=Channel.WEBSITE,
            receiving_type=receiving_type,
            payment_method=payment_method,
            delivery_address=str(payload.get("delivery_address") or "").strip(),
            customer_comment=str(payload.get("customer_comment") or "").strip(),
            is_new_customer=identity.is_new_customer,
            status_source=StatusChangeSource.WEBSITE,
        )
        confirmation_url = ""
        if payment_method == PaymentMethod.CARD_PREPAYMENT:
            try:
                payment = PaymentService.ensure_payment_link(order)
                confirmation_url = payment.confirmation_url or ""
            except PaymentError:
                confirmation_url = ""
        data = OrderSerializer(order).data
        data["confirmation_url"] = confirmation_url
        return JsonResponse(data, status=201)
