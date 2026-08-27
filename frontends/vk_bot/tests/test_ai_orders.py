from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from vk_bot.handlers.ai_orders import handle_natural_order_message


@pytest.mark.asyncio
async def test_natural_order_is_submitted_and_response_is_sent(monkeypatch):
    message = MagicMock()
    message.text = "Две упаковки креветок, самовывоз"
    message.id = 90
    message.conversation_message_id = 55
    message.peer_id = 777
    message.from_id = 123
    message.ctx_api = MagicMock()
    api = AsyncMock()
    api.submit_inbound_event.return_value = {"event_id": "event-uuid"}
    api.wait_for_inbound_event.return_value = {
        "complete": True,
        "response": {
            "type": "clarification",
            "message": "Как будете оплачивать?",
        },
    }
    api_holder = {
        "api": api,
        "bot": MagicMock(),
        "settings": SimpleNamespace(
            vk_ai_poll_attempts=3,
            vk_ai_poll_interval_seconds=0,
        ),
    }
    send_message = AsyncMock()
    monkeypatch.setattr(
        "vk_bot.handlers.ai_orders.ensure_identified",
        AsyncMock(return_value={"customer_id": 42}),
    )
    monkeypatch.setattr("vk_bot.handlers.ai_orders.send_message", send_message)

    await handle_natural_order_message(message, api_holder)

    payload = api.submit_inbound_event.await_args.args[0]
    assert payload["external_event_id"] == "777:55"
    assert payload["customer_id"] == 42
    api.wait_for_inbound_event.assert_awaited_once_with(
        "event-uuid",
        channel="vk",
        external_user_id="123",
        attempts=3,
        interval=0,
    )
    assert send_message.await_args_list[-1].args[2] == "Как будете оплачивать?"
