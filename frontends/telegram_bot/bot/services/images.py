"""Загрузка изображений товаров для Telegram."""
from urllib.parse import urljoin, urlparse

import httpx
from aiogram.types import BufferedInputFile


def resolve_image_url(image_url: str, backend_base_url: str) -> str:
    if not image_url:
        return ""
    if image_url.startswith(("http://web:", "http://localhost:", "https://localhost:")):
        parsed = urlparse(image_url)
        path = parsed.path
        if parsed.query:
            path = f"{path}?{parsed.query}"
        return urljoin(backend_base_url.rstrip("/") + "/", path.lstrip("/"))
    return image_url


async def load_image_file(
    image_url: str,
    backend_base_url: str,
    *,
    filename: str = "product.jpg",
    timeout: float = 10.0,
) -> BufferedInputFile | None:
    resolved = resolve_image_url(image_url, backend_base_url)
    if not resolved:
        return None
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(resolved)
            response.raise_for_status()
        return BufferedInputFile(response.content, filename=filename)
    except Exception:
        return None
