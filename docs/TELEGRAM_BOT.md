# Telegram-бот WebMarket — настройка и запуск

Telegram-бот — frontend-адаптер продаж. Работает через тот же REST API, что и VK-бот и будущие каналы (MAX, сайт). Бизнес-логика магазина находится только в Django backend.

## 1. Создание бота в BotFather

1. Откройте [@BotFather](https://t.me/BotFather) в Telegram.
2. Команда `/newbot` → задайте **имя** (отображаемое) и **username** (должен заканчиваться на `bot`).
3. Скопируйте выданный токен в `.env`: `TELEGRAM_BOT_TOKEN=...`
4. (Рекомендуется) `/setprivacy` → **Disable** — если бот должен видеть все сообщения в группах. Для личных чатов магазина достаточно стандартных настроек.
5. (Опционально) `/setcommands` — задайте команды, например:
   - `start` — Начать работу с магазином

Бот работает **только в личных чатах** (`PrivateChatMiddleware` отклоняет группы и каналы).

## 2. Переменные окружения

В `.env` (см. `.env.example`):

```env
TELEGRAM_BOT_TOKEN=<токен-от-BotFather>
ADAPTER_API_TOKEN=<тот-же-токен-что-в-ADAPTER_API_TOKENS>
BACKEND_API_BASE_URL=http://web:8000
TELEGRAM_BOT_USE_POLLING=true
TELEGRAM_BOT_LOG_LEVEL=INFO
TELEGRAM_AI_POLL_ATTEMPTS=20
TELEGRAM_AI_POLL_INTERVAL_SECONDS=0.75
# TELEGRAM_PROXY=socks5://127.0.0.1:1080
```

| Переменная | Назначение |
|------------|------------|
| `TELEGRAM_BOT_TOKEN` | Токен от BotFather |
| `ADAPTER_API_TOKEN` | Заголовок `X-Adapter-Token` для REST API |
| `BACKEND_API_BASE_URL` | URL backend (`http://web:8000` в Docker, `http://localhost:8000` локально, `http://nginx` на VPS) |
| `TELEGRAM_BOT_USE_POLLING` | `true` — единственный поддерживаемый режим (webhook не реализован) |
| `TELEGRAM_BOT_LOG_LEVEL` | `INFO`, `DEBUG`, … |
| `TELEGRAM_AI_POLL_ATTEMPTS` | Число проверок результата AI-события |
| `TELEGRAM_AI_POLL_INTERVAL_SECONDS` | Интервал между проверками результата |
| `TELEGRAM_PROXY` | Опционально: HTTP/SOCKS proxy для доступа к `api.telegram.org` (РФ / корпоративная сеть) |

`ADAPTER_API_TOKEN` должен совпадать с одним из значений `ADAPTER_API_TOKENS` в backend.

## 3. Локальный запуск

### Без Docker

```bash
cd frontends/telegram_bot
pip install -e ".[dev]"
# backend на :8000, в .env: TELEGRAM_BOT_TOKEN, ADAPTER_API_TOKEN, BACKEND_API_BASE_URL
python -m bot
```

### Docker Compose

```bash
# backend + telegram (по умолчанию)
docker compose up --build -d

docker compose logs -f telegram_bot
```

Сервис `telegram_bot` зависит от `web`, но при падении бота backend, API и Django Admin продолжают работать.

После изменения кода бота пересоберите образ:

```bash
docker compose up --build -d telegram_bot
```

## 4. Production (VPS)

Образ собирается в GitHub Actions: `ghcr.io/svv-aiconsulting/webmarket-telegram-bot`.

Telegram-бот входит в стандартный `docker-compose.prod.yml` и **стартует автоматически** вместе с `nginx`, `web` и `db`.

На VPS в `.env`:

```env
TELEGRAM_BOT_TOKEN=<токен>
ADAPTER_API_TOKEN=<токен-адаптера>
BACKEND_API_BASE_URL=http://nginx
TELEGRAM_BOT_USE_POLLING=true
```

`BACKEND_API_BASE_URL=http://nginx` нужен, чтобы бот корректно загружал изображения товаров из `/media/` через nginx.

## 5. Ручная проверка сценария

Перед тестом загрузите демо-данные (если каталог пуст):

```bash
docker compose exec web python manage.py load_demo_data
```

Сценарий в Telegram:

1. `/start` — идентификация через `POST /api/identify-customer/`.
2. Если нужна регистрация — кнопка **«Зарегистрироваться по номеру телефона»** (`request_contact`).
3. **Каталог** — отдельная карточка на каждый товар (фото, описание, `+` / `−`, «Добавить в корзину»). Количество и фото обновляются **на месте** (редактирование сообщения).
4. **Корзина** (кнопка в нижнем меню) — позиции с `+` / `−` и «Удалить»; итого обновляется динамически без пересылки всей корзины.
5. **Оформить заказ** — доставка/самовывоз, адрес, оплата, подтверждение.
6. **Мои заказы** — история и детали.
7. В Django Admin проверьте заказ и клиента с `channel=telegram`.

Новый Telegram ID всегда получает карточку текущего канала. Если переданный контакт
уже записан в другой карточке, создаётся неблокирующий конфликт идентификации; заказ
продолжает оформляться через Telegram-карточку.

Свободное текстовое сообщение, не занятое командой, кнопкой или FSM checkout,
передаётся в единый `intake`. Бот получает уточнение, сумму к подтверждению или номер
созданного заказа через защищённый polling endpoint.

## 6. Логи

```bash
docker compose logs -f telegram_bot
# production:
docker compose -f docker-compose.prod.yml logs -f telegram_bot
```

## 7. Отключить Telegram-бот без остановки backend

```bash
docker compose stop telegram_bot
# или production:
docker compose -f docker-compose.prod.yml stop telegram_bot
```

Backend, nginx и Django Admin продолжат работать.

## 8. Прокси (РФ / корпоративная сеть)

Если контейнер не достучится до `api.telegram.org`, задайте в `.env`:

```env
TELEGRAM_PROXY=http://host.docker.internal:10801
```

Перезапуск:

```bash
docker compose up -d telegram_bot
```

## 9. Сессия и перезапуск бота

Бот использует `MemoryStorage` (aiogram FSM): состояние оформления заказа **сбрасывается при перезапуске** контейнера.

При нажатии старых inline-кнопок бот пытается восстановить клиента через `identify-customer`. Данные checkout (способ получения, оплата) **не восстанавливаются** — пользователь должен начать оформление из корзины заново.

Для production с высокой нагрузкой можно позже заменить storage на Redis.

## 10. UX и навигация

- **Каталог** — полные карточки товаров друг под другом.
- **Корзина** — цена за единицу в тексте позиции; количество — в центральной inline-кнопке; итого — отдельным сообщением внизу.
- **Очистить корзину** — кнопка в footer корзины (inline). API `DELETE /api/cart/items/` доступен в клиенте.
- **Навигация** — основное меню (Каталог, Корзина, Мои заказы, Помощь) всегда в reply-клавиатуре внизу чата; лишние inline-кнопки «В меню» в корзине убраны.

## 11. Структура кода

```
frontends/telegram_bot/
├── bot/
│   ├── api/           # REST-клиент Storefront API
│   ├── handlers/      # start, catalog, cart, checkout, orders, …
│   ├── keyboards/     # reply и inline-клавиатуры
│   ├── middlewares/   # API client, private chat only
│   ├── services/      # identify, formatting, images, session
│   └── states/        # FSM регистрации
├── tests/
├── Dockerfile
└── pyproject.toml
```

Канал в API: `channel=telegram`. Внешний ID пользователя — `message.from_user.id`.

## 12. Тесты

```bash
cd frontends/telegram_bot
pip install -e ".[dev]"
pytest
```

Покрытие: API client, identify/session recovery, error mapping, registration handler, checkout session.

## Ограничения MVP

- Только long polling (`TELEGRAM_BOT_USE_POLLING=true`); webhook не реализован.
- Только личные чаты.
- FSM в памяти (без Redis).
- Регистрация через `request_contact` (номер из Telegram).

## Типовые ошибки

| Симптом | Что сделать |
|---------|-------------|
| «Магазин временно недоступен» | Проверить `curl http://localhost:8000/api/health/`, логи `web` |
| Бот не отвечает / timeout к Telegram | Задать `TELEGRAM_PROXY`, перезапустить контейнер |
| Фото товаров не грузятся на VPS | `BACKEND_API_BASE_URL=http://nginx`, в `DJANGO_ALLOWED_HOSTS` — `nginx` |
| `Backend not available at startup` | Дождаться `healthy` у `web` / `nginx`, перезапустить бота |
| Бот 400 при старте | Проверить `TELEGRAM_BOT_TOKEN` и доступность backend |
| `Invalid line: \ufeff# Django` | Сохранить `.env` в UTF-8 **без BOM** (Windows) |

## Связанные документы

- [docs/api.md](./api.md) — REST Storefront API
- [docs/VK_BOT.md](./VK_BOT.md) — VK-адаптер (аналогичный сценарий)
- [docs/PRODUCTION_OPERATIONS.md](./PRODUCTION_OPERATIONS.md) — backup, rollback, demo checklist на VPS
