"""История и детали заказов."""
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.api.errors import ApiError, BackendUnavailableError
from bot.constants import CHANNEL
from bot.handlers.common import answer_api_error
from bot.keyboards.inline import orders_list_keyboard
from bot.services.formatting import format_datetime, format_price, format_quantity
from bot.services.identify import require_identified_callback
from bot.services.session import get_session

router = Router(name="orders")


async def show_orders_list(
    message: Message,
    state: FSMContext,
    api,
    *,
    user_id: int,
) -> None:
    session = await get_session(state, str(user_id))
    public_code = session.get("customer_public_code")
    if not public_code:
        await message.answer("Сначала пройдите регистрацию: /start")
        return

    try:
        orders = await api.list_customer_orders(
            public_code,
            channel=CHANNEL,
            external_user_id=session["external_user_id"],
        )
    except (ApiError, BackendUnavailableError) as exc:
        await answer_api_error(message, exc)
        return

    if not orders:
        await message.answer("У вас пока нет заказов.")
        return

    lines = ["<b>Мои заказы</b>\n"]
    for order in orders[:10]:
        lines.append(
            f"• <b>{order['public_number']}</b> — {format_price(order['total_amount'])}\n"
            f"  {format_datetime(order['created_at'])} — {order['order_status_label']}"
        )

    await message.answer("\n".join(lines), reply_markup=orders_list_keyboard(orders))


@router.callback_query(F.data == "orders:list")
async def callback_orders_list(callback: CallbackQuery, state: FSMContext, api) -> None:
    await show_orders_list(
        callback.message,
        state,
        api,
        user_id=callback.from_user.id,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("order:open:"))
async def callback_order_detail(callback: CallbackQuery, state: FSMContext, api) -> None:
    session = await require_identified_callback(callback, state, api)
    if session is None:
        return
    public_number = callback.data.split(":", 2)[-1]

    try:
        order = await api.get_order(
            public_number,
            channel=CHANNEL,
            external_user_id=session["external_user_id"],
        )
    except (ApiError, BackendUnavailableError) as exc:
        await answer_api_error(callback, exc)
        await callback.answer()
        return

    items_lines = []
    for item in order.get("items") or []:
        items_lines.append(
            f"• {item['product_name_snapshot']}: "
            f"{format_quantity(item['quantity'])} × {format_price(item['unit_price'])} "
            f"= {format_price(item['total_price'])}"
        )

    text = (
        f"<b>Заказ {order['public_number']}</b>\n\n"
        f"Статус: {order['order_status_label']}\n"
        f"Оплата: {order['payment_method_label']}\n"
        f"Получение: {order['receiving_type_label']}\n"
        f"Дата: {format_datetime(order['created_at'])}\n"
        f"Сумма: {format_price(order['total_amount'])}\n"
    )
    if order.get("delivery_address"):
        text += f"Адрес: {order['delivery_address']}\n"
    if items_lines:
        text += "\n<b>Позиции:</b>\n" + "\n".join(items_lines)

    await callback.message.answer(text)
    await callback.answer()
