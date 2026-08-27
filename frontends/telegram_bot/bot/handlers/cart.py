"""Корзина."""
from decimal import Decimal

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.api.errors import ApiError, BackendUnavailableError
from bot.constants import CHANNEL
from bot.handlers.common import answer_api_error
from bot.keyboards.inline import cart_footer_keyboard, cart_item_keyboard
from bot.services.formatting import format_price, format_quantity
from bot.services.identify import require_identified_callback
from bot.services.session import get_session, update_session

router = Router(name="cart")


def _format_cart_item_line(item: dict) -> str:
    product = item["product"]
    unit = product.get("unit_label", "шт")
    unit_price = format_price(product["base_price"])
    return f"Позиция: <b>{product['name']}</b> — {unit_price} / {unit}"


def _format_cart_footer(cart: dict) -> str:
    return f"<b>Итого:</b> {format_price(cart['items_total'])}"


def _find_cart_item(cart: dict, product_id: int) -> dict | None:
    for item in cart.get("items") or []:
        if item["product"]["id"] == product_id:
            return item
    return None


async def _get_cart(session: dict, api) -> dict:
    return await api.get_cart(
        channel=CHANNEL,
        external_user_id=session["external_user_id"],
        customer_id=session["customer_id"],
    )


async def _update_cart_footer(callback: CallbackQuery, session: dict, cart: dict) -> None:
    cart_ui = session.get("cart_ui") or {}
    footer_id = cart_ui.get("footer_message_id")
    chat_id = cart_ui.get("chat_id")
    if not footer_id or not chat_id:
        return

    items = cart.get("items") or []
    if items:
        text = _format_cart_footer(cart)
        markup = cart_footer_keyboard()
    else:
        text = "Корзина пуста."
        markup = None

    try:
        await callback.bot.edit_message_text(
            text=text,
            chat_id=chat_id,
            message_id=footer_id,
            reply_markup=markup,
        )
    except Exception:
        pass


async def show_cart(
    message: Message,
    state: FSMContext,
    api,
    *,
    user_id: int,
) -> None:
    session = await get_session(state, str(user_id))
    try:
        cart = await _get_cart(session, api)
    except (ApiError, BackendUnavailableError) as exc:
        await answer_api_error(message, exc)
        return

    items = cart.get("items") or []
    if not items:
        await message.answer("Корзина пуста.")
        await update_session(state, cart_ui=None)
        return

    cart_ui_items: dict[str, int] = {}
    for item in items:
        product_id = item["product"]["id"]
        qty_label = format_quantity(item["quantity"])
        sent = await message.answer(
            _format_cart_item_line(item),
            reply_markup=cart_item_keyboard(product_id, qty_label),
        )
        cart_ui_items[str(product_id)] = sent.message_id

    footer = await message.answer(
        _format_cart_footer(cart),
        reply_markup=cart_footer_keyboard(),
    )
    await update_session(
        state,
        cart_ui={
            "chat_id": message.chat.id,
            "footer_message_id": footer.message_id,
            "items": cart_ui_items,
        },
    )


async def _apply_cart_quantity_change(
    callback: CallbackQuery,
    state: FSMContext,
    api,
    *,
    product_id: int,
    delta: Decimal,
) -> None:
    session = await require_identified_callback(callback, state, api)
    if session is None:
        return

    try:
        cart = await _get_cart(session, api)
    except (ApiError, BackendUnavailableError) as exc:
        await answer_api_error(callback, exc)
        return

    item = _find_cart_item(cart, product_id)
    if item is None:
        await callback.answer("Позиция не найдена")
        return

    current_qty = Decimal(str(item["quantity"]))
    min_qty = Decimal(str(item["product"].get("min_quantity", "1")))
    new_qty = current_qty + delta

    if delta < 0 and new_qty < min_qty:
        await callback.answer(f"Минимум: {format_quantity(min_qty)}")
        return

    if new_qty <= 0:
        await callback.answer("Используйте «Удалить»")
        return

    quantity = format(new_qty.normalize(), "f")

    try:
        cart = await api.set_cart_item(
            product_id,
            channel=CHANNEL,
            external_user_id=session["external_user_id"],
            customer_id=session["customer_id"],
            quantity=quantity,
        )
    except (ApiError, BackendUnavailableError) as exc:
        await answer_api_error(callback, exc)
        return

    updated_item = _find_cart_item(cart, product_id)
    if updated_item is None:
        await callback.answer()
        return

    qty_label = format_quantity(updated_item["quantity"])
    try:
        await callback.message.edit_text(
            _format_cart_item_line(updated_item),
            reply_markup=cart_item_keyboard(product_id, qty_label),
        )
    except Exception:
        pass

    session = await get_session(state, str(callback.from_user.id))
    await _update_cart_footer(callback, session, cart)
    await callback.answer()


@router.callback_query(F.data == "cart:noop")
async def callback_cart_noop(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(F.data.startswith("cart:inc:"))
async def callback_cart_inc(callback: CallbackQuery, state: FSMContext, api) -> None:
    product_id = int(callback.data.split(":")[-1])
    await _apply_cart_quantity_change(
        callback,
        state,
        api,
        product_id=product_id,
        delta=Decimal("1"),
    )


@router.callback_query(F.data.startswith("cart:dec:"))
async def callback_cart_dec(callback: CallbackQuery, state: FSMContext, api) -> None:
    product_id = int(callback.data.split(":")[-1])
    await _apply_cart_quantity_change(
        callback,
        state,
        api,
        product_id=product_id,
        delta=Decimal("-1"),
    )


@router.callback_query(F.data.startswith("cart:del:"))
async def callback_cart_delete(callback: CallbackQuery, state: FSMContext, api) -> None:
    session = await require_identified_callback(callback, state, api)
    if session is None:
        return
    product_id = int(callback.data.split(":")[-1])

    try:
        cart = await api.remove_cart_item(
            product_id,
            channel=CHANNEL,
            external_user_id=session["external_user_id"],
            customer_id=session["customer_id"],
        )
    except (ApiError, BackendUnavailableError) as exc:
        await answer_api_error(callback, exc)
        await callback.answer()
        return

    try:
        await callback.message.delete()
    except Exception:
        pass

    cart_ui = dict(session.get("cart_ui") or {})
    items_map = dict(cart_ui.get("items") or {})
    items_map.pop(str(product_id), None)
    cart_ui["items"] = items_map
    session["cart_ui"] = cart_ui
    await update_session(state, cart_ui=cart_ui)

    session = await get_session(state, str(callback.from_user.id))
    await _update_cart_footer(callback, session, cart)
    await callback.answer("Удалено")
