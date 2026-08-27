"""Тесты handler регистрации."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.constants import WRONG_CONTACT_TEXT
from bot.handlers.registration import on_contact


@pytest.mark.asyncio
async def test_on_contact_rejects_foreign_contact():
    message = MagicMock()
    message.from_user = MagicMock()
    message.from_user.id = 12345
    message.contact = MagicMock()
    message.contact.user_id = 99999
    message.contact.phone_number = "+79991234567"
    message.answer = AsyncMock()

    state = MagicMock()
    api = AsyncMock()

    await on_contact(message, state, api)

    message.answer.assert_awaited_once()
    assert message.answer.call_args.args[0] == WRONG_CONTACT_TEXT
    api.identify_customer.assert_not_called()
