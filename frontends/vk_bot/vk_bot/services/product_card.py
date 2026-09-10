"""Отображение карточки товара в VK."""
from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from decimal import Decimal
from typing import TYPE_CHECKING, Awaitable, Callable

from vk_bot.keyboards import product_card_keyboard
from vk_bot.services.formatting import format_product_card, format_quantity
from vk_bot.services.images import load_image_bytes
from vk_bot.utils import parse_decimal, send_message

if TYPE_CHECKING:
    from vkbottle.tools import PhotoMessageUploader
    from vkbottle.tools import VKAPI


logger = logging.getLogger(__name__)


class ProductPhotoAttachmentCache:
    """Кэш уже загруженных в VK фотографий.

    VK возвращает постоянную строку attachment после saveMessagesPhoto. Повторная
    загрузка одной и той же фотографии для каждого открытия каталога была самой
    дорогой частью выдачи и периодически попадала под лимит API. Ключом служит
    URL изображения: когда менеджер заменяет файл, URL меняется и фото будет
    загружено заново.
    """

    def __init__(self, max_entries: int = 300) -> None:
        self.max_entries = max_entries
        self._attachments: OrderedDict[str, str] = OrderedDict()
        self._locks: dict[str, asyncio.Lock] = {}

    async def get_or_create(
        self,
        key: str,
        factory: Callable[[], Awaitable[str | None]],
    ) -> str | None:
        cached = self._attachments.get(key)
        if cached:
            self._attachments.move_to_end(key)
            return cached

        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            cached = self._attachments.get(key)
            if cached:
                self._attachments.move_to_end(key)
                return cached

            attachment = await factory()
            if attachment:
                self._attachments[key] = attachment
                self._attachments.move_to_end(key)
                while len(self._attachments) > self.max_entries:
                    self._attachments.popitem(last=False)
            return attachment


async def _upload_image_with_retry(
    photo_uploader: PhotoMessageUploader,
    image_bytes: bytes,
    peer_id: int,
) -> str | None:
    for attempt in range(3):
        try:
            return await photo_uploader.upload(image_bytes, peer_id=peer_id)
        except Exception as exc:
            logger.warning(
                "VK product photo upload failed (attempt %s/3): %s",
                attempt + 1,
                exc,
            )
            if attempt < 2:
                await asyncio.sleep(0.4 * (attempt + 1))
    return None


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
    media_base_url: str,
    photo_cache: ProductPhotoAttachmentCache | None = None,
) -> str | None:
    if photo_uploader is None:
        return None
    image_bytes = await load_image_bytes(image_url, media_base_url)
    if not image_bytes:
        logger.warning("Product card has no downloadable image: %s", image_url or "<empty>")
        return None

    async def upload() -> str | None:
        return await _upload_image_with_retry(photo_uploader, image_bytes, peer_id)

    if photo_cache is None:
        return await upload()
    return await photo_cache.get_or_create(image_url, upload)


async def send_product_card(
    api: VKAPI,
    peer_id: int,
    product: dict,
    quantity: Decimal,
    *,
    media_base_url: str,
    photo_uploader: PhotoMessageUploader | None = None,
    photo_cache: ProductPhotoAttachmentCache | None = None,
    attachment: str | None = None,
    attachment_prepared: bool = False,
) -> None:
    text = format_product_card(product)
    keyboard = product_card_keyboard(product["id"], format_quantity(quantity))
    if not attachment_prepared and attachment is None:
        attachment = await upload_product_photo(
            photo_uploader,
            peer_id,
            product.get("main_image_url") or "",
            media_base_url,
            photo_cache,
        )
    await send_message(api, peer_id, text, keyboard, attachment=attachment)


async def update_product_card_event(
    event,
    product: dict,
    quantity: Decimal,
    *,
    media_base_url: str = "",
    photo_uploader: PhotoMessageUploader | None = None,
    photo_cache: ProductPhotoAttachmentCache | None = None,
) -> None:
    text = format_product_card(product)
    keyboard = product_card_keyboard(product["id"], format_quantity(quantity))
    attachment = None
    if media_base_url:
        attachment = await upload_product_photo(
            photo_uploader,
            event.peer_id,
            product.get("main_image_url") or "",
            media_base_url,
            photo_cache,
        )
    try:
        await event.edit_message(
            message=text,
            keyboard=keyboard.get_json(),
            attachment=attachment,
        )
    except Exception:
        if media_base_url:
            await send_product_card(
                event.ctx_api,
                event.peer_id,
                product,
                quantity,
                media_base_url=media_base_url,
                photo_uploader=photo_uploader,
                photo_cache=photo_cache,
            )
