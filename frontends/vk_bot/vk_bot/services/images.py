"""Загрузка изображений товаров для VK."""
import logging
from urllib.parse import urljoin, urlparse

import httpx

logger = logging.getLogger(__name__)


def resolve_image_url(image_url: str, media_base_url: str) -> str:
    if not image_url:
        return ""
    if image_url.startswith(("http://web:", "http://localhost:", "https://localhost:")):
        parsed = urlparse(image_url)
        path = parsed.path
        if parsed.query:
            path = f"{path}?{parsed.query}"
        return urljoin(media_base_url.rstrip("/") + "/", path.lstrip("/"))
    return image_url


async def load_image_bytes(
    image_url: str,
    media_base_url: str,
    *,
    timeout: float = 10.0,
) -> bytes | None:
    resolved = resolve_image_url(image_url, media_base_url)
    if not resolved:
        return None
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(resolved)
            response.raise_for_status()
        return response.content
    except Exception as exc:
        logger.warning("Failed to download product image: %s", exc)
        return None
