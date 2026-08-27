# Структура базы данных WebMarket

Ниже описана структура БД по текущим моделям Django (приложения `apps/*`).

## Общая информация

- СУБД: PostgreSQL.
- Для всех моделей, унаследованных от `TimeStampedModel`, автоматически есть поля:
  - `id` (PK, `BigAutoField`);
  - `created_at` (`timestamp`, авто при создании);
  - `updated_at` (`timestamp`, авто при обновлении).
- Таблицы Django (`auth_*`, `django_*`, `admin_log` и т.д.) также создаются миграциями, но ниже перечислены бизнес-таблицы проекта.

## Таблицы каталога

### `catalog_product`
- `id` — PK.
- `created_at`, `updated_at`.
- `public_code` — `varchar(32)`, `UNIQUE`, код товара.
- `name` — `varchar(255)`, наименование.
- `unit` — `varchar(16)`, единица измерения (choices).
- `min_quantity` — `decimal(10,3)`, минимум `>= 0.001`.
- `base_price` — `decimal(10,2)`, минимум `>= 0`.
- `is_active` — `boolean`.
- `description` — `text`, может быть пустым.
- `sort_order` — `integer` (positive), порядок сортировки.

**Индексы:**
- составной индекс: (`is_active`, `sort_order`).

### `catalog_productimage`
- `id` — PK.
- `product_id` — FK -> `catalog_product.id` (`ON DELETE CASCADE`).
- `image` — `varchar` (путь к файлу в media).
- `alt_text` — `varchar(255)`, может быть пустым.
- `is_main` — `boolean`.
- `sort_order` — `integer` (positive).
- `created_at` — `timestamp`, авто при создании.

**Ограничения:**
- `UNIQUE` условный: только одно главное фото на товар  
  (`unique_main_image_per_product`, условие `is_main = true`).

## Таблицы клиентов

### `customers_customer`
- `id` — PK.
- `created_at`, `updated_at`.
- `public_code` — `varchar(32)`, `UNIQUE`.
- `name` — `varchar(255)`.
- `phone` — `varchar(11)`, `UNIQUE`, валидация формата (11 цифр).
- `first_source` — `varchar(16)` (choices).
- `first_order_at` — `timestamp`, `NULL`.
- `last_order_at` — `timestamp`, `NULL`.
- `orders_count` — `integer` (positive).
- `total_orders_sum` — `decimal(12,2)`.
- `status` — `varchar(16)` (choices).
- `marketing_consent` — `boolean`.
- `personal_data_consent` — `boolean`.
- `personal_data_consent_link` — `varchar`/URL, может быть пустым.
- `phone_verified_at` — `timestamp`, `NULL` (дата подтверждения телефона).
- `manager_comment` — `text`, может быть пустым.

### `customers_customerchannelidentity`
- `id` — PK.
- `created_at`, `updated_at`.
- `customer_id` — FK -> `customers_customer.id` (`ON DELETE CASCADE`).
- `channel` — `varchar(16)` (choices).
- `external_user_id` — `varchar(128)`.
- `username` — `varchar(255)`, может быть пустым.

**Ограничения:**
- `UNIQUE(channel, external_user_id)`  
  (`unique_channel_external_user`).
- `UNIQUE(customer_id, channel)`  
  (`unique_customer_channel`).

## Таблицы корзины

### `carts_cart`
- `id` — PK.
- `created_at`, `updated_at`.
- `customer_id` — FK -> `customers_customer.id` (`ON DELETE SET NULL`), `NULL`.
- `channel` — `varchar(16)` (choices).
- `external_user_id` — `varchar(128)`.
- `status` — `varchar(16)` (choices).

**Ограничения:**
- условный `UNIQUE(channel, external_user_id)` только для активной корзины  
  (`unique_active_cart_per_channel_user`, условие `status = 'active'`).

### `carts_cartitem`
- `id` — PK.
- `created_at`, `updated_at`.
- `cart_id` — FK -> `carts_cart.id` (`ON DELETE CASCADE`).
- `product_id` — FK -> `catalog_product.id` (`ON DELETE CASCADE`).
- `quantity` — `decimal(10,3)`, минимум `>= 0.001`.

**Ограничения:**
- `UNIQUE(cart_id, product_id)`  
  (`unique_product_in_cart`).

## Таблицы доставки

