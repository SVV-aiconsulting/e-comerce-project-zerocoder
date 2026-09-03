# ЮKassa: sandbox-MVP и переход в production

## Решение

Для WebMarket утверждена **ЮKassa**. На разработке и при первом VPS-deployment
используется только тестовый магазин. Реальные платежи, чеки и возвраты не создаются,
пока не выполнен production-чек-лист ниже.

## Реализованный поток

```text
Подтверждённый заказ
        ↓
PaymentService → POST /v3/payments (Idempotence-Key)
        ↓
confirmation_url → клиенту в Telegram / VK / email / web
        ↓
Webhook payment.succeeded или payment.canceled
        ↓
GET /v3/payments/{id} и сверка ID, суммы, валюты, metadata.order_public_number
        ↓
Payment + PaymentWebhookEvent + статус заказа в PostgreSQL
```

Возврат клиента на `YOOKASSA_RETURN_URL` не является подтверждением оплаты. Единственный
доверенный путь — webhook и повторное получение объекта платежа у ЮKassa.

## Конфигурация test-контура

В `.env` уже должны быть только тестовые реквизиты:

```env
YOOKASSA_ENABLED=False
YOOKASSA_ENVIRONMENT=test
YOOKASSA_TEST_SHOP_ID=<идентификатор тестового магазина>
YOOKASSA_TEST_SECRET_KEY=<секретный ключ тестового магазина>
YOOKASSA_RETURN_URL=http://localhost:8000/payment/return/
YOOKASSA_TIMEOUT_SECONDS=20
YOOKASSA_DEFAULT_VAT_CODE=1
YOOKASSA_VERIFY_WEBHOOK_IP=True
YOOKASSA_PRODUCTION_ENABLED=False
```

`YOOKASSA_DEFAULT_VAT_CODE=1` означает «без НДС» и подходит только если это соответствует
налоговому режиму продавца. Перед production значение необходимо сверить с бухгалтерией и
официальным справочником ЮKassa.

Чтобы безопасно проверить ключ без создания платёжной операции:

```powershell
$env:YOOKASSA_ENABLED = 'True'
py -3.14 manage.py check_yookassa
Remove-Item Env:YOOKASSA_ENABLED
```

Команда выполняет только `GET /v3/payments?limit=1`. Для тестового создания ссылки
временно включите `YOOKASSA_ENABLED=True` в локальном `.env`. Не включайте production-флаг.

## Webhook

В личном кабинете тестового магазина укажите публичный HTTPS URL:

```text
https://<ваш-домен>/api/webhooks/payments/yookassa/
```

Подпишите события `payment.succeeded` и `payment.canceled`. WebMarket принимает JSON,
сохраняет deduplication fingerprint и возвращает HTTP 200. Перед изменением заказа он
проверяет текущий объект платежа через API и при включённом флаге проверяет IP-адрес
отправителя по официальной сети ЮKassa.

Для локальной разработки публичный webhook обычно недоступен; используйте временный HTTPS
tunnel только для ручного теста и не публикуйте его как production URL.

## Данные чека и возвраты

Для платежа формируется `receipt`: позиции заказа, отдельная строка доставки, email
покупателя, НДС, тип расчёта, предмет расчёта и мера количества. Для онлайн-оплаты
email обязателен: ЮKassa направляет зарегистрированный электронный чек только на email,
поэтому телефон и email, введённый пользователем уже на платёжной странице, не могут
заменить контакт в ранее созданном API-запросе. В Telegram бот запрашивает email до
создания ссылки; website требует email при выборе «Картой онлайн».

Сумма всех строк `receipt` всегда равна `Order.total_amount`: цена в строке — цена за
единицу, а скидка распределяется по товарным строкам до передачи в ЮKassa. Это особенно
важно для весовых товаров, например `1.500 кг`. В ответе ЮKassa сохраняется
`receipt_registration` (статус и ошибка), его можно увидеть в карточке платежа Django
Admin и сверить с кабинетом ЮKassa.

`YOOKASSA_DEFAULT_VAT_CODE=1` означает «без НДС». Поддерживаются коды 1–12, включая
ставки 22% и 22/122, действующие с 2026 года. `YOOKASSA_TAX_SYSTEM_CODE` (1–6) можно
задать в `.env`, если его требует подключённая касса. Конкретные коды не выбираются
разработчиком: их должен подтвердить бухгалтер/владелец магазина по налоговому режиму.

`Refund` хранит отдельную операцию и свой idempotence key; полный и частичный возвраты
создаются через API.

Тестовый режим проверяет API-сценарий и данные чека, но не заменяет настройку 54‑ФЗ в
личном кабинете. Для получения письма в новом тестовом заказе владелец магазина должен
подключить в тестовом магазине «Чеки от ЮKassa» либо совместимую стороннюю онлайн-кассу,
выбрать сценарий «Платёж и чек одновременно» и после оплаты проверить
`receipt_registration.status` в кабинете. Без этой настройки платёж может успешно пройти,
а фискальный чек не будет зарегистрирован или отправлен.

При онлайн-предоплате текущий чек платежа имеет признак `full_prepayment`. После реальной
выдачи товара требуется отдельный чек зачёта предоплаты с `full_payment`; это будущая
операция fulfilment, её нельзя подменять уведомлением об оплате в Telegram.

## Production-чек-лист

1. Заключить договор с ЮKassa и создать **отдельный коммерческий магазин**.
2. Выпустить отдельный production secret key; не переиспользовать тестовый.
3. Настроить совместимую онлайн-кассу, сценарий 54-ФЗ и фактический НДС.
4. Развернуть VPS только с HTTPS и публичным доменом.
5. Указать production webhook и подписать `payment.succeeded`, `payment.canceled`,
   `refund.succeeded`.
6. Внести только в production secret store:
   `YOOKASSA_PRODUCTION_SHOP_ID` и `YOOKASSA_PRODUCTION_SECRET_KEY`.
7. Установить `YOOKASSA_RETURN_URL=https://<домен>/payment/return/`.
8. Установить одновременно `YOOKASSA_ENVIRONMENT=production` и
   `YOOKASSA_PRODUCTION_ENABLED=True`.
9. Проверить IP filtering reverse proxy, повторный GET платежа, идемпотентность и логи без
   секретов на одном тестовом заказе.
10. Провести контролируемый платёж, отмену и возврат малой суммы; сверить CRM, ЮKassa,
    кассу и ОФД.

## Официальные источники

- [Тестирование платежей и чеков](https://yookassa.ru/developers/payment-acceptance/testing-and-going-live/testing)
- [Формат API и Idempotence-Key](https://yookassa.ru/developers/using-api/interaction-format)
- [Создание redirect-платежа](https://yookassa.ru/developers/payment-acceptance/getting-started/payment-process)
- [Webhook и проверка подлинности](https://yookassa.ru/developers/using-api/webhooks)
- [Возвраты](https://yookassa.ru/developers/payment-acceptance/after-the-payment/refunds)
- [Чеки по 54-ФЗ](https://yookassa.ru/developers/payment-acceptance/receipts/54fz/other-services/basics)
