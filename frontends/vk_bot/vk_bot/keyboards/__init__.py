"""Клавиатуры VK-бота."""
from vkbottle import Callback, Keyboard, KeyboardButtonColor, Text

from vk_bot.constants import MENU_CART, MENU_CATALOG, MENU_HELP, MENU_ORDERS


def main_menu_keyboard() -> Keyboard:
    keyboard = Keyboard(one_time=False, inline=False)
    keyboard.add(Text(MENU_CATALOG), KeyboardButtonColor.PRIMARY)
    keyboard.add(Text(MENU_CART), KeyboardButtonColor.PRIMARY)
    keyboard.row()
    keyboard.add(Text(MENU_ORDERS), KeyboardButtonColor.PRIMARY)
    keyboard.add(Text(MENU_HELP), KeyboardButtonColor.SECONDARY)
    return keyboard


def empty_keyboard() -> Keyboard:
    return Keyboard(one_time=False, inline=False)


def product_card_keyboard(product_id: int, quantity_label: str) -> Keyboard:
    keyboard = Keyboard(inline=True)
    keyboard.add(Callback("−", payload={"cmd": "prod_dec", "id": product_id}))
    keyboard.add(Callback(quantity_label, payload={"cmd": "noop"}))
    keyboard.add(Callback("+", payload={"cmd": "prod_inc", "id": product_id}))
    keyboard.row()
    keyboard.add(Callback("Добавить в корзину", payload={"cmd": "prod_add", "id": product_id}))
    return keyboard


def cart_item_keyboard(product_id: int, quantity_label: str) -> Keyboard:
    keyboard = Keyboard(inline=True)
    keyboard.add(Callback("−", payload={"cmd": "cart_dec", "id": product_id}))
    keyboard.add(Callback(quantity_label, payload={"cmd": "noop"}))
    keyboard.add(Callback("+", payload={"cmd": "cart_inc", "id": product_id}))
    keyboard.row()
    keyboard.add(Callback("Удалить", payload={"cmd": "cart_del", "id": product_id}))
    return keyboard


def cart_footer_keyboard() -> Keyboard:
    keyboard = Keyboard(inline=True)
    keyboard.add(Callback("Оформить заказ", payload={"cmd": "checkout"}))
    keyboard.row()
    keyboard.add(Callback("Очистить корзину", payload={"cmd": "cart_clear"}))
    return keyboard


def receiving_type_keyboard(options: list) -> Keyboard:
    keyboard = Keyboard(inline=True)
    for item in options:
        keyboard.add(
            Callback(item["label"], payload={"cmd": "recv", "value": item["value"]})
        )
        keyboard.row()
    return keyboard


def payment_method_keyboard(options: list) -> Keyboard:
    allowed = {"cash_on_delivery", "card_on_delivery"}
    keyboard = Keyboard(inline=True)
    for item in options:
        if item["value"] not in allowed:
            continue
        keyboard.add(
            Callback(item["label"], payload={"cmd": "pay", "value": item["value"]})
        )
        keyboard.row()
    return keyboard


def skip_comment_keyboard() -> Keyboard:
    keyboard = Keyboard(inline=True)
    keyboard.add(Callback("Пропустить", payload={"cmd": "skip_comment"}))
    return keyboard


def confirm_order_keyboard() -> Keyboard:
    keyboard = Keyboard(inline=True)
    keyboard.add(Callback("Подтвердить заказ", payload={"cmd": "confirm"}))
    keyboard.row()
    keyboard.add(Callback("Отмена", payload={"cmd": "menu"}))
    return keyboard


def orders_list_keyboard(orders: list) -> Keyboard:
    keyboard = Keyboard(inline=True)
    for order in orders[:10]:
        keyboard.add(
            Callback(
                f"{order['public_number']} — {order['total_amount']} ₽",
                payload={"cmd": "order", "num": order["public_number"]},
            )
        )
        keyboard.row()
    keyboard.add(Callback("В меню", payload={"cmd": "menu"}))
    return keyboard
