"""Отображение карточки товара в чате."""
from decimal import Decimal

from aiogram.types import CallbackQuery, Message

from bot.keyboards.inline import product_keyboard
from bot.services.formatting import format_price, format_quantity, truncate_text
from bot.services.images import load_image_file


def parse_decimal(value: str | Decimal) -> Decimal:
    return Decimal(str(value))


def format_product_card(product: dict) -> str:
    return (
        f"<b>{product['name']}</b>\n\n"
        f"{truncate_text(product.get('description') or '')}\n\n"
        f"Цена: {format_price(product['base_price'])}\n"
        f"Единица: {product['unit_label']}\n"
        f"Мин. количество: {format_quantity(product['min_quantity'])}"
    )


def quantity_label(value: Decimal) -> str:
    return format_quantity(value)


def get_product_quantity(session: dict, product: dict) -> Decimal:
    product_id = str(product["id"])
    quantities = session.get("product_quantities") or {}
    min_qty = parse_decimal(product["min_quantity"])
    raw = quantities.get(product_id)
    if raw is None:
        return min_qty
    try:
        qty = parse_decimal(raw)
    except Exception:
        return min_qty
    return max(min_qty, qty)


def sync_catalog_quantities(session: dict, products: list) -> dict:
    quantities = dict(session.get("product_quantities") or {})
    min_quantities = dict(session.get("product_min_quantities") or {})
    product_codes = dict(session.get("product_codes") or {})

    for product in products:
        product_id = str(product["id"])
        min_qty = str(product["min_quantity"])
        min_quantities[product_id] = min_qty
        product_codes[product_id] = product["public_code"]
        if product_id not in quantities:
            quantities[product_id] = min_qty

    session["product_quantities"] = quantities
    session["product_min_quantities"] = min_quantities
    session["product_codes"] = product_codes
    return session


async def send_product_card(
    message: Message,
    product: dict,
    quantity: Decimal,
    *,
    media_base_url: str,
) -> None:
    text = format_product_card(product)
    keyboard = product_keyboard(product["id"], quantity_label(quantity))
    image_url = product.get("main_image_url") or ""
    photo_file = await load_image_file(image_url, media_base_url, filename="product.jpg")
    if photo_file is not None:
        await message.answer_photo(photo=photo_file, caption=text, reply_markup=keyboard)
        return
    await message.answer(text, reply_markup=keyboard)


async def update_product_card_message(
    callback: CallbackQuery,
    product: dict,
    quantity: Decimal,
) -> None:
    text = format_product_card(product)
    keyboard = product_keyboard(product["id"], quantity_label(quantity))
    message = callback.message
    if message.photo:
        await message.edit_caption(caption=text, reply_markup=keyboard)
    else:
        await message.edit_text(text, reply_markup=keyboard)
