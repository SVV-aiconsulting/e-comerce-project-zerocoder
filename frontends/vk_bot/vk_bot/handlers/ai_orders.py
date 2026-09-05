"""Свободный текст VK через единый AI intake pipeline."""
from __future__ import annotations

from vk_bot.api.errors import ApiError, BackendUnavailableError
from vk_bot.handlers.common import answer_api_error, ensure_identified
from vk_bot.handlers.registration import prompt_registration
from vk_bot.utils import channel, send_message


async def handle_natural_order_message(message, api_holder: dict) -> None:
    api = api_holder["api"]
    bot = api_holder["bot"]
    settings = api_holder["settings"]
    external_user_id = str(message.from_id)

    try:
        session = await ensure_identified(api, message.from_id)
        if session is None:
            await prompt_registration(message, bot)
            return

        message_id = message.conversation_message_id or message.id
        submission = await api.submit_inbound_event(
            {
                "channel": channel(),
                "external_event_id": f"{message.peer_id}:{message_id}",
                "external_user_id": external_user_id,
                "conversation_key": str(message.peer_id),
                "customer_id": session["customer_id"],
                "raw_text": (message.text or "").strip(),
                "raw_payload": {
                    "message_id": message.id,
                    "conversation_message_id": message.conversation_message_id,
                    "peer_id": message.peer_id,
                },
            }
        )
        result = await api.wait_for_inbound_event(
            submission["event_id"],
            channel=channel(),
            external_user_id=external_user_id,
            attempts=settings.vk_ai_poll_attempts,
            interval=settings.vk_ai_poll_interval_seconds,
        )
    except (ApiError, BackendUnavailableError) as exc:
        await answer_api_error(message.ctx_api, message.peer_id, exc)
        return

    response = result.get("response") or {}
    if response.get("message"):
        await send_message(message.ctx_api, message.peer_id, response["message"])
        action_url = response.get("action_url") or ""
        if action_url and action_url not in response["message"]:
            await send_message(message.ctx_api, message.peer_id, action_url)
    else:
        await send_message(
            message.ctx_api,
            message.peer_id,
            "Запрос сохранён и ещё обрабатывается. Повторите сообщение через минуту, "
            "если ответ не появился.",
        )
