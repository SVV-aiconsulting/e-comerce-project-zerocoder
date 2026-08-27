"""Оформление заказа."""
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.api.errors import ApiError, BackendUnavailableError
from bot.constants import CHANNEL
from bot.handlers.common import answer_api_error
from bot.keyboards.inline import (
    confirm_order_keyboard,
    payment_method_keyboard,
    receiving_type_keyboard,
    skip_comment_keyboard,
)
from bot.keyboards.reply import main_menu_keyboard
from bot.services.error_messages import CHECKOUT_SESSION_STALE_MESSAGE
from bot.services.formatting import format_price
from bot.services.identify import require_identified_callback
from bot.services.session import get_session, update_session
from bot.states import CheckoutStates

router = Router(name="checkout")

_meta_cache: dict | None = None


def _checkout_ready_for_preview(session: dict) -> bool:
    return bool(session.get("receiving_type"))


def _checkout_ready_for_confirm(session: dict) -> bool:
    return bool(session.get("receiving_type") and session.get("payment_method"))


async def _answer_checkout_stale(target: Message | CallbackQuery) -> None:
    if isinstance(target, CallbackQuery):
        await target.message.answer(CHECKOUT_SESSION_STALE_MESSAGE)
        await target.answer()
        return
    await target.answer(CHECKOUT_SESSION_STALE_MESSAGE)


async def get_meta(api) -> dict:
    global _meta_cache
    if _meta_cache is None:
        _meta_cache = await api.get_meta()
    return _meta_cache


def _format_preview(preview: dict) -> str:
    free = " (бесплатно)" if preview.get("free_delivery") else ""
    return (
        "<b>Превью заказа</b>\n\n"
        f"Товары: {format_price(preview['items_total'])}\n"
        f"Скидка: {format_price(preview['discount_amount'])}\n"
        f"Доставка: {format_price(preview['delivery_cost'])}{free}\n"
        f"<b>Итого: {format_price(preview['total_amount'])}</b>"
    )


@router.callback_query(F.data == "checkout:start")
async def callback_checkout_start(callback: CallbackQuery, state: FSMContext, api) -> None:
    session = await require_identified_callback(callback, state, api)
    if session is None:
        return
    try:
        cart = await api.get_cart(
            channel=CHANNEL,
            external_user_id=session["external_user_id"],
            customer_id=session["customer_id"],
        )
    except (ApiError, BackendUnavailableError) as exc:
        await answer_api_error(callback, exc)
        await callback.answer()
        return

    if not cart.get("items"):
        await callback.message.answer("Корзина пуста. Добавьте товары из каталога.")
        await callback.answer()
        return

    try:
        meta = await get_meta(api)
    except (ApiError, BackendUnavailableError) as exc:
        await answer_api_error(callback, exc)
        await callback.answer()
        return

    await callback.message.answer(
        "Выберите способ получения:",
        reply_markup=receiving_type_keyboard(meta["receiving_types"]),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("checkout:recv:"))
async def callback_checkout_receiving(callback: CallbackQuery, state: FSMContext, api) -> None:
    session = await require_identified_callback(callback, state, api)
    if session is None:
        return
    receiving_type = callback.data.split(":")[-1]
    await update_session(state, receiving_type=receiving_type)

    if receiving_type == "delivery":
        await state.set_state(CheckoutStates.entering_address)
        await callback.message.answer("Введите адрес доставки:")
    else:
        await update_session(state, delivery_address="")
        await _show_payment_methods(callback.message, api)

    await callback.answer()


async def _show_payment_methods(message: Message, api) -> None:
    meta = await get_meta(api)
    await message.answer(
        "Выберите способ оплаты:",
        reply_markup=payment_method_keyboard(meta["payment_methods"]),
    )


