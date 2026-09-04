"""Типизированные инструменты агента над доменными сервисами WebMarket."""

import hashlib
import json
import re
import time
from dataclasses import dataclass
from decimal import Decimal
from difflib import SequenceMatcher

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from pydantic import ValidationError as PydanticValidationError

from apps.assistant.schemas import (
    ConfigureCheckoutArgs,
    ConfirmOrderArgs,
    EmptyArgs,
    ListOrdersArgs,
    PaymentLinkArgs,
    RemoveCartItemArgs,
    RepeatOrderArgs,
    SearchProductsArgs,
    SetCartItemArgs,
)
from apps.catalog.models import Product, normalize_product_text
from apps.catalog.services import CatalogService
from apps.common.enums import PaymentMethod, ReceivingType
from apps.intake.enums import (
    AssistantToolCallStatus,
    ClarificationStatus,
    ItemMatchStatus,
    OrderDraftStatus,
    OrderIntent,
    ResolutionSource,
)
from apps.intake.fulfillment import DraftOrderConversionService, DraftPricingService
from apps.intake.models import AssistantToolCall, Clarification, OrderDraft, OrderDraftItem
from apps.intake.services import OrderDraftService
from apps.orders.models import Order
from apps.payments.services import PaymentService
from apps.customers.validators import normalize_email, normalize_phone
from apps.delivery.models import DeliveryQuoteStatus


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    schema: type
    mutating: bool = False

    def function_definition(self) -> dict:
        schema = self.schema.model_json_schema(mode="validation")
        return {
            "name": self.name,
            "description": self.description,
            "parameters": _provider_compatible_schema(schema),
        }


def _provider_compatible_schema(value):
    """Убирает nullable-обёртки, не поддерживаемые GigaChat functions.

    Необязательный аргумент можно не передавать, поэтому JSON Schema не должна
    объявлять для него ``null``. Фактические аргументы всё равно проверяет
    исходная строгая Pydantic-модель перед вызовом backend-инструмента.
    """
    if isinstance(value, list):
        return [_provider_compatible_schema(item) for item in value]
    if not isinstance(value, dict):
        return value

    variants = value.get("anyOf")
    if isinstance(variants, list):
        non_null = [item for item in variants if item.get("type") != "null"]
        if len(non_null) == 1 and len(non_null) != len(variants):
            merged = {
                key: item
                for key, item in value.items()
                if key not in {"anyOf", "default"}
            }
            merged.update(non_null[0])
            return _provider_compatible_schema(merged)

    return {
        key: _provider_compatible_schema(item)
        for key, item in value.items()
        if key != "default"
    }


TOOL_SPECS = (
    ToolSpec("search_products", "Найти активные товары по названию, категории или синониму.", SearchProductsArgs),
    ToolSpec("get_cart", "Получить актуальный состав AI-корзины и состояние оформления.", EmptyArgs),
    ToolSpec("set_cart_item", "Добавить товар по коду или установить его количество.", SetCartItemArgs, True),
    ToolSpec("remove_cart_item", "Удалить товар из AI-корзины по коду.", RemoveCartItemArgs, True),
    ToolSpec("configure_checkout", "Настроить получение, адрес, оплату и контакты черновика.", ConfigureCheckoutArgs, True),
    ToolSpec("preview_order", "Рассчитать актуальные цены, скидку, доставку и итог перед подтверждением.", EmptyArgs, True),
    ToolSpec("list_customer_orders", "Показать последние заказы идентифицированного клиента.", ListOrdersArgs),
    ToolSpec("repeat_order", "Скопировать позиции прошлого заказа в новый черновик по текущим товарам.", RepeatOrderArgs, True),
    ToolSpec("get_payment_link", "Получить или идемпотентно восстановить ссылку ЮKassa для своего заказа.", PaymentLinkArgs, True),
    ToolSpec("confirm_order", "После явного подтверждения в текущем сообщении клиента создать один заказ и при онлайн-оплате получить ссылку ЮKassa.", ConfirmOrderArgs, True),
)
TOOL_BY_NAME = {spec.name: spec for spec in TOOL_SPECS}


