# Интеграция GigaChat

Проверено: 04.09.2026

## Архитектурная роль

GigaChat ведёт диалог и выбирает один из тринадцати строго описанных backend-инструментов.
Он не получает SQL/ORM-доступ: поиск каталога, корзина, заказы, Яндекс Доставка и
ЮKassa исполняются обычными Django-сервисами, а результат возвращается модели как
сообщение роли `function`.

Для MVP используется прямой REST-адаптер, без LangChain/Haystack. PostgreSQL уже
хранит состояние диалога и аудит, Celery обеспечивает durable execution, поэтому
дополнительный agent runtime не нужен и не расходует память VPS.

Базовая модель — `GigaChat-2` (Lite). Если evaluation покажет недостаточное качество извлечения, конфигурация позволяет переключиться на `GigaChat-2-Pro` без изменения доменной логики.

## Конфигурация

```dotenv
AI_ORDER_PROCESSING_ENABLED=False
AI_ASSISTANT_ENABLED=False
AI_ASSISTANT_MAX_TOOL_CALLS=8
AI_ASSISTANT_HISTORY_MESSAGES=20
AI_ASSISTANT_STALE_CART_SECONDS=3600
GIGACHAT_CREDENTIALS=<Authorization Key из личного кабинета>
GIGACHAT_SCOPE=GIGACHAT_API_PERS
GIGACHAT_MODEL=GigaChat-2
GIGACHAT_BASE_URL=https://api.giga.chat/v1
GIGACHAT_AUTH_URL=https://ngw.devices.sberbank.ru:9443/api/v2/oauth
GIGACHAT_CA_BUNDLE=<путь к russian_trusted_root_ca_pem.crt>
GIGACHAT_VERIFY_SSL=True
```

Локально также поддержано существующее имя `GIGACHAT_TOKEN`; оно используется как fallback для `GIGACHAT_CREDENTIALS`. Значение не должно попадать в Git, логи, traceback или документацию.

Scope:

- `GIGACHAT_API_PERS` — физическое лицо;
- `GIGACHAT_API_B2B` — ИП/юрлицо с пакетами;
- `GIGACHAT_API_CORP` — ИП/юрлицо pay-as-you-go.

OAuth access token кэшируется только в памяти worker и обновляется до истечения. Постоянный ключ читается из окружения.

## Tool-calling workflow

Доступны функции `search_products`, `get_cart`, `set_cart_item`,
`remove_cart_item`, `configure_checkout`, `preview_order`,
`list_customer_orders`, `repeat_order`, `get_payment_link`, `confirm_order`,
`get_cancellation_options`, `clear_cart`, `cancel_order`.

- Аргументы проверяются Pydantic до вызова доменного сервиса.
- Перед отправкой в GigaChat nullable-обёртки `anyOf(..., null)`, создаваемые
  Pydantic для необязательных полей, преобразуются в поддерживаемый одиночный
  `type`: необязательное поле можно не передавать, а принятые аргументы всё равно
  проверяются исходной строгой моделью.
- Каждый вызов сохраняется в `AssistantToolCall`; повтор мутации одного события
  возвращает прежний результат по idempotency key.
- `confirm_order` проверяет статус и revision preview, а также отдельное явное
  подтверждение текстом или callback.
- Финальный текст GigaChat сохраняется в `AssistantMessage` и затем отдаётся
  каналам без повторной генерации из изменившегося черновика.
- Текущая корзина и последний результат поиска передаются как read-only
  `BACKEND_CONTEXT`, поэтому короткий ответ клиента («2 упаковки») продолжает
  выбор по точному `public_code`, а не по придуманному моделью slug.
- Preview, параметры доставки, единственный вопрос подтверждения, создание заказа,
  ссылка оплаты и история заказов рендерятся детерминированно из результата tools.
  Явное подтверждение обрабатывается backend без повторного model call.
- Пустой `query` у `search_products` возвращает полный активный каталог; фильтр
  использует названия и синонимы. Ответ каталога всегда содержит цену, единицу и
  минимальное количество из CRM.