@router.message(CheckoutStates.entering_address)
async def on_delivery_address(message: Message, state: FSMContext, api) -> None:
    session = await get_session(state, str(message.from_user.id))
    if not _checkout_ready_for_preview(session):
        await _answer_checkout_stale(message)
        await state.set_state(None)
        return

    address = (message.text or "").strip()
    if len(address) < 5:
        await message.answer("Введите полный адрес доставки.")
        return

    await update_session(state, delivery_address=address)
    await state.set_state(None)
    await _show_payment_methods(message, api)


@router.callback_query(F.data.startswith("checkout:pay:"))
async def callback_checkout_payment(callback: CallbackQuery, state: FSMContext, api) -> None:
    session = await require_identified_callback(callback, state, api)
    if session is None:
        return
    if not _checkout_ready_for_preview(session):
        await _answer_checkout_stale(callback)
        return

    payment_method = callback.data.split(":")[-1]
    await update_session(state, payment_method=payment_method)
    await state.set_state(CheckoutStates.entering_comment)
    await callback.message.answer(
        "Добавьте комментарий к заказу или нажмите «Пропустить»:",
        reply_markup=skip_comment_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "checkout:skip_comment")
async def callback_skip_comment(callback: CallbackQuery, state: FSMContext, api) -> None:
    session = await require_identified_callback(callback, state, api)
    if session is None:
        return
    if not _checkout_ready_for_preview(session):
        await _answer_checkout_stale(callback)
        return

    await update_session(state, customer_comment="")
    await state.set_state(None)
    await _show_preview(callback.message, state, api, user_id=callback.from_user.id)
    await callback.answer()


@router.message(CheckoutStates.entering_comment)
async def on_customer_comment(message: Message, state: FSMContext, api) -> None:
    session = await get_session(state, str(message.from_user.id))
    if not _checkout_ready_for_preview(session):
        await _answer_checkout_stale(message)
        await state.set_state(None)
        return

    await update_session(state, customer_comment=(message.text or "").strip())
    await state.set_state(None)
    await _show_preview(message, state, api, user_id=message.from_user.id)


async def _show_preview(
    message: Message,
    state: FSMContext,
    api,
    *,
    user_id: int,
) -> None:
    session = await get_session(state, str(user_id))
    if not _checkout_ready_for_preview(session):
        await _answer_checkout_stale(message)
        return

    try:
        preview = await api.checkout_preview(
            channel=CHANNEL,
            external_user_id=session["external_user_id"],
            customer_id=session["customer_id"],
            receiving_type=session["receiving_type"],
        )
    except (ApiError, BackendUnavailableError) as exc:
        await answer_api_error(message, exc)
        return

    await update_session(state, checkout_preview=preview)
    await message.answer(_format_preview(preview), reply_markup=confirm_order_keyboard())


@router.callback_query(F.data == "checkout:confirm")
async def callback_confirm_order(callback: CallbackQuery, state: FSMContext, api) -> None:
    session = await require_identified_callback(callback, state, api)
    if session is None:
        return
    if not _checkout_ready_for_confirm(session):
        await _answer_checkout_stale(callback)
        return

    payload = {
        "channel": CHANNEL,
        "external_user_id": session["external_user_id"],
        "customer_id": session["customer_id"],
        "receiving_type": session["receiving_type"],
        "payment_method": session["payment_method"],
        "delivery_address": session.get("delivery_address") or "",
        "customer_comment": session.get("customer_comment") or "",
        "is_new_customer": session.get("is_new_customer", False),
    }

    try:
        order = await api.create_order(payload)
    except (ApiError, BackendUnavailableError) as exc:
        await answer_api_error(callback, exc)
        await callback.answer()
        return

    await state.set_state(None)
    await callback.message.answer(
        f"<b>Заказ принят!</b>\n\n"
        f"Номер: <b>{order['public_number']}</b>\n"
        f"Статус: {order['order_status_label']}\n"
        f"Сумма: {format_price(order['total_amount'])}",
        reply_markup=main_menu_keyboard(),
    )
    await callback.answer()
