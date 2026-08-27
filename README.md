# WebMarket — Backend интернет-магазина

Backend-ядро интернет-магазина с CRM-функциями: товары, клиенты, корзины, заказы, доставка и скидки.

Реализованы backend, Django Admin, сервисный слой и тесты. REST Storefront API покрывает каталог, корзину, заказы и единый AI intake. Telegram-бот и VK-бот остаются **независимыми** frontend-адаптерами, а публичная веб-форма принимает заказ обычным текстом. MAX остаётся дополнительным каналом.

## Документация

| Документ | Содержание |
|----------|------------|
| [docs/TECHNICAL_SPECIFICATION.md](./docs/TECHNICAL_SPECIFICATION.md) | Техническое задание выпускного проекта |
| [docs/MODERNIZATION_PLAN.md](./docs/MODERNIZATION_PLAN.md) | Поэтапный план модернизации и границы LLM |
| [docs/AI_ORDER_DATA_MODEL.md](./docs/AI_ORDER_DATA_MODEL.md) | Сущности и состояния AI-заявки до создания миграций |
| [docs/LOCAL_DATABASE.md](./docs/LOCAL_DATABASE.md) | Отдельный локальный PostgreSQL для разработки и тестов |
| [docs/QUEUE.md](./docs/QUEUE.md) | Единая очередь Celery/Redis, retry и диагностика |
| [docs/GIGACHAT.md](./docs/GIGACHAT.md) | GigaChat NLU, TLS, конфигурация и live smoke-test |
| [docs/EMAIL_CHANNEL.md](./docs/EMAIL_CHANNEL.md) | Яндекс IMAP/SMTP, безопасная настройка и retry email-ответов |
| [docs/YANDEX_DELIVERY.md](./docs/YANDEX_DELIVERY.md) | Яндекс Доставка по России: test-flow и production-чек-лист |
| [docs/YOOKASSA.md](./docs/YOOKASSA.md) | ЮKassa: sandbox, webhook, чеки, возвраты и production-чек-лист |
| [docs/MANAGER_DASHBOARD.md](./docs/MANAGER_DASHBOARD.md) | Дашборд менеджера: метрики, фильтр периода и очередь исключений |
| [docs/TESTING_EVIDENCE.md](./docs/TESTING_EVIDENCE.md) | Прогоны тестов и сценарий скринкаста для защиты |
| [docs/GITHUB_ACTIONS_DEPLOYMENT.md](./docs/GITHUB_ACTIONS_DEPLOYMENT.md) | Настройка CI/CD: GitHub Actions, GHCR, HTTPS и VPS |
| [docs/PAYMENT_PROVIDER_COMPARISON.md](./docs/PAYMENT_PROVIDER_COMPARISON.md) | Сравнение ЮKassa и Robokassa и рекомендуемая архитектура оплаты |
| [docs/PROMPT_LIBRARY.md](./docs/PROMPT_LIBRARY.md) | Библиотека development/runtime/evaluation-промптов |
| [docs/api.md](./docs/api.md) | REST Storefront API |
| [docs/TELEGRAM_BOT.md](./docs/TELEGRAM_BOT.md) | Telegram-бот: BotFather, env, Docker, VPS, типовые ошибки |
| [docs/VK_BOT.md](./docs/VK_BOT.md) | VK-бот: сообщество, Long Poll, env, Docker, VPS |
| [docs/PRODUCTION_OPERATIONS.md](./docs/PRODUCTION_OPERATIONS.md) | Backup, rollback, demo checklist на VPS |
| [docs/SECURITY_HARDENING_PLAN.md](./docs/SECURITY_HARDENING_PLAN.md) | HTTPS, firewall, ротация секретов |
| [base.md](./base.md) | Структура базы данных |

## Архитектура

Проект разделён на слои:

- **models.py** — структура данных (Django ORM)
- **selectors.py** — запросы на чтение
- **services.py** — бизнес-логика
- **admin.py** — интерфейс для менеджера

