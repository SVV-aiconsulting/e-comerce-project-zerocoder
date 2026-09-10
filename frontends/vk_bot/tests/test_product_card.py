"""Тесты изображений и карточек товаров VK-бота."""
from decimal import Decimal

from vk_bot.services.formatting import format_product_card
from vk_bot.services.images import resolve_image_url
from vk_bot.services.product_card import ProductPhotoAttachmentCache, get_product_quantity


def test_resolve_image_url_rewrites_docker_host_to_internal_media_server():
    url = "http://web:8000/media/products/sea-urchin.jpg"
    resolved = resolve_image_url(url, "http://nginx:8080")
    assert resolved == "http://nginx:8080/media/products/sea-urchin.jpg"


def test_resolve_image_url_keeps_public_url():
    url = "https://cdn.example.com/product.jpg"
    assert resolve_image_url(url, "http://localhost:8000") == url


def test_format_product_card_contains_name_and_price():
    product = {
        "name": "Морской еж",
        "description": "Свежий продукт",
        "base_price": "1500.00",
        "unit_label": "Штука",
        "min_quantity": "1",
    }
    text = format_product_card(product)
    assert "Морской еж" in text
    assert "1500" in text
    assert "Штука" in text


def test_get_product_quantity_uses_session_or_minimum():
    product = {"id": 1, "min_quantity": "10"}
    session = {"product_quantities": {"1": "12"}}
    assert get_product_quantity(session, product) == Decimal("12")

    session = {"product_quantities": {}}
    assert get_product_quantity(session, product) == Decimal("10")


async def test_product_photo_cache_reuses_attachment_for_same_image():
    cache = ProductPhotoAttachmentCache()
    calls = 0

    async def upload():
        nonlocal calls
        calls += 1
        return "photo-1_2"

    assert await cache.get_or_create("https://images.example/product.jpg", upload) == "photo-1_2"
    assert await cache.get_or_create("https://images.example/product.jpg", upload) == "photo-1_2"
    assert calls == 1


async def test_product_photo_cache_does_not_cache_failed_upload():
    cache = ProductPhotoAttachmentCache()
    calls = 0

    async def upload():
        nonlocal calls
        calls += 1
        return None

    assert await cache.get_or_create("https://images.example/product.jpg", upload) is None
    assert await cache.get_or_create("https://images.example/product.jpg", upload) is None
    assert calls == 2


def test_product_card_keyboard_has_three_buttons_in_first_row():
    from vk_bot.keyboards import product_card_keyboard

    keyboard = product_card_keyboard(42, "10")
    data = __import__("json").loads(keyboard.get_json())
    first_row = data["buttons"][0]
    assert len(first_row) == 3
    assert first_row[1]["action"]["payload"]["cmd"] == "noop"


def test_cart_footer_keyboard_has_no_menu_button():
    from vk_bot.keyboards import cart_footer_keyboard

    keyboard = cart_footer_keyboard()
    data = __import__("json").loads(keyboard.get_json())
    labels = [
        button["action"]["label"]
        for row in data["buttons"]
        for button in row
    ]
    assert "В меню" not in labels
    assert "Оформить заказ" in labels
    assert "Очистить корзину" in labels


def test_privacy_consent_uses_explicit_callback_buttons():
    import json

    from vk_bot.keyboards import personal_data_consent_keyboard

    data = json.loads(personal_data_consent_keyboard().get_json())
    actions = [button["action"] for button in data["buttons"][0]]
    assert [action["label"] for action in actions] == ["Согласен", "Не согласен"]
    assert [action["payload"]["action"] for action in actions] == ["granted", "declined"]
    assert all(action["type"] == "callback" for action in actions)
