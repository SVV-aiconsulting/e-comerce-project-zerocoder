from aiogram.types import KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove

from bot.constants import (
    MENU_CART,
    MENU_CATALOG,
    MENU_HELP,
    MENU_ORDERS,
    REGISTRATION_BUTTON_TEXT,
)


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=MENU_CATALOG), KeyboardButton(text=MENU_CART)],
            [KeyboardButton(text=MENU_ORDERS), KeyboardButton(text=MENU_HELP)],
        ],
        resize_keyboard=True,
    )


def registration_contact_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=REGISTRATION_BUTTON_TEXT, request_contact=True)],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def remove_keyboard() -> ReplyKeyboardRemove:
    return ReplyKeyboardRemove()
