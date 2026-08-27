"""Тесты guard-ов checkout-сессии после потери FSM."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.types import CallbackQuery

from bot.services.error_messages import CHECKOUT_SESSION_STALE_MESSAGE
from bot.services.session import SESSION_KEY


class FakeFSMContext:
    def __init__(self, data: dict | None = None) -> None:
        self._data = dict(data or {})

    async def get_data(self) -> dict:
        return self._data

    async def update_data(self, data: dict) -> None:
        self._data.update(data)

    async def set_state(self, state) -> None:
        return None


def _make_callback(user_id: int = 12345):
    callback = MagicMock()
    callback.__class__ = CallbackQuery
    callback.from_user = MagicMock()
    callback.from_user.id = user_id
    callback.message = MagicMock()
    callback.message.answer = AsyncMock()
    callback.answer = AsyncMock()
    return callback


@pytest.mark.asyncio
async def test_confirm_order_rejects_stale_checkout_session():
    from bot.handlers.checkout import callback_confirm_order

    state = FakeFSMContext(
        {
            SESSION_KEY: {
                "external_user_id": "12345",
                "customer_id": 1,
                "customer_public_code": "CL-1",
            }
        }
    )
    callback = _make_callback()
    api = AsyncMock()

    identified_session = {
        "external_user_id": "12345",
        "customer_id": 1,
        "customer_public_code": "CL-1",
    }

    with patch(
        "bot.handlers.checkout.require_identified_callback",
        new_callable=AsyncMock,
        return_value=identified_session,
    ):
        await callback_confirm_order(callback, state, api)

    callback.message.answer.assert_awaited_once_with(CHECKOUT_SESSION_STALE_MESSAGE)
    callback.answer.assert_awaited_once()
    api.create_order.assert_not_called()


@pytest.mark.asyncio
async def test_skip_comment_rejects_stale_checkout_session():
    from bot.handlers.checkout import callback_skip_comment

    state = FakeFSMContext(
        {
            SESSION_KEY: {
                "external_user_id": "12345",
                "customer_id": 1,
            }
        }
    )
    callback = _make_callback()
    api = AsyncMock()

    with patch(
        "bot.handlers.checkout.require_identified_callback",
        new_callable=AsyncMock,
        return_value={"external_user_id": "12345", "customer_id": 1},
    ):
        await callback_skip_comment(callback, state, api)

    callback.message.answer.assert_awaited_once_with(CHECKOUT_SESSION_STALE_MESSAGE)
    api.checkout_preview.assert_not_called()
