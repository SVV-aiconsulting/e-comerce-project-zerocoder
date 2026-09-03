"""Оформление заказа."""
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.api.errors import ApiError, BackendUnavailableError
from bot.constants import CHANNEL
from bot.handlers.common import answer_api_error
from bot.keyboards.inline import (
    confirm_order_keyboard,
    delivery_quote_keyboard,
    payment_link_keyboard,
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
    if not (session.get("receiving_type") and session.get("payment_method")):
        return False
    return (
        session.get("payment_method") != "card_prepayment"
        or bool(session.get("checkout_email"))
    )


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


def _format_delivery_quote(preview: dict, address: str) -> str:
    days = (
        f"\nОриентировочный срок: {preview['delivery_days']} дн."
        if preview.get("delivery_days")
        else ""
    )
    return (
        "<b>Параметры доставки</b>\n\n"
        f"Адрес: {address}\n"
        f"Стоимость: <b>{format_price(preview['delivery_cost'])}</b>{days}\n"
        f"Итого с доставкой: <b>{format_price(preview['total_amount'])}</b>\n\n"
        "Подтвердите адрес и стоимость доставки."
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

    await update_session(
        state,
        receiving_type=None,
        delivery_address="",
        payment_method=None,
        customer_comment="",
        checkout_preview=None,
        delivery_quote_id=None,
        delivery_confirmed=False,
        checkout_email="",
    )

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
    await update_session(
        state,
        receiving_type=receiving_type,
        delivery_quote_id=None,
        delivery_confirmed=False,
        checkout_preview=None,
    )

    if receiving_type == "delivery":
        await state.set_state(CheckoutStates.entering_address)
        await callback.message.answer("Введите адрес доставки:")
    else:
        await update_session(state, delivery_address="", delivery_confirmed=True)
        await _show_payment_methods(callback.message, api)

    await callback.answer()


async def _show_payment_methods(message: Message, api) -> None:
    meta = await get_meta(api)
    await message.answer(
        "Выберите способ оплаты:",
        reply_markup=payment_method_keyboard(meta["payment_methods"]),
    )


async def _ask_for_comment(message: Message, state: FSMContext) -> None:
    await state.set_state(CheckoutStates.entering_comment)
    await message.answer(
        "Добавьте комментарий к заказу или нажмите «Пропустить»: ",
        reply_markup=skip_comment_keyboard(),
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
    try:
        preview = await api.checkout_preview(
            channel=CHANNEL,
            external_user_id=session["external_user_id"],
            customer_id=session["customer_id"],
            receiving_type="delivery",
            delivery_address=address,
            payment_method="card_prepayment",
        )
    except (ApiError, BackendUnavailableError) as exc:
        await answer_api_error(message, exc)
        await message.answer("Проверьте адрес и отправьте его ещё раз.")
        return

    await update_session(
        state,
        checkout_preview=preview,
        delivery_quote_id=preview.get("delivery_quote_id"),
        delivery_confirmed=False,
    )
    await state.set_state(None)
    await message.answer(
        _format_delivery_quote(preview, address),
        reply_markup=delivery_quote_keyboard(),
    )


@router.callback_query(F.data == "checkout:delivery:confirm")
async def callback_confirm_delivery(callback: CallbackQuery, state: FSMContext, api) -> None:
    session = await require_identified_callback(callback, state, api)
    if session is None:
        return
    if not session.get("delivery_quote_id") or not session.get("delivery_address"):
        await _answer_checkout_stale(callback)
        return
    await update_session(state, delivery_confirmed=True)
    await _show_payment_methods(callback.message, api)
    await callback.answer()


@router.callback_query(F.data == "checkout:delivery:change")
async def callback_change_delivery_address(
    callback: CallbackQuery,
    state: FSMContext,
    api,
) -> None:
    session = await require_identified_callback(callback, state, api)
    if session is None:
        return
    await update_session(
        state,
        delivery_quote_id=None,
        delivery_confirmed=False,
        checkout_preview=None,
    )
    await state.set_state(CheckoutStates.entering_address)
    await callback.message.answer("Введите новый адрес доставки:")
    await callback.answer()


@router.callback_query(F.data.startswith("checkout:pay:"))
async def callback_checkout_payment(callback: CallbackQuery, state: FSMContext, api) -> None:
    session = await require_identified_callback(callback, state, api)
    if session is None:
        return
    if not _checkout_ready_for_preview(session):
        await _answer_checkout_stale(callback)
        return

    payment_method = callback.data.split(":")[-1]
    if payment_method not in {"cash_on_delivery", "card_prepayment"}:
        await callback.answer("Этот способ оплаты недоступен", show_alert=True)
        return
    if session.get("receiving_type") == "delivery" and not session.get(
        "delivery_confirmed"
    ):
        await callback.answer("Сначала подтвердите параметры доставки", show_alert=True)
        return
    await update_session(state, payment_method=payment_method)
    if payment_method == "card_prepayment":
        await state.set_state(CheckoutStates.entering_receipt_email)
        await callback.message.answer(
            "Укажите email для электронного чека. Его получит только ЮKassa для отправки чека:"
        )
    else:
        await _ask_for_comment(callback.message, state)
    await callback.answer()


@router.message(CheckoutStates.entering_receipt_email)
async def on_receipt_email(message: Message, state: FSMContext, api) -> None:
    session = await get_session(state, str(message.from_user.id))
    if not _checkout_ready_for_preview(session) or session.get("payment_method") != "card_prepayment":
        await _answer_checkout_stale(message)
        await state.set_state(None)
        return

    email = (message.text or "").strip()
    try:
        response = await api.identify_customer(
            {
                "channel": CHANNEL,
                "external_user_id": session["external_user_id"],
                "email": email,
                "username": session.get("username", ""),
                "display_name": session.get("display_name", ""),
            }
        )
    except (ApiError, BackendUnavailableError) as exc:
        await answer_api_error(message, exc)
        await message.answer("Проверьте email и отправьте его ещё раз.")
        return
    if response.get("status") != "identified":
        await message.answer("Не удалось сохранить email. Отправьте корректный адрес ещё раз.")
        return

    await update_session(state, checkout_email=email.lower())
    await _ask_for_comment(message, state)


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
        preview = session.get("checkout_preview")
        if session["receiving_type"] == "pickup" or not preview:
            preview = await api.checkout_preview(
                channel=CHANNEL,
                external_user_id=session["external_user_id"],
                customer_id=session["customer_id"],
                receiving_type=session["receiving_type"],
                payment_method=session.get("payment_method") or "card_prepayment",
            )
    except (ApiError, BackendUnavailableError) as exc:
        await answer_api_error(message, exc)
        return

    await update_session(state, checkout_preview=preview)
    payment_label = (
        "Карта онлайн"
        if session.get("payment_method") == "card_prepayment"
        else "Наличными при получении"
    )
    details = _format_preview(preview)
    if session.get("receiving_type") == "delivery":
        details += f"\nАдрес: {session.get('delivery_address')}"
    details += f"\nОплата: {payment_label}"
    await message.answer(details, reply_markup=confirm_order_keyboard())


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
        "customer_email": session.get("checkout_email") or "",
        "delivery_quote_id": session.get("delivery_quote_id"),
        "is_new_customer": session.get("is_new_customer", False),
    }

    try:
        order = await api.create_order(payload)
    except (ApiError, BackendUnavailableError) as exc:
        await answer_api_error(callback, exc)
        await callback.answer()
        return

    payment = None
    payment_error = None
    if session["payment_method"] == "card_prepayment":
        try:
            payment = await api.create_payment(
                order["public_number"],
                channel=CHANNEL,
                external_user_id=session["external_user_id"],
            )
        except (ApiError, BackendUnavailableError) as exc:
            payment_error = exc

    await state.set_state(None)
    await callback.message.answer(
        "<b>Ваш заказ оформлен.</b> "
        "При необходимости наш менеджер свяжется с вами.\n\n"
        f"Номер: <b>{order['public_number']}</b>\n"
        f"Сумма: {format_price(order['total_amount'])}",
        reply_markup=main_menu_keyboard(),
    )
    if payment and payment.get("confirmation_url"):
        await callback.message.answer(
            "Для оплаты банковской картой перейдите по ссылке:",
            reply_markup=payment_link_keyboard(payment["confirmation_url"]),
        )
    elif payment_error is not None:
        await callback.message.answer(
            "Заказ сохранён, но ссылку на оплату сейчас создать не удалось. "
            "Менеджер проверит оплату и при необходимости свяжется с вами."
        )

    await update_session(
        state,
        receiving_type=None,
        delivery_address="",
        payment_method=None,
        customer_comment="",
        checkout_preview=None,
        delivery_quote_id=None,
        delivery_confirmed=False,
        checkout_email="",
    )
    await callback.answer()