```
apps/
├── catalog/      # Товары
├── customers/    # Клиенты
├── carts/        # Корзины
├── orders/       # Заказы
├── delivery/     # Правила доставки
├── dashboard/    # Сводные метрики и очередь исключений менеджера
├── discounts/    # Правила скидок
├── common/       # Общие enums и утилиты
└── api/          # REST API (health, catalog, cart, orders)

frontends/        # Frontend-адаптеры (telegram_bot, vk_bot — полностью независимые)
```

Каждый адаптер (`frontends/telegram_bot`, `frontends/vk_bot`) содержит свой API-клиент, форматирование и обработчики. Общий пакет `frontends/shared` **не используется** — дублирование минимально и осознанно.

### Почему backend независим от frontend

- Backend запускается отдельным процессом (`web` в Docker Compose)
- Бизнес-логика находится только в `apps/*/services.py`
- Боты и сайт **не должны** дублировать логику заказов
- Если бот упадёт — Django Admin и сервисы продолжают работать
- В коде приложений **нет** импортов Telegram/VK/MAX SDK

## Быстрый старт

### 1. Создайте файл `.env`

```bash
cp .env.example .env
```

По умолчанию шаблон настроен на **Docker Compose** (`POSTGRES_HOST=db`).

Для **локального запуска без Docker** в `.env` поменяйте:
- `POSTGRES_HOST=localhost`
- `BACKEND_API_BASE_URL=http://localhost:8000`

| Сценарий | `POSTGRES_HOST` |
|----------|-----------------|
| Docker Compose (`web` + `db`) | `db` |
| Локально на хосте | `localhost` |

Для защищённых endpoints задайте `ADAPTER_API_TOKENS` (список через запятую) и передавайте токен в заголовке `X-Adapter-Token`. Каталог по умолчанию публичный (`ADAPTER_API_PUBLIC_CATALOG=True`).

При необходимости отредактируйте остальные значения в `.env`.

### 2. Запустите проект через Docker Compose

```bash
docker compose up --build -d
```

### 3. Примените миграции

```bash
docker compose exec web python manage.py migrate
```

### 4. Создайте суперпользователя

```bash
docker compose exec web python manage.py createsuperuser
```

### 5. Откройте Django Admin

http://localhost:8000/admin/

### 6. Проверьте API проверки работоспособности

http://localhost:8000/api/health/

Ожидаемый ответ: `{"status":"успешно"}`

### 7. (Опционально) Демо-данные для витрины

```bash
docker compose exec web python manage.py load_demo_data
```

## REST API (Storefront)

Полная документация: [docs/api.md](./docs/api.md)

| Method | Path | Доступ | Назначение |
|--------|------|--------|------------|
| GET | `/api/health/` | Публично | Проверка работоспособности |
| GET | `/api/meta/` | Публично | Справочники для UI |
| GET | `/api/products/` | Публично* | Список активных товаров |
| GET | `/api/products/{code}/` | Публично* | Карточка товара |
| POST | `/api/identify-customer/` | Token | Идентификация клиента |
| GET | `/api/cart/` | Token | Корзина |
| PUT | `/api/cart/items/{id}/` | Token | Установить количество |
| DELETE | `/api/cart/items/{id}/` | Token | Удалить позицию |
| DELETE | `/api/cart/items/` | Token | Очистить корзину |
| POST | `/api/checkout/preview/` | Token | Превью сумм |
| POST | `/api/orders/` | Token | Оформить заказ |
| GET | `/api/orders/{number}/` | Token | Детали заказа |
| GET | `/api/customers/{code}/orders/` | Token | История заказов |

\* Каталог можно закрыть токеном: `ADAPTER_API_PUBLIC_CATALOG=False`

### Контекст сессии

Все защищённые endpoints требуют:
- `channel` — `telegram`, `vk`, `max`, `website`
- `external_user_id` — ID пользователя в канале

Дополнительно:
- `customer_id` — из ответа `identify-customer`; **обязателен** для checkout и оформления заказа
- `customer_id` должен соответствовать `channel + external_user_id` (иначе `409 customer_context_mismatch`)
- `GET /api/cart/` допускает anonymous cart без `customer_id` до идентификации
- `GET /api/orders/{number}/` и `GET /api/customers/{code}/orders/` требуют `channel` и `external_user_id` в query-параметрах; доступ только к своим заказам

