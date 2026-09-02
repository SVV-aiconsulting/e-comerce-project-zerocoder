"""Сессия сайта и JSON-адаптер витрины без публикации adapter token."""
import json
import re
import uuid
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
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
from apps.delivery.checkout import CheckoutDeliveryService
from apps.orders.services import OrderService
from apps.payments.exceptions import PaymentError
from apps.payments.services import PaymentService
from apps.intake.enums import InboundEventKind
from apps.intake.models import InboundEvent
from apps.intake.responses import InboundEventResponseService
from apps.intake.services import InboundEventService

SESSION_USER_KEY = "website_external_user_id"
SESSION_CUSTOMER_KEY = "website_customer_id"
SESSION_ASSISTANT_CONVERSATION_KEY = "website_assistant_conversation_id"
ASSISTANT_MESSAGE_MAX_LENGTH = 20_000
PHONE_IN_TEXT_RE = re.compile(r"(?<!\d)(?:\+7|7|8)[\s().-]*\d(?:[\s().-]*\d){9}(?!\d)")
EMAIL_IN_TEXT_RE = re.compile(
    r"(?<![\w.+-])[\w.+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+(?![\w.+-])"
)


def get_or_create_website_user_id(request) -> str:
    """Стабильный ID браузерной сессии для корзины канала website."""
    user_id = request.session.get(SESSION_USER_KEY)
    if not user_id:
        user_id = f"web:{uuid.uuid4()}"
        request.session[SESSION_USER_KEY] = user_id
        request.session.modified = True
    return user_id


def get_or_create_assistant_conversation_key(request) -> str:
    """Отдельный ключ текущего AI-диалога внутри стабильной website-сессии."""
    conversation_id = request.session.get(SESSION_ASSISTANT_CONVERSATION_KEY)
    if not conversation_id:
        conversation_id = str(uuid.uuid4())
        request.session[SESSION_ASSISTANT_CONVERSATION_KEY] = conversation_id
        request.session.modified = True
    return f"{get_or_create_website_user_id(request)}:assistant:{conversation_id}"


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


