"""Каталог товаров."""
from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.api.errors import ApiError, BackendUnavailableError
from bot.config import get_settings
from bot.handlers.common import answer_api_error
from bot.services.product_card import get_product_quantity, send_product_card, sync_catalog_quantities
from bot.services.session import get_session, save_session

router = Router(name="catalog")


async def show_catalog(
    message: Message,
    state: FSMContext,
    api,
    *,
    user_id: int,
) -> None:
    try:
        products = await api.list_products()
    except (ApiError, BackendUnavailableError) as exc:
        await answer_api_error(message, exc)
        return

    if not products:
        await message.answer("В каталоге пока нет товаров.")
        return

    session = await get_session(state, str(user_id))
    session = sync_catalog_quantities(session, products)
    await save_session(state, session)

    settings = get_settings()
    await message.answer("<b>Каталог</b>")

    for product in products:
        quantity = get_product_quantity(session, product)
        await send_product_card(
            message,
            product,
            quantity,
            media_base_url=(
                settings.product_media_base_url or settings.backend_api_base_url
            ),
        )