### `delivery_deliveryrule`
- `id` — PK.
- `created_at`, `updated_at`.
- `name` — `varchar(255)`.
- `is_active` — `boolean`.
- `delivery_cost` — `decimal(10,2)`, минимум `>= 0`.
- `free_delivery_from` — `decimal(10,2)`, `NULL`, минимум `>= 0`.
- `min_order_amount` — `decimal(10,2)`, минимум `>= 0`.
- `delivery_zone` — `varchar(255)`, может быть пустым.
- `comment` — `text`, может быть пустым.

## Таблицы скидок

### `discounts_discountrule`
- `id` — PK.
- `created_at`, `updated_at`.
- `name` — `varchar(255)`.
- `is_active` — `boolean`.
- `priority` — `integer` (positive).
- `min_order_amount` — `decimal(10,2)`, минимум `>= 0`.
- `min_customer_orders` — `integer` (positive).
- `discount_percent` — `decimal(5,2)`, диапазон `0..100`.
- `discount_amount` — `decimal(10,2)`, минимум `>= 0`.
- `free_delivery` — `boolean`.
- `date_start` — `date`, `NULL`.
- `date_end` — `date`, `NULL`.
- `comment` — `text`, может быть пустым.

## Таблицы заказов

### `orders_order`
- `id` — PK.
- `created_at`, `updated_at`.
- `public_number` — `varchar(32)`, `UNIQUE`.
- `customer_id` — FK -> `customers_customer.id` (`ON DELETE PROTECT`).
- `customer_code_snapshot` — `varchar(32)`.
- `customer_name_snapshot` — `varchar(255)`.
- `customer_phone_snapshot` — `varchar(11)`.
- `channel` — `varchar(16)` (choices).
- `is_new_customer` — `boolean`.
- `receiving_type` — `varchar(16)` (choices).
- `desired_date` — `date`, `NULL`.
- `desired_time_interval` — `varchar(8)`, может быть пустым.
- `delivery_address` — `text`, может быть пустым.
- `customer_comment` — `text`, может быть пустым.
- `manager_comment` — `text`, может быть пустым.
- `items_total` — `decimal(12,2)`, минимум `>= 0`.
- `discount_amount` — `decimal(12,2)`, минимум `>= 0`.
- `delivery_cost` — `decimal(12,2)`, минимум `>= 0`.
- `total_amount` — `decimal(12,2)`, минимум `>= 0`.
- `order_status` — `varchar(16)` (choices).
- `payment_status` — `varchar(16)` (choices).
- `payment_method` — `varchar(32)` (choices).

### `orders_orderitem`
- `id` — PK.
- `order_id` — FK -> `orders_order.id` (`ON DELETE CASCADE`).
- `product_id` — FK -> `catalog_product.id` (`ON DELETE PROTECT`).
- `product_name_snapshot` — `varchar(255)`.
- `product_unit_snapshot` — `varchar(16)`.
- `quantity` — `decimal(10,3)`, минимум `>= 0.001`.
- `unit_price` — `decimal(10,2)`, минимум `>= 0`.
- `total_price` — `decimal(12,2)`, минимум `>= 0`.

### `orders_orderstatushistory`
- `id` — PK.
- `order_id` — FK -> `orders_order.id` (`ON DELETE CASCADE`).
- `event_datetime` — `timestamp`, авто при создании.
- `old_status` — `varchar(16)`, может быть пустым (первый переход).
- `new_status` — `varchar(16)`.
- `source` — `varchar(16)` (choices).
- `changed_by_id` — FK -> `auth_user.id` (`ON DELETE SET NULL`), `NULL`.
- `comment` — `text`, может быть пустым.

## Схема связей (кратко)

- `catalog_product` 1 -> N `catalog_productimage`
- `customers_customer` 1 -> N `customers_customerchannelidentity`
- `customers_customer` 1 -> N `carts_cart` (опционально, т.к. `SET NULL`)
- `carts_cart` 1 -> N `carts_cartitem`
- `catalog_product` 1 -> N `carts_cartitem`
- `customers_customer` 1 -> N `orders_order`
- `orders_order` 1 -> N `orders_orderitem`
- `catalog_product` 1 -> N `orders_orderitem` (`PROTECT`)
- `orders_order` 1 -> N `orders_orderstatushistory`
- `auth_user` 1 -> N `orders_orderstatushistory` (опционально)

## Важные бизнес-ограничения

- У одного пользователя канала может быть только одна активная корзина.
- В одной корзине товар может присутствовать только один раз.
- У товара может быть только одно главное фото.
- Канальная идентичность клиента уникальна в пределах канала.
- Заказ нельзя удалить вместе с клиентом (`PROTECT`), чтобы не терять историю.
