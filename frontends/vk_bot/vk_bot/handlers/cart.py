"""Корзина."""
from __future__ import annotations

from decimal import Decimal

from vk_bot.api.errors import ApiError, BackendUnavailableError
from vk_bot.handlers.common import answer_api_error, ensure_identified
from vk_bot.services.error_messages import NOT_IDENTIFIED_MESSAGE
from vk_bot.services.formatting import format_cart_footer, format_cart_item_line, format_quantity
from vk_bot.keyboards import cart_footer_keyboard, cart_item_keyboard
from vk_bot.texts import CART_CLEARED, EMPTY_CART
from vk_bot.utils import (
    channel,
    delete_peer_message,
    edit_peer_message,
    get_session,
    send_message,
    update_session,
)


async def show_cart(api, peer_id: int, user_id: int, storefront_api) -> None:
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

    items = cart.get("items") or []
    if not items:
        update_session(str(user_id), cart_ui=None)
        await send_message(api, peer_id, EMPTY_CART)
        return

    await send_message(api, peer_id, "Корзина\n")

    cart_ui_items: dict[str, int] = {}
    for item in items:
        product_id = item["product"]["id"]
        qty_label = format_quantity(item["quantity"])
        message_id = await send_message(
            api,
            peer_id,
            format_cart_item_line(item),
            cart_item_keyboard(product_id, qty_label),
        )
        if message_id is not None:
            cart_ui_items[str(product_id)] = message_id

    footer_id = await send_message(
        api,
        peer_id,
        format_cart_footer(cart),
        cart_footer_keyboard(),
    )
    update_session(
        str(user_id),
        cart_ui={
            "peer_id": peer_id,
            "footer_cmid": footer_id,
            "items": cart_ui_items,
        },
    )


async def _update_cart_footer(api, peer_id: int, session: dict, cart: dict) -> None:
    cart_ui = session.get("cart_ui") or {}
    footer_cmid = cart_ui.get("footer_cmid")
    if not footer_cmid:
        return

    items = cart.get("items") or []
    if items:
        text = format_cart_footer(cart)
        keyboard = cart_footer_keyboard()
    else:
        text = EMPTY_CART
        keyboard = None

    try:
        await edit_peer_message(api, peer_id, footer_cmid, text, keyboard)
    except Exception:
        pass


async def _apply_cart_quantity_change(event, storefront_api, *, product_id: int, delta: Decimal) -> None:
    try:
        session = await ensure_identified(storefront_api, event.user_id)
    except (ApiError, BackendUnavailableError) as exc:
        await answer_api_error(event.ctx_api, event.peer_id, exc)
        await _answer_event(event)
        return

    if session is None:
        await send_message(event.ctx_api, event.peer_id, NOT_IDENTIFIED_MESSAGE)
        await _answer_event(event)
        return

    try:
        cart = await storefront_api.get_cart(
            channel=channel(),
            external_user_id=session["external_user_id"],
            customer_id=session["customer_id"],
        )
    except (ApiError, BackendUnavailableError) as exc:
        await answer_api_error(event.ctx_api, event.peer_id, exc)
        await _answer_event(event)
        return

    item = _find_cart_item(cart, product_id)
    if item is None:
        await _answer_event(event, "Позиция не найдена")
        return

    current_qty = Decimal(str(item["quantity"]))
    min_qty = Decimal(str(item["product"].get("min_quantity", "1")))
    new_qty = current_qty + delta

    if delta < 0 and new_qty < min_qty:
        await _answer_event(event, f"Минимум: {format_quantity(min_qty)}")
        return

    if new_qty <= 0:
        await _answer_event(event, "Используйте «Удалить»")
        return

    quantity = format(new_qty.normalize(), "f")
    try:
        cart = await storefront_api.set_cart_item(
            product_id,
            channel=channel(),
            external_user_id=session["external_user_id"],
            customer_id=session["customer_id"],
            quantity=quantity,
        )
    except (ApiError, BackendUnavailableError) as exc:
        await answer_api_error(event.ctx_api, event.peer_id, exc)
        await _answer_event(event)
        return

    updated_item = _find_cart_item(cart, product_id)
    if updated_item is None:
        await _answer_event(event)
        return

    qty_label = format_quantity(updated_item["quantity"])
    try:
        await event.edit_message(
            message=format_cart_item_line(updated_item),
            keyboard=cart_item_keyboard(product_id, qty_label).get_json(),
        )
    except Exception:
        pass

    session = get_session(str(event.user_id))
    await _update_cart_footer(event.ctx_api, event.peer_id, session, cart)
    await _answer_event(event)