- Двусмысленная отмена сначала показывает текущую корзину и активные заказы.
  Очистка корзины и отмена оформленного заказа являются разными audited tools.
- Наполненная корзина после 3600 секунд без диалога блокирует продолжение, пока
  клиент явно не подтвердит её актуальность либо не очистит.
- Невалидный агентный ход не запускает `ORDER_REPAIR` и не вызывает автоматическую
  передачу менеджеру; клиент получает один сохранённый безопасный ответ.

## TLS

Проверка TLS обязательна. `verify=False` намеренно запрещён адаптером. Официальный корневой сертификат НУЦ Минцифры можно скачать по инструкции GigaChat:

```powershell
.\scripts\setup_gigachat_ca.ps1
```

Скрипт сохраняет публичный сертификат в `.local/certs/`, которая исключена из Git. При наличии этого стандартного локального пути Django подхватывает его автоматически. На VPS сертификат следует установить в системное trust store контейнера/хоста либо передать путь через `GIGACHAT_CA_BUNDLE`.

Официальные материалы:

- [авторизация и актуальные API URL](https://developers.sber.ru/docs/ru/gigachat/api/reference/rest/gigachat-api);
- [structured output с JSON Schema и `strict=true`](https://developers.sber.ru/docs/ru/gigachat/guides/structured-output);
- [генерация аргументов пользовательских функций](https://developers.sber.ru/docs/ru/gigachat/guides/functions/generating-arguments-for-custom-functions);
- [сертификаты НУЦ Минцифры](https://developers.sber.ru/docs/ru/gigachat/certificates);
- [модели GigaChat](https://developers.sber.ru/docs/ru/gigachat/models/main).

## Проверенный результат

Live smoke-test выполнен с секретом из локального `.env` и включённой TLS-проверкой:

- OAuth — успешно;
- фактическая модель — `GigaChat-2:2.0.30.01`;
- structured output прошёл Pydantic/JSON Schema;
- намерение — `create_order`;
- товар сопоставлен с локальным каталогом детерминированным matcher;
- количество записано в `OrderDraftItem`;
- отсутствующий клиент привёл к `needs_clarification`, а не к созданию заказа.

Тестовые записи выполнялись в откатываемой транзакции и не остались в локальной БД.

## Evaluation

Воспроизводимый набор находится в
`apps/intake/evaluation/order_extraction_cases.json` и запускается командой:

```powershell
python manage.py evaluate_gigachat --fail-under 0.80
```

Команда проверяет шесть обезличенных сценариев: доставку с онлайн-оплатой,
самовывоз, изменение количества, явное подтверждение, неизвестный товар и prompt
injection. Все записи создаются внутри транзакции и в конце принудительно
откатываются.

Результаты 24.08.2026:

- `ORDER_EXTRACTION 1.0.0`: baseline 4/6 (67%);
- `1.1.0`: мета-текст больше не попадал в поля, но «добавь» на пустом черновике
  всё ещё классифицировалось как `modify_order`;
- `1.2.0` + детерминированные server guardrails: 6/6 (100%);
- `ECOMMERCE_TOOLS_AGENT 1.3.0`: карточки товаров, повтор заказа и переход к
  preview дополнительно защищены детерминированными backend-маршрутами;
- `ECOMMERCE_TOOLS_AGENT 1.4.0`: история чата исключена из источников фактов;
  каталог, CRM-заказы, новая доставка и оплата требуют backend tool call текущего
  хода, а базовые переходы checkout маршрутизируются сервером;
- после прогона в БД осталось 0 evaluation-событий и 0 evaluation-черновиков.

Guardrails исправляют только однозначные инварианты workflow: `modify_order`
невозможен для пустого черновика, а явные формулировки онлайн/по ссылке/предоплата
означают `card_prepayment`. Исходный ответ модели остаётся в `raw_response`,
нормализованный — в `structured_output`.
