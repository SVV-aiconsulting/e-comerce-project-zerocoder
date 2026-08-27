"""Каталог товаров."""
from __future__ import annotations

from decimal import Decimal

from vk_bot.api.errors import ApiError, BackendUnavailableError
from vk_bot.services.product_card import (
    get_product_quantity,
    send_product_card,
    update_product_card_event,
)
from vk_bot.services.session import sync_catalog_quantities
from vk_bot.handlers.common import answer_api_error
from vk_bot.texts import EMPTY_CATALOG
from vk_bot.utils import get_session, parse_decimal, save_session, send_message
from vk_bot.utils_events import answer_callback, parse_event_payload


async def show_catalog(api, peer_id: int, user_id: int, storefront_api, api_holder: dict) -> None:
    try:
        products = await storefront_api.list_products()
    except (ApiError, BackendUnavailableError) as exc:
        await answer_api_error(api, peer_id, exc)
        return

    if not products:
        await send_message(api, peer_id, EMPTY_CATALOG)
        return

    session = get_session(str(user_id))
    session = sync_catalog_quantities(session, products)
    save_session(str(user_id), session)

    settings = api_holder["settings"]
    photo_uploader = api_holder.get("photo_uploader")

    await send_message(api, peer_id, "Каталог")

    for product in products:
        quantity = get_product_quantity(session, product)
        await send_product_card(
            api,
            peer_id,
            product,
            quantity,
            backend_base_url=settings.backend_api_base_url,
            photo_uploader=photo_uploader,
        )


def register_catalog_handlers(bot, api_holder: dict) -> None:
    from vkbottle import GroupEventType
    from vkbottle.bot import MessageEvent
    from vk_bot.rules import cmd_payload

    @bot.on.raw_event(GroupEventType.MESSAGE_EVENT, MessageEvent, cmd_payload("catalog"))
    async def catalog_event(event: MessageEvent):
        await show_catalog(event.ctx_api, event.peer_id, event.user_id, api_holder["api"], api_holder)
        await answer_callback(event)

    @bot.on.raw_event(GroupEventType.MESSAGE_EVENT, MessageEvent, cmd_payload("noop"))
    async def noop_event(event: MessageEvent):
        await answer_callback(event)

    @bot.on.raw_event(GroupEventType.MESSAGE_EVENT, MessageEvent, cmd_payload("prod_inc"))
    async def product_inc_event(event: MessageEvent):
        await _adjust_product_qty(event, api_holder, delta=Decimal("1"))

    @bot.on.raw_event(GroupEventType.MESSAGE_EVENT, MessageEvent, cmd_payload("prod_dec"))
    async def product_dec_event(event: MessageEvent):
        await _adjust_product_qty(event, api_holder, delta=Decimal("-1"))

    @bot.on.raw_event(GroupEventType.MESSAGE_EVENT, MessageEvent, cmd_payload("prod_add"))
    async def product_add_event(event: MessageEvent):
        await _add_product_to_cart(event, api_holder["api"])


async def _adjust_product_qty(event, api_holder: dict, *, delta: Decimal) -> None:
    from vk_bot.handlers.common import answer_api_error, ensure_identified
    from vk_bot.services.error_messages import NOT_IDENTIFIED_MESSAGE
    from vk_bot.api.errors import ApiError, BackendUnavailableError

    storefront_api = api_holder["api"]
    settings = api_holder["settings"]

    try:
        session = await ensure_identified(storefront_api, event.user_id)
    except (ApiError, BackendUnavailableError) as exc:
        await answer_api_error(event.ctx_api, event.peer_id, exc)
        await answer_callback(event)
        return

    if session is None:
        await send_message(event.ctx_api, event.peer_id, NOT_IDENTIFIED_MESSAGE)
        await answer_callback(event)
        return

    payload = parse_event_payload(event) or {}
    product_id = int(payload.get("id", 0))
    product_id_str = str(product_id)
    quantities = dict(session.get("product_quantities") or {})
    min_quantities = session.get("product_min_quantities") or {}
    min_qty = parse_decimal(min_quantities.get(product_id_str, "1"))
    current = parse_decimal(quantities.get(product_id_str, str(min_qty)))
    new_qty = current + delta if delta > 0 else max(min_qty, current + delta)
    quantities[product_id_str] = str(new_qty)
    session["product_quantities"] = quantities
    save_session(str(event.user_id), session)

    public_code = (session.get("product_codes") or {}).get(product_id_str)
    if public_code:
        try:
            product = await storefront_api.get_product(public_code)
        except (ApiError, BackendUnavailableError) as exc:
            await answer_api_error(event.ctx_api, event.peer_id, exc)
            await answer_callback(event)
            return

        try:
            await update_product_card_event(
                event,
                product,
                new_qty,
                backend_base_url=settings.backend_api_base_url,
                photo_uploader=api_holder.get("photo_uploader"),
            )
        except Exception:
            await send_product_card(
                event.ctx_api,
                event.peer_id,
                product,
                new_qty,
                backend_base_url=settings.backend_api_base_url,
                photo_uploader=api_holder.get("photo_uploader"),
            )

    await answer_callback(event)


async def _add_product_to_cart(event, storefront_api) -> None:
    from vk_bot.handlers.common import answer_api_error, ensure_identified
    from vk_bot.services.error_messages import NOT_IDENTIFIED_MESSAGE
    from vk_bot.api.errors import ApiError, BackendUnavailableError
    from vk_bot.services.formatting import format_price
    from vk_bot.texts import ITEM_ADDED
    from vk_bot.utils import channel

    try:
        session = await ensure_identified(storefront_api, event.user_id)
    except (ApiError, BackendUnavailableError) as exc:
        await answer_api_error(event.ctx_api, event.peer_id, exc)
        await answer_callback(event)
        return

    if session is None:
        await send_message(event.ctx_api, event.peer_id, NOT_IDENTIFIED_MESSAGE)
        await answer_callback(event)
        return

    payload = parse_event_payload(event) or {}
    product_id = int(payload.get("id", 0))
    product_id_str = str(product_id)
    quantities = session.get("product_quantities") or {}
    min_quantities = session.get("product_min_quantities") or {}
    quantity = str(quantities.get(product_id_str) or min_quantities.get(product_id_str) or "1")

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
        await answer_callback(event)
        return

    await send_message(
        event.ctx_api,
        event.peer_id,
        ITEM_ADDED.format(total=format_price(cart["items_total"])),
    )
    await answer_callback(event, snackbar="Добавлено")
