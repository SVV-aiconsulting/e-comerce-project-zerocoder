"""Отображение карточки товара в VK."""
from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from vk_bot.keyboards import product_card_keyboard
from vk_bot.services.formatting import format_product_card, format_quantity
from vk_bot.services.images import load_image_bytes
from vk_bot.utils import parse_decimal, send_message

if TYPE_CHECKING:
    from vkbottle.tools import PhotoMessageUploader
    from vkbottle.tools import VKAPI


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


async def upload_product_photo(
    photo_uploader: PhotoMessageUploader | None,
    peer_id: int,
    image_url: str,
    backend_base_url: str,
) -> str | None:
    if photo_uploader is None:
        return None
    image_bytes = await load_image_bytes(image_url, backend_base_url)
    if not image_bytes:
        return None
    try:
        return await photo_uploader.upload(image_bytes, peer_id=peer_id)
    except Exception:
        return None


async def send_product_card(
    api: VKAPI,
    peer_id: int,
    product: dict,
    quantity: Decimal,
    *,
    backend_base_url: str,
    photo_uploader: PhotoMessageUploader | None = None,
) -> None:
    text = format_product_card(product)
    keyboard = product_card_keyboard(product["id"], format_quantity(quantity))
    attachment = await upload_product_photo(
        photo_uploader,
        peer_id,
        product.get("main_image_url") or "",
        backend_base_url,
    )
    await send_message(api, peer_id, text, keyboard, attachment=attachment)


async def update_product_card_event(
    event,
    product: dict,
    quantity: Decimal,
    *,
    backend_base_url: str = "",
    photo_uploader: PhotoMessageUploader | None = None,
) -> None:
    text = format_product_card(product)
    keyboard = product_card_keyboard(product["id"], format_quantity(quantity))
    attachment = None
    if backend_base_url:
        attachment = await upload_product_photo(
            photo_uploader,
            event.peer_id,
            product.get("main_image_url") or "",
            backend_base_url,
        )
    try:
        await event.edit_message(
            message=text,
            keyboard=keyboard.get_json(),
            attachment=attachment,
        )
    except Exception:
        if backend_base_url:
            await send_product_card(
                event.ctx_api,
                event.peer_id,
                product,
                quantity,
                backend_base_url=backend_base_url,
                photo_uploader=photo_uploader,
            )
