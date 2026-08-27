import pytest

from bot.api.errors import ApiError
from bot.services.error_messages import (
    CONFLICT_USER_MESSAGE,
    PHONE_INVALID_USER_MESSAGE,
    user_message_for_error,
    is_phone_validation_error,
)
from bot.api.errors import BackendUnavailableError


def test_phone_validation_error_detection():
    exc = ApiError(
        "validation_error",
        "Ошибка валидации",
        {"phone": ["Телефон должен быть указан в формате 79991234567"]},
    )
    assert is_phone_validation_error(exc)
    assert user_message_for_error(exc) == PHONE_INVALID_USER_MESSAGE


def test_channel_identity_conflict():
    exc = ApiError("channel_identity_conflict", "conflict")
    assert user_message_for_error(exc) == CONFLICT_USER_MESSAGE


def test_backend_unavailable():
    assert "недоступен" in user_message_for_error(BackendUnavailableError()).lower()


def test_customer_context_mismatch():
    exc = ApiError("customer_context_mismatch", "mismatch")
    assert "/start" in user_message_for_error(exc)
