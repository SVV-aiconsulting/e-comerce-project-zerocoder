"""Свободный текст заказа через единый AI intake pipeline."""
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.api.client import StorefrontApiClient
from bot.api.errors import ApiError, BackendUnavailableError
from bot.config import Settings
from bot.constants import CHANNEL
from bot.handlers.common import answer_api_error
from bot.handlers.registration import prompt_registration
from bot.services.identify import ensure_identified

router = Router(name="ai_orders")


@router.message(F.text)
async def on_natural_order_message(
    message: Message,
    state: FSMContext,
    api: StorefrontApiClient,
    settings: Settings,
) -> None:
    text = (message.text or "").strip()
    if not text or text.startswith("/"):
        return

    try:
        session = await ensure_identified(message, state, api)
        if session is None:
            await prompt_registration(message, state)
            return

        external_user_id = str(message.from_user.id)
        submission = await api.submit_inbound_event(
            {
                "channel": CHANNEL,
                "external_event_id": f"{message.chat.id}:{message.message_id}",
                "external_user_id": external_user_id,
                "conversation_key": str(message.chat.id),
                "customer_id": session["customer_id"],
                "raw_text": text,
                "raw_payload": {
                    "message_id": message.message_id,
                    "chat_id": message.chat.id,
                },
            }
        )
        await message.answer("Принял запрос, проверяю товары и условия заказа…")
        result = await api.wait_for_inbound_event(
            submission["event_id"],
            channel=CHANNEL,
            external_user_id=external_user_id,
            attempts=settings.telegram_ai_poll_attempts,
            interval=settings.telegram_ai_poll_interval_seconds,
        )
    except (ApiError, BackendUnavailableError) as exc:
        await answer_api_error(message, exc)
        return

    response = result.get("response") or {}
    if response.get("message"):
        await message.answer(response["message"], parse_mode=None)
    else:
        await message.answer(
            "Запрос сохранён и ещё обрабатывается. Ответ появится после обработки; "
            "при необходимости повторите сообщение через минуту."
        )