Повторный checkout после оформления заказа возвращает `422 empty_cart` — у пользователя создаётся новая пустая активная корзина.

## Мультифронтенд идентификация клиента

Единая точка входа для ID-каналов Telegram, VK и MAX:

`POST /api/identify-customer/`

Пример запроса:

```json
{
  "channel": "telegram",
  "external_user_id": "123456789",
  "phone": "+7 (912) 345-67-89",
  "phone_verification_source": "platform_contact",
  "username": "ivan_ivanov",
  "display_name": "Иван"
}
```

Возможные статусы ответа:

- `identified` — клиент найден/создан и готов к работе с корзиной и заказом.
- `registration_required` — для нового канального ID нужно запросить номер телефона.
- `conflict` — сам канальный ID уже привязан к другой карточке.

При `identified` ответ также содержит: `phone`, `email`, `display_name`, `channel`,
`external_user_id`.

Требования безопасности:

- endpoint `identify-customer` доступен только для доверенных адаптеров по заголовку `X-Adapter-Token`;
- токены задаются через `ADAPTER_API_TOKENS` в `.env`.

Ключевые принципы:

- Telegram/VK/MAX сначала ищут карточку по `(channel, external_user_id)`.
- Новый ID-канал создаёт собственную карточку; совпавший телефон/email создаёт
  неблокирующий `CustomerIdentityConflict`, а не автоматическое объединение.
- Email-канал идентифицирует отправителя по нормализованному адресу и может создать
  карточку без телефона.
- Веб-форма ищет существующую CRM-карточку по нормализованному телефону/email;
  UUID формы является только ID отправки, а не идентификатором клиента.
- При неоднозначном совпадении веб-форма создаёт новую карточку и конфликты, но заказ
  продолжает оформляться.
- Телефон и email индексируются, но временно могут повторяться в разных карточках.
- Заказы всегда сохраняют текущий канал и снимки использованных контактов.

Рекомендации по фронтендам:

- Telegram: кнопка `request_contact` в приватном чате.
- VK Mini App: `VKWebAppGetPhoneNumber` / `VKWebAppGetPersonalCard`.
- VK-бот/MAX (без системной выдачи номера): ручной ввод телефона с валидацией.

## Telegram-бот

Telegram-бот — **frontend-адаптер**: не содержит бизнес-логики магазина и обращается к backend только через REST API.

**Подробная инструкция:** [docs/TELEGRAM_BOT.md](./docs/TELEGRAM_BOT.md)

Кратко:

```bash
docker compose up --build -d
docker compose logs -f telegram_bot
```

Переменные: `TELEGRAM_BOT_TOKEN`, `ADAPTER_API_TOKEN`, `BACKEND_API_BASE_URL`, `TELEGRAM_BOT_USE_POLLING`.

Тесты:

```bash
cd frontends/telegram_bot
pip install -e ".[dev]"
pytest
```

## VK-бот

VK-бот — второй frontend-адаптер с тем же REST API.

**Подробная инструкция:** [docs/VK_BOT.md](./docs/VK_BOT.md)

Кратко:

```bash
# Docker (profile vk — не мешает telegram/backend)
docker compose --profile vk up --build -d vk_bot
docker compose logs -f vk_bot
```

Переменные: `VK_BOT_TOKEN`, `VK_GROUP_ID`, `VK_BOT_USE_LONGPOLL`, `ADAPTER_API_TOKEN`, `BACKEND_API_BASE_URL`.

Тесты:

```bash
cd frontends/vk_bot
pip install -e ".[dev]"
pytest
```

## Запуск тестов

Через Docker Compose (рекомендуется, использует `.env` с `POSTGRES_HOST=db`):

```bash
docker compose exec web pytest
docker compose exec web pytest apps/api/
```

Адаптеры (отдельно от Django):

```bash
cd frontends/telegram_bot && pytest
cd frontends/vk_bot && pytest
```

