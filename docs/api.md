# REST API WebMarket

Краткая документация Storefront API для frontend-адаптеров (Telegram, VK, MAX, сайт).

**Адаптеры:** [TELEGRAM_BOT.md](./TELEGRAM_BOT.md) · [VK_BOT.md](./VK_BOT.md)

## Авторизация

| Группа endpoints | Доступ |
|------------------|--------|
| `health`, `meta`, `products` | Публично (каталог можно закрыть: `ADAPTER_API_PUBLIC_CATALOG=False`) |
| `identify-customer`, `intake`, `cart`, `checkout`, `orders` | Заголовок `X-Adapter-Token` |

Токены задаются в `.env`: `ADAPTER_API_TOKENS=token1,token2`

## Контекст сессии

Все защищённые endpoints требуют:
- `channel` — `telegram`, `vk`, `max`, `website`, `email`
- `external_user_id` — ID пользователя в канале

Дополнительно:
- `customer_id` — из ответа `identify-customer`; **обязателен** для checkout и оформления заказа
- `customer_id` должен соответствовать `channel + external_user_id` (иначе `409 customer_context_mismatch`)
- `GET /api/cart/` допускает anonymous cart без `customer_id` до идентификации
- `GET /api/orders/{number}/` и история заказов требуют `channel` и `external_user_id` в query-параметрах; доступ только к своим заказам

`identify-customer` используется ID-каналами Telegram/VK/MAX. Новый ID создаёт
карточку текущего канала даже при совпадении телефона с другой карточкой; совпадение
фиксируется как неблокирующий конфликт. Email разрешается адаптером по адресу
отправителя, а web — серверной формой по телефону/email без публикации adapter token.

## Endpoints

| Method | Path | Назначение |
|--------|------|------------|
| GET | `/api/health/` | Проверка работоспособности |
| GET | `/api/meta/` | Справочники choices |
| GET | `/api/products/` | Список активных товаров |
| GET | `/api/products/{public_code}/` | Карточка товара |
| POST | `/api/identify-customer/` | Идентификация клиента |
| POST | `/api/intake/events/` | Идемпотентно принять событие в единую очередь |
| GET | `/api/intake/events/{event_id}/` | Получить статус и единый ответ (query: channel, external_user_id) |
| GET | `/api/cart/` | Получить/создать корзину |
| PUT | `/api/cart/items/{product_id}/` | Установить количество |
| DELETE | `/api/cart/items/{product_id}/` | Удалить позицию |
| DELETE | `/api/cart/items/` | Очистить корзину |
| POST | `/api/checkout/preview/` | Превью сумм заказа |
| POST | `/api/orders/` | Оформить заказ |
| GET | `/api/orders/{public_number}/` | Детали заказа (query: channel, external_user_id) |
| GET | `/api/customers/{public_code}/orders/` | История заказов (query: channel, external_user_id) |

## Полный сценарий (curl)

Событие естественного языка передаётся адаптером с неизменным ID источника:

```bash
curl -X POST http://localhost:8000/api/intake/events/ \
  -H "Content-Type: application/json" \
  -H "X-Adapter-Token: your-token" \
  -d '{"channel":"telegram","external_event_id":"message-100","external_user_id":"123","conversation_key":"chat-123","raw_text":"Хочу две упаковки креветок"}'
```

Первый запрос возвращает `202`, повтор с тем же `channel + external_event_id` — `200` и `duplicate: true`. Поля `raw_text` и `raw_payload` дубликатом не перезаписываются.

Результат обрабатывается асинхронно. Адаптер опрашивает endpoint статуса с тем же
контекстом канала; несовпадающий `channel + external_user_id` получает `404`:

```bash
curl "http://localhost:8000/api/intake/events/<event-uuid>/?channel=telegram&external_user_id=123" \
  -H "X-Adapter-Token: your-token"
```

Ответ содержит `complete`, безопасный снимок `draft` и унифицированный `response`
с типом и готовым текстом для клиента. Внутренняя ошибка и секреты не возвращаются.

