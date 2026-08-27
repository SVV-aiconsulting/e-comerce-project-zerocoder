# Модель данных AI-обработки заказов

Версия: 0.1  
Дата: 24.08.2026  
Статус: согласованный проект перед созданием миграций

## 1. Граница приложения

Новые сущности первого этапа размещаются в Django-приложении `apps.intake`.
Приложение отвечает за путь **от входящего сообщения до подтверждённого черновика**.

`apps.intake` не выполняет:

- расчёт фактической цены и скидки;
- создание финального заказа в обход `OrderService`;
- вызов платёжного провайдера;
- создание доставки;
- произвольные операции LLM с Django ORM.

После подтверждения отдельный сервис конвертации переносит проверенные позиции в
существующую корзину и вызывает доменный `OrderService`. Это сохраняет единственную
реализацию расчёта и создания заказа.

## 2. Схема связей

```text
Customer 1 -------- N OrderDraft 1 -------- N OrderDraftItem N -------- 0..1 Product
   |                       |
   |                       +-------- N Clarification
   |                       |
   +------ N InboundEvent N -------- 0..1 OrderDraft
                              |
                              +------ N AIExtractionRun

OrderDraft 0..1 ---------------------------------------------- 1 Order
```

Основные правила:

- входящее событие сохраняется до вызова LLM;
- одно событие может быть обработано только один раз;
- черновик существует независимо от финального заказа;
- позиция может оставаться без `Product`, пока товар не сопоставлен;
- AI-вызов и уточнение являются отдельными аудируемыми сущностями;
- один черновик может быть преобразован максимум в один заказ.

## 3. Изменения общих справочников

В `apps.common.enums` требуется добавить:

- `Channel.EMAIL = "email"`;
- `CustomerSource.EMAIL = "email"`;
- `StatusChangeSource.EMAIL = "email"`.

Веб-форма продолжает использовать `Channel.WEBSITE`. UUID формы является только
техническим ключом события/диалога; CRM-клиент ищется по контактам формы. Для email
`external_user_id` является HMAC нормализованного адреса отправителя, а сам адрес
хранится в карточке клиента и защищённом payload.

### 3.1 Контакты и конфликты CRM

- `Customer.phone` и `Customer.email` индексируются, но не являются глобально
  уникальными: временные дубли допустимы до решения менеджера.
- У клиента должен быть хотя бы один контакт: телефон или email.
- Уникальным остаётся `(CustomerChannelIdentity.channel, external_user_id)`.
- Совпадение контактов разных карточек создаёт `CustomerIdentityConflict`.
- Конфликт не входит в условия готовности черновика и не блокирует заказ.
- Для web однозначное совпадение телефона/email использует существующую карточку;
  неоднозначное создаёт новую карточку и конфликты.

## 4. `InboundEvent`

Единая запись входящего сообщения или действия из любого канала.

| Поле | Тип | Правило |
|---|---|---|
| `public_id` | UUID | Уникальный публичный/correlation ID |
| `channel` | choices | Telegram, VK, MAX, website, email |
| `external_event_id` | varchar(255) | ID сообщения/события у источника; обязателен |
| `external_user_id` | varchar(255) | Пользователь/отправитель в канале |
| `conversation_key` | varchar(255) | Диалог, email-thread или web-session |
| `customer` | FK nullable, `SET_NULL` | Известный клиент CRM |
| `draft` | FK nullable, `SET_NULL` | Черновик, к которому отнесено событие |
| `kind` | choices | `message`, `callback`, `form_submission` |
| `raw_text` | text blank | Исходный пользовательский текст |
| `raw_payload` | JSON default dict | Минимально необходимый payload адаптера |
| `payload_schema_version` | positive int | Версия нормализованного payload |
| `status` | choices | Состояние обработки |
| `processing_attempts` | positive int | Число попыток worker |
| `next_retry_at` | datetime nullable | Время следующей попытки |
| `started_at` | datetime nullable | Начало текущей обработки |
| `processed_at` | datetime nullable | Успешное/финальное завершение |
| `last_error` | text blank | Обезличенное техническое описание ошибки |
| `created_at`, `updated_at` | datetime | Из `TimeStampedModel` |

### Статусы события

```text
received -> queued -> processing -> processed
                             |----> retry_scheduled -> queued
                             |----> failed
received/queued ------------------> ignored
```

### Ограничения и индексы

- Unique: `(channel, external_event_id)` — основная защита от дублей.
- Index: `(status, next_retry_at)` — выбор задач worker.
- Index: `(channel, conversation_key, created_at)` — восстановление диалога.
- `external_event_id` генерируется адаптером, если канал не предоставляет свой ID.
- В `raw_payload` не сохраняются токены, заголовки авторизации и лишние персональные
  данные.

## 5. `OrderDraft`

Агрегат формирующегося заказа. Это единственный источник состояния AI-диалога;
память Telegram/VK не считается надёжным состоянием.

