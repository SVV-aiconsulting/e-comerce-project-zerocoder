"""Тесты guard-ов checkout-сессии после потери FSM."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.types import CallbackQuery, Message

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


def _make_message(text: str, user_id: int = 12345):
    message = MagicMock()
    message.__class__ = Message
    message.text = text
    message.from_user = MagicMock()
    message.from_user.id = user_id
    message.answer = AsyncMock()
    return message


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


@pytest.mark.asyncio
async def test_delivery_address_is_quoted_before_payment_choice():
    from bot.handlers.checkout import on_delivery_address

    state = FakeFSMContext(
        {
            SESSION_KEY: {
                "external_user_id": "12345",
                "customer_id": 1,
                "receiving_type": "delivery",
            }
        }
    )
    message = _make_message("Москва, Тверская, 1")
    api = AsyncMock()
    api.checkout_preview.return_value = {
        "items_total": "1000.00",
        "discount_amount": "0.00",
        "delivery_cost": "420.00",
        "total_amount": "1420.00",
        "delivery_quote_id": 77,
        "delivery_days": 2,
    }

    await on_delivery_address(message, state, api)

    api.checkout_preview.assert_awaited_once_with(
        channel="telegram",
        external_user_id="12345",
        customer_id=1,
        receiving_type="delivery",
        delivery_address="Москва, Тверская, 1",
        payment_method="card_prepayment",
    )
    session = state._data[SESSION_KEY]
    assert session["delivery_quote_id"] == 77
    assert session["delivery_confirmed"] is False
    sent_text = message.answer.await_args.args[0]
    assert "Москва, Тверская, 1" in sent_text
    assert "420" in sent_text
    assert "Подтвердите" in sent_text


@pytest.mark.asyncio
async def test_card_online_order_returns_yookassa_link():
    from bot.handlers.checkout import callback_confirm_order

    session = {
        "external_user_id": "12345",
        "customer_id": 1,
        "customer_public_code": "CL-1",
        "receiving_type": "delivery",
        "delivery_address": "Москва, Тверская, 1",
        "delivery_quote_id": 77,
        "delivery_confirmed": True,
        "payment_method": "card_prepayment",
        "customer_comment": "",
        "checkout_preview": {"total_amount": "1420.00"},
    }
    state = FakeFSMContext({SESSION_KEY: session})
    callback = _make_callback()
    api = AsyncMock()
    api.create_order.return_value = {
        "public_number": "WM-TEST-1",
        "total_amount": "1420.00",
        "order_status_label": "Новый",
    }
    api.create_payment.return_value = {
        "confirmation_url": "https://yookassa.test/pay/1"
    }

    with patch(
        "bot.handlers.checkout.require_identified_callback",
        new_callable=AsyncMock,
        return_value=session,
    ):
        await callback_confirm_order(callback, state, api)

    assert api.create_order.await_args.args[0]["delivery_quote_id"] == 77
    api.create_payment.assert_awaited_once_with(
        "WM-TEST-1",
        channel="telegram",
        external_user_id="12345",
    )
    messages = [call.args[0] for call in callback.message.answer.await_args_list]
    assert any("Ваш заказ оформлен" in text for text in messages)
    assert any("перейдите по ссылке" in text for text in messages)
