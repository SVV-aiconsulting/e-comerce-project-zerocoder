from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.constants import AI_ASSISTANT_WELCOME
from bot.handlers.start import cmd_start


@pytest.mark.asyncio
async def test_start_invites_dialog_without_dumping_catalog(monkeypatch):
    message = MagicMock()
    message.from_user = SimpleNamespace(
        id=123,
        username="tester",
        first_name="Иван",
        last_name="",
    )
    message.answer = AsyncMock()
    state = MagicMock()
    state.set_state = AsyncMock()
    api = AsyncMock()

    monkeypatch.setattr(
        "bot.handlers.start.get_session",
        AsyncMock(return_value={"customer_id": 42, "display_name": "Иван"}),
    )
    monkeypatch.setattr("bot.handlers.start.save_session", AsyncMock())
    monkeypatch.setattr(
        "bot.handlers.start.identify_without_phone",
        AsyncMock(
            return_value={
                "status": "identified",
                "customer": {"id": 42, "public_code": "CUS-42", "name": "Иван"},
            }
        ),
    )

    await cmd_start(message, state, api)

    message.answer.assert_awaited_once()
    assert AI_ASSISTANT_WELCOME in message.answer.await_args.args[0]
    api.list_products.assert_not_awaited()
