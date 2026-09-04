from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.handlers.ai_orders import on_natural_order_message


@pytest.mark.asyncio
async def test_natural_order_is_submitted_and_response_is_sent(monkeypatch):
    message = MagicMock()
    message.text = "Две упаковки креветок, самовывоз"
    message.message_id = 55
    message.chat.id = 777
    message.from_user.id = 123
    message.answer = AsyncMock()
    state = MagicMock()
    api = AsyncMock()
    api.submit_inbound_event.return_value = {"event_id": "event-uuid"}
    api.wait_for_inbound_event.return_value = {
        "complete": True,
        "response": {
            "type": "clarification",
            "message": "Как будете оплачивать?",
        },
    }
    monkeypatch.setattr(
        "bot.handlers.ai_orders.ensure_identified",
        AsyncMock(return_value={"customer_id": 42}),
    )
    settings = SimpleNamespace(
        telegram_ai_poll_attempts=3,
        telegram_ai_poll_interval_seconds=0,
    )

    await on_natural_order_message(message, state, api, settings)

    payload = api.submit_inbound_event.await_args.args[0]
    assert payload["external_event_id"] == "777:55"
    assert payload["customer_id"] == 42
    api.wait_for_inbound_event.assert_awaited_once_with(
        "event-uuid",
        channel="telegram",
        external_user_id="123",
        attempts=3,
        interval=0,
    )
    assert message.answer.await_count == 1
    assert message.answer.await_args_list[-1].args[0] == "Как будете оплачивать?"
    assert message.answer.await_args_list[-1].kwargs["parse_mode"] is None
