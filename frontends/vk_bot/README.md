# VK-бот WebMarket

Frontend-адаптер продаж для VK-сообщества. Общается с backend **только** через REST API.

**Полная документация:** [docs/VK_BOT.md](../../docs/VK_BOT.md)

## Быстрый старт

```bash
# Docker (profile vk — не мешает telegram/backend)
docker compose --profile vk up --build -d vk_bot
docker compose logs -f vk_bot

# Локально
cd frontends/vk_bot
pip install -e ".[dev]"
python -m vk_bot
```

## Тесты

```bash
cd frontends/vk_bot
pytest
```
