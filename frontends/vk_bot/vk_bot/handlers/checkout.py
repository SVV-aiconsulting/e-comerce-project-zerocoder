"""Оформление заказа."""
from __future__ import annotations

from vkbottle.bot import Message

from vk_bot.api.errors import ApiError, BackendUnavailableError
from vk_bot.handlers.common import answer_api_error, ensure_identified
from vk_bot.services.error_messages import CHECKOUT_SESSION_STALE_MESSAGE, NOT_IDENTIFIED_MESSAGE
from vk_bot.services.formatting import format_checkout_preview, format_order_created
from vk_bot.keyboards import (
    confirm_order_keyboard,
    main_menu_keyboard,
    payment_method_keyboard,
    receiving_type_keyboard,
    skip_comment_keyboard,
)
from vk_bot.states import CheckoutStates
from vk_bot.texts import EMPTY_CART
from vk_bot.utils import channel, clear_checkout_state, get_session, send_message, update_session

_meta_cache: dict | None = None


def _checkout_ready_for_preview(session: dict) -> bool:
    return bool(session.get("receiving_type"))


def _checkout_ready_for_confirm(session: dict) -> bool:
    return bool(session.get("receiving_type") and session.get("payment_method"))


async def get_meta(storefront_api) -> dict:
    global _meta_cache
    if _meta_cache is None:
        _meta_cache = await storefront_api.get_meta()
    return _meta_cache


async def start_checkout(api, peer_id: int, user_id: int, storefront_api) -> None:
    try:
        session = await ensure_identified(storefront_api, user_id)
    except (ApiError, BackendUnavailableError) as exc:
        await answer_api_error(api, peer_id, exc)
        return

    if session is None:
        await send_message(api, peer_id, NOT_IDENTIFIED_MESSAGE)
        return

    try:
        cart = await storefront_api.get_cart(
            channel=channel(),
            external_user_id=session["external_user_id"],
            customer_id=session["customer_id"],
        )
    except (ApiError, BackendUnavailableError) as exc:
        await answer_api_error(api, peer_id, exc)
        return

    if not cart.get("items"):
        await send_message(api, peer_id, EMPTY_CART)
        return

    try:
        meta = await get_meta(storefront_api)
    except (ApiError, BackendUnavailableError) as exc:
        await answer_api_error(api, peer_id, exc)
        return

    clear_checkout_state(str(user_id))
    await send_message(
        api,
        peer_id,
        "Выберите способ получения:",
        receiving_type_keyboard(meta["receiving_types"]),
    )


async def show_preview(api, peer_id: int, user_id: int, storefront_api) -> None:
    session = get_session(str(user_id))
    if not _checkout_ready_for_preview(session):
        await send_message(api, peer_id, CHECKOUT_SESSION_STALE_MESSAGE)
        return

    try:
        preview = await storefront_api.checkout_preview(
            channel=channel(),
            external_user_id=session["external_user_id"],
            customer_id=session["customer_id"],
            receiving_type=session["receiving_type"],
        )
    except (ApiError, BackendUnavailableError) as exc:
        await answer_api_error(api, peer_id, exc)
        return

    update_session(str(user_id), checkout_preview=preview)
    await send_message(
        api,
        peer_id,
        format_checkout_preview(preview),
        confirm_order_keyboard(),
    )


async def confirm_order(api, peer_id: int, user_id: int, storefront_api) -> None:
    session = get_session(str(user_id))
    if not _checkout_ready_for_confirm(session):
        await send_message(api, peer_id, CHECKOUT_SESSION_STALE_MESSAGE)
        return

    payload = {
        "channel": channel(),
        "external_user_id": session["external_user_id"],
        "customer_id": session["customer_id"],
        "receiving_type": session["receiving_type"],
        "payment_method": session["payment_method"],
        "delivery_address": session.get("delivery_address") or "",
        "customer_comment": session.get("customer_comment") or "",
        "is_new_customer": session.get("is_new_customer", False),
    }

    try:
        order = await storefront_api.create_order(payload)
    except (ApiError, BackendUnavailableError) as exc:
        await answer_api_error(api, peer_id, exc)
        return

    clear_checkout_state(str(user_id))
    await send_message(
        api,
        peer_id,
        format_order_created(order),
        main_menu_keyboard(),
    )


