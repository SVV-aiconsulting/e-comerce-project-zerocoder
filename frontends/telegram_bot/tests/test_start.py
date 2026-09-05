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
    api.get_personal_data_consent.return_value = {"granted": True}

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


@pytest.mark.asyncio
async def test_start_requests_explicit_consent_before_identification(monkeypatch):
    message = MagicMock()
    message.from_user = SimpleNamespace(id=321)
    message.answer = AsyncMock()
    state = MagicMock()
    api = AsyncMock()
    api.get_personal_data_consent.return_value = {
        "granted": False,
        "policy_url": "https://shop.test/privacy-policy/",
        "consent_url": "https://shop.test/personal-data-consent/",
    }
    identify = AsyncMock()
    monkeypatch.setattr("bot.handlers.start.identify_without_phone", identify)

    await cmd_start(message, state, api)

    identify.assert_not_awaited()
    message.answer.assert_awaited_once()
    text = message.answer.await_args.args[0]
    assert "privacy-policy" in text
    assert "personal-data-consent" in text
    markup = message.answer.await_args.kwargs["reply_markup"]
    assert [row[0].text for row in markup.inline_keyboard] == ["Согласен", "Не согласен"]
