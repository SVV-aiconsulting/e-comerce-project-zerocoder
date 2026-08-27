"""Тесты ensure_identified и recovery сессии."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.api.errors import BackendUnavailableError
from bot.services.identify import ensure_identified, require_identified_callback
from bot.services.session import SESSION_KEY, is_identified


class FakeFSMContext:
    def __init__(self, data: dict | None = None) -> None:
        self._data = dict(data or {})

    async def get_data(self) -> dict:
        return self._data

    async def update_data(self, data: dict) -> None:
        self._data.update(data)

    async def set_state(self, state) -> None:
        return None


def _make_user(user_id: int = 12345):
    user = MagicMock()
    user.id = user_id
    user.username = "buyer"
    user.first_name = "Покупатель"
    user.last_name = ""
    return user


def _make_message(user_id: int = 12345):
    message = MagicMock()
    message.from_user = _make_user(user_id)
    return message


def _make_callback(user_id: int = 12345):
    callback = MagicMock()
    callback.from_user = _make_user(user_id)
    callback.message = MagicMock()
    callback.message.answer = AsyncMock()
    callback.answer = AsyncMock()
    return callback


@pytest.mark.asyncio
async def test_ensure_identified_returns_existing_session():
    state = FakeFSMContext(
        {
            SESSION_KEY: {
                "external_user_id": "12345",
                "customer_id": 7,
                "customer_public_code": "CL-7",
            }
        }
    )
    api = AsyncMock()

    session = await ensure_identified(_make_message(), state, api)

    assert session["customer_id"] == 7
    api.identify_customer.assert_not_called()


@pytest.mark.asyncio
async def test_ensure_identified_restores_session_after_memory_loss():
    state = FakeFSMContext()
    api = AsyncMock()
    api.identify_customer.return_value = {
        "status": "identified",
        "customer_id": 42,
        "customer_public_code": "CL-42",
        "is_new_customer": False,
    }

    session = await ensure_identified(_make_message(), state, api)

    assert is_identified(session)
    assert session["customer_id"] == 42
    api.identify_customer.assert_awaited_once()


@pytest.mark.asyncio
async def test_ensure_identified_raises_backend_unavailable():
    state = FakeFSMContext()
    api = AsyncMock()
    api.identify_customer.side_effect = BackendUnavailableError("connection failed")

    with pytest.raises(BackendUnavailableError):
        await ensure_identified(_make_message(), state, api)


@pytest.mark.asyncio
async def test_ensure_identified_returns_none_for_registration_required():
    state = FakeFSMContext()
    api = AsyncMock()
    api.identify_customer.return_value = {
        "status": "registration_required",
        "registration_required": True,
    }

    session = await ensure_identified(_make_message(), state, api)

    assert session is None


@pytest.mark.asyncio
async def test_require_identified_callback_handles_backend_error():
    state = FakeFSMContext()
    api = AsyncMock()
    api.identify_customer.side_effect = BackendUnavailableError("down")
    callback = _make_callback()

    with patch("bot.handlers.common.answer_api_error", new_callable=AsyncMock) as mock_answer:
        session = await require_identified_callback(callback, state, api)

    assert session is None
    mock_answer.assert_awaited_once()
    callback.answer.assert_awaited_once()