| Поле | Тип | Правило |
|---|---|---|
| `public_id` | UUID | Уникальный внешний ID черновика |
| `customer` | FK nullable, `SET_NULL` | Клиент может идентифицироваться позднее |
| `channel` | choices | Канал создания |
| `external_user_id` | varchar(255) | Пользователь канала |
| `conversation_key` | varchar(255) | Ключ текущего диалога |
| `intent` | choices | Распознанное намерение |
| `status` | choices | Состояние черновика |
| `receiving_type` | choices blank | Доставка или самовывоз |
| `desired_date` | date nullable | Желаемая дата |
| `desired_time_interval` | choices blank | Интервал существующего справочника |
| `delivery_address` | text blank | Адрес до проверки delivery API |
| `payment_method` | choices blank | Предпочтение клиента |
| `contact_phone` | varchar blank | Телефон, использованный в текущем заказе |
| `contact_email` | email blank | Email, использованный в текущем заказе |
| `customer_comment` | text blank | Комментарий к заказу |
| `missing_fields` | JSON default list | Поля, которые необходимо запросить |
| `manager_attention_required` | bool | Требуется менеджер |
| `escalation_reason` | text blank | Причина передачи менеджеру |
| `revision` | positive int | Увеличивается при каждом изменении состава/условий |
| `previewed_revision` | positive int nullable | Версия, для которой рассчитан preview |
| `confirmed_revision` | positive int nullable | Версия, подтверждённая клиентом |
| `items_total` | decimal nullable | Последний серверный preview |
| `discount_amount` | decimal nullable | Последний серверный preview |
| `delivery_cost` | decimal nullable | Последний серверный preview |
| `total_amount` | decimal nullable | Последний серверный preview |
| `priced_at` | datetime nullable | Время расчёта preview |
| `confirmed_at` | datetime nullable | Время явного подтверждения |
| `converted_order` | OneToOne nullable, `PROTECT` | Финальный заказ |
| `created_at`, `updated_at` | datetime | Из `TimeStampedModel` |

### Намерения

- `create_order` — создать новый заказ;
- `modify_order` — изменить формирующийся заказ;
- `cancel_order` — отменить черновик;
- `order_status` — запросить статус существующего заказа;
- `product_question` — вопрос по каталогу;
- `unknown` — намерение не определено.

События `order_status`, `product_question` и `unknown` могут быть обработаны без
создания `OrderDraft`.

### Статусы черновика

```text
collecting <--------------------------+
    |                                 |
    +-> needs_clarification ----------+
    |
    +-> ready_for_preview -> awaiting_confirmation
                                  |
                                  +-> collecting (клиент изменил данные)
                                  +-> confirmed -> converted

Из любого активного состояния: -> cancelled / escalated / failed
```

Активные состояния: `collecting`, `needs_clarification`, `ready_for_preview`,
`awaiting_confirmation`, `confirmed`.

Для MVP действует частичное уникальное ограничение: один активный черновик на
`(channel, conversation_key)`. Новый параллельный заказ создаётся после завершения,
отмены или передачи предыдущего менеджеру.

## 6. `OrderDraftItem`

| Поле | Тип | Правило |
|---|---|---|
| `draft` | FK, `CASCADE` | Родительский черновик |
| `line_number` | positive int | Стабильный номер позиции |
| `raw_product_name` | varchar(255) | Название в тексте клиента |
| `requested_quantity` | decimal nullable | Может отсутствовать до уточнения |
| `requested_unit` | choices blank | Единица, указанная клиентом |
| `product` | FK nullable, `PROTECT` | Подтверждённый товар каталога |
| `match_status` | choices | Результат сопоставления |
| `candidate_product_ids` | JSON default list | ID допустимых кандидатов |
| `resolution_source` | choices blank | exact, alias, trigram, semantic, customer, manager |
| `resolution_confidence` | decimal nullable | Диагностика, не основание для записи |
| `validation_errors` | JSON default list | Машиночитаемые ошибки позиции |
| `created_at`, `updated_at` | datetime | Из `TimeStampedModel` |

`match_status`: `unresolved`, `matched`, `ambiguous`, `not_found`, `invalid`.

Ограничения:

- Unique: `(draft, line_number)`;
- quantity должна быть больше нуля, если она заполнена;
- статус `matched` невозможен без `product`;
- перед preview все активные позиции должны иметь `matched`, product и quantity.

## 7. `AIExtractionRun`

Аудит одного вызова LLM. Запись создаётся до сетевого запроса и получает финальный
статус даже при ошибке провайдера.

| Поле | Тип | Правило |
|---|---|---|
| `run_id` | UUID | Уникальный ID AI-вызова |
| `event` | FK, `PROTECT` | Входящее событие |
| `draft` | FK nullable, `SET_NULL` | Связанный черновик |
| `purpose` | choices | extraction, clarification, disambiguation, repair |
| `status` | choices | pending, succeeded, schema_invalid, provider_error, skipped |
| `provider` | varchar | Провайдер без секретных данных |
| `model_name` | varchar | Фактическая модель |
| `prompt_id` | varchar | ID из библиотеки промптов |
| `prompt_version` | varchar | Неизменяемая версия промпта |
| `input_hash` | varchar(64) | SHA-256 фактического входа |
| `raw_response` | text blank | Сырой ответ для аудита с политикой хранения |
| `structured_output` | JSON default dict | Результат structured output |
| `validation_errors` | JSON default list | Ошибки схемы/бизнес-валидации |
| `latency_ms` | positive int nullable | Задержка провайдера |
| `input_tokens`, `output_tokens` | positive int nullable | Расход токенов |
| `estimated_cost` | decimal nullable | Расчётная стоимость |
| `started_at`, `completed_at` | datetime | Длительность вызова |
| `created_at`, `updated_at` | datetime | Из `TimeStampedModel` |