```bash
# 1. Каталог (публично)
curl http://localhost:8000/api/products/

# 2. Идентификация
curl -X POST http://localhost:8000/api/identify-customer/ \
  -H "Content-Type: application/json" \
  -H "X-Adapter-Token: your-token" \
  -d '{"channel":"telegram","external_user_id":"123","phone":"+79123456789","display_name":"Иван","phone_verification_source":"platform_contact"}'

# 3. Получить корзину (anonymous — без customer_id, или с customer_id после identify)
curl "http://localhost:8000/api/cart/?channel=telegram&external_user_id=123&customer_id=1" \
  -H "X-Adapter-Token: your-token"

# 4. Добавить в корзину
curl -X PUT http://localhost:8000/api/cart/items/1/ \
  -H "Content-Type: application/json" \
  -H "X-Adapter-Token: your-token" \
  -d '{"channel":"telegram","external_user_id":"123","customer_id":1,"quantity":"2"}'

# 5. Превью заказа
curl -X POST http://localhost:8000/api/checkout/preview/ \
  -H "Content-Type: application/json" \
  -H "X-Adapter-Token: your-token" \
  -d '{"channel":"telegram","external_user_id":"123","customer_id":1,"receiving_type":"delivery"}'

# 6. Оформить заказ
curl -X POST http://localhost:8000/api/orders/ \
  -H "Content-Type: application/json" \
  -H "X-Adapter-Token: your-token" \
  -d '{"channel":"telegram","external_user_id":"123","customer_id":1,"receiving_type":"delivery","payment_method":"card_prepayment","delivery_address":"Москва, ул. Примерная, 1"}'

# 7. Детали заказа
curl "http://localhost:8000/api/orders/WM-XXXXXX/?channel=telegram&external_user_id=123" \
  -H "X-Adapter-Token: your-token"

# 8. История заказов
curl "http://localhost:8000/api/customers/CL-XXXXXX/orders/?channel=telegram&external_user_id=123" \
  -H "X-Adapter-Token: your-token"
```

Повторный `POST /api/orders/` с тем же `channel + external_user_id` после успешного оформления вернёт `422 empty_cart` — создаётся новая пустая активная корзина.

## Формат ошибок

```json
{
  "error": {
    "code": "invalid_quantity",
    "message": "Минимальное количество для «Товар»: 1.000",
    "details": {}
  }
}
```

Пример ошибки валидации serializer:

```json
{
  "error": {
    "code": "validation_error",
    "message": "Ошибка валидации данных",
    "details": {
      "channel": ["Обязательное поле."],
      "external_user_id": ["Обязательное поле."]
    }
  }
}
```

Коды безопасности:
- `customer_context_mismatch` (409) — `customer_id` не соответствует `channel + external_user_id`
- `cart_customer_mismatch` (409) — попытка работы с корзиной другого клиента
- `order_access_denied` (403) — заказ или история не принадлежат текущему пользователю канала

## Демо-данные на VPS

```bash
docker compose exec web python manage.py load_demo_data
```

Создаёт товары (пример seafood-магазина), правило доставки и скидку (идемпотентно).
## Платежи ЮKassa

| Метод | Path | Доступ | Назначение |
|---|---|---|---|
| POST | `/api/orders/{public_number}/payments/` | Adapter token + identity клиента | Создать или вернуть идемпотентную ссылку ЮKassa |
| POST | `/api/webhooks/payments/yookassa/` | Публичный URL ЮKassa | Принять и серверно проверить webhook |

Для первого endpoint передаются `channel` и `external_user_id`; ссылка создаётся только
для заказа этого клиента с методом `card_prepayment`. Факт оплаты устанавливает только
webhook. Подробнее: [docs/YOOKASSA.md](./YOOKASSA.md).
Возвраты создаются сотрудником только через Django Admin, а не через токен публичного
channel-адаптера.
