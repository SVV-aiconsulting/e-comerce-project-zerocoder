from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def product_keyboard(product_id: int, quantity_label: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="−", callback_data=f"prod:dec:{product_id}"),
                InlineKeyboardButton(text=quantity_label, callback_data="prod:noop"),
                InlineKeyboardButton(text="+", callback_data=f"prod:inc:{product_id}"),
            ],
            [InlineKeyboardButton(text="Добавить в корзину", callback_data=f"prod:add:{product_id}")],
        ]
    )


def cart_item_keyboard(product_id: int, quantity_label: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="−", callback_data=f"cart:dec:{product_id}"),
                InlineKeyboardButton(text=quantity_label, callback_data="cart:noop"),
                InlineKeyboardButton(text="+", callback_data=f"cart:inc:{product_id}"),
            ],
            [InlineKeyboardButton(text="Удалить", callback_data=f"cart:del:{product_id}")],
        ]
    )


def cart_footer_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Оформить заказ", callback_data="checkout:start")],
        ]
    )


def receiving_type_keyboard(options: list) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=item["label"], callback_data=f"checkout:recv:{item['value']}")]
            for item in options
        ]
    )


def payment_method_keyboard(options: list) -> InlineKeyboardMarkup:
    allowed = {"cash_on_delivery", "card_on_delivery"}
    rows = [
        [InlineKeyboardButton(text=item["label"], callback_data=f"checkout:pay:{item['value']}")]
        for item in options
        if item["value"] in allowed
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_order_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Подтвердить заказ", callback_data="checkout:confirm")],
            [InlineKeyboardButton(text="Отмена", callback_data="nav:menu")],
        ]
    )


def skip_comment_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Пропустить", callback_data="checkout:skip_comment")]]
    )


def orders_list_keyboard(orders: list) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"{o['public_number']} — {o['total_amount']} ₽", callback_data=f"order:open:{o['public_number']}")]
        for o in orders[:10]
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def order_detail_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[])
