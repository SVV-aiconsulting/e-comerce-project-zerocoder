from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.keyboards.inline import personal_data_consent_keyboard

router = Router(name="privacy")


def consent_text(status: dict) -> str:
    return (
        "Для регистрации и оформления заказа требуется Ваше отдельное согласие "
        "на обработку персональных данных.\n\n"
        f"Политика: {status['policy_url']}\n"
        f"Согласие: {status['consent_url']}"
    )


async def ensure_consent(message: Message, api) -> bool:
    status = await api.get_personal_data_consent(channel="telegram", external_user_id=str(message.from_user.id))
    if status.get("granted"):
        return True
    await message.answer(consent_text(status), reply_markup=personal_data_consent_keyboard())
    return False


@router.callback_query(F.data.in_({"privacy:granted", "privacy:declined"}))
async def consent_callback(callback: CallbackQuery, state: FSMContext, api) -> None:
    action = callback.data.split(":", 1)[1]
    await api.record_personal_data_consent(
        channel="telegram", external_user_id=str(callback.from_user.id),
        action=action, source="telegram_bot_button",
    )
    await callback.answer()
    if action == "granted":
        await callback.message.answer("Согласие сохранено. Для продолжения нажмите /start.")
    else:
        await state.clear()
        await callback.message.answer("Без обработки необходимых данных регистрация и оформление заказа недоступны.")


@router.message(Command("privacy_withdraw"))
async def withdraw_consent(message: Message, state: FSMContext, api) -> None:
    await api.record_personal_data_consent(
        channel="telegram", external_user_id=str(message.from_user.id),
        action="withdrawn", source="telegram_bot_command",
    )
    await state.clear()
    await message.answer("Согласие отозвано. Новые операции с персональными данными остановлены; сведения по заказам могут храниться на ином законном основании.")