def _find_cart_item(cart: dict, product_id: int) -> dict | None:
    for item in cart.get("items") or []:
        if item["product"]["id"] == product_id:
            return item
    return None


async def _answer_event(event, text: str = "") -> None:
    from vk_bot.utils_events import answer_callback

    await answer_callback(event, snackbar=text or None)


def register_cart_handlers(bot, api_holder: dict) -> None:
    from vkbottle import GroupEventType
    from vkbottle.bot import MessageEvent
    from vk_bot.rules import cmd_payload
    from vk_bot.utils_events import parse_event_payload

    @bot.on.raw_event(GroupEventType.MESSAGE_EVENT, MessageEvent, cmd_payload("cart_clear"))
    async def cart_clear_event(event: MessageEvent):
        try:
            session = await ensure_identified(api_holder["api"], event.user_id)
        except (ApiError, BackendUnavailableError) as exc:
            await answer_api_error(event.ctx_api, event.peer_id, exc)
            await _answer_event(event)
            return

        if session is None:
            await send_message(event.ctx_api, event.peer_id, NOT_IDENTIFIED_MESSAGE)
            await _answer_event(event)
            return

        try:
            await api_holder["api"].clear_cart(
                channel=channel(),
                external_user_id=session["external_user_id"],
                customer_id=session["customer_id"],
            )
        except (ApiError, BackendUnavailableError) as exc:
            await answer_api_error(event.ctx_api, event.peer_id, exc)
            await _answer_event(event)
            return

        update_session(str(event.user_id), cart_ui=None)
        await send_message(event.ctx_api, event.peer_id, CART_CLEARED)
        await _answer_event(event, "Корзина очищена")

    @bot.on.raw_event(GroupEventType.MESSAGE_EVENT, MessageEvent, cmd_payload("cart_inc"))
    async def cart_inc_event(event: MessageEvent):
        payload = parse_event_payload(event) or {}
        product_id = int(payload.get("id", 0))
        await _apply_cart_quantity_change(event, api_holder["api"], product_id=product_id, delta=Decimal("1"))

    @bot.on.raw_event(GroupEventType.MESSAGE_EVENT, MessageEvent, cmd_payload("cart_dec"))
    async def cart_dec_event(event: MessageEvent):
        payload = parse_event_payload(event) or {}
        product_id = int(payload.get("id", 0))
        await _apply_cart_quantity_change(event, api_holder["api"], product_id=product_id, delta=Decimal("-1"))

    @bot.on.raw_event(GroupEventType.MESSAGE_EVENT, MessageEvent, cmd_payload("cart_del"))
    async def cart_del_event(event: MessageEvent):
        payload = parse_event_payload(event) or {}
        product_id = int(payload.get("id", 0))
        try:
            session = await ensure_identified(api_holder["api"], event.user_id)
        except (ApiError, BackendUnavailableError) as exc:
            await answer_api_error(event.ctx_api, event.peer_id, exc)
            await _answer_event(event)
            return

        if session is None:
            await send_message(event.ctx_api, event.peer_id, NOT_IDENTIFIED_MESSAGE)
            await _answer_event(event)
            return

        try:
            cart = await api_holder["api"].remove_cart_item(
                product_id,
                channel=channel(),
                external_user_id=session["external_user_id"],
                customer_id=session["customer_id"],
            )
        except (ApiError, BackendUnavailableError) as exc:
            await answer_api_error(event.ctx_api, event.peer_id, exc)
            await _answer_event(event)
            return

        try:
            await delete_peer_message(
                event.ctx_api,
                event.peer_id,
                event.conversation_message_id,
            )
        except Exception:
            pass

        cart_ui = dict(session.get("cart_ui") or {})
        items_map = dict(cart_ui.get("items") or {})
        items_map.pop(str(product_id), None)
        cart_ui["items"] = items_map
        update_session(str(event.user_id), cart_ui=cart_ui)

        session = get_session(str(event.user_id))
        await _update_cart_footer(event.ctx_api, event.peer_id, session, cart)
        await _answer_event(event, "Удалено")
