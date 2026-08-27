"""Действия с карточками товаров в каталоге."""
from decimal import Decimal

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.api.errors import ApiError, BackendUnavailableError
from bot.constants import CHANNEL
from bot.handlers.common import answer_api_error
from bot.config import get_settings
from bot.services.formatting import format_price
from bot.services.identify import require_identified_callback
from bot.services.product_card import (
    parse_decimal,
    send_product_card,
    update_product_card_message,
)
from bot.services.session import get_session, save_session

router = Router(name="product")


async def _add_to_cart(
    message: Message,
    state: FSMContext,
    api,
    product_id: int,
    quantity: str,
    *,
    user_id: int,
) -> None:
    session = await get_session(state, str(user_id))
    try:
        cart = await api.set_cart_item(
            product_id,
            channel=CHANNEL,
            external_user_id=session["external_user_id"],
            customer_id=session["customer_id"],
            quantity=quantity,
        )
    except (ApiError, BackendUnavailableError) as exc:
        await answer_api_error(message, exc)
        return

    await message.answer(
        f"Товар добавлен в корзину.\nИтого в корзине: {format_price(cart['items_total'])}"
    )


@router.callback_query(F.data == "prod:noop")
async def callback_product_noop(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(F.data.startswith("prod:add:"))
async def callback_product_add(callback: CallbackQuery, state: FSMContext, api) -> None:
    session = await require_identified_callback(callback, state, api)
    if session is None:
        return
    _, _, product_id_raw = callback.data.split(":", 2)
    product_id = str(int(product_id_raw))
    quantities = session.get("product_quantities") or {}
    min_quantities = session.get("product_min_quantities") or {}
    quantity = str(quantities.get(product_id) or min_quantities.get(product_id) or "1")
    await _add_to_cart(
        callback.message,
        state,
        api,
        int(product_id),
        quantity,
        user_id=callback.from_user.id,
    )
    await callback.answer("Добавлено")


@router.callback_query(F.data.startswith("prod:inc:"))
@router.callback_query(F.data.startswith("prod:dec:"))
async def callback_product_adjust_qty(callback: CallbackQuery, state: FSMContext, api) -> None:
    session = await require_identified_callback(callback, state, api)
    if session is None:
        return
    _, action, product_id_raw = callback.data.split(":", 2)
    product_id = str(int(product_id_raw))
    quantities = dict(session.get("product_quantities") or {})
    min_quantities = session.get("product_min_quantities") or {}
    product_codes = session.get("product_codes") or {}

    min_qty = parse_decimal(min_quantities.get(product_id, "1"))
    current = parse_decimal(quantities.get(product_id, str(min_qty)))

    if action == "inc":
        current += Decimal("1")
    else:
        current = max(min_qty, current - Decimal("1"))

    quantities[product_id] = str(current)
    session["product_quantities"] = quantities
    await save_session(state, session)

    public_code = product_codes.get(product_id)
    if not public_code:
        await callback.answer()
        return

    try:
        product = await api.get_product(public_code)
    except (ApiError, BackendUnavailableError) as exc:
        await answer_api_error(callback, exc)
        await callback.answer()
        return

    try:
        await update_product_card_message(callback, product, current)
    except Exception:
        settings = get_settings()
        await send_product_card(
            callback.message,
            product,
            current,
            backend_base_url=settings.backend_api_base_url,
        )
    await callback.answer()