def contacts_from_message(message: str) -> tuple[str, str]:
    """Извлечь только явно написанные в сообщении контакты для CRM-идентификации."""
    phone = ""
    email = ""
    phone_match = PHONE_IN_TEXT_RE.search(message)
    if phone_match:
        try:
            phone = normalize_phone(phone_match.group(0))
            validate_phone(phone)
        except ValidationError:
            phone = ""
    email_match = EMAIL_IN_TEXT_RE.search(message)
    if email_match:
        try:
            email = normalize_email(email_match.group(0))
        except ValidationError:
            email = ""
    return phone, email


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
        preview = CheckoutDeliveryService.preview(
            cart=cart,
            customer=customer,
            receiving_type=receiving_type,
            delivery_address=str(payload.get("delivery_address") or ""),
            payment_method=str(
                payload.get("payment_method") or PaymentMethod.CARD_PREPAYMENT
            ),
        )
        totals = preview.totals
        quote = preview.quote
        return JsonResponse(
            {
                "items_total": str(totals.items_total),
                "discount_amount": str(totals.discount_amount),
                "delivery_cost": str(totals.delivery_cost),
                "total_amount": str(totals.total_amount),
                "free_delivery": totals.free_delivery,
                "delivery_quote_id": quote.pk if quote else None,
                "delivery_days": quote.delivery_days if quote else None,
                "delivery_provider": quote.provider if quote else "",
                "delivery_address": quote.destination_address if quote else "",
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
        quote = CheckoutDeliveryService.selected_quote(
            cart=cart,
            receiving_type=receiving_type,
            delivery_address=str(payload.get("delivery_address") or ""),
            quote_id=payload.get("delivery_quote_id"),
        )
        delivery_cost_override = CheckoutDeliveryService.delivery_cost_for_quote(
            cart=cart,
            customer=customer,
            quote=quote,
        )
        order = OrderService.create_order_from_cart(
            cart,
            customer=customer,
            channel=Channel.WEBSITE,
            receiving_type=receiving_type,
            payment_method=payment_method,
            delivery_address=str(payload.get("delivery_address") or "").strip(),
            customer_comment=str(payload.get("customer_comment") or "").strip(),
            delivery_cost_override=delivery_cost_override,
            is_new_customer=identity.is_new_customer,
            status_source=StatusChangeSource.WEBSITE,
        )
        CheckoutDeliveryService.attach_quote(quote, order)
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


class WebsiteAssistantMessageView(WebsiteApiView):
    """Публичный browser-safe вход в существующий AI order pipeline."""

    def post(self, request):
        if not settings.AI_ASSISTANT_ENABLED:
            return json_error(
                "AI-консультант временно отключён. Оформите заказ через каталог.",
                code="assistant_disabled",
                status=503,
            )
        payload = parse_json(request)
        message = str(payload.get("message") or "").strip()
        if not message:
            return json_error("Напишите сообщение для консультанта.")
        if len(message) > ASSISTANT_MESSAGE_MAX_LENGTH:
            return json_error("Сообщение слишком длинное.")

        customer = session_customer(request)
        phone, email = contacts_from_message(message)
        if phone or email:
            if not payload.get("personal_data_consent"):
                return json_error(
                    "Отметьте согласие на обработку данных, чтобы передать контакты для заказа."
                )
            identity = CustomerService.resolve_website_customer(
                name="Покупатель",
                phone=phone,
                email=email,
                external_user_id=get_or_create_website_user_id(request),
            )
            customer = identity.customer
            if customer is None:
                return json_error("Не удалось идентифицировать клиента.")
            if not customer.personal_data_consent:
                customer.personal_data_consent = True
                customer.save(update_fields=["personal_data_consent", "updated_at"])
            request.session[SESSION_CUSTOMER_KEY] = customer.pk

        external_user_id = get_or_create_website_user_id(request)
        registration = InboundEventService.register(
            channel=Channel.WEBSITE,
            external_event_id=str(uuid.uuid4()),
            external_user_id=external_user_id,
            conversation_key=get_or_create_assistant_conversation_key(request),
            customer=customer,
            kind=InboundEventKind.MESSAGE,
            raw_text=message,
            raw_payload={
                "source": "website_ai_assistant",
                "contact_phone": phone or (customer.phone if customer else ""),
                "contact_email": email or (customer.email if customer else ""),
            },
        )
        InboundEventService.enqueue(registration.event)
        return JsonResponse(
            {"event_id": str(registration.event.public_id), "status": registration.event.status},
            status=202,
        )


class WebsiteAssistantEventView(WebsiteApiView):
    def get(self, request, event_id):
        event = get_object_or_404(
            InboundEvent.objects.select_related("draft__converted_order"),
            public_id=event_id,
            channel=Channel.WEBSITE,
            external_user_id=get_or_create_website_user_id(request),
        )
        return JsonResponse(InboundEventResponseService.present(event))


class WebsiteAssistantHistoryView(WebsiteApiView):
    def get(self, request):
        events = list(
            InboundEvent.objects.select_related("draft__converted_order")
            .filter(
                channel=Channel.WEBSITE,
                external_user_id=get_or_create_website_user_id(request),
                conversation_key=get_or_create_assistant_conversation_key(request),
            )
            .order_by("-created_at")[:30]
        )
        messages = []
        for event in reversed(events):
            messages.append({"role": "user", "message": event.raw_text})
            payload = InboundEventResponseService.present(event)
            response = payload.get("response")
            if payload["complete"] and response:
                messages.append(
                    {
                        "role": "assistant",
                        "message": response["message"],
                        "action_url": response.get("action_url", ""),
                    }
                )
        return JsonResponse({"messages": messages})


class WebsiteAssistantConversationView(WebsiteApiView):
    """Начать новый черновик, не меняя website-идентичность и корзину."""

    def post(self, request):
        request.session[SESSION_ASSISTANT_CONVERSATION_KEY] = str(uuid.uuid4())
        request.session.modified = True
        return JsonResponse({"messages": [], "status": "new"}, status=201)
