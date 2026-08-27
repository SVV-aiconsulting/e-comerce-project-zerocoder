"""История и детали заказов."""
from __future__ import annotations

from vk_bot.api.errors import ApiError, BackendUnavailableError
from vk_bot.services.formatting import format_order_detail, format_orders_list_item
from vk_bot.handlers.common import answer_api_error, ensure_identified
from vk_bot.services.error_messages import NOT_IDENTIFIED_MESSAGE
from vk_bot.keyboards import orders_list_keyboard
from vk_bot.texts import EMPTY_ORDERS
from vk_bot.utils import channel, get_session, send_message


async def show_orders_list(api, peer_id: int, user_id: int, storefront_api) -> None:
    try:
        session = await ensure_identified(storefront_api, user_id)
    except (ApiError, BackendUnavailableError) as exc:
        await answer_api_error(api, peer_id, exc)
        return

    if session is None:
        await send_message(api, peer_id, NOT_IDENTIFIED_MESSAGE)
        return

    public_code = session.get("customer_public_code")
    if not public_code:
        await send_message(api, peer_id, NOT_IDENTIFIED_MESSAGE)
        return

    try:
        orders = await storefront_api.list_customer_orders(
            public_code,
            channel=channel(),
            external_user_id=session["external_user_id"],
        )
    except (ApiError, BackendUnavailableError) as exc:
        await answer_api_error(api, peer_id, exc)
        return

    if not orders:
        await send_message(api, peer_id, EMPTY_ORDERS)
        return

    lines = ["Мои заказы\n"]
    for order in orders[:10]:
        lines.append(format_orders_list_item(order))

    await send_message(
        api,
        peer_id,
        "\n".join(lines),
        orders_list_keyboard(orders),
    )


async def show_order_detail(
    api,
    peer_id: int,
    user_id: int,
    storefront_api,
    public_number: str,
) -> None:
    session = get_session(str(user_id))
    try:
        order = await storefront_api.get_order(
            public_number,
            channel=channel(),
            external_user_id=session["external_user_id"],
        )
    except (ApiError, BackendUnavailableError) as exc:
        await answer_api_error(api, peer_id, exc)
        return

    await send_message(api, peer_id, format_order_detail(order))


def register_orders_handlers(bot, api_holder: dict) -> None:
    from vkbottle import GroupEventType
    from vkbottle.bot import MessageEvent
    from vk_bot.rules import cmd_payload
    from vk_bot.utils_events import answer_callback, parse_event_payload

    @bot.on.raw_event(GroupEventType.MESSAGE_EVENT, MessageEvent, cmd_payload("order"))
    async def order_detail_event(event: MessageEvent):
        payload = parse_event_payload(event) or {}
        public_number = payload.get("num")
        if not public_number:
            await answer_callback(event)
            return
        await show_order_detail(
            event.ctx_api,
            event.peer_id,
            event.user_id,
            api_holder["api"],
            public_number,
        )
        await answer_callback(event)
