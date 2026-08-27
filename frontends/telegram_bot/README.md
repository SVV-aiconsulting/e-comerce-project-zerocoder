# Telegram-бот WebMarket

Frontend-адаптер магазина. Общается с backend **только** через REST API (`/api/*`), без импорта Django models.

**Полная документация:** [docs/TELEGRAM_BOT.md](../../docs/TELEGRAM_BOT.md)

## Быстрый старт

```bash
# Docker (из корня проекта)
docker compose up --build -d
docker compose logs -f telegram_bot

# Локально
cd frontends/telegram_bot
pip install -e ".[dev]"
python -m bot
```

## Тесты

```bash
cd frontends/telegram_bot
pytest
```
