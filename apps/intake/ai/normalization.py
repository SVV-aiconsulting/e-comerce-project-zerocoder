"""Детерминированные инварианты поверх недоверенного structured output LLM."""
import re

from apps.common.enums import PaymentMethod
from apps.intake.enums import OrderIntent

ONLINE_PAYMENT_PATTERN = re.compile(
    r"\b(?:онлайн|предоплат\w*|по\s+ссылк\w*|сейчас\s+карт\w*)\b",
    re.IGNORECASE,
)
CARD_ON_RECEIPT_PATTERN = re.compile(
    r"\b(?:карт\w*\s+при\s+получени\w*|карт\w*\s+курьер\w*)\b",
    re.IGNORECASE,
)
CASH_PATTERN = re.compile(r"\bналичн\w*\b", re.IGNORECASE)


def normalize_extraction(event, draft, extraction):
    """Исправить только выводимые из состояния workflow однозначные значения."""
    updates = {}
    if (
        extraction.intent == OrderIntent.MODIFY_ORDER
        and extraction.items
        and not draft.items.exists()
    ):
        updates["intent"] = OrderIntent.CREATE_ORDER

    raw_text = event.raw_text
    if ONLINE_PAYMENT_PATTERN.search(raw_text):
        updates["payment_method"] = PaymentMethod.CARD_PREPAYMENT
    elif CARD_ON_RECEIPT_PATTERN.search(raw_text):
        updates["payment_method"] = PaymentMethod.CARD_ON_DELIVERY
    elif CASH_PATTERN.search(raw_text):
        updates["payment_method"] = PaymentMethod.CASH_ON_DELIVERY

    return extraction.model_copy(update=updates) if updates else extraction
