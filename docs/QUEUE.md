# Единая очередь входящих заказов

## Назначение

Все каналы продаж регистрируют входящее обращение как `InboundEvent` в PostgreSQL. После фиксации транзакции Celery публикует в Redis только числовой ID события. Исходный текст, payload, статус и ошибки остаются в PostgreSQL — это источник истины и журнал обработки.

```text
Telegram / VK / email / web
            |
            v
  InboundEvent (PostgreSQL)
            |
        after commit
            v
       Redis / Celery
            |
            v
   intake.process_inbound_event
            |
            v
       OrderDraft
```

## Гарантии MVP

- Дубликат от канала блокируется ограничением `(channel, external_event_id)`.
- Повторный запуск задачи не создаёт второй черновик и не обрабатывает финальное событие заново.
- `processing_token` действует как lease: старый worker не сможет записать результат после повторного захвата зависшего события.
- Временная ошибка получает exponential backoff с jitter и не более пяти попыток по умолчанию.
- Финальная ошибка и число попыток видны в Django Admin.
- Celery Beat каждые 30 секунд переотправляет due-события, оставшиеся в PostgreSQL после сбоя публикации или worker.
- Пустое текстовое сообщение помечается `ignored`; полезное событие связывается с активным `OrderDraft`.

Это доставка **at least once**, поэтому каждый следующий обработчик — LLM, каталог, доставка и платёжные webhook — обязан быть идемпотентным.

## Конфигурация

Переменные приведены в `.env.example`:

- `CELERY_BROKER_URL` — Redis broker;
- `INTAKE_EVENT_LEASE_SECONDS` — время до повторного захвата зависшей задачи;
- `INTAKE_MAX_PROCESSING_ATTEMPTS` — общий лимит попыток;
- `INTAKE_RETRY_BASE_SECONDS` и `INTAKE_RETRY_MAX_SECONDS` — границы backoff;
- `INTAKE_DISPATCH_BATCH_SIZE` — размер выборки dispatcher.

`CELERY_TASK_ALWAYS_EAGER=True` разрешён только для автоматических тестов и локальной отладки одного процесса. Он не проверяет реальную доставку через Redis.

## Запуск через Docker Compose

```powershell
docker compose up -d db redis web celery_worker celery_beat
docker compose ps
docker compose logs -f celery_worker celery_beat
```

Локальная `.env` может указывать на PostgreSQL по `127.0.0.1:55432`; `docker-compose.yml` специально переопределяет адрес базы на `db:5432` только внутри контейнеров.

## Запуск без Docker

1. Запустить локальный PostgreSQL по инструкции `docs/LOCAL_DATABASE.md`.
2. Запустить Redis на `127.0.0.1:6379`.
3. Указать `CELERY_BROKER_URL=redis://127.0.0.1:6379/0`.
4. В отдельных терминалах выполнить:

```powershell
celery -A config worker --loglevel=INFO --queues=intake --pool=solo
celery -A config beat --loglevel=INFO
python manage.py runserver
```

На Windows `--pool=solo` нужен для предсказуемого локального worker. В production используется стандартный prefork worker из `docker-compose.prod.yml`.

## Диагностика

В Django Admin открыть раздел «Входящие события» и проверить:

- `status`;
- `processing_attempts`;
- `next_retry_at`;
- `last_error`;
- связь с `OrderDraft`.

Финальный `failed` не переотправляется автоматически: менеджер сначала устраняет причину, после чего для ручного retry будет добавлено отдельное безопасное admin-действие на этапе dashboard.
