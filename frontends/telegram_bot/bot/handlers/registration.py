"""Регистрация через Telegram contact button."""
import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.api.client import StorefrontApiClient
from bot.api.errors import ApiError, BackendUnavailableError
from bot.constants import (
    AI_ASSISTANT_WELCOME,
    REGISTRATION_REMINDER_TEXT,
    REGISTRATION_TEXT,
    WRONG_CONTACT_TEXT,
)
from bot.services.error_messages import CONFLICT_USER_MESSAGE, is_phone_validation_error
from bot.handlers.common import answer_api_error, telegram_user_context
from bot.keyboards.reply import main_menu_keyboard, registration_contact_keyboard, remove_keyboard
from bot.services.session import apply_identify_response, get_session, save_session
from bot.states import RegistrationStates

logger = logging.getLogger(__name__)
router = Router(name="registration")


async def prompt_registration(message: Message, state: FSMContext) -> None:
    await state.set_state(RegistrationStates.waiting_contact)
    await message.answer(REGISTRATION_TEXT, reply_markup=registration_contact_keyboard())


@router.message(RegistrationStates.waiting_contact, F.contact)
async def on_contact(
    message: Message,
    state: FSMContext,
    api: StorefrontApiClient,
) -> None:
    contact = message.contact
    if contact.user_id != message.from_user.id:
        await message.answer(WRONG_CONTACT_TEXT, reply_markup=registration_contact_keyboard())
        return

    user_ctx = telegram_user_context(message.from_user)
    session = await get_session(state, user_ctx["external_user_id"])

    payload = {
        "channel": user_ctx["channel"],
        "external_user_id": user_ctx["external_user_id"],
        "phone": contact.phone_number,
        "phone_verification_source": "platform_contact",
        "username": user_ctx["username"],
        "display_name": user_ctx["display_name"],
    }

    try:
        response = await api.identify_customer(payload)
    except ApiError as exc:
        if exc.code == "channel_identity_conflict" or (
            exc.status_code == 409 and exc.code in {"channel_identity_conflict", "api_error"}
        ):
            await state.clear()
            await message.answer(CONFLICT_USER_MESSAGE, reply_markup=remove_keyboard())
            return
        if is_phone_validation_error(exc):
            await message.answer(
                "Номер телефона указан неверно. Формат: 79991234567. "
                "Попробуйте снова через кнопку регистрации.",
                reply_markup=registration_contact_keyboard(),
            )
            return
        await answer_api_error(message, exc)
        return
    except BackendUnavailableError as exc:
        await answer_api_error(message, exc)
        return

    if response.get("status") == "conflict":
        await state.clear()
        await message.answer(CONFLICT_USER_MESSAGE, reply_markup=remove_keyboard())
        return

    if response.get("status") != "identified":
        await message.answer(
            "Не удалось завершить регистрацию. Попробуйте снова.",
            reply_markup=registration_contact_keyboard(),
        )
        return

    session = apply_identify_response(session, response)
    await save_session(state, session)
    # Важно: очищаем только FSM-state, но сохраняем session с customer_id.
    await state.set_state(None)

    name = session.get("display_name") or "друг"
    await message.answer(
        f"Регистрация завершена. Здравствуйте, {name}!",
        reply_markup=main_menu_keyboard(),
    )
    await message.answer(AI_ASSISTANT_WELCOME, reply_markup=main_menu_keyboard())


@router.message(RegistrationStates.waiting_contact)
async def on_registration_non_contact(message: Message) -> None:
    await message.answer(REGISTRATION_REMINDER_TEXT, reply_markup=registration_contact_keyboard())