class AssistantToolExecutor:
    def __init__(self, *, event, draft, turn):
        self.event = event
        self.draft_id = draft.pk
        self.turn = turn

    @staticmethod
    def definitions() -> list[dict]:
        return [spec.function_definition() for spec in TOOL_SPECS]

    def execute(self, name: str, raw_arguments: dict, call_index: int) -> dict:
        spec = TOOL_BY_NAME.get(name)
        canonical = json.dumps(raw_arguments, ensure_ascii=False, sort_keys=True, default=str)
        identity = (
            f"{self.event.public_id}:{name}:{canonical}"
            if spec and spec.mutating
            else f"{self.event.public_id}:{call_index}:{name}:{canonical}"
        )
        key = hashlib.sha256(
            identity.encode()
        ).hexdigest()
        existing = AssistantToolCall.objects.filter(idempotency_key=key).first()
        if existing and existing.status == AssistantToolCallStatus.SUCCEEDED:
            return existing.result
        indexed = AssistantToolCall.objects.filter(
            turn=self.turn,
            call_index=call_index,
        ).first()
        if indexed and indexed.completed_at:
            return indexed.result
        audit, _ = AssistantToolCall.objects.get_or_create(
            idempotency_key=key,
            defaults={
                "turn": self.turn,
                "call_index": call_index,
                "tool_name": name,
                "arguments": raw_arguments,
            },
        )
        started = time.monotonic()
        if spec is None:
            return self._finish_error(audit, started, "unknown_tool", "Инструмент недоступен")
        try:
            arguments = spec.schema.model_validate(raw_arguments, strict=True)
        except PydanticValidationError as exc:
            return self._finish_error(
                audit,
                started,
                "invalid_arguments",
                "Аргументы не прошли типизированную проверку",
                details=exc.errors(include_url=False),
                status=AssistantToolCallStatus.REJECTED,
            )
        try:
            result = getattr(self, f"_tool_{name}")(arguments)
        except Exception as exc:
            return self._finish_error(
                audit,
                started,
                type(exc).__name__,
                str(exc) or "Ошибка backend-инструмента",
            )
        audit.result = result
        audit.status = (
            AssistantToolCallStatus.SUCCEEDED
            if result.get("ok") is not False
            else AssistantToolCallStatus.REJECTED
        )
        if result.get("ok") is False:
            audit.error_code = str(result.get("error", {}).get("code", "tool_rejected"))[:64]
        audit.latency_ms = int((time.monotonic() - started) * 1000)
        audit.completed_at = timezone.now()
        audit.save(update_fields=["result", "status", "error_code", "latency_ms", "completed_at", "updated_at"])
        return result

    @staticmethod
    def _finish_error(audit, started, code, message, *, details=None, status=AssistantToolCallStatus.FAILED):
        result = {"ok": False, "error": {"code": code, "message": message}}
        if details:
            result["error"]["details"] = details
        audit.result = result
        audit.status = status
        audit.error_code = code[:64]
        audit.latency_ms = int((time.monotonic() - started) * 1000)
        audit.completed_at = timezone.now()
        audit.save(update_fields=["result", "status", "error_code", "latency_ms", "completed_at", "updated_at"])
        return result

    def _draft(self):
        return OrderDraft.objects.select_related("customer").get(pk=self.draft_id)

    @staticmethod
    def _product_payload(product) -> dict:
        return {
            "code": product.public_code,
            "name": product.name,
            "description": product.description,
            "unit": product.unit,
            "unit_label": product.get_unit_display(),
            "min_quantity": str(product.min_quantity),
            "price": str(product.base_price),
            "currency": "RUB",
        }

    def _tool_search_products(self, args: SearchProductsArgs) -> dict:
        query = normalize_product_text(args.query)
        ranked = []
        for product in CatalogService.get_active_products().prefetch_related("aliases"):
            variants = [normalize_product_text(product.name)] + [a.normalized_alias for a in product.aliases.all()]
            substring = any(query in value or value in query for value in variants)
            score = max(SequenceMatcher(None, query, value).ratio() for value in variants)
            if substring or score >= 0.25:
                ranked.append((1 if substring else 0, score, product))
        ranked.sort(key=lambda row: (-row[0], -row[1], row[2].sort_order, row[2].name))
        products = [self._product_payload(row[2]) for row in ranked[: args.limit]]
        return {"ok": True, "query": args.query, "products": products, "count": len(products)}

    def _cart_payload(self, draft=None) -> dict:
        draft = draft or self._draft()
        items = [
            {
                "code": item.product.public_code,
                "name": item.product.name,
                "quantity": str(item.requested_quantity),
                "unit": item.product.unit,
                "unit_label": item.product.get_unit_display().lower(),
                "unit_price": str(item.product.base_price),
                "line_total": str(item.requested_quantity * item.product.base_price),
            }
            for item in draft.items.select_related("product").filter(product__isnull=False).order_by("line_number")
        ]
        return {
            "ok": True,
            "draft_id": str(draft.public_id),
            "revision": draft.revision,
            "status": draft.status,
            "items": items,
            "receiving_type": draft.receiving_type or None,
            "delivery_address": draft.delivery_address or None,
            "payment_method": draft.payment_method or None,
            "missing_fields": draft.missing_fields,
            "total_amount": str(draft.total_amount) if draft.total_amount is not None else None,
        }

    def _tool_get_cart(self, _args: EmptyArgs) -> dict:
        return self._cart_payload()

    def _prepare_change(self):
        draft = self._draft()
        if draft.status == OrderDraftStatus.CONVERTED:
            raise ValueError("Оформленный заказ нельзя изменять; начните новый диалог")
        Clarification.objects.filter(draft=draft, status=ClarificationStatus.PENDING).update(status=ClarificationStatus.CANCELLED)
        return OrderDraftService.record_change(draft)

    @staticmethod
    def _refresh_state(draft):
        draft.refresh_from_db()
        missing = []
        items = list(draft.items.select_related("product").order_by("line_number"))
        if not items:
            missing.append("items")
        if not draft.receiving_type:
            missing.append("receiving_type")
        if draft.receiving_type == ReceivingType.DELIVERY and not draft.delivery_address.strip():
            missing.append("delivery_address")
        if settings.YANDEX_DELIVERY_ENABLED and draft.receiving_type == ReceivingType.DELIVERY and not draft.contact_phone:
            missing.append("contact_phone")
        if not draft.payment_method:
            missing.append("payment_method")
        if settings.YANDEX_DELIVERY_ENABLED and draft.receiving_type == ReceivingType.DELIVERY and draft.payment_method == PaymentMethod.CASH_ON_DELIVERY:
            missing.append("delivery_payment_method")
        if draft.customer_id is None:
            missing.append("customer")
        draft.intent = OrderIntent.CREATE_ORDER
        draft.missing_fields = missing
        draft.status = OrderDraftStatus.NEEDS_CLARIFICATION if missing else OrderDraftStatus.READY_FOR_PREVIEW
        draft.save(update_fields=["intent", "missing_fields", "status", "updated_at"])
        return draft

    def _tool_set_cart_item(self, args: SetCartItemArgs) -> dict:
        if not self._message_has_quantity(self.event.raw_text):
            return {
                "ok": False,
                "error": {
                    "code": "quantity_required",
                    "message": "Укажите количество товара, которое нужно добавить в заказ.",
                },
            }
        product = Product.objects.filter(public_code=args.product_code, is_active=True).first()
        if product is None:
            raise ValueError("Активный товар с таким кодом не найден")
        quantity = Decimal(str(args.quantity))
        CatalogService.check_availability(product, quantity)
        draft = self._prepare_change()
        item = draft.items.filter(product=product).first()
        if item is None:
            line = (draft.items.aggregate(value=Max("line_number"))["value"] or 0) + 1
            OrderDraftItem.objects.create(
                draft=draft,
                line_number=line,
                raw_product_name=product.name,
                requested_quantity=quantity,
                requested_unit=product.unit,
                product=product,
                match_status=ItemMatchStatus.MATCHED,
                candidate_product_ids=[product.pk],
                resolution_source=ResolutionSource.EXACT,
                resolution_confidence=Decimal("1"),
            )
        else:
            item.requested_quantity = quantity
            item.requested_unit = product.unit
            item.validation_errors = []
            item.save(update_fields=["requested_quantity", "requested_unit", "validation_errors", "updated_at"])
        return self._cart_payload(self._refresh_state(draft))

    @staticmethod
    def _message_has_quantity(text: str) -> bool:
        normalized = normalize_product_text(text)
        if re.search(r"\d+(?:[.,]\d+)?", text):
            return True
        number_words = (
            "один", "одна", "одно", "одну", "два", "две", "три", "четыре",
            "пять", "шесть", "семь", "восемь", "девять", "десять", "полкило",
            "полкилограмма", "половина", "половину",
        )
        return any(re.search(rf"\b{word}\b", normalized) for word in number_words)

    def _tool_remove_cart_item(self, args: RemoveCartItemArgs) -> dict:
        draft = self._prepare_change()
        deleted, _ = draft.items.filter(product__public_code=args.product_code).delete()
        if not deleted:
            raise ValueError("Товар отсутствует в корзине")
        return self._cart_payload(self._refresh_state(draft))

    def _tool_configure_checkout(self, args: ConfigureCheckoutArgs) -> dict:
        draft = self._prepare_change()
        updates = {}
        for field in ("receiving_type", "delivery_address", "payment_method", "customer_comment"):
            value = getattr(args, field)
            if value is not None:
                updates[field] = value
        try:
            if args.contact_phone is not None:
                updates["contact_phone"] = normalize_phone(args.contact_phone)
            if args.contact_email is not None:
                updates["contact_email"] = normalize_email(args.contact_email)
        except DjangoValidationError as exc:
            raise ValueError("Контактные данные имеют неверный формат") from exc
        if updates:
            OrderDraft.objects.filter(pk=draft.pk).update(**updates)
        return self._cart_payload(self._refresh_state(draft))

    def _tool_preview_order(self, _args: EmptyArgs) -> dict:
        draft = self._refresh_state(self._draft())
        if draft.missing_fields:
            return {"ok": False, "error": {"code": "draft_incomplete", "message": "Для расчёта не хватает данных", "missing_fields": draft.missing_fields}, "cart": self._cart_payload(draft)}
        draft = DraftPricingService.preview(draft)
        if draft.status != OrderDraftStatus.AWAITING_CONFIRMATION:
            return {"ok": False, "error": {"code": "preview_failed", "message": "Не удалось выполнить актуальный расчёт", "missing_fields": draft.missing_fields}}
        result = self._cart_payload(draft)
        result["preview"] = {
            "revision": draft.previewed_revision,
            "items_total": str(draft.items_total),
            "discount_amount": str(draft.discount_amount),
            "delivery_cost": str(draft.delivery_cost),
            "total_amount": str(draft.total_amount),
        }
        quote = (
            draft.delivery_quotes.filter(status=DeliveryQuoteStatus.SUCCEEDED)
            .order_by("-created_at")
            .first()
        )
        if quote is not None:
            result["preview"]["delivery_days"] = quote.delivery_days
            result["preview"]["delivery_currency"] = quote.currency
        result["requires_explicit_confirmation"] = True
        return result

    def context_payload(self) -> dict:
        """Авторитетное состояние для следующего model turn без доступа LLM к ORM."""
        recent_search = (
            AssistantToolCall.objects.filter(
                turn__event__channel=self.event.channel,
                turn__event__external_user_id=self.event.external_user_id,
                turn__event__conversation_key=self.event.conversation_key,
                tool_name="search_products",
                status=AssistantToolCallStatus.SUCCEEDED,
            )
            .order_by("-created_at", "-id")
            .values_list("result", flat=True)
            .first()
        )
        return {
            "cart": self._cart_payload(),
            "recent_product_search": recent_search or None,
        }

    def _tool_list_customer_orders(self, args: ListOrdersArgs) -> dict:
        draft = self._draft()
        if draft.customer_id is None:
            return {"ok": False, "error": {"code": "customer_required", "message": "Сначала нужен телефон или email клиента"}}
        orders = Order.objects.filter(customer_id=draft.customer_id).prefetch_related("items").order_by("-created_at")[: args.limit]
        return {"ok": True, "orders": [self._order_payload(order) for order in orders]}

    @staticmethod
    def _order_payload(order) -> dict:
        return {
            "number": order.public_number,
            "created_at": order.created_at.isoformat(),
            "status": order.order_status,
            "payment_status": order.payment_status,
            "payment_method": order.payment_method,
            "receiving_type": order.receiving_type,
            "delivery_address": order.delivery_address,
            "delivery_cost": str(order.delivery_cost),
            "total_amount": str(order.total_amount),
            "items": [
                {
                    "code": item.product.public_code,
                    "name": item.product_name_snapshot,
                    "quantity": str(item.quantity),
                    "unit": item.product_unit_snapshot,
                    "unit_label": item.product.get_unit_display().lower(),
                    "unit_price": str(item.unit_price),
                    "total_price": str(item.total_price),
                }
                for item in order.items.all()
            ],
        }

    @transaction.atomic
    def _tool_repeat_order(self, args: RepeatOrderArgs) -> dict:
        draft = self._prepare_change()
        if draft.customer_id is None:
            return {"ok": False, "error": {"code": "customer_required", "message": "Для повтора заказа нужно идентифицировать клиента"}}
        orders = Order.objects.filter(customer_id=draft.customer_id).prefetch_related("items__product").order_by("-created_at")
        order = orders.filter(public_number=args.order_number).first() if args.order_number else orders.first()
        if order is None:
            return {"ok": False, "error": {"code": "order_not_found", "message": "Подходящий прошлый заказ не найден"}}
        rows = []
        for line, old_item in enumerate(order.items.all(), start=1):
            product = old_item.product
            if not product.is_active:
                continue
            rows.append(OrderDraftItem(
                draft=draft, line_number=line, raw_product_name=product.name,
                requested_quantity=max(old_item.quantity, product.min_quantity), requested_unit=product.unit,
                product=product, match_status=ItemMatchStatus.MATCHED,
                candidate_product_ids=[product.pk], resolution_source=ResolutionSource.EXACT,
                resolution_confidence=Decimal("1"),
            ))
        if not rows:
            return {"ok": False, "error": {"code": "products_unavailable", "message": "Товары прошлого заказа сейчас недоступны"}}
        draft.items.all().delete()
        OrderDraftItem.objects.bulk_create(rows)
        draft.receiving_type = order.receiving_type
        draft.delivery_address = order.delivery_address
        draft.payment_method = order.payment_method
        draft.save(update_fields=["receiving_type", "delivery_address", "payment_method", "updated_at"])
        return {"ok": True, "repeated_order": order.public_number, "cart": self._cart_payload(self._refresh_state(draft)), "requires_new_preview": True}

    @staticmethod
    def _explicit_confirmation(event) -> bool:
        if event.kind == "callback" and bool(event.raw_payload.get("confirmed")):
            return True
        text = normalize_product_text(event.raw_text)
        return bool(
            re.fullmatch(
                r"(?:да(?:\s+подтверждаю(?:\s+(?:этот\s+)?заказ)?)?"
                r"|(?:подтверждаю|оформляйте|оформляем)(?:\s+(?:этот\s+)?заказ)?"
                r"|готов\s+к\s+оплате|я\s+хочу\s+оплатить(?:\s+(?:этот|свой)\s+заказ)?)",
                text,
            )
        )

    def _tool_confirm_order(self, args: ConfirmOrderArgs) -> dict:
        draft = self._draft()
        if not self._explicit_confirmation(self.event):
            return {"ok": False, "error": {"code": "explicit_confirmation_required", "message": "Заказ создаётся только после отдельного явного подтверждения клиента"}}
        if draft.status == OrderDraftStatus.CONVERTED and draft.converted_order_id:
            order = draft.converted_order
        else:
            if draft.status != OrderDraftStatus.AWAITING_CONFIRMATION or draft.previewed_revision != args.preview_revision:
                return {"ok": False, "error": {"code": "stale_preview", "message": "Нужен новый актуальный preview перед подтверждением"}}
            draft = OrderDraftService.confirm(draft)
            order = DraftOrderConversionService.convert(draft)
        payment_url = ""
        if settings.YOOKASSA_ENABLED and order.payment_method == PaymentMethod.CARD_PREPAYMENT:
            payment_url = PaymentService.ensure_payment_link(order).confirmation_url
        return {"ok": True, "order_number": order.public_number, "total_amount": str(order.total_amount), "payment_url": payment_url, "message": "Ваш заказ оформлен. При необходимости наш менеджер свяжется с вами."}

    def _tool_get_payment_link(self, args: PaymentLinkArgs) -> dict:
        draft = self._draft()
        if draft.customer_id is None:
            return {"ok": False, "error": {"code": "customer_required", "message": "Сначала нужно идентифицировать клиента"}}
        order = Order.objects.filter(
            customer_id=draft.customer_id,
            public_number=args.order_number,
        ).first()
        if order is None:
            return {"ok": False, "error": {"code": "order_not_found", "message": "Заказ клиента не найден"}}
        if order.payment_method != PaymentMethod.CARD_PREPAYMENT:
            return {"ok": False, "error": {"code": "online_payment_not_selected", "message": "Для заказа не выбрана онлайн-оплата"}}
        payment = PaymentService.ensure_payment_link(order)
        return {"ok": True, "order_number": order.public_number, "payment_status": order.payment_status, "payment_url": payment.confirmation_url}
