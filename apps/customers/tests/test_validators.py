import pytest
from django.core.exceptions import ValidationError

from apps.customers.validators import (
    EMAIL_ERROR_MESSAGE,
    PHONE_ERROR_MESSAGE,
    normalize_email,
    normalize_phone,
    validate_phone,
)


@pytest.mark.parametrize(
    "phone",
    ["79991234567", "+7 999 123-45-67", "8 (999) 123 45 67", "9991234567"],
)
def test_valid_phone(phone):
    validate_phone(phone)


@pytest.mark.parametrize(
    ("phone", "expected"),
    [
        ("+7 999 123-45-67", "79991234567"),
        ("8 (999) 123-45-67", "79991234567"),
        ("9991234567", "79991234567"),
        ("79991234567", "79991234567"),
    ],
)
def test_normalize_phone_to_single_canonical_format(phone, expected):
    assert normalize_phone(phone) == expected


@pytest.mark.parametrize(
    "phone",
    [
        "+7912345678901",
        "8912345678901",
        "91234567890",
        "1234567890",
        "912345678901",
        "912-345-67-8900",
        "abc",
        "",
    ],
)
def test_invalid_phone(phone):
    with pytest.raises(ValidationError) as exc_info:
        validate_phone(phone)
    assert PHONE_ERROR_MESSAGE in str(exc_info.value)


def test_normalize_email():
    assert normalize_email(" Anna.User@Example.COM ") == "anna.user@example.com"


def test_invalid_email():
    with pytest.raises(ValidationError) as exc_info:
        normalize_email("not-an-email")
    assert EMAIL_ERROR_MESSAGE in str(exc_info.value)
