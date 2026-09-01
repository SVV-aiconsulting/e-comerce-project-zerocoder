"""Тесты получения фото карточек Telegram-ботом."""

from bot.services.images import resolve_image_url


def test_resolve_image_url_rewrites_docker_host_to_internal_media_server():
    url = "http://web:8000/media/products/sea-urchin.jpg"

    assert (
        resolve_image_url(url, "http://nginx:8080")
        == "http://nginx:8080/media/products/sea-urchin.jpg"
    )


def test_resolve_image_url_keeps_public_url():
    url = "https://cdn.example.com/product.jpg"

    assert resolve_image_url(url, "http://nginx:8080") == url
