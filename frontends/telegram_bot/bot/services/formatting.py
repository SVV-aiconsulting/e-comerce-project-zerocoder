"""Форматирование данных для отображения в Telegram."""
from datetime import datetime
from decimal import Decimal


def format_price(value: str | Decimal | float) -> str:
    amount = Decimal(str(value)).quantize(Decimal("0.01"))
    return f"{amount} ₽"


def format_quantity(value: str | Decimal | float) -> str:
    qty = Decimal(str(value)).normalize()
    text = format(qty, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def format_datetime(value: str) -> str:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.strftime("%d.%m.%Y %H:%M")
    except ValueError:
        return value


def truncate_text(text: str, limit: int = 500) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."
