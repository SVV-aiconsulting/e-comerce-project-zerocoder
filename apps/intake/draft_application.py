"""Серверное применение проверенного AI-результата к черновику заказа."""
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.common.enums import ReceivingType
from apps.intake.catalog_matching import CatalogMatcher
from apps.intake.enums import ItemMatchStatus, OrderDraftStatus, OrderIntent
from apps.intake.models import OrderDraft, OrderDraftItem
from apps.intake.services import OrderDraftService


class DraftExtractionApplier:
    """LLM предлагает факты; этот сервис проверяет и записывает их в ORM."""

    @classmethod
    @transaction.atomic
    def apply(cls, draft: OrderDraft, extraction) -> OrderDraft:
        if extraction.confirmation == "confirm":
            return OrderDraftService.confirm(draft)

        changed = OrderDraftService.record_change(draft)
        locked = OrderDraft.objects.select_for_update().get(pk=changed.pk)
        locked.intent = extraction.intent

        if extraction.intent == OrderIntent.CANCEL_ORDER:
            locked.status = OrderDraftStatus.CANCELLED
            locked.missing_fields = []
            locked.save(
                update_fields=["intent", "status", "missing_fields", "updated_at"]
            )
            return locked

        if extraction.confirmation == "reject":
            locked.status = OrderDraftStatus.CANCELLED
            locked.missing_fields = []
            locked.save(
                update_fields=["intent", "status", "missing_fields", "updated_at"]
            )
            return locked

        cls._apply_scalar_fields(locked, extraction)
        if extraction.intent in (
            OrderIntent.CREATE_ORDER,
            OrderIntent.MODIFY_ORDER,
            OrderIntent.PRODUCT_QUESTION,
        ) and extraction.items:
            cls._replace_items(locked, extraction.items)

        missing_fields = cls._collect_missing_fields(locked, extraction)
        locked.missing_fields = missing_fields
        locked.status = (
            OrderDraftStatus.NEEDS_CLARIFICATION
            if missing_fields
            else OrderDraftStatus.READY_FOR_PREVIEW
        )
        locked.save(
            update_fields=[
                "intent",
                "receiving_type",
                "desired_date",
                "desired_time_interval",
                "delivery_address",
                "payment_method",
                "customer_comment",
                "missing_fields",
                "status",
                "updated_at",
            ]
        )
        return locked

    @staticmethod
    def _apply_scalar_fields(draft, extraction):
        for field in (
            "receiving_type",
            "desired_date",
            "desired_time_interval",
            "delivery_address",
            "payment_method",
            "customer_comment",
        ):
            value = getattr(extraction, field)
            if value is not None:
                setattr(draft, field, value)

    @classmethod
    def _replace_items(cls, draft, extracted_items):
        draft.items.all().delete()
        items = []
        for line_number, extracted in enumerate(extracted_items, start=1):
            match = CatalogMatcher.match(extracted.raw_product_name)
            quantity = (
                Decimal(str(extracted.quantity))
                if extracted.quantity is not None
                else None
            )
            errors = []
            status = match.status
            if match.product and extracted.unit and extracted.unit != match.product.unit:
                status = ItemMatchStatus.INVALID
                errors.append("unit_mismatch")
            if match.product and quantity is not None and quantity < match.product.min_quantity:
                errors.append("below_min_quantity")

            items.append(
                OrderDraftItem(
                    draft=draft,
                    line_number=line_number,
                    raw_product_name=extracted.raw_product_name,
                    requested_quantity=quantity,
                    requested_unit=extracted.unit or "",
                    product=match.product,
                    match_status=status,
                    candidate_product_ids=[product.pk for product in match.candidates],
                    resolution_source=match.source,
                    resolution_confidence=match.confidence,
                    validation_errors=errors,
                )
            )
        OrderDraftItem.objects.bulk_create(items)

    @staticmethod
    def _collect_missing_fields(draft, extraction):
        # Порядок — это управляемый sales-сценарий: сначала состав заказа,
        # затем получение, доставка, оплата и только перед оформлением контакт.
        missing = []
        items = list(draft.items.order_by("line_number"))
        if not items and extraction.intent in (
            OrderIntent.CREATE_ORDER,
            OrderIntent.MODIFY_ORDER,
            OrderIntent.PRODUCT_QUESTION,
        ):
            missing.append("items")
        for index, item in enumerate(items):
            prefix = f"items.{index}"
            if item.match_status != ItemMatchStatus.MATCHED:
                missing.append(f"{prefix}.product")
            if item.requested_quantity is None:
                missing.append(f"{prefix}.quantity")
            if not item.requested_unit:
                missing.append(f"{prefix}.unit")
            if item.validation_errors:
                missing.append(f"{prefix}.validation")

        # Информационный вопрос по каталогу не запускает оформление сам по себе.
        if extraction.intent == OrderIntent.PRODUCT_QUESTION:
            if not missing:
                missing.append("sales_intent")
            return list(dict.fromkeys(missing))

        if extraction.intent == OrderIntent.ORDER_STATUS:
            return ["order_status"]
        if extraction.intent not in (OrderIntent.CREATE_ORDER, OrderIntent.MODIFY_ORDER):
            return ["assistant_intent"]
        if not draft.receiving_type:
            missing.append("receiving_type")
        if (
            draft.receiving_type == ReceivingType.DELIVERY
            and not draft.delivery_address.strip()
        ):
            missing.append("delivery_address")
        if (
            settings.YANDEX_DELIVERY_ENABLED
            and draft.receiving_type == ReceivingType.DELIVERY
            and not draft.contact_phone
        ):
            missing.append("contact_phone")
        if not draft.payment_method:
            missing.append("payment_method")
        elif (
            settings.YANDEX_DELIVERY_ENABLED
            and draft.receiving_type == ReceivingType.DELIVERY
            and draft.payment_method == "cash_on_delivery"
        ):
            missing.append("delivery_payment_method")
        if draft.customer_id is None:
            missing.append("customer")
        if draft.desired_date and draft.desired_date < timezone.localdate():
            missing.append("desired_date")
        return list(dict.fromkeys(missing))
