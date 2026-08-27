"""Главное меню и навигация."""
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.api.errors import ApiError, BackendUnavailableError
from bot.constants import MENU_CART, MENU_CATALOG, MENU_HELP, MENU_ORDERS
from bot.handlers.catalog import show_catalog
from bot.handlers.cart import show_cart
from bot.handlers.common import answer_api_error
from bot.handlers.help import send_help
from bot.handlers.orders import show_orders_list
from bot.keyboards.reply import main_menu_keyboard
from bot.services.error_messages import NOT_IDENTIFIED_MESSAGE
from bot.services.identify import ensure_identified

router = Router(name="menu")


async def _require_identified(message: Message, state: FSMContext, api):
    try:
        return await ensure_identified(message, state, api)
    except (ApiError, BackendUnavailableError) as exc:
        await answer_api_error(message, exc)
        return None


@router.message(F.text == MENU_CATALOG)
@router.message(Command("catalog"))
async def menu_catalog(message: Message, state: FSMContext, api) -> None:
    session = await _require_identified(message, state, api)
    if session is None:
        await message.answer(NOT_IDENTIFIED_MESSAGE)
        return
    await show_catalog(message, state, api, user_id=message.from_user.id)


@router.message(F.text == MENU_CART)
@router.message(Command("cart"))
async def menu_cart(message: Message, state: FSMContext, api) -> None:
    session = await _require_identified(message, state, api)
    if session is None:
        await message.answer(NOT_IDENTIFIED_MESSAGE)
        return
    await show_cart(message, state, api, user_id=message.from_user.id)


@router.message(F.text == MENU_ORDERS)
@router.message(Command("orders"))
async def menu_orders(message: Message, state: FSMContext, api) -> None:
    session = await _require_identified(message, state, api)
    if session is None:
        await message.answer(NOT_IDENTIFIED_MESSAGE)
        return
    await show_orders_list(message, state, api, user_id=message.from_user.id)


@router.message(F.text == MENU_HELP)
@router.message(Command("help"))
async def menu_help(message: Message) -> None:
    await send_help(message)


@router.callback_query(F.data == "nav:menu")
async def callback_nav_menu(callback: CallbackQuery, state: FSMContext, api) -> None:
    await state.set_state(None)
    try:
        session = await ensure_identified(callback, state, api)
    except (ApiError, BackendUnavailableError) as exc:
        await answer_api_error(callback, exc)
        await callback.answer()
        return
    if session is None:
        await callback.message.answer(NOT_IDENTIFIED_MESSAGE)
        await callback.answer()
        return
    await show_catalog(callback.message, state, api, user_id=callback.from_user.id)
    await callback.message.answer("Меню:", reply_markup=main_menu_keyboard())
    await callback.answer()
