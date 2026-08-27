# Email-канал через Яндекс Почту

Проверено по официальной документации: 24.08.2026.

## Архитектура

```text
Яндекс IMAP (SSL 993)
        |
  Celery Beat: poll_yandex_mail
        |
 Message-ID -> идемпотентный InboundEvent(channel=email)
        |
 GigaChat -> validation -> OrderDraft -> clarification/order
        |
 durable OutboundMessage + retry/lease
        |
Яндекс SMTP (SSL 465)
```

Входящее письмо читается через `BODY.PEEK[]`, поэтому получение содержимого само по
себе не ставит флаг `Seen`. Письмо помечается прочитанным только после регистрации
события в PostgreSQL и постановки в durable-очередь. Повторное письмо с тем же
`Message-ID` не создаёт второе событие.

Email отправителя преобразуется в HMAC-идентификатор и не используется открыто как
`external_user_id`. Исходный адрес сохраняется в карточке клиента и защищённом payload
для ответа. Новый адрес сразу создаёт email-only карточку, поэтому отсутствие телефона
не блокирует обработку заказа. Если адрес однозначно найден в CRM, email-канал
привязывается к существующей карточке.

Телефон из письма считается контактом текущей email-карточки. Совпадение номера с
другой карточкой создаёт неблокирующий `CustomerIdentityConflict`: заказ продолжает
оформляться через email-карточку, а менеджер позднее решает, объединять ли клиентов.

Ответы сначала сохраняются как `OutboundMessage`. Отправка использует lease-токен,
лимит попыток, exponential backoff и стабильный `Message-ID`. Это защищает от
параллельных worker, а заголовки `In-Reply-To`/`References` сохраняют цепочку писем.

## Настройка Яндекс Почты

В настройках ящика необходимо разрешить IMAP и использование паролей приложений,
затем создать отдельный пароль приложения типа «Почта». Официальные параметры:

- IMAP: `imap.yandex.ru`, SSL, порт `993`;
- SMTP: `smtp.yandex.ru`, SSL, порт `465`;
- для Яндекс 360 логином служит полный адрес ящика.

Источник: [официальная инструкция Яндекс Почты](https://yandex.ru/support/yandex-360/business/mail/ru/mail-clients/others).

## Переменные окружения

```dotenv
EMAIL_CHANNEL_ENABLED=False
YANDEX_EMAIL_ADDRESS=orders@example.ru
YANDEX_EMAIL_APP_PASSWORD=<отдельный пароль приложения>
YANDEX_IMAP_HOST=imap.yandex.ru
YANDEX_IMAP_PORT=993
YANDEX_IMAP_FOLDER=INBOX
YANDEX_SMTP_HOST=smtp.yandex.ru
YANDEX_SMTP_PORT=465
EMAIL_POLL_INTERVAL_SECONDS=60
EMAIL_RESPONSE_DISPATCH_INTERVAL_SECONDS=15
EMAIL_POLL_BATCH_SIZE=50
EMAIL_MAX_MESSAGE_BYTES=5242880
EMAIL_OUTBOUND_LEASE_SECONDS=300
EMAIL_OUTBOUND_MAX_ATTEMPTS=5
EMAIL_NETWORK_TIMEOUT_SECONDS=30
```

Секрет нельзя отправлять в чат, добавлять в документацию или коммитить. Сначала
сохраните значения в локальном `.env`, оставив `EMAIL_CHANNEL_ENABLED=False`.

## Безопасная проверка подключения

Команда проверяет TLS и авторизацию, открывая IMAP-папку только в read-only режиме;
она не читает, не помечает и не отправляет письма:

```powershell
python manage.py check_yandex_mail
```

После успешной проверки можно включить канал:

```dotenv
EMAIL_CHANNEL_ENABLED=True
```

Затем Celery Beat запускает приём и отправку автоматически. Для локального запуска
нужны Redis, worker очереди `intake` и Beat.

## Ограничения MVP

- обрабатывается `text/plain`, при его отсутствии — текст из `text/html`;
- вложения игнорируются;
- письма больше установленного лимита и автоответы пропускаются;
- телефон из email считается сообщённым клиентом, но не подтверждённым SMS;
- совпадение телефона/email с другой карточкой не блокирует заказ и не приводит к
  автоматическому объединению;
- SMTP имеет семантику at-least-once: при редком падении worker после фактической
  отправки и до фиксации `sent` сервер может повторить письмо с тем же `Message-ID`.
