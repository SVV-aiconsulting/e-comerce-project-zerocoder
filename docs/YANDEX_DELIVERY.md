# Яндекс Доставка по России

## Зафиксированное решение

Для дипломного MVP выбран API «Яндекс Доставка по России». На локальной разработке
и первом развёртывании на VPS используется только официальный тестовый контур.
Переключение в коммерческий контур выполняется отдельной операционной процедурой.

Официальная документация:

- [доступ и тестовый контур](https://yandex.ru/support/delivery-profile/ru/api/other-day/access);
- [предварительный расчёт стоимости](https://yandex.ru/support/delivery-profile/ru/api/other-day/ref/1.-Podgotovka-zayavki/apib2bplatformpricing-calculator-post);
- [получение и подтверждение офферов](https://yandex.ru/support/delivery-profile/ru/api/other-day/ref/3.-Osnovnye-zaprosy/apib2bplatformofferscreate-post);
- [информация о заявке](https://yandex.ru/support/delivery-profile/ru/api/other-day/ref/3.-Osnovnye-zaprosy/apib2bplatformrequestinfo-get);
- [отмена заявки](https://yandex.ru/support/delivery-profile/ru/api/other-day/ref/3.-Osnovnye-zaprosy/apib2bplatformrequestcancel-post).

## Реализованный flow

```text
OrderDraft
  -> server validation
  -> pricing-calculator
  -> DeliveryQuote(preliminary)
  -> подтверждение клиентом
  -> Order + Shipment(draft)
  -> offers/create
  -> DeliveryQuote(offer)
  -> выбор оффера менеджером/автоматикой
  -> для онлайн-предоплаты: успешный webhook оплаты
  -> offers/confirm
  -> Shipment(confirmed)
  -> request/info polling
  -> delivered / cancelled / failed
```

LLM не вызывает доставку и не рассчитывает цену. GigaChat извлекает адрес, дату и
способ получения, а payload, стоимость, подтверждение, статусы и отмену обрабатывает
детерминированный backend.

Каждый расчёт хранит:

- test/production-контур;
- снимок адреса и грузоместа;
- стоимость, валюту и срок;
- внешний `offer_id`, время истечения и интервалы, если получен оффер;
- безопасные request/response payload без Bearer-токена;
- технические события расчёта, подтверждения, синхронизации и отмены.

Существующий `DeliveryRule` не удалён. Он остаётся ручным/fallback-расчётом, если
интеграция выключена, не заполнены характеристики товара или API временно недоступен.

## Требования к данным заказа

Для внешнего расчёта у каждого товара должны быть заполнены:

- вес брутто одной единицы в граммах;
- длина, ширина и высота упаковки в сантиметрах.

MVP формирует одно консервативное грузоместо: вес суммируется, максимальные длина и
ширина сохраняются, высота единиц складывается. Перед коммерческим запуском упаковку
нужно проверить на реальных заказах и при необходимости заменить алгоритм на явные
грузоместа менеджера/склада.

Для курьерской доставки обязательны полный адрес и телефон получателя. Совпадение
телефона с другой CRM-карточкой не блокирует заказ, но отсутствие телефона вызывает
уточняющий вопрос.

Наличные при получении с этим API не используются. `postpay` Яндекса означает оплату
картой в приложении Go или по ссылке из СМС. Для MVP доступны онлайн-предоплата либо
карта при получении.

Оффер с `card_prepayment` нельзя подтвердить до получения проверенного платёжного
webhook и статуса заказа `paid`. Этот guard уже реализован и будет связан с выбранным
платёжным провайдером на этапе 6.

## Конфигурация test

```dotenv
YANDEX_DELIVERY_ENABLED=True
YANDEX_DELIVERY_ENVIRONMENT=test
YANDEX_DELIVERY_TEST_TOKEN=<актуальный публичный тестовый Bearer-токен из документации>
YANDEX_DELIVERY_TEST_STATION_ID=fbed3aa1-2cc6-4370-ab4d-59c5cc9bb924
YANDEX_DELIVERY_TIMEOUT_SECONDS=20
YANDEX_DELIVERY_MERCHANT_INN=
YANDEX_DELIVERY_VAT_CODE=-1

YANDEX_DELIVERY_PRODUCTION_ENABLED=False
YANDEX_DELIVERY_PRODUCTION_TOKEN=
YANDEX_DELIVERY_PRODUCTION_STATION_ID=
```

Проверка соединения и расчёта:

```bash
python manage.py check_yandex_delivery
```

Команда намеренно работает только в test-контуре и выполняет предварительный расчёт
для официального тестового ПВЗ. Она не вызывает `offers/create`, `offers/confirm` и
не создаёт доставку. На 25.08.2026 live-проверка вернула `182.39 RUB`, 4 дня; это
проверочный результат, а не фиксированный тариф.

Тестовый контур обрабатывает только тестовые адреса Москвы. Ошибка
`no_delivery_options` для произвольного адреса не означает, что адрес недоступен в
коммерческом контуре.

## Переключение в коммерческий режим

Нельзя переносить тестовый токен или тестовый склад в production. Перед
переключением необходимо:

1. Заключить договор и получить доступ к коммерческому кабинету Яндекс Доставки.
2. Получить отдельный коммерческий Bearer-токен.
3. Получить у коммерческого менеджера production `platform_station_id` склада.
4. Заполнить ИНН продавца и проверить ставку НДС для каждого типа товара.
5. Заполнить и проверить реальные вес/габариты всего активного каталога.
6. Выполнить контролируемые тесты курьерской доставки, ПВЗ, отмены и возврата.
7. Проверить юридические тексты, стоимость для клиента и политику бесплатной доставки.
8. Создать резервную копию PostgreSQL и назначить ответственного за первый заказ.
9. Только после этого изменить `.env`:

```dotenv
YANDEX_DELIVERY_ENVIRONMENT=production
YANDEX_DELIVERY_PRODUCTION_ENABLED=True
YANDEX_DELIVERY_PRODUCTION_TOKEN=<commercial-token>
YANDEX_DELIVERY_PRODUCTION_STATION_ID=<commercial-station-id>
YANDEX_DELIVERY_MERCHANT_INN=<merchant-inn>
YANDEX_DELIVERY_VAT_CODE=<actual-vat-code>
```

10. Перезапустить `web`, Celery worker и Celery beat и проверить конфигурацию командой
    `python manage.py check`.

Код не принимает произвольный base URL: хост выбирается по режиму. Для production
одновременно требуются `environment=production`, отдельные production-реквизиты и
предохранитель `YANDEX_DELIVERY_PRODUCTION_ENABLED=True`.

## Границы текущего этапа

- Live проверен только read-only `pricing-calculator` тестового контура.
- `offers/create`, бронирование и отмена покрыты contract-тестами без изменения
  внешнего состояния.
- Первый live E2E с созданием и отменой тестовой заявки выполняется после появления
  подготовленного тестового заказа CRM с реальными контактами и габаритами.