Полный пользовательский ввод не дублируется в request payload: источником является
`InboundEvent`. API-ключи и служебные заголовки не сохраняются.

## 8. `Clarification`

| Поле | Тип | Правило |
|---|---|---|
| `draft` | FK, `CASCADE` | Черновик |
| `field_path` | varchar(255) | Например `items.0.product` или `delivery_address` |
| `question` | text | Фактически отправленный вопрос |
| `status` | choices | pending, answered, skipped, cancelled |
| `trigger_event` | FK nullable, `SET_NULL` | Событие, вызвавшее вопрос |
| `answered_by_event` | FK nullable, `SET_NULL` | Ответ клиента |
| `answer_text` | text blank | Нормализованный ответ для аудита |
| `attempt_number` | positive int | Номер повторного уточнения поля |
| `asked_at`, `answered_at` | datetime | Временные отметки |
| `created_at`, `updated_at` | datetime | Из `TimeStampedModel` |

Одновременно допускается не более одного `pending`-уточнения для одинаковых
`(draft, field_path)`. После лимита попыток черновик получает статус `escalated`.

## 9. Условия preview, подтверждения и конвертации

Preview разрешён, когда:

- клиент идентифицирован;
- есть минимум одна позиция;
- все позиции сопоставлены с активными товарами;
- количество прошло существующие ограничения каталога;
- заполнен способ получения;
- для доставки заполнен адрес;
- при включённой Яндекс Доставке указан телефон получателя и выбран поддерживаемый
  безналичный способ оплаты;
- нет pending-уточнений и ошибок валидации.

Подтверждение действительно, только если:

```text
revision == previewed_revision == confirmed_revision
```

Любое изменение состава, адреса, доставки или оплаты увеличивает `revision`, очищает
`confirmed_revision` и возвращает черновик к расчёту.

Конвертация выполняется в транзакции:

1. блокировка `OrderDraft` через `select_for_update`;
2. повторная проверка статуса, revision и отсутствия `converted_order`;
3. повторная проверка каталога и серверный перерасчёт;
4. при изменении суммы — новый preview и повторное подтверждение;
5. синхронизация существующей корзины;
6. вызов `OrderService.create_order_from_cart`;
7. запись `converted_order` и статуса `converted`.

Повторный вызов возвращает уже созданный `converted_order`, не создавая дубликат.

## 10. Данные Яндекс Доставки

`DeliveryQuote` связывается с `OrderDraft` и/или `Order` и хранит тип расчёта,
test/production-контур, fingerprint запроса, снимок грузоместа, стоимость, срок,
`offer_id`, время истечения и интервалы. Ошибка API сохраняется как failed quote и не
стирает возможность локального fallback.

`Shipment` имеет связь OneToOne с финальным `Order`. До подтверждения оффера это
локальный draft; после `offers/confirm` в нём появляются `external_request_id`,
внешний/нормализованный статус, tracking URL и время последней синхронизации.

`DeliverySyncEvent` записывает операцию, HTTP-статус и безопасные payload расчёта,
получения оффера, подтверждения, polling и отмены. Bearer-токен в БД не сохраняется.

## 11. Персональные данные и хранение

- Сохраняется только payload, необходимый для воспроизведения обработки.
- Токены каналов, cookies и заголовки авторизации запрещены в JSON-полях.
- Логи не должны содержать полный телефон, email или адрес.
- Raw AI-ответ доступен только менеджерам с соответствующими правами.
- Срок хранения raw payload/response задаётся до production и документируется в ТЗ.

## 12. Порядок реализации

1. Добавить email в общие enums.
2. Создать `apps.intake` и локальные enums состояний.
3. Реализовать модели и DB constraints.
4. Создать миграции.
5. Добавить Django Admin в read-oriented режиме.
6. Написать model/service-тесты идемпотентности и переходов.
7. Только после этого добавлять API и очередь.
## Платежи ЮKassa

- `Payment` — одна попытка онлайн-предоплаты с внешним ID, идемпотентностью, ссылкой,
  состоянием и снимком данных чека.
- `PaymentWebhookEvent` — неизменяемый аудит входящего уведомления и результата его
  серверной проверки.
- `Refund` — отдельная полная или частичная возвратная операция с собственным
  idempotence key.
- Переход `Order.payment_status=paid` выполняется только после webhook и повторного
  `GET /payments/{id}` у ЮKassa, а не после возврата клиента в браузер.
