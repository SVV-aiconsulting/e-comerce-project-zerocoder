"""Типизированные инструменты агента над доменными сервисами WebMarket."""

import hashlib
import json
import re
import time
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from difflib import SequenceMatcher

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from pydantic import ValidationError as PydanticValidationError

from apps.assistant.schemas import (
    CompareProductsArgs,
    CancelOrderArgs,
    ClearCartArgs,
    ConfigureCheckoutArgs,
    ConfirmOrderArgs,
    EmptyArgs,
    ListOrdersArgs,
    PaymentLinkArgs,
    RecommendProductsArgs,
    RemoveCartItemArgs,
    RepeatOrderArgs,
    SearchProductsArgs,
    SetCartItemArgs,
)
from apps.catalog.models import Product, normalize_product_text
from apps.catalog.services import CatalogService
from apps.common.enums import OrderStatus, PaymentMethod, PaymentStatus, ReceivingType, StatusChangeSource
from apps.intake.enums import (
    AssistantToolCallStatus,
    ClarificationStatus,
    ItemMatchStatus,
    OrderDraftStatus,
    OrderIntent,
    ResolutionSource,
)
from apps.intake.fulfillment import DraftOrderConversionService, DraftPricingService
from apps.intake.cart_bridge import UnifiedCartBridge
from apps.intake.models import AssistantMessage, AssistantToolCall, Clarification, OrderDraft, OrderDraftItem
from apps.intake.services import OrderDraftService
from apps.orders.models import Order
from apps.orders.services import OrderService
from apps.payments.models import PaymentState
from apps.payments.exceptions import PaymentError
from apps.payments.services import PaymentService
from apps.customers.validators import normalize_email, normalize_phone
from apps.delivery.models import DeliveryQuoteStatus
from apps.delivery.checkout import normalize_delivery_address


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
    ToolSpec("compare_products", "Сравнить 2–4 найденных товара по актуальным карточкам каталога.", CompareProductsArgs),
    ToolSpec("search_products", "Показать весь активный каталог при пустом query или найти товары по названию, виду и синониму.", SearchProductsArgs),
    ToolSpec(
        "recommend_products",
        "Подобрать товары по смыслу запроса из полного catalog_snapshot. Передай только коды реально подходящих товаров; если явно запрошенного товара нет, укажи его в unavailable_item и предложи близкие варианты.",
        RecommendProductsArgs,
    ),
    ToolSpec("get_cart", "Получить актуальный состав AI-корзины и состояние оформления.", EmptyArgs),
    ToolSpec("set_cart_item", "Добавить товар по коду или установить его количество.", SetCartItemArgs, True),
    ToolSpec("remove_cart_item", "Удалить товар из AI-корзины по коду.", RemoveCartItemArgs, True),
    ToolSpec("configure_checkout", "Настроить получение, адрес, оплату и контакты черновика.", ConfigureCheckoutArgs, True),
    ToolSpec("preview_order", "Рассчитать актуальные цены, скидку, доставку и итог перед подтверждением.", EmptyArgs, True),
    ToolSpec("list_customer_orders", "Показать последние заказы идентифицированного клиента.", ListOrdersArgs),
    ToolSpec("repeat_order", "Скопировать позиции прошлого заказа в новый черновик по текущим товарам.", RepeatOrderArgs, True),
    ToolSpec("get_payment_link", "Получить или идемпотентно восстановить ссылку ЮKassa для своего заказа.", PaymentLinkArgs, True),
    ToolSpec("confirm_order", "После явного подтверждения в текущем сообщении клиента создать один заказ и при онлайн-оплате получить ссылку ЮKassa.", ConfirmOrderArgs, True),
    ToolSpec("get_cancellation_options", "Проверить текущую корзину и активные оформленные заказы перед уточнением отмены.", EmptyArgs),
    ToolSpec("clear_cart", "После явного выбора очистить текущее неоформленное содержимое корзины.", ClearCartArgs, True),
    ToolSpec("cancel_order", "Отменить конкретный активный оформленный, но ещё не исполненный заказ клиента.", CancelOrderArgs, True),
)
TOOL_BY_NAME = {spec.name: spec for spec in TOOL_SPECS}


