# WebMarket

WebMarket — омниканальная система обработки заказов. Она объединяет сайт, мессенджеры и email в единый backend: каталог, клиентов, корзины, заказы, доставку, оплату и рабочее место менеджера.

Каждый канал использует актуальные данные и единые бизнес-правила. Клиент может начать подбор в одном интерфейсе, а система сохраняет согласованный состав корзины и состояние оформления в соответствующем диалоге.

## Возможности

- **Сайт-витрина** с каталогом, карточками товаров, корзиной, гостевым оформлением, входом по коду email и личным кабинетом.
- **AI-консультант** на сайте, в Telegram, VK и email: подбирает товары по текущему каталогу, понимает несколько позиций в одном сообщении и ведёт диалог до оформления.
- **Единый разбор заказа**: сообщение с товарами, количеством, доставкой, адресом, датой и оплатой обрабатывается за один ход. Если обязательных данных не хватает, система запрашивает только недостающие.
- **Безопасный checkout**: версия корзины и неизменяемый `CheckoutPreview` защищают от устаревших расчётов и повторного создания заказа.
- **Интеграции**: расчёт доставки, онлайн-оплата, webhooks оплаты, возвраты и синхронизация статусов.
- **Django Admin**: управление каталогом, клиентами и заказами, единый раздел AI-диалогов с историей и дашборд менеджера.
- **Надёжность**: PostgreSQL, Celery, Redis, очереди по назначению, readiness/liveness, идемпотентные операции и CI/CD.

## Каналы

| Канал | Возможности |
|---|---|
| Website | Витрина, ручная корзина, popup-консультант, гостевой checkout, кабинет по email-коду |
| Telegram | Каталог, AI-диалог, корзина, доставка и оплата |
| VK | Каталог и корзина, AI-диалог, согласие на обработку персональных данных и оформление |
| Email | Приём естественно-языковых заявок и подтверждений через общую очередь |

LLM помогает понять намерение и сформулировать ответ, но не является источником данных. Наличие, цены, количества, права доступа, расчёт, номер заказа и ссылки оплаты всегда проверяются backend.

## Архитектура

```text
Website / Telegram / VK / Email
             │
       Django + REST API
             │
PostgreSQL ─ Cart/Order services ─ Django Admin
             │
 Celery + Redis ─ LLM / Delivery API / Payment API
```

`apps/*` содержит модели и бизнес-сервисы. Адаптеры в `frontends/website`, `frontends/telegram_bot` и `frontends/vk_bot` независимы от предметной логики и обращаются к backend по HTTP.

## Документация

| Документ | Содержание |
|---|---|
| [docs/api.md](./docs/api.md) | REST API, website endpoints и контракт checkout |
| [docs/DATA_FLOW.md](./docs/DATA_FLOW.md) | Потоки данных между каналами, очередью и backend |
| [docs/MODERNIZATION_RELEASE.md](./docs/MODERNIZATION_RELEASE.md) | Контракт корзины, доступа и AI-диалога |
| [docs/AI_DIALOGUE_ACCEPTANCE.md](./docs/AI_DIALOGUE_ACCEPTANCE.md) | Сценарии приёмки консультанта и метрики |
| [docs/MANAGER_DASHBOARD.md](./docs/MANAGER_DASHBOARD.md) | Дашборд и работа менеджера |
| [docs/TELEGRAM_BOT.md](./docs/TELEGRAM_BOT.md) | Настройка Telegram-бота |
| [docs/VK_BOT.md](./docs/VK_BOT.md) | Настройка VK-бота и Long Poll |
| [docs/EMAIL_CHANNEL.md](./docs/EMAIL_CHANNEL.md) | Email-канал и повторная обработка |
| [docs/YANDEX_DELIVERY.md](./docs/YANDEX_DELIVERY.md) | Контракт и настройка доставки |
| [docs/YOOKASSA.md](./docs/YOOKASSA.md) | Онлайн-оплата, webhooks и возвраты |
| [docs/PRODUCTION_OPERATIONS.md](./docs/PRODUCTION_OPERATIONS.md) | Backup, rollback и эксплуатационные проверки |
| [docs/GITHUB_ACTIONS_DEPLOYMENT.md](./docs/GITHUB_ACTIONS_DEPLOYMENT.md) | CI/CD и immutable-deploy |
| [docs/TESTING_EVIDENCE.md](./docs/TESTING_EVIDENCE.md) | Результаты проверок и демонстрационные сценарии |

## Локальный запуск

```bash
cp .env.example .env
docker compose up --build -d
docker compose exec web python manage.py migrate
docker compose exec web python manage.py load_demo_data
docker compose exec web python manage.py createsuperuser
```

После запуска доступны:

- сайт: <http://localhost:8000/>;
- административный интерфейс: <http://localhost:8000/admin/>;
- health-check: <http://localhost:8000/api/health/>.

Для запуска Django без Docker укажите `POSTGRES_HOST=localhost` и `BACKEND_API_BASE_URL=http://localhost:8000` в `.env`.

## Тесты

```bash
pytest -q
python -m pytest frontends/telegram_bot/tests -q
python -m pytest frontends/vk_bot/tests -q
```

## Развёртывание

Push в `main` запускает проверку backend и адаптеров, сборку SHA-образов и immutable-deploy. Порядок выпуска, отката и восстановления описан в [docs/GITHUB_ACTIONS_DEPLOYMENT.md](./docs/GITHUB_ACTIONS_DEPLOYMENT.md) и [docs/PRODUCTION_OPERATIONS.md](./docs/PRODUCTION_OPERATIONS.md).
