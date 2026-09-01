# VK-бот WebMarket — настройка и запуск

VK-бот — второй frontend-адаптер продаж. Работает через тот же REST API, что и Telegram-бот.

## 1. Создание сообщества VK

1. Откройте [vk.com/groups](https://vk.com/groups) → **Создать сообщество** → **Бизнес** или **Тематическое**.
2. Перейдите в **Управление** → **Сообщения** → включите **Сообщения сообщества**.
3. В разделе **Настройки для бота** включите возможность писать боту (доступ к API сообщений).

## 2. Получение токена и ID группы

1. **Управление** → **Дополнительно** → **Работа с API** → **Создать ключ**.
2. Выдайте права: **Управление сообществом**, **Сообщения сообщества**, **Фотографии** (нужно для фото товаров в каталоге).
3. Скопируйте токен в `VK_BOT_TOKEN`.
4. ID группы — число в адресе `vk.com/club123456789` → `VK_GROUP_ID=123456789` (без минуса; в API peer_id будет отрицательным автоматически).

## 2.1. Включить Long Poll API (обязательно)

Без этого бот падает с ошибкой:

```text
longpoll for this group is not enabled
```

В сообществе VK:

1. **Управление сообществом** → **Дополнительно** → **Работа с API**.
2. Откройте вкладку **Long Poll API**.
3. Переключите в **Включено**.
4. Версия API: `5.199` или новее (как в документации VK).
5. В типах событий в разделе **Сообщения** отметьте минимум:
   - **Входящее сообщение** (`message_new`)
   - **Действие с сообщением** (`message_event`) — это нажатия на inline-кнопки (`+`, `−`, «Добавить в корзину» и т.д.)

   В интерфейсе VK нет отдельного пункта «callback-кнопка» — нужен именно **«Действие с сообщением»**.

6. После изменения типов событий **пересоберите** VK-бота (для обновления кода недостаточно `restart`):

```bash
docker compose --profile vk up --build -d vk_bot
```

Также проверьте:

- **Сообщения сообщества** — включены (раздел **Сообщения**).
- Токен — ключ **сообщества**, не личный пользовательский.

## 3. Переменные окружения

В `.env` (см. `.env.example`):

```env
VK_BOT_TOKEN=<токен-сообщества>
VK_GROUP_ID=<id-группы>
VK_BOT_LOG_LEVEL=INFO
VK_BOT_USE_LONGPOLL=True
VK_AI_POLL_ATTEMPTS=20
VK_AI_POLL_INTERVAL_SECONDS=0.75

BACKEND_API_BASE_URL=http://web:8000
PRODUCT_MEDIA_BASE_URL=http://nginx:8080
ADAPTER_API_TOKEN=<тот-же-токен-что-в-ADAPTER_API_TOKENS>
```

`ADAPTER_API_TOKEN` должен совпадать с одним из значений `ADAPTER_API_TOKENS` в backend.

## 4. Локальный запуск

### Без Docker

```bash
cd frontends/vk_bot
pip install -e ".[dev]"
# backend на :8000, в .env: VK_BOT_TOKEN, ADAPTER_API_TOKEN, BACKEND_API_BASE_URL
python -m vk_bot
```

### Docker Compose

```bash
# backend + telegram (как обычно)
docker compose up --build -d

# добавить VK-бота (отдельный profile)
docker compose --profile vk up --build -d vk_bot
```

## 5. Production (VPS)

Образ собирается в GitHub Actions: `ghcr.io/svv-aiconsulting/webmarket-vk-bot`.

VK-бот в `docker-compose.prod.yml` использует profile `vk` — не стартует, пока вы явно не включите:

```bash
docker compose -f docker-compose.prod.yml --profile vk up -d vk_bot
```

После добавления `VK_BOT_TOKEN` в `.env` на VPS.

В production `PRODUCT_MEDIA_BASE_URL=http://nginx:8080`: бот скачивает фото
через внутренний Nginx, а не через Gunicorn. Этот порт доступен только в Docker-сети.

## 6. Ручная проверка сценария

1. Напишите в сообщество: **Начать** или `/start`.
2. Если нужна регистрация — введите телефон в формате `79991234567`.
3. **Каталог** — отдельная карточка на каждый товар (фото, описание, кнопки +/− и «Добавить в корзину»). Фото и количество обновляются **на месте** (редактирование сообщения).
4. **Корзина** — измените количество (`+`/`−` на месте), удалите позицию («Удалить» убирает сообщение), оформите заказ. Итого обновляется отдельным сообщением внизу. Навигация — через нижнее reply-меню (кнопки «В меню» в корзине нет).
5. Выберите доставку/самовывоз, адрес, оплату, подтвердите.
6. **Мои заказы** — история и детали.
7. В Django Admin проверьте заказ и клиента с `channel=vk`.

Новый VK ID всегда получает карточку текущего канала. Совпадение введённого телефона
с другой карточкой фиксируется как неблокирующий конфликт и не мешает заказу.

Свободное сообщение вне активного FSM передаётся в единый AI `intake`. Уточнения,
подтверждение суммы и результат создания заказа приходят через тот же polling-
контракт, что и в Telegram.

## 7. Логи

```bash
docker compose logs -f vk_bot
# production:
docker compose -f docker-compose.prod.yml logs -f vk_bot
```

## 8. Отключить VK-бот без остановки backend

```bash
docker compose stop vk_bot
# или production:
docker compose -f docker-compose.prod.yml stop vk_bot
```

Backend, Telegram-бот и nginx продолжат работать.

## Ограничения MVP

- Long Poll (без Callback API / webhook).
- Регистрация — ручной ввод телефона (без VK Mini App).
- VK-бот в production не стартует автоматически (profile `vk`).
- FSM/сессия в памяти процесса (сбрасывается при перезапуске контейнера).

## Связанные документы

- [docs/api.md](./api.md) — REST Storefront API
- [docs/TELEGRAM_BOT.md](./TELEGRAM_BOT.md) — Telegram-адаптер (аналогичный сценарий)
- [docs/PRODUCTION_OPERATIONS.md](./PRODUCTION_OPERATIONS.md) — backup, rollback, demo checklist на VPS

## Типовые ошибки

| Ошибка в логах | Что сделать |
|----------------|-------------|
| `longpoll for this group is not enabled` | Включить Long Poll API в настройках сообщества (раздел 2.1) |
| Кнопки `+` / `−` крутятся бесконечно | В Long Poll включить **«Действие с сообщением»**, затем `docker compose --profile vk up --build -d vk_bot` |
| Фото пропадает при `+`/`−` в каталоге | Пересобрать бота (`up --build`); при редактировании карточки фото передаётся заново |
| Корзина дублируется при каждом `+`/`−` | Устаревший образ бота — `docker compose --profile vk up --build -d vk_bot` |
| `Backend not available at startup` | Подождать старт `web` или проверить `BACKEND_API_BASE_URL=http://web:8000` в Docker |
| Контейнер постоянно перезапускается | `docker compose stop vk_bot`, исправить настройки VK, затем `docker compose --profile vk up -d vk_bot` |