Локально на хосте (нужен PostgreSQL и `POSTGRES_HOST=localhost` в `.env`):

```bash
pip install -e ".[dev]"
pytest
```

## Структура папок

```
WebMarket/
├── manage.py
├── pyproject.toml
├── docker-compose.yml
├── Dockerfile
├── config/           # Настройки Django
├── apps/             # Django-приложения
├── frontends/        # Frontend-адаптеры (telegram_bot, vk_bot — независимые)
├── conftest.py       # Фикстуры pytest
└── tests/            # (опционально) общие тесты
```

## Основные сервисы

| Сервис | Назначение |
|--------|------------|
| CatalogService | Товары и их доступность |
| CustomerService | Клиенты и каналы (Telegram/VK/...) |
| CartService | Корзина и позиции |
| PricingService | Расчёт сумм заказа |
| OrderService | Создание заказа из корзины |
| DeliveryService | Стоимость доставки |
| DiscountService | Правила скидок |

## Следующие этапы

1. ~~REST API для товаров, клиентов и заказов~~ (готово)
2. ~~Telegram-бот (отдельный контейнер)~~ (готово) — [docs/TELEGRAM_BOT.md](./docs/TELEGRAM_BOT.md)
3. ~~Production-деплой на VPS (Gunicorn + nginx)~~ (готово)
4. ~~Операционный runbook (backup, rollback, demo)~~ — [docs/PRODUCTION_OPERATIONS.md](./docs/PRODUCTION_OPERATIONS.md)
5. ~~VK-бот~~ (готово) — [docs/VK_BOT.md](./docs/VK_BOT.md); MAX-бот
6. Сайт
7. Оплата
8. Celery + Redis для фоновых задач
9. S3/MinIO для медиафайлов
10. **Финальный hardening prod** (после демо заказчику / перед production-эталоном): HTTPS, backup+restore, ротация секретов, ufw — [SECURITY_HARDENING_PLAN.md](./docs/SECURITY_HARDENING_PLAN.md)

## Запуск на VPS (с нуля)

Пошаговая инструкция для развёртывания на **любом** VPS (Ubuntu/Debian). После настройки обновления можно вести через GitHub Actions.

Операционный runbook (backup, rollback, demo): [docs/PRODUCTION_OPERATIONS.md](./docs/PRODUCTION_OPERATIONS.md).

### Что понадобится

