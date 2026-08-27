"""Команда /start и первичная идентификация."""
import logging

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.api.client import StorefrontApiClient
from bot.api.errors import ApiError, BackendUnavailableError
from bot.handlers.catalog import show_catalog
from bot.handlers.common import answer_api_error
from bot.handlers.registration import prompt_registration
from bot.keyboards.reply import main_menu_keyboard
from bot.services.identify import identify_without_phone
from bot.services.session import SESSION_KEY, apply_identify_response, get_session, is_identified, save_session

logger = logging.getLogger(__name__)
router = Router(name="start")


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, api: StorefrontApiClient) -> None:
    user_ctx = message.from_user
    external_user_id = str(user_ctx.id)
    was_identified = is_identified(await get_session(state, external_user_id))

    await state.set_state(None)

    session = await get_session(state, external_user_id)
    session["username"] = user_ctx.username or ""
    display_name = " ".join(
        part for part in [user_ctx.first_name or "", user_ctx.last_name or ""] if part
    ).strip() or (user_ctx.username or "Покупатель")
    session["display_name"] = display_name
    await save_session(state, session)

    try:
        response = await identify_without_phone(api, user_ctx)
    except (ApiError, BackendUnavailableError) as exc:
        await answer_api_error(message, exc)
        return

    if response.get("status") == "identified":
        session = apply_identify_response(session, response)
        await save_session(state, session)

        if was_identified:
            await show_catalog(message, state, api, user_id=user_ctx.id)
            await message.answer("Меню:", reply_markup=main_menu_keyboard())
            return

        name = session.get("display_name") or "друг"
        await message.answer(
            f"Здравствуйте, {name}! Добро пожаловать в магазин.",
            reply_markup=main_menu_keyboard(),
        )
        await show_catalog(message, state, api, user_id=user_ctx.id)
        return

    if response.get("status") == "registration_required":
        await prompt_registration(message, state)
        return

    await message.answer("Не удалось выполнить вход. Попробуйте позже командой /start.")


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    session = data.get(SESSION_KEY)
    await state.clear()
    if session:
        await state.update_data({SESSION_KEY: session})
    await message.answer("Действие отменено.", reply_markup=main_menu_keyboard())
