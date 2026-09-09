# WebMarket

WebMarket — учебный омниканальный интернет-магазин морепродуктов. Покупатель может выбрать товар и оформить заказ на сайте, в Telegram, VK или email. Все каналы используют один Django backend, PostgreSQL, общую корзину, единый расчёт заказа и CRM-записи в Django Admin.

Публичный стенд: <https://webmarket.apernova.ru/>. Проверка работоспособности: <https://webmarket.apernova.ru/api/health/>.

## Что реализовано

- **Сайт-витрина** с каталогом, карточками товаров, корзиной, гостевым оформлением, входом по коду email и личным кабинетом.
- **AI-консультант** на сайте, в Telegram, VK и email: помогает подобрать товары только по актуальному каталогу, поддерживает несколько позиций в одном сообщении и ведёт поэтапное оформление.
- **Единое сообщение заказа**: например, «Привезите завтра 2 упаковки креветок по адресу Москва, улица Разина, 15. Оплата будет картой» сохраняет товары, доставку, адрес, дату и оплату за один ход. Если обязательных данных нет, ассистент спрашивает только их.
- **Безопасный checkout**: версия корзины и неизменяемый `CheckoutPreview` защищают от устаревшего расчёта и двойного создания заказа.
- **Доставка и оплата**: расчёт Яндекс Доставки, тестовая ЮKassa, возврат на страницу магазина и администрирование возвратов.
- **Django Admin**: товары, клиенты, заказы, единый раздел «AI-ассистент» с историей диалогов и дашборд менеджера.
- **Эксплуатация**: Docker Compose, Celery/Redis, readiness/liveness, фоновые очереди оплаты и доставки, GitHub Actions → GHCR → VPS.

## Каналы и границы ответственности

| Канал | Возможности |
|---|---|
| Website | Витрина, ручная корзина, popup-консультант, гостевой checkout, кабинет по email-коду |
| Telegram | Каталог, AI-диалог, корзина, доставка и оплата |
| VK | Каталог и корзина, AI-диалог, согласие на обработку ПДн и оформление |
| Email | Приём естественно-языковых заявок и подтверждений в общей очереди |

LLM не является источником цен, наличия, суммы, ссылок оплаты или прав доступа. Она помогает понять запрос; товары, количества, контакты, расчёт и создание заказа проверяются сервером.

## Документация

| Документ | Содержание |
|---|---|
| [docs/api.md](./docs/api.md) | REST API, website endpoints и контракт checkout |
| [docs/DATA_FLOW.md](./docs/DATA_FLOW.md) | Потоки данных между каналами, очередью и backend |
| [docs/MODERNIZATION_RELEASE.md](./docs/MODERNIZATION_RELEASE.md) | Безопасный контракт корзины, доступа и AI-диалога |
| [docs/AI_DIALOGUE_ACCEPTANCE.md](./docs/AI_DIALOGUE_ACCEPTANCE.md) | Сценарии приёмки консультанта и метрики |
| [docs/MANAGER_DASHBOARD.md](./docs/MANAGER_DASHBOARD.md) | Дашборд и работа менеджера |
| [docs/TELEGRAM_BOT.md](./docs/TELEGRAM_BOT.md) | Настройка Telegram-бота |
| [docs/VK_BOT.md](./docs/VK_BOT.md) | Настройка VK-бота и Long Poll |
| [docs/EMAIL_CHANNEL.md](./docs/EMAIL_CHANNEL.md) | Email-канал и повторная обработка |
| [docs/YANDEX_DELIVERY.md](./docs/YANDEX_DELIVERY.md) | Доставка и тестовый контур Яндекса |
| [docs/YOOKASSA.md](./docs/YOOKASSA.md) | Sandbox ЮKassa, webhooks и возвраты |
| [docs/PRODUCTION_OPERATIONS.md](./docs/PRODUCTION_OPERATIONS.md) | Backup, rollback и demo-checklist |
| [docs/GITHUB_ACTIONS_DEPLOYMENT.md](./docs/GITHUB_ACTIONS_DEPLOYMENT.md) | CI/CD и immutable-deploy на VPS |
| [docs/TESTING_EVIDENCE.md](./docs/TESTING_EVIDENCE.md) | Результаты проверок и сценарий демонстрации |

## Архитектура

```text
Website / Telegram / VK / Email
             │
       Django + REST API
             │
PostgreSQL ─ Cart/Order services ─ Django Admin
             │
   Celery + Redis ─ GigaChat / Яндекс Доставка / ЮKassa
```

`apps/*` содержит модели и бизнес-сервисы. Адаптеры в `frontends/website`, `frontends/telegram_bot` и `frontends/vk_bot` не содержат расчётов или правил оформления и обращаются к backend по HTTP.

## Локальный запуск

```bash
cp .env.example .env
docker compose up --build -d
docker compose exec web python manage.py migrate
docker compose exec web python manage.py load_demo_data
docker compose exec web python manage.py createsuperuser
```

После запуска:

- сайт: <http://localhost:8000/>;
- admin: <http://localhost:8000/admin/>;
- health: <http://localhost:8000/api/health/>.

Для запуска Django без Docker установите `POSTGRES_HOST=localhost` и `BACKEND_API_BASE_URL=http://localhost:8000` в `.env`.

## Тесты

```bash
pytest -q
python -m pytest frontends/telegram_bot/tests -q
python -m pytest frontends/vk_bot/tests -q
```

Перед выпуском также проверяются сайт, Telegram, VK, email, sandbox ЮKassa и Яндекс Доставка по сценариям из [docs/TESTING_EVIDENCE.md](./docs/TESTING_EVIDENCE.md).

## Production

Push в `main` запускает GitHub Actions: тесты backend и адаптеров, сборку SHA-образов в GHCR и immutable-deploy на VPS. Полный порядок выпуска и отката описан в [docs/GITHUB_ACTIONS_DEPLOYMENT.md](./docs/GITHUB_ACTIONS_DEPLOYMENT.md) и [docs/PRODUCTION_OPERATIONS.md](./docs/PRODUCTION_OPERATIONS.md).