| Компонент | Требование |
|-----------|------------|
| VPS | Ubuntu 22.04+ / Debian 12+, 1–2 GB RAM, 10+ GB диск |
| Порты снаружи | `22` (SSH), `80` (HTTP); позже `443` (HTTPS) |
| Docker | Docker Engine + Docker Compose plugin |
| GitHub | Репозиторий с кодом, образы в GHCR |
| Telegram | Токен бота от [@BotFather](https://t.me/BotFather) (если нужен бот) |
| VK | Токен сообщества и ID группы (опционально, profile `vk`) — [docs/VK_BOT.md](./docs/VK_BOT.md) |

Схема на VPS: `nginx:80` → `web` (Gunicorn) → `db` (PostgreSQL) + `telegram_bot` (+ опционально `vk_bot` с profile `vk`).

### Подставьте свои значения

Перед командами замените плейсхолдеры:

| Плейсхолдер | Пример | Где используется |
|-------------|--------|------------------|
| `<VPS_IP>` | `203.0.113.10` | `DJANGO_ALLOWED_HOSTS`, проверки в браузере |
| `<VPS_USER>` | `root` или `deploy` | SSH, GitHub Secret |
| `<APP_DIR>` | `/opt/webmarket` | Каталог проекта на VPS |
| `<GITHUB_OWNER>` | `svv-aiconsulting` | Имена образов GHCR |
| `<GITHUB_REPO>` | `Webmarket` | URL репозитория |
| `<GHCR_PAT>` | GitHub PAT | `docker login`, `git fetch` (приватный repo) |

Образы в `docker-compose.prod.yml` сейчас: `ghcr.io/<GITHUB_OWNER>/webmarket`, `ghcr.io/<GITHUB_OWNER>/webmarket-telegram-bot` и `ghcr.io/<GITHUB_OWNER>/webmarket-vk-bot`. При форке смените owner в compose и в `.github/workflows/deploy.yml`.

---

### Шаг 1. Подготовка VPS

Подключитесь по SSH:

```bash
ssh <VPS_USER>@<VPS_IP>
```

Установите Docker (официальный скрипт):

```bash
curl -fsSL https://get.docker.com | sh
docker compose version
```

Создайте каталог проекта:

```bash
export APP_DIR=/opt/webmarket   # или свой путь
mkdir -p "$APP_DIR"
cd "$APP_DIR"
```

---

### Шаг 2. Клонирование репозитория

**Публичный репозиторий:**

```bash
git clone https://github.com/<GITHUB_OWNER>/<GITHUB_REPO>.git "$APP_DIR"
cd "$APP_DIR"
```

**Приватный репозиторий** (через PAT с правом `repo`):

```bash
git clone "https://x-access-token:<GHCR_PAT>@github.com/<GITHUB_OWNER>/<GITHUB_REPO>.git" "$APP_DIR"
cd "$APP_DIR"
```

---

### Шаг 3. Настройка `.env`

```bash
cp .env.example .env
chmod 600 .env
nano .env   # или vim
```

Минимальный набор для продакшна (замените значения на свои):

```env
# Django
DJANGO_SECRET_KEY=<случайная-длинная-строка>
DJANGO_DEBUG=False
DJANGO_SETTINGS_MODULE=config.settings.production
DJANGO_ALLOWED_HOSTS=<VPS_IP>,localhost,127.0.0.1,web,nginx

# PostgreSQL
POSTGRES_DB=webmarket
POSTGRES_USER=webmarket
POSTGRES_PASSWORD=<надёжный-пароль>
POSTGRES_HOST=db
POSTGRES_PORT=5432

# API
ADAPTER_API_TOKENS=<токен-для-адаптеров>
ADAPTER_API_PUBLIC_CATALOG=False

# Telegram-бот
TELEGRAM_BOT_TOKEN=<токен-от-BotFather>
ADAPTER_API_TOKEN=<тот-же-токен-что-в-ADAPTER_API_TOKENS>
BACKEND_API_BASE_URL=http://nginx
TELEGRAM_BOT_USE_POLLING=true
TELEGRAM_BOT_LOG_LEVEL=INFO

# VK-бот (опционально; стартует с profile vk)
# VK_BOT_TOKEN=<токен-сообщества>
# VK_GROUP_ID=<id-группы>
# VK_BOT_USE_LONGPOLL=True
```

Важно:

- `DJANGO_DEBUG=False` — **обязательно** (иначе автодеплой из GitHub Actions остановится).
- В `DJANGO_ALLOWED_HOSTS` обязательно есть `<VPS_IP>` и `nginx` (бот ходит в API через nginx).
- `ADAPTER_API_TOKEN` должен совпадать с одним из значений в `ADAPTER_API_TOKENS`.
- Файл `.env` **не в git** — при деплое не перезаписывается.

Сгенерировать `DJANGO_SECRET_KEY` (на VPS или локально):

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(50))"
```

---

### Шаг 4. Вход в GHCR и первый запуск

Образы backend и бота берутся из GitHub Container Registry. PAT нужен с правами **`read:packages`** (для приватных пакетов — также `repo`).

```bash
cd "$APP_DIR"
echo "<GHCR_PAT>" | docker login ghcr.io -u <GITHUB_OWNER> --password-stdin

docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d
# опционально VK-бот:
# docker compose -f docker-compose.prod.yml --profile vk up -d vk_bot
```

Дождитесь готовности (все сервисы `Up`, `web`/`nginx` — `healthy`):

```bash
docker compose -f docker-compose.prod.yml ps
```

---

### Шаг 5. Миграции и первый вход в admin

```bash
docker compose -f docker-compose.prod.yml exec web python manage.py migrate
docker compose -f docker-compose.prod.yml exec web python manage.py createsuperuser
```

Опционально — демо-товары для показа:

```bash
docker compose -f docker-compose.prod.yml exec web python manage.py load_demo_data
```

---

### Шаг 6. Проверка

На VPS:

```bash
curl -i http://localhost/api/health/
# Ожидается: HTTP/1.1 200 и {"status":"успешно"}

docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml logs --tail=50 telegram_bot
```

В браузере:

- Admin: `http://<VPS_IP>/admin/`
- Health: `http://<VPS_IP>/api/health/`
- Telegram: `/start` в боте

Порты снаружи (должен быть открыт только 80):

```bash
ss -tlnp | grep -E ':80|:8000|:5432'
```

---

### Шаг 7. Автодеплой через GitHub Actions (опционально)

После ручного первого запуска настройте CI/CD: push в `main` → сборка образов → деплой на VPS.

**Secrets в GitHub** (Settings → Secrets and variables → Actions):

| Secret | Значение |
|--------|----------|
| `VPS_HOST` | `<VPS_IP>` |
| `VPS_USER` | `<VPS_USER>` |
| `VPS_APP_DIR` | `<APP_DIR>` (например `/opt/webmarket`) |
| `VPS_SSH_KEY` | Приватный SSH-ключ для входа на VPS |
| `VPS_GHCR_TOKEN` | PAT (`read:packages` + `repo` для приватного репо) |
| `VPS_PORT` | `22` (если нестандартный SSH-порт) |

**На VPS:** публичный ключ от `VPS_SSH_KEY` должен быть в `~/.ssh/authorized_keys` пользователя деплоя.

При каждом push в `main` workflow:

1. Собирает и пушит образы в GHCR (`latest` + SHA коммита).
2. По SSH на VPS: `git fetch` + `git reset --hard`, `docker compose pull/up`, `migrate`.
3. Проверяет `DJANGO_DEBUG=False` и `/api/health/`.

Подробнее об откате и backup: [docs/PRODUCTION_OPERATIONS.md](./docs/PRODUCTION_OPERATIONS.md).

---

### Перенос на другой VPS (чеклист)

1. Новый VPS: Docker, каталог `<APP_DIR>`.
2. `git clone` репозитория.
3. Скопировать или заново создать `.env` (с новым `<VPS_IP>` в `ALLOWED_HOSTS`).
4. `docker login ghcr.io` → `pull` → `up -d` → `migrate` → `createsuperuser`.
5. Обновить GitHub Secrets (`VPS_HOST`, при необходимости `VPS_APP_DIR`, SSH-ключ).
6. Добавить SSH-ключ Actions на новый сервер.
7. Push в `main` — проверить зелёный workflow.

Данные БД и media **не переносятся автоматически** — нужен backup/restore из [docs/PRODUCTION_OPERATIONS.md](./docs/PRODUCTION_OPERATIONS.md).

---

### Типовые проблемы

| Симптом | Решение |
|---------|---------|
| `denied` при `docker pull` | `docker login ghcr.io` с PAT (`read:packages`) |
| Admin `400 Bad Request` | Добавить `<VPS_IP>` и `nginx` в `DJANGO_ALLOWED_HOSTS`, затем `docker compose -f docker-compose.prod.yml up -d` |
| Admin без стилей | Ctrl+F5; в образе уже есть `collectstatic` + WhiteNoise |
| Бот не грузит фото | В `.env` на VPS: `BACKEND_API_BASE_URL=http://nginx`, в `ALLOWED_HOSTS` — `nginx` |
| VK-бот не стартует | Добавить `VK_BOT_TOKEN`, `VK_GROUP_ID` в `.env` и запустить с `--profile vk` |
| Deploy в Actions упал на preflight | В VPS `.env`: `DJANGO_DEBUG=False` |
| CSRF 403 в admin по HTTP | Ожидаемо до HTTPS; после TLS — secure cookies в `production.py` |

HTTPS, firewall и ротация секретов: [SECURITY_HARDENING_PLAN.md](./docs/SECURITY_HARDENING_PLAN.md).
