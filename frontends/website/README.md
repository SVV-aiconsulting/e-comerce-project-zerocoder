# Сайт WebMarket

Публичная одностраничная витрина на `https://webmarket.apernova.ru/`.

Сайт — frontend-адаптер канала `website`. Карточки, цены, фото и корзина живут в backend. Браузер не содержит бизнес-логики и не получает adapter token.

## Источник данных

- Каталог: `CatalogService` / `GET /api/products/` — те же активные товары, что в Telegram и VK.
- Фото и описания: `catalog_product` + `catalog_productimage` в PostgreSQL. Правка в Django Admin сразу видна во всех каналах.
- Корзина и заказ: `CartService` и `OrderService` через сессионные URL `/store/*` (CSRF, без токена в JavaScript).
- AI-форма остаётся вторым способом заказа: тот же `InboundEventService`.

Статичные JPEG в `static/website/catalog/` — только заготовка для `load_demo_data`. Витрина их не подменяет, если в админке другое фото.

## Локально

```bash
python manage.py collectstatic --noinput
python manage.py load_demo_data
python manage.py runserver
```