class AssistantToolExecutor:
    # This vocabulary determines only the category of a product that is not
    # sold. The actual alternatives always come from active catalogue cards
    # with the respective managed alias.
    UNAVAILABLE_PRODUCT_ALTERNATIVES = {
        "пикша": "белая рыба",
        "нерка": "красная рыба",
    }

    def __init__(self, *, event, draft, turn):
        self.event = event
        self.draft_id = draft.pk
        self.turn = turn

    @staticmethod
    def definitions() -> list[dict]:
        return [spec.function_definition() for spec in TOOL_SPECS]

    def execute(self, name: str, raw_arguments: dict, call_index: int) -> dict:
        from apps.intake.leases import check_lease, fenced_write

        # Delivery and payment tools perform network I/O. Keeping the event row
        # locked around those calls can hide a freshly-created order from a fast
        # webhook and stalls recovery. Their domain services are idempotent, so
        # fence immediately before and after the call instead.
        network_tools = {
            "preview_order", "confirm_order", "get_payment_link", "cancel_order"
        }
        if name in network_tools:
            check_lease()
            result = self._execute(name, raw_arguments, call_index)
            check_lease()
            return result
        with fenced_write():
            return self._execute(name, raw_arguments, call_index)

    def _execute(self, name: str, raw_arguments: dict, call_index: int) -> dict:
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
            if indexed.tool_name == name and indexed.arguments == raw_arguments:
                return indexed.result
            return {"ok": False, "error": {"code": "replay_mismatch", "message": "Ход уже обработан с другими действиями. Уточните запрос новым сообщением."}}
        from apps.intake.leases import fenced_write

        with fenced_write():
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
            from apps.intake.leases import check_lease

            check_lease()
        except Exception as exc:
            from apps.intake.leases import LeaseLost

            if isinstance(exc, LeaseLost):
                raise
            return self._finish_error(
                audit,
                started,
                type(exc).__name__,
                str(exc) or "Ошибка backend-инструмента",
            )
        with fenced_write():
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
        from apps.intake.leases import fenced_write

        with fenced_write():
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
        # Internal deterministic resolvers join independent catalogue intents
        # with ``|``.  Each part is matched separately so a request such as
        # "белая рыба и креветки" does not degrade into a fuzzy full-catalog
        # response.
        query_parts = [
            normalize_product_text(part)
            for part in str(args.query).split("|")
            if normalize_product_text(part)
        ] or ([query] if query else [])
        exact_matches = []
        literal_matches = []
        fuzzy_matches = []
        for product in CatalogService.get_active_products().prefetch_related("aliases"):
            if not query:
                literal_matches.append((1, 1.0, product))
                continue
            variants = [normalize_product_text(product.name)] + [a.normalized_alias for a in product.aliases.all()]
            exact = any(part == value for part in query_parts for value in variants)
            substring = any(part in value for part in query_parts for value in variants)
            score = max(
                SequenceMatcher(None, part, value).ratio()
                for part in query_parts
                for value in variants
            )
            if exact:
                exact_matches.append((2, 1.0, product))
            elif substring:
                literal_matches.append((1, score, product))
            elif score >= 0.75:
                fuzzy_matches.append((0, score, product))

        # Нечёткий поиск нужен только как fallback для опечаток. Если каталог уже
        # дал буквальное совпадение по названию или управляемому синониму, нельзя
        # примешивать похожие слова (например, «краб» к запросу «икра»).
        # Если в CRM есть точный управляемый синоним, общий вложенный синоним
        # не расширяет выборку: «красная рыба» не становится просто «рыбой».
        ranked = exact_matches or literal_matches or fuzzy_matches
        ranked.sort(key=lambda row: (-row[0], -row[1], row[2].sort_order, row[2].name))
        products = [self._product_payload(row[2]) for row in ranked[: args.limit]]
        selection_request = bool(
            len(query_parts) > 1
            and re.search(
                r"\b(?:заказ\w*|закаж\w*|добав\w*|возьм\w*|хоч\w*)\b",
                normalize_product_text(self.event.raw_text),
            )
        )
        return {
            "ok": True,
            "query": args.query,
            "scope": (
                "full_catalog" if not query
                else "selection" if selection_request
                else "filtered"
            ),
            "products": products,
            "count": len(products),
        }

    def _tool_recommend_products(self, args: RecommendProductsArgs) -> dict:
        """Возвращает только актуальные карточки выбранных моделью кодов.

        Модель выполняет смысловую классификацию, но не формирует факты о
        товарах. Backend повторно проверяет коды, активность и утверждение об
        отсутствии конкретного товара перед тем, как показать результат.
        """
        requested_codes = list(dict.fromkeys(args.product_codes))
        products = list(
            CatalogService.get_active_products()
            .filter(public_code__in=requested_codes)
            .prefetch_related("aliases")
        )
        by_code = {product.public_code: product for product in products}
        invalid_codes = [code for code in requested_codes if code not in by_code]
        if invalid_codes:
            return {
                "ok": False,
                "error": {
                    "code": "invalid_catalog_selection",
                    "message": "Подбор содержит товар, которого нет в актуальном каталоге. Повторите подбор по catalog_snapshot.",
                },
            }

        unavailable_item = (args.unavailable_item or "").strip()
        if unavailable_item:
            normalized_unavailable = normalize_product_text(unavailable_item)
            directly_available = any(
                self._variant_is_mentioned(normalized_unavailable, variant)
                for product in CatalogService.get_active_products().prefetch_related("aliases")
                for variant in (product.name, *(alias.alias for alias in product.aliases.all()))
            )
            if directly_available:
                unavailable_item = ""

        ordered = [by_code[code] for code in requested_codes]
        alternative_alias = normalize_product_text(args.alternative_alias)
        if unavailable_item:
            # Для аналогов LLM определяет смысловую категорию, а backend
            # проверяет её по управляемым синонимам. Поэтому «пикша» может быть
            # отнесена к «белой рыбе», но кальмар в ответ уже не попадёт.
            if alternative_alias:
                ordered = [
                    product
                    for product in ordered
                    if any(
                        alias.normalized_alias == alternative_alias
                        for alias in product.aliases.all()
                    )
                ]
            else:
                ordered = []
        return {
            "ok": True,
            "query": args.query,
            "scope": "recommendation",
            "unavailable_item": unavailable_item,
            "alternative_alias": alternative_alias,
            "products": [self._product_payload(product) for product in ordered],
            "count": len(ordered),
        }

    def unavailable_catalog_action(self):
        """Offer only verified alternatives for common absent fish."""
        text = normalize_product_text(self.event.raw_text)
        products = list(
            CatalogService.get_active_products().prefetch_related("aliases")
        )
        for item, alternative_alias in self.UNAVAILABLE_PRODUCT_ALTERNATIVES.items():
            if not any(self._word_matches(item, word) for word in text.split()):
                continue
            if any(
                item
                in {
                    normalize_product_text(product.name),
                    *(alias.normalized_alias for alias in product.aliases.all()),
                }
                for product in products
            ):
                continue
            alternatives = list(
                CatalogService.get_active_products()
                .filter(aliases__normalized_alias=alternative_alias)
                .distinct()
                .order_by("sort_order", "name")
            )
            return "recommend_products", {
                "query": item,
                "product_codes": [product.public_code for product in alternatives],
                "unavailable_item": item,
                "alternative_alias": alternative_alias,
            }
        return None

    @staticmethod
    def _word_matches(left: str, right: str) -> bool:
        """Сопоставляет простые русские словоформы без отдельного NLP-пакета."""
        if left == right:
            return True
        # У некоторых существительных меняется основа при склонении. Это
        # особенно важно для "тунец" → "тунца": без него явная позиция в
        # заказе выпадала, и ход ошибочно переходил к LLM.
        irregular_stems = {
            "тунец": "тунц",
            "тунца": "тунц",
            "тунцу": "тунц",
            "тунцем": "тунц",
            "тунце": "тунц",
            "тунцы": "тунц",
        }
        if irregular_stems.get(left, left) == irregular_stems.get(right, right):
            return True
        endings = (
            "иями", "ями", "ами", "его", "ого", "ему", "ому", "ими", "ыми",
            "ая", "яя", "ой", "ей", "ую", "юю", "ов", "ев", "ах", "ях",
            "ом", "ем", "ы", "и", "а", "я", "у", "ю",
        )

        def stem(value):
            for ending in endings:
                if value.endswith(ending) and len(value) - len(ending) >= 3:
                    return value[:-len(ending)]
            return value

        if stem(left) == stem(right):
            return True
        return False

    @classmethod
    def _variant_is_mentioned(cls, text: str, variant: str) -> bool:
        text_words = normalize_product_text(text).split()
        variant_words = normalize_product_text(variant).split()
        return bool(variant_words) and all(
            any(
                cls._word_matches(expected, actual)
                or (
                    min(len(expected), len(actual)) >= 5
                    and SequenceMatcher(None, expected, actual).ratio() >= 0.75
                )
                for actual in text_words
            )
            for expected in variant_words
        )

    @classmethod
    def _mentioned_products(cls, text: str) -> list[Product]:
        mentioned = []
        for product in CatalogService.get_active_products().prefetch_related("aliases"):
            variants = [product.name, *(alias.alias for alias in product.aliases.all())]
            if any(cls._variant_is_mentioned(text, variant) for variant in variants):
                mentioned.append(product)
        return mentioned

    def _tool_compare_products(self, args):
        products = list(Product.objects.filter(public_code__in=args.product_codes, is_active=True))
        if len(products) != len(set(args.product_codes)):
            return {"ok": False, "error": {"message": "Один из выбранных товаров недоступен. Обновите поиск."}}
        by_code = {p.public_code:p for p in products}
        return {"ok": True, "scope": "comparison", "query": "сравнение", "products": [self._product_payload(by_code[c]) for c in args.product_codes]}

    def catalog_action(self):
        """Детерминированно направляет вопросы о каталоге в источник истины."""
        text = normalize_product_text(self.event.raw_text)
        products = self._mentioned_products(text)
        exact_aliases = [
            alias.alias
            for product in CatalogService.get_active_products().prefetch_related("aliases")
            for alias in product.aliases.all()
            if self._variant_is_mentioned(text, alias.alias)
        ]
        if re.search(r"\b(?:заказ\w*|корзин\w*)\b", text):
            has_conjunction = bool(re.search(r"\b(?:и|а\s+также)\b", text))
            selection_is_complete = len(products) >= 2 or not has_conjunction
            if products and selection_is_complete and not self._quantity_values(self.event.raw_text):
                return "search_products", {
                    "query": " | ".join(product.name for product in products),
                    "limit": 30,
                }
            return None
        full_catalog_request = bool(
            re.search(r"\b(?:каталог\w*|ассортимент\w*)\b", text)
            or re.search(r"\bпокаж\w*\b.*\b(?:все\s+)?(?:ваш\w*\s+)?товар\w*\b", text)
            or re.search(r"\bчто\s+у\s+вас\s+есть\s+в\s+продаж\w*\b", text)
            or re.search(r"\bчто\s+(?:вы\s+)?прода[её]те\b", text)
        )
        if full_catalog_request and not products and not exact_aliases:
            return "search_products", {"query": "", "limit": 30}
        catalog_question = bool(
            re.search(
                r"\b(?:каталог\w*|ассортимент\w*|продаж\w*|описан\w*|"
                r"состав\w*|вход\w*|подробн\w*|расскаж\w*)\b",
                text,
            )
            or re.search(r"\bчто\s+у\s+вас\s+есть\b", text)
            or re.search(r"\b(?:есть\s+ли|где)\b", text)
            or re.search(r"^есть\s+\w+", text)
            or re.search(
                r"\b(?:какая|какие|какой|что)\b.*\b(?:есть|имеется|прода\w*)\b",
                text,
            )
                or bool(
                    products
                    and (
                        re.search(r"\b(?:какая|какие|какой)\b", text)
                    or text.startswith(("а ", "и "))
                )
            )
        )
        unavailable = self.unavailable_catalog_action()
        if unavailable is not None and (
            catalog_question
            or re.search(r"\b(?:заказ\w*|закаж\w*|добав\w*|куп\w*|хоч\w*)\b", text)
        ):
            return unavailable

        # Exact managed aliases are already safe category selections. This
        # covers natural requests such as "Хочу белую рыбу" and follow-ups
        # like "Это вся белая рыба?" without leaving the choice to the LLM.
        if not catalog_question and not exact_aliases:
            return None
        if exact_aliases:
            # Самый конкретный управляемый синоним важнее вложенного общего:
            # «красная рыба» не должна превращаться в запрос «рыба».
            unique_aliases = {
                normalize_product_text(alias): alias for alias in exact_aliases
            }
            specific_aliases = [
                alias
                for normalized, alias in unique_aliases.items()
                if not any(
                    normalized != other and normalized in other
                    for other in unique_aliases
                )
            ]
            text_words = text.split()

            def alias_position(value):
                first = normalize_product_text(value).split()[0]
                for index, word in enumerate(text_words):
                    if self._word_matches(first, word) or (
                        min(len(first), len(word)) >= 5
                        and SequenceMatcher(None, first, word).ratio() >= 0.75
                    ):
                        return index
                return len(text_words)

            query = " | ".join(sorted(specific_aliases, key=alias_position))
        elif products:
            names = {product.name for product in products}
            if len(names) == 1:
                query = next(iter(names))
            else:
                # Общий синоним вроде «рыба» должен вернуть всю категорию.
                aliases = [
                    alias.alias
                    for product in products
                    for alias in product.aliases.all()
                    if self._variant_is_mentioned(text, alias.alias)
                ]
                query = max(aliases, key=len) if aliases else ""
        elif not settings.AI_CONSULTANT_ENABLED:
            subject = re.search(
                r"\b(?:есть|имеется|прода\w*)\b\s+(.+)$",
                text,
            )
            if subject:
                query = subject.group(1).strip()
            elif text.startswith("а "):
                query = text[2:].strip()
            else:
                query = ""
        else:
            # Категории и свойства, которых нет среди управляемых синонимов,
            # анализирует модель по полному catalog_snapshot. Пустой буквальный
            # поиск здесь лишил бы её возможности подобрать реальные аналоги.
            return None
        return "search_products", {"query": query, "limit": 30}

    def semantic_catalog_request(self) -> bool:
        """Нужен ли отдельный смысловой выбор по полному снимку каталога."""
        if not settings.AI_CONSULTANT_ENABLED:
            return False
        text = normalize_product_text(self.event.raw_text)
        products = self._mentioned_products(text)
        asks_catalog = bool(
            re.search(r"\b(?:есть\s+ли|что\s+у\s+вас|како\w*\s+у\s+вас|"
                      r"подбер\w*|посовет\w*|предлож\w*|подойд\w*)\b", text)
            or re.search(r"\b(?:товар\w*|ассортимент\w*|прода[её]те|в\s+продаже)\b", text)
        )
        unknown_order_item = bool(
            re.search(r"\b(?:заказ\w*|закаж\w*|добав\w*|куп\w*|хоч\w*)\b", text)
            and (
                not products
                or (
                    len(products) == 1
                    and re.search(r"\b(?:и|а\s+также)\b", text)
                )
            )
        )
        return asks_catalog or unknown_order_item

    @staticmethod
    def _quantity_values(text: str) -> list[Decimal]:
        """Extract likely item quantities while excluding contacts and addresses."""
        cleaned = re.sub(r"[\w.+-]+@[\w.-]+", "", text)
        cleaned = re.sub(
            r"(?<!\d)(?:[+78][\s().-]*)?9(?:[\s().-]*\d){9}(?!\d)",
            "",
            cleaned,
        )
        cleaned = re.sub(
            r"\b(?:улица|ул\.?|дом|д\.?|проспект)\s+[^;\n]+",
            "",
            cleaned,
            flags=re.I,
        )
        normalized = normalize_product_text(cleaned)
        values = [
            Decimal(value.replace(",", "."))
            for value in re.findall(r"\d+(?:[.,]\d+)?", cleaned)
        ]
        word_values = {
            "один": Decimal(1), "одна": Decimal(1), "одно": Decimal(1),
            "одну": Decimal(1), "два": Decimal(2), "две": Decimal(2),
            "три": Decimal(3), "четыре": Decimal(4), "пять": Decimal(5),
            "шесть": Decimal(6), "семь": Decimal(7), "восемь": Decimal(8),
            "девять": Decimal(9), "десять": Decimal(10),
            "полкило": Decimal("0.5"), "полкилограмма": Decimal("0.5"),
            "половина": Decimal("0.5"), "половину": Decimal("0.5"),
        }
        values.extend(
            value
            for word, value in word_values.items()
            if re.search(rf"\b{word}\b", normalized)
        )
        return values

    def cart_mutation_actions(self):
        """Resolve explicit product/quantity pairs without trusting model memory.

        Each conjunction-separated clause is interpreted independently. This
        prevents a preceding full-catalog result from making the model select
        its first item when the customer explicitly names other products.
        """
        raw_text = self.event.raw_text
        text = normalize_product_text(raw_text)
        if "?" in raw_text or re.search(r"\b(?:удал|убер|отмен|сравн)\w*", text):
            return None

        clauses = re.split(r"\s+(?:и|а\s+также)\s+|[;,]", raw_text, flags=re.I)
        actions = []
        seen_codes = set()
        quantity_clause_count = 0
        for clause in clauses:
            products = self._mentioned_products(clause)
            quantities = self._quantity_values(clause)
            if quantities:
                quantity_clause_count += 1
            if len(products) != 1 or not quantities:
                continue
            product = products[0]
            if product.public_code in seen_codes:
                continue
            actions.append(
                (
                    "set_cart_item",
                    {
                        "product_code": product.public_code,
                        "quantity": float(quantities[0]),
                    },
                )
            )
            seen_codes.add(product.public_code)

        if actions and len(actions) == quantity_clause_count:
            return actions

        # В живой речи союз часто опускают: «тунец 1 кг осьминог 2 кг».
        # Привязываем число к следующему упомянутому товару по границам между
        # названиями, а не к первой позиции всего сообщения.
        normalized = normalize_product_text(raw_text)
        positioned = []
        for product in self._mentioned_products(normalized):
            words = normalized.split()
            variants = [product.name, *(alias.alias for alias in product.aliases.all())]
            positions = []
            for variant in variants:
                first = normalize_product_text(variant).split()[0]
                for index, word in enumerate(words):
                    if self._word_matches(first, word) or (
                        min(len(first), len(word)) >= 5
                        and SequenceMatcher(None, first, word).ratio() >= 0.75
                    ):
                        positions.append(index)
                        break
            if positions:
                positioned.append((min(positions), product))
        positioned.sort(key=lambda row: row[0])
        words = normalized.split()
        if len(positioned) >= 2:
            paired_actions = []
            paired_codes = set()
            for index, (start, product) in enumerate(positioned):
                end = positioned[index + 1][0] if index + 1 < len(positioned) else len(words)
                quantities = self._quantity_values(" ".join(words[start:end]))
                if not quantities or product.public_code in paired_codes:
                    continue
                paired_actions.append((
                    "set_cart_item",
                    {"product_code": product.public_code, "quantity": float(quantities[0])},
                ))
                paired_codes.add(product.public_code)
            if len(paired_actions) == len(positioned):
                return paired_actions

        # Follow-up like "2 кг" after a single server-backed product card.
        from apps.intake.models import ConversationMemory

        memory = ConversationMemory.objects.filter(
            channel=self.event.channel,
            external_user_id=self.event.external_user_id,
            conversation_key=self.event.conversation_key,
        ).first()
        options = list(memory.options or []) if memory else []
        quantities = self._quantity_values(raw_text)
        if len(options) == 1 and quantities and re.search(
            r"\b(?:кг|килограмм\w*|шт\.?|штук\w*|упаков\w*|банк\w*)\b",
            text,
        ):
            return [
                (
                    "set_cart_item",
                    {
                        "product_code": options[0]["code"],
                        "quantity": float(quantities[0]),
                    },
                )
            ]
        return None

    def referential_catalog_action(self):
        """Resolve pronouns and ordinals from the last server-backed product list.

        A model may answer a short follow-up without selecting a tool.  That is
        unsafe for price comparisons and makes phrases such as ``из них`` lose
        their meaning.  The memory contains product codes only from successful
        catalogue tools, so it can safely select which current cards to reread.
        """
        if not settings.AI_CONSULTANT_ENABLED:
            return None
        from apps.intake.models import ConversationMemory

        memory = ConversationMemory.objects.filter(
            channel=self.event.channel,
            external_user_id=self.event.external_user_id,
            conversation_key=self.event.conversation_key,
        ).first()
        options = list(memory.options or []) if memory else []
        options = [
            option for option in options
            if isinstance(option, dict) and option.get("code") and option.get("name")
        ][:4]
        if not options:
            return None

        text = normalize_product_text(self.event.raw_text)
        comparison = re.search(
            r"\b(?:сравн\w*|дешев\w*|дороже\w*|выгодн\w*|цен\w*)\b",
            text,
        )
        refers_to_list = bool(re.search(r"\b(?:из\s+них|этих|между\s+ними)\b", text))
        if len(options) >= 2 and (comparison or refers_to_list):
            return "compare_products", {
                "product_codes": [option["code"] for option in options]
            }

        ordinals = (
            (r"\b(?:перв\w*|1(?:-й|-я|-е)?)\b", 0),
            (r"\b(?:втор\w*|2(?:-й|-я|-е)?)\b", 1),
            (r"\b(?:трет\w*|3(?:-й|-я|-е)?)\b", 2),
            (r"\b(?:четверт\w*|4(?:-й|-я|-е)?)\b", 3),
        )
        for pattern, index in ordinals:
            if re.search(pattern, text) and index < len(options):
                return "search_products", {"query": options[index]["name"], "limit": 1}

        if len(options) == 1 and re.search(r"\b(?:этот|эта|это|него|не[её])\b", text):
            return "search_products", {"query": options[0]["name"], "limit": 1}
        return None

    def checkout_action(self):
        """Серверные переходы checkout, которые нельзя оставлять на память LLM."""
        text = normalize_product_text(self.event.raw_text)
        draft = self._draft()
        arguments = {}
        if "?" in self.event.raw_text or re.search(r"\b(?:убер|замен|сравн)\w*", text):
            return None

        delivery_requested = bool(
            re.search(r"\b(?:достав\w*|привез\w*)\b", text)
        )
        if re.fullmatch(r"(?:доставка|нужна доставка|доставк(?:ой|у))", text) or delivery_requested:
            arguments["receiving_type"] = ReceivingType.DELIVERY
        elif re.fullmatch(
            r"(?:самовывоз|самовывозом|заберу сам(?:а)?|нужен самовывоз)", text
        ):
            arguments["receiving_type"] = ReceivingType.PICKUP
        if re.fullmatch(
            r"(?:карта|картой|карта онлайн|картой онлайн|оплата картой(?: онлайн)?)",
            text,
        ) or re.search(r"\b(?:оплат\w*|оплач\w*|плат\w*)\b[^.!?;]*\bкарт\w*\b", text):
            arguments["payment_method"] = PaymentMethod.CARD_PREPAYMENT
        elif re.fullmatch(
            r"(?:наличные|наличными|наличные при получении|наличными при получении)",
            text,
        ):
            arguments["payment_method"] = PaymentMethod.CASH_ON_DELIVERY

        address = self._delivery_address_from_message(self.event.raw_text)
        if address and (delivery_requested or draft.receiving_type == ReceivingType.DELIVERY):
            arguments.update({
                "receiving_type": ReceivingType.DELIVERY,
                "delivery_address": address,
            })

        if re.search(r"\bзавтра\b", text):
            arguments["desired_date"] = (timezone.localdate() + timedelta(days=1)).isoformat()
        elif re.search(r"\bсегодня\b", text):
            arguments["desired_date"] = timezone.localdate().isoformat()

        email_match = re.search(
            r"(?<![\w.+-])[\w.+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+(?![\w.+-])",
            self.event.raw_text,
        )
        if email_match and draft.payment_method == PaymentMethod.CARD_PREPAYMENT:
            arguments["contact_email"] = email_match.group(0)

        if not arguments:
            return None
        return "configure_checkout", arguments

    @staticmethod
    def _delivery_address_from_message(raw_text: str) -> str:
        """Extract only an address, never the surrounding order request.

        A delivery provider must receive ``Москва, улица ...``, not a complete
        sentence that also contains products and payment method.
        """
        match = re.search(
            r"\b(?:по\s+адресу|адрес(?:у|\s+доставки)?|на)\s+(.+)",
            raw_text,
            flags=re.IGNORECASE,
        )
        candidate = match.group(1) if match else raw_text
        candidate = re.split(
            r"(?:[.!?;]|\b(?:оплат\w*|оплач\w*|плат\w*)\b)",
            candidate,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0].strip(" ,:-")
        normalized = normalize_product_text(candidate)
        has_street = re.search(
            r"\b(?:улиц\w*|ул\.?|проспект\w*|переулок\w*|бульвар\w*|"
            r"шоссе|набережн\w*|дом\w*|д\.)\b",
            normalized,
        )
        if not has_street or not re.search(r"\d", candidate):
            return ""
        return normalize_delivery_address(candidate)

    def preview_action(self):
        """Не отдаёт короткое согласие модели, если черновик уже готов к расчёту."""
        draft = self._draft()
        if draft.status != OrderDraftStatus.READY_FOR_PREVIEW:
            return None
        text = normalize_product_text(self.event.raw_text)
        if re.fullmatch(
            r"(?:да|рассчитай(?:те)?(?:\s+(?:итог|заказ))?|"
            r"покажи(?:те)?\s+(?:итог|расч[её]т)|я\s+уже\s+указал(?:а)?)",
            text,
        ):
            return "preview_order", {}
        return None

    def repeat_order_action(self):
        """Распознаёт явный запрос повтора без ожидания решения модели."""
        text = normalize_product_text(self.event.raw_text)
        if not (
            re.search(r"\bповтор\w*\b", text)
            and re.search(r"\bзаказ\w*\b", text)
        ):
            return None
        number = re.search(r"\b[0-9A-F]{10}\b", self.event.raw_text.upper())
        return "repeat_order", {
            "order_number": number.group(0) if number else None,
        }

    def cart_read_action(self):
        """Текущий состав берётся только из черновика backend."""
        text = normalize_product_text(self.event.raw_text)
        if re.search(
            r"\b(?:покаж\w*|како\w*)\b.*\b(?:состав\w*|корзин\w*)\b",
            text,
        ) or re.fullmatch(r"(?:что\s+)?(?:сейчас\s+)?в\s+корзине", text) or (
            self._draft().items.exists()
            and (
                re.search(r"\b(?:оформ\w*|продолж\w*)\b.*\bзаказ\w*\b", text)
                or re.search(r"\bуже\b.*\bдобав\w*\b.*\bкорзин\w*\b", text)
            )
        ):
            return "get_cart", {}
        return None

    def order_history_action(self):
        """История и статусы заказов всегда читаются из CRM, не из чата."""
        text = normalize_product_text(self.event.raw_text)
        if re.search(r"\bповтор\w*\b", text):
            return None
        asks_history = bool(
            re.search(r"\b(?:мои|последн\w*|предыдущ\w*|истори\w*)\b.*\bзаказ\w*\b", text)
            or re.search(r"\bзаказ\w*\b.*\b(?:статус\w*|оплачен\w*)\b", text)
            or re.search(r"\b(?:статус\w*|оплачен\w*)\b.*\bзаказ\w*\b", text)
            or re.fullmatch(r"(?:он|заказ)\s+оплачен\??", text)
        )
        if not asks_history:
            return None
        limit = 1 if re.search(r"\b(?:последн\w*|предыдущ\w*|он)\b", text) else 5
        return "list_customer_orders", {"limit": limit}

    def authoritative_fallback_action(self):
        """Запрещает factual-ответ модели без backend tool call."""
        for resolver in (
            self.cart_read_action,
            self.order_history_action,
            self.catalog_action,
        ):
            action = resolver()
            if action is not None:
                return action

        text = normalize_product_text(self.event.raw_text)
        products = self._mentioned_products(text)
        if products:
            # Если модель не вызвала инструмент даже для фразы с товаром,
            # безопасно показываем карточку/выборку CRM вместо ответа по памяти.
            names = {product.name for product in products}
            query = next(iter(names)) if len(names) == 1 else ""
            return "search_products", {"query": query, "limit": 30}
        return None

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
        if draft.payment_method == PaymentMethod.CARD_PREPAYMENT and not draft.contact_email:
            missing.append("contact_email")
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
        if not self._message_has_quantity(self.event.raw_text, args.quantity):
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
        mentioned = self._mentioned_products(self.event.raw_text)
        if mentioned and product.pk not in {item.pk for item in mentioned}:
            return {
                "ok": False,
                "error": {
                    "code": "product_mismatch",
                    "message": (
                        "Код товара не соответствует названию в сообщении клиента. "
                        "Повторно найдите указанный товар в каталоге."
                    ),
                    "mentioned_products": [
                        {"code": item.public_code, "name": item.name}
                        for item in mentioned
                    ],
                },
            }
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
        draft = self._refresh_state(draft)
        UnifiedCartBridge.draft_to_cart(draft)
        return self._cart_payload(draft)

    @staticmethod
    def _message_has_quantity(text: str, expected=None) -> bool:
        mentioned = AssistantToolExecutor._quantity_values(text)
        if expected is None:
            return bool(mentioned)
        return Decimal(str(expected)) in mentioned

    def _tool_remove_cart_item(self, args: RemoveCartItemArgs) -> dict:
        draft = self._prepare_change()
        deleted, _ = draft.items.filter(product__public_code=args.product_code).delete()
        if not deleted:
            raise ValueError("Товар отсутствует в корзине")
        draft = self._refresh_state(draft)
        UnifiedCartBridge.draft_to_cart(draft)
        return self._cart_payload(draft)

    def _tool_configure_checkout(self, args: ConfigureCheckoutArgs) -> dict:
        if all(
            getattr(args, field) is None
            for field in (
                "receiving_type",
                "delivery_address",
                "desired_date",
                "payment_method",
                "contact_phone",
                "contact_email",
                "customer_comment",
            )
        ):
            return {
                "ok": False,
                "error": {
                    "code": "checkout_value_required",
                    "message": "Да, параметры можно изменить. Укажите новое значение.",
                },
            }
        draft = self._prepare_change()
        updates = {}
        for field in ("receiving_type", "delivery_address", "desired_date", "payment_method", "customer_comment"):
            value = getattr(args, field)
            if value is not None:
                if field == "delivery_address":
                    value = normalize_delivery_address(value)
                if field == "desired_date":
                    try:
                        value = date.fromisoformat(value)
                    except ValueError as exc:
                        raise ValueError("Дата получения должна быть в формате ГГГГ-ММ-ДД") from exc
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
        draft = self._refresh_state(draft)
        UnifiedCartBridge.draft_to_cart(draft)
        return self._cart_payload(draft)

    def _tool_preview_order(self, _args: EmptyArgs) -> dict:
        draft = self._refresh_state(self._draft())
        if draft.missing_fields:
            # После имени, телефона и адреса можно рассчитать доставку до
            # выбора оплаты. Временный способ «картой при получении» нужен
            # Яндексу только для тарифа и не сохраняется как выбор пользователя.
            if (
                draft.receiving_type == ReceivingType.DELIVERY
                and set(draft.missing_fields) == {"payment_method"}
            ):
                return self._preliminary_delivery_preview(draft)
            return {
                "ok": False,
                "error": {
                    "code": "draft_incomplete",
                    "message": self._missing_fields_message(draft.missing_fields),
                    "missing_fields": draft.missing_fields,
                },
                "cart": self._cart_payload(draft),
            }
        draft = DraftPricingService.preview(draft)
        if draft.status != OrderDraftStatus.AWAITING_CONFIRMATION:
            error = {
                "code": "preview_failed",
                "message": "Не удалось выполнить актуальный расчёт",
                "missing_fields": draft.missing_fields,
            }
            failed_quote = (
                draft.delivery_quotes.filter(status=DeliveryQuoteStatus.FAILED)
                .order_by("-created_at", "-id")
                .first()
            )
            if failed_quote is not None:
                code = failed_quote.error_code or "delivery_quote_failed"
                if code == "no_delivery_options":
                    message = (
                        "Яндекс Доставка не предложила вариант для указанных "
                        "адреса и параметров заказа. Измените адрес, выберите "
                        "самовывоз или повторите расчёт позже."
                    )
                elif code.isdigit() and int(code) >= 500:
                    message = (
                        "Тестовый контур Яндекс Доставки временно недоступен. "
                        "Корзина и адрес сохранены: повторите расчёт позже или "
                        "выберите самовывоз."
                    )
                else:
                    message = (
                        "Не удалось рассчитать доставку. Проверьте адрес и "
                        "повторите расчёт позже."
                    )
                error.update(
                    {
                        "code": code,
                        "message": message,
                        "provider_message": failed_quote.error_message,
                    }
                )
            return {
                "ok": False,
                "error": error,
                # Даже при недоступности внешнего тарифа клиент должен видеть,
                # что товары и параметры сохранены в текущем черновике.
                "cart": self._cart_payload(draft),
            }
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

    def _preliminary_delivery_preview(self, draft) -> dict:
        OrderDraft.objects.filter(pk=draft.pk).update(
            payment_method=PaymentMethod.CARD_ON_DELIVERY
        )
        try:
            previewed = DraftPricingService.preview(draft)
        finally:
            OrderDraft.objects.filter(pk=draft.pk).update(payment_method="")

        previewed.refresh_from_db()
        if previewed.status != OrderDraftStatus.AWAITING_CONFIRMATION:
            failed_quote = (
                previewed.delivery_quotes.filter(status=DeliveryQuoteStatus.FAILED)
                .order_by("-created_at", "-id")
                .first()
            )
            return {
                "ok": False,
                "error": {
                    "code": (failed_quote.error_code if failed_quote else "delivery_quote_failed"),
                    "message": "Не удалось рассчитать доставку. Проверьте адрес и повторите расчёт позже.",
                },
                "cart": self._cart_payload(self._refresh_state(previewed)),
            }

        OrderDraft.objects.filter(pk=previewed.pk).update(
            status=OrderDraftStatus.NEEDS_CLARIFICATION,
            missing_fields=["payment_method"],
            previewed_revision=None,
            confirmed_revision=None,
        )
        previewed.refresh_from_db()
        result = self._cart_payload(previewed)
        result["preliminary_delivery_quote"] = {
            "delivery_cost": str(previewed.delivery_cost),
            "delivery_days": (
                previewed.delivery_quotes.filter(status=DeliveryQuoteStatus.SUCCEEDED)
                .order_by("-created_at")
                .values_list("delivery_days", flat=True)
                .first()
            ),
            "total_amount": str(previewed.total_amount),
        }
        return result

    @staticmethod
    def _missing_fields_message(missing_fields) -> str:
        missing = set(missing_fields or [])
        if "items" in missing:
            return "Укажите товары и их количество."
        if "receiving_type" in missing:
            return "Выберите способ получения: доставка или самовывоз."
        if "delivery_address" in missing:
            return "Укажите адрес доставки."
        if "customer" in missing:
            return (
                "Для оформления заказа прошу сообщить Ваше имя и контактный "
                "телефон в формате 9XXXXXXXXX."
            )
        if "contact_phone" in missing:
            return "Укажите контактный телефон для доставки."
        if "payment_method" in missing:
            return "Выберите способ оплаты: наличными при получении или картой онлайн."
        if "contact_email" in missing:
            return "Для онлайн-оплаты укажите email для электронного чека ЮKassa."
        if "delivery_quote" in missing:
            return (
                "Не удалось рассчитать доставку. Измените адрес, выберите "
                "самовывоз или повторите расчёт позже."
            )
        return "Для расчёта не хватает обязательных данных."

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
        catalog_terms = [
            {
                "code": product.public_code,
                "name": product.name,
                "description": product.description,
                "aliases": [alias.alias for alias in product.aliases.all()],
                "unit": product.get_unit_display().lower(),
                "min_quantity": str(product.min_quantity),
                "price": str(product.base_price),
                "currency": "RUB",
            }
            for product in CatalogService.get_active_products().prefetch_related("aliases")
        ]
        from apps.intake.models import ConversationMemory
        memory = ConversationMemory.objects.filter(channel=self.event.channel,
            external_user_id=self.event.external_user_id, conversation_key=self.event.conversation_key).first()
        return {
            "conversation": {"last_question": memory.last_question, "options": memory.options,
                "selected_product_code": memory.selected_product_code, "expected_fields": memory.expected_fields} if memory else {},
            "cart": self._cart_payload(),
            "recent_product_search": recent_search or None,
            # Полный снимок нужен только для смыслового выбора кодов. Цены и
            # наличие перед ответом всё равно перечитывает backend-инструмент.
            "catalog_snapshot": catalog_terms,
        }

    def _visible_orders(self):
        from apps.orders.access import OrderAccessService
        return OrderAccessService.visible(channel=self.event.channel, external_user_id=self.event.external_user_id)

    def _tool_list_customer_orders(self, args: ListOrdersArgs) -> dict:
        draft = self._draft()
        if draft.customer_id is None:
            return {"ok": False, "error": {"code": "customer_required", "message": "Сначала нужен телефон или email клиента"}}
        orders = self._visible_orders().prefetch_related("items").order_by("-created_at")[: args.limit]
        return {"ok": True, "orders": [self._order_payload(order) for order in orders]}

    def cancellation_action(self):
        """Возвращает безопасное детерминированное действие для фраз об отмене."""
        text = normalize_product_text(self.event.raw_text)
        previous_type = (
            AssistantMessage.objects.filter(
                conversation_key=self.event.conversation_key,
                event__channel=self.event.channel,
                event__external_user_id=self.event.external_user_id,
                role="assistant",
            )
            .exclude(event=self.event)
            .order_by("-created_at", "-id")
            .values_list("response_type", flat=True)
            .first()
        )
        pending_choice = previous_type == "cancellation_choice"
        has_cancel_verb = bool(re.search(r"\b(?:отмен\w*|отказ\w*)\b", text)) or bool(
            re.search(r"\bочист\w*\b", text) and re.search(r"\bкорзин\w*\b", text)
        )
        current_scope = bool(
            re.search(r"\b(?:корзин\w*|черновик\w*|текущ\w*\s+(?:заказ\w*|оформлен\w*))\b", text)
        )
        placed_scope = bool(
            re.search(r"\b(?:оформлен\w*|создан\w*|оплачен\w*)\s+заказ\w*\b", text)
        )
        order_number = re.search(r"\b[0-9A-F]{10}\b", self.event.raw_text.upper())

        if current_scope and (has_cancel_verb or pending_choice):
            return "clear_cart", {"confirmation": "clear_current_cart"}
        if order_number and (has_cancel_verb or pending_choice):
            return "cancel_order", {
                "order_number": order_number.group(0),
                "confirmation": "cancel_placed_order",
            }
        if placed_scope and pending_choice:
            options = self._cancellation_options_payload()
            orders = options["active_orders"]
            if len(orders) == 1:
                return "cancel_order", {
                    "order_number": orders[0]["number"],
                    "confirmation": "cancel_placed_order",
                }
            return "get_cancellation_options", {}
        if has_cancel_verb or (pending_choice and text in {"корзину", "заказ", "оформленный"}):
            return "get_cancellation_options", {}
        return None

    def stale_cart_action(self):
        """Останавливает новый диалог, если наполненная корзина старше таймаута."""
        if not self._draft().items.exists():
            return None
        latest = (
            AssistantMessage.objects.filter(
                conversation_key=self.event.conversation_key,
                event__channel=self.event.channel,
                event__external_user_id=self.event.external_user_id,
                role="assistant",
            )
            .exclude(event=self.event)
            .order_by("-created_at", "-id")
            .first()
        )
        text = normalize_product_text(self.event.raw_text)
        if latest and latest.response_type == "stale_cart_choice":
            if re.search(r"\b(?:очист\w*|неактуал\w*|сброс\w*|заново)\b", text):
                return "clear_cart", {"confirmation": "clear_current_cart"}
            if re.search(r"\b(?:актуал\w*|продолж\w*|остав\w*|сохран\w*|да)\b", text):
                return "keep_stale_cart", {}
            return "prompt_stale_cart", {}
        if latest is None:
            return None
        timeout = timedelta(seconds=settings.AI_ASSISTANT_STALE_CART_SECONDS)
        if timezone.now() - latest.created_at >= timeout:
            return "prompt_stale_cart", {}
        return None

    def _cancellation_options_payload(self) -> dict:
        draft = self._draft()
        cart = self._cart_payload(draft)
        active_orders = []
        if draft.customer_id:
            orders = (
                self._visible_orders().filter(
                    order_status__in=[
                        OrderStatus.NEW,
                        OrderStatus.ASSEMBLED,
                        OrderStatus.DELIVERING,
                    ],
                )
                .order_by("-created_at")
            )
            active_orders = [
                {
                    "number": order.public_number,
                    "status": order.order_status,
                    "status_label": order.get_order_status_display(),
                    "payment_status": order.payment_status,
                    "payment_status_label": order.get_payment_status_display(),
                    "total_amount": str(order.total_amount),
                    "automatic_cancellation": (
                        order.order_status == OrderStatus.NEW
                        and order.payment_status != PaymentStatus.PAID
                    ),
                }
                for order in orders
            ]
        return {
            "ok": True,
            "current_cart": {
                "has_items": bool(cart["items"]),
                "items": cart["items"],
                "status": cart["status"],
            },
            "active_orders": active_orders,
        }

    def _tool_get_cancellation_options(self, _args: EmptyArgs) -> dict:
        return self._cancellation_options_payload()

    def _tool_clear_cart(self, _args: ClearCartArgs) -> dict:
        action = self.cancellation_action()
        stale_action = self.stale_cart_action()
        if not (
            (action is not None and action[0] == "clear_cart")
            or (stale_action is not None and stale_action[0] == "clear_cart")
        ):
            return {
                "ok": False,
                "error": {
                    "code": "explicit_cart_cancellation_required",
                    "message": "Сначала явно подтвердите, что нужно очистить текущую корзину.",
                },
            }
        draft = self._prepare_change()
        deleted, _ = draft.items.all().delete()
        OrderDraft.objects.filter(pk=draft.pk).update(
            receiving_type="",
            delivery_address="",
            payment_method="",
            desired_date=None,
            desired_time_interval="",
            customer_comment="",
            missing_fields=["items"],
            intent=OrderIntent.CREATE_ORDER,
            status=OrderDraftStatus.COLLECTING,
        )
        draft.refresh_from_db()
        UnifiedCartBridge.draft_to_cart(draft)
        return {
            "ok": True,
            "cleared_items": deleted,
            "message": "Текущее оформление отменено, корзина очищена.",
        }

    def _tool_cancel_order(self, args: CancelOrderArgs) -> dict:
        action = self.cancellation_action()
        if (
            action is None
            or action[0] != "cancel_order"
            or action[1].get("order_number") != args.order_number
        ):
            return {
                "ok": False,
                "error": {
                    "code": "explicit_order_cancellation_required",
                    "message": "Сначала явно выберите оформленный заказ для отмены.",
                },
            }
        draft = self._draft()
        if draft.customer_id is None:
            return {
                "ok": False,
                "error": {"code": "customer_required", "message": "Не удалось определить клиента."},
            }
        order = self._visible_orders().filter(
            public_number=args.order_number,
        ).first()
        if order is None:
            return {
                "ok": False,
                "error": {"code": "order_not_found", "message": "Активный заказ с таким номером не найден."},
            }
        if order.order_status in {OrderStatus.COMPLETED, OrderStatus.CANCELLED}:
            return {
                "ok": False,
                "error": {"code": "order_not_active", "message": "Этот заказ уже завершён или отменён."},
            }
        if order.payment_status == PaymentStatus.PAID:
            return {
                "ok": False,
                "error": {
                    "code": "paid_order_requires_manager",
                    "message": "Заказ уже оплачен. Для отмены и возврата средств потребуется менеджер.",
                },
            }
        if order.order_status != OrderStatus.NEW:
            return {
                "ok": False,
                "error": {
                    "code": "order_in_fulfillment",
                    "message": "Заказ уже передан в исполнение. Для отмены потребуется менеджер.",
                },
            }
        payment = (
            order.payments.filter(state__in=[PaymentState.PENDING, PaymentState.WAITING_FOR_CAPTURE])
            .order_by("-created_at")
            .first()
        )
        if payment is not None:
            PaymentService.cancel_payment(payment)
            order.refresh_from_db()
            if order.payment_status == PaymentStatus.PAID:
                return {"ok": False, "error": {"code": "paid_order_requires_manager", "message": "Оплата уже прошла. Отмену и возврат проверит менеджер."}}
        source = self.event.channel if self.event.channel in StatusChangeSource.values else StatusChangeSource.AUTOMATIC
        OrderService.change_status(
            order,
            OrderStatus.CANCELLED,
            source=source,
            comment="Отменён клиентом через AI-ассистента",
        )
        return {
            "ok": True,
            "order_number": order.public_number,
            "order_status": OrderStatus.CANCELLED,
            "message": f"Заказ {order.public_number} отменён.",
        }

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
        text = normalize_product_text(self.event.raw_text)
        if not (
            re.search(r"\bповтор\w*\b", text)
            and re.search(r"\bзаказ\w*\b", text)
        ):
            return {
                "ok": False,
                "error": {
                    "code": "repeat_intent_required",
                    "message": (
                        "Повтор заказа выполняется только по явному запросу клиента."
                    ),
                },
            }
        draft = self._prepare_change()
        if draft.customer_id is None:
            return {"ok": False, "error": {"code": "customer_required", "message": "Для повтора заказа нужно идентифицировать клиента"}}
        orders = self._visible_orders().prefetch_related("items__product").order_by("-created_at")
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
        draft = self._refresh_state(draft)
        UnifiedCartBridge.draft_to_cart(draft)
        return {"ok": True, "repeated_order": order.public_number, "cart": self._cart_payload(draft), "requires_new_preview": True}

    @staticmethod
    def _explicit_confirmation(event) -> bool:
        if event.kind == "callback" and bool(event.raw_payload.get("confirmed")):
            return True
        source = event.raw_text
        if getattr(event, "channel", "") == "email" and "\n\n" in source:
            source = source.split("\n\n", 1)[1]
        if getattr(event, "channel", "") == "email":
            # Mail clients often append the whole previous thread. Only the
            # first non-empty paragraph is the customer's new reply; quoted
            # text must not invalidate an otherwise clear "Согласен".
            reply_lines = []
            for line in source.replace("\r\n", "\n").split("\n"):
                stripped = line.strip()
                if reply_lines and (
                    not stripped
                    or stripped.startswith(">")
                    or re.match(r"^(?:on .+wrote:|в .+писал[аи]?:)", stripped, re.I)
                ):
                    break
                if stripped:
                    reply_lines.append(stripped)
            source = " ".join(reply_lines)
        text = normalize_product_text(source)
        return bool(
            re.fullmatch(
                r"(?:да(?:\s+(?:согласен|согласна|подтверждаю)(?:\s+(?:этот\s+)?заказ)?)?"
                r"|(?:согласен|согласна|подтверждаю|подтверждаем|оформляйте|оформите|оформляем)(?:\s+(?:этот\s+)?заказ)?"
                r"|(?:ок|ok|окей)"
                r"|можно\s+оформля(?:ть|йте)(?:\s+(?:этот\s+)?заказ)?"
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
            try:
                payment_url = PaymentService.ensure_payment_link(order).confirmation_url
            except PaymentError:
                return {"ok": True, "order_number": order.public_number, "total_amount": str(order.total_amount),
                    "payment_url": "", "payment_pending": True, "message": "Заказ сохранён. Ссылка оплаты временно недоступна; запросите её повторно."}
        return {"ok": True, "order_number": order.public_number, "total_amount": str(order.total_amount), "payment_url": payment_url, "message": "Ваш заказ оформлен. При необходимости наш менеджер свяжется с вами."}

    def _tool_get_payment_link(self, args: PaymentLinkArgs) -> dict:
        draft = self._draft()
        if draft.customer_id is None:
            return {"ok": False, "error": {"code": "customer_required", "message": "Сначала нужно идентифицировать клиента"}}
        order = self._visible_orders().filter(
            public_number=args.order_number,
        ).first()
        if order is None:
            return {"ok": False, "error": {"code": "order_not_found", "message": "Заказ клиента не найден"}}
        if order.payment_method != PaymentMethod.CARD_PREPAYMENT:
            return {"ok": False, "error": {"code": "online_payment_not_selected", "message": "Для заказа не выбрана онлайн-оплата"}}
        payment = PaymentService.ensure_payment_link(order)
        return {"ok": True, "order_number": order.public_number, "payment_status": order.payment_status, "payment_url": payment.confirmation_url}
