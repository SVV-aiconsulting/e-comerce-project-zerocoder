import re

from django.core.exceptions import ValidationError
from django.core.validators import validate_email

PHONE_REGEX = re.compile(r"^\d{11}$")
PHONE_ERROR_MESSAGE = "Телефон должен быть указан в формате 79991234567"
EMAIL_ERROR_MESSAGE = "Укажите корректный email-адрес"


def normalize_phone(value: str) -> str:
    """Нормализовать телефон к канону РФ: 7XXXXXXXXXX."""
    if not isinstance(value, str):
        raise ValidationError(PHONE_ERROR_MESSAGE)

    digits = re.sub(r"\D", "", value)
    if len(digits) == 10 and digits.startswith("9"):
        digits = f"7{digits}"
    elif len(digits) == 11 and digits.startswith("8"):
        digits = f"7{digits[1:]}"
    elif len(digits) == 11 and digits.startswith("7"):
        pass

    if not PHONE_REGEX.match(digits) or not digits.startswith("7"):
        raise ValidationError(PHONE_ERROR_MESSAGE)
    return digits


def validate_phone(value: str) -> None:
    """Проверить, что телефон приводится к допустимому формату."""
    normalize_phone(value)


def normalize_email(value: str) -> str:
    """Нормализовать email для сопоставления контактов и email-канала."""
    if not isinstance(value, str):
        raise ValidationError(EMAIL_ERROR_MESSAGE)
    normalized = value.strip().casefold()
    try:
        validate_email(normalized)
    except ValidationError as exc:
        raise ValidationError(EMAIL_ERROR_MESSAGE) from exc
    return normalized