def register_checkout_handlers(bot, api_holder: dict) -> None:
    from vkbottle import GroupEventType
    from vkbottle.bot import MessageEvent
    from vk_bot.rules import cmd_payload
    from vk_bot.utils_events import answer_callback, parse_event_payload

    @bot.on.raw_event(GroupEventType.MESSAGE_EVENT, MessageEvent, cmd_payload("checkout"))
    async def checkout_start_event(event: MessageEvent):
        await start_checkout(event.ctx_api, event.peer_id, event.user_id, api_holder["api"])
        await answer_callback(event)

    @bot.on.raw_event(GroupEventType.MESSAGE_EVENT, MessageEvent, cmd_payload("recv"))
    async def receiving_event(event: MessageEvent):
        payload = parse_event_payload(event) or {}
        receiving_type = payload.get("value")
        update_session(str(event.user_id), receiving_type=receiving_type)
        if receiving_type == "delivery":
            await api_holder["bot"].state_dispenser.set(event.peer_id, CheckoutStates.ENTERING_ADDRESS)
            await send_message(event.ctx_api, event.peer_id, "Введите адрес доставки:")
        else:
            update_session(str(event.user_id), delivery_address="")
            meta = await get_meta(api_holder["api"])
            await send_message(
                event.ctx_api,
                event.peer_id,
                "Выберите способ оплаты:",
                payment_method_keyboard(meta["payment_methods"]),
            )
        await answer_callback(event)

    @bot.on.raw_event(GroupEventType.MESSAGE_EVENT, MessageEvent, cmd_payload("pay"))
    async def payment_event(event: MessageEvent):
        session = get_session(str(event.user_id))
        if not _checkout_ready_for_preview(session):
            await send_message(event.ctx_api, event.peer_id, CHECKOUT_SESSION_STALE_MESSAGE)
            await answer_callback(event)
            return

        payload = parse_event_payload(event) or {}
        payment_method = payload.get("value")
        update_session(str(event.user_id), payment_method=payment_method)
        await api_holder["bot"].state_dispenser.set(event.peer_id, CheckoutStates.ENTERING_COMMENT)
        await send_message(
            event.ctx_api,
            event.peer_id,
            "Добавьте комментарий к заказу или нажмите «Пропустить»:",
            skip_comment_keyboard(),
        )
        await answer_callback(event)

    @bot.on.raw_event(GroupEventType.MESSAGE_EVENT, MessageEvent, cmd_payload("skip_comment"))
    async def skip_comment_event(event: MessageEvent):
        session = get_session(str(event.user_id))
        if not _checkout_ready_for_preview(session):
            await send_message(event.ctx_api, event.peer_id, CHECKOUT_SESSION_STALE_MESSAGE)
        else:
            update_session(str(event.user_id), customer_comment="")
            await api_holder["bot"].state_dispenser.delete(event.peer_id)
            await show_preview(event.ctx_api, event.peer_id, event.user_id, api_holder["api"])
        await answer_callback(event)

    @bot.on.raw_event(GroupEventType.MESSAGE_EVENT, MessageEvent, cmd_payload("confirm"))
    async def confirm_event(event: MessageEvent):
        await confirm_order(event.ctx_api, event.peer_id, event.user_id, api_holder["api"])
        await answer_callback(event)

    @bot.on.message(state=CheckoutStates.ENTERING_ADDRESS)
    async def address_handler(message: Message):
        session = get_session(str(message.from_id))
        if not _checkout_ready_for_preview(session):
            await send_message(message.ctx_api, message.peer_id, CHECKOUT_SESSION_STALE_MESSAGE)
            await api_holder["bot"].state_dispenser.delete(message.peer_id)
            return

        address = (message.text or "").strip()
        if len(address) < 5:
            await send_message(message.ctx_api, message.peer_id, "Введите полный адрес доставки.")
            return

        update_session(str(message.from_id), delivery_address=address)
        await api_holder["bot"].state_dispenser.delete(message.peer_id)
        meta = await get_meta(api_holder["api"])
        await send_message(
            message.ctx_api,
            message.peer_id,
            "Выберите способ оплаты:",
            payment_method_keyboard(meta["payment_methods"]),
        )

    @bot.on.message(state=CheckoutStates.ENTERING_COMMENT)
    async def comment_handler(message: Message):
        session = get_session(str(message.from_id))
        if not _checkout_ready_for_preview(session):
            await send_message(message.ctx_api, message.peer_id, CHECKOUT_SESSION_STALE_MESSAGE)
            await api_holder["bot"].state_dispenser.delete(message.peer_id)
            return

        update_session(str(message.from_id), customer_comment=(message.text or "").strip())
        await api_holder["bot"].state_dispenser.delete(message.peer_id)
        await show_preview(message.ctx_api, message.peer_id, message.from_id, api_holder["api"])
