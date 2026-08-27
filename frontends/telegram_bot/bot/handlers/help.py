"""Справка."""
from aiogram.types import Message

HELP_TEXT = (
    "<b>Помощь</b>\n\n"
    "Этот бот — витрина интернет-магазина.\n\n"
    "Доступные разделы:\n"
    "• <b>Каталог</b> — просмотр и добавление товаров\n"
    "• <b>Корзина</b> — просмотр и изменение заказа\n"
    "• <b>Мои заказы</b> — история покупок\n\n"
    "Для начала работы нажмите /start\n"
    "Для отмены текущего действия — /cancel"
)


async def send_help(message: Message) -> None:
    await message.answer(HELP_TEXT)
