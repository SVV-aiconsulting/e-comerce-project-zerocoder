# Локальная база данных WebMarket

## Решение

Во время разработки и интеграционного тестирования WebMarket использует отдельный
локальный PostgreSQL. SQLite допускается только как быстрый дополнительный unit-test
runtime и не заменяет PostgreSQL-проверку.

На рабочей машине найден PostgreSQL 18.4. Чтобы не менять системный экземпляр и не
зависеть от неизвестных учётных данных, проект создаёт собственный кластер:

- data directory: `.local/postgres-18-data`;
- bind: `127.0.0.1`;
- port: `55432`;
- role: `webmarket`;
- database: `webmarket`;
- authentication: `trust`, допустимая только для локального loopback-кластера;
- `.local/` полностью исключена из Git.

Production пока остаётся на PostgreSQL 16 из `docker-compose.prod.yml`. До релиза
полный набор миграций и тестов обязательно выполняется на целевой production-версии.

## Управление кластером

Инициализация и первый запуск:

```powershell
.\scripts\local_postgres.ps1 setup
```

Последующие команды:

```powershell
.\scripts\local_postgres.ps1 start
.\scripts\local_postgres.ps1 status
.\scripts\local_postgres.ps1 stop
```

Если PostgreSQL установлен в другом каталоге:

```powershell
$env:WEBMARKET_POSTGRES_BIN = "C:\path\to\PostgreSQL\bin"
.\scripts\local_postgres.ps1 setup
```

## Локальный `.env`

```env
POSTGRES_DB=webmarket
POSTGRES_USER=webmarket
POSTGRES_PASSWORD=local-only
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=55432
```

Файл `.env` не входит в Git. Production VPS использует собственный `.env` с
контейнерным host `db`, отдельным паролем и портом `5432`.

## Миграции и тесты

```powershell
python manage.py migrate --noinput
python manage.py makemigrations --check --dry-run
python manage.py check
python -m pytest apps -q -p no:cacheprovider
```

Pytest-django создаёт отдельную `test_webmarket` и удаляет её после тестов. Рабочие
локальные данные в `webmarket` не используются тестами.

## Перенос на VPS

Рекомендуемый production-процесс:

1. Создать backup текущей VPS-базы.
2. Развернуть новую версию приложения.
3. Выполнить `python manage.py migrate --noinput` на VPS.
4. Загрузить только согласованные справочные данные каталога/настроек.
5. При необходимости переноса локальных бизнес-данных использовать проверенный
   `pg_dump`/`pg_restore` в отдельное окно обслуживания.

Тестовых клиентов, сообщений, AI-ответов и платежей на production переносить нельзя.
