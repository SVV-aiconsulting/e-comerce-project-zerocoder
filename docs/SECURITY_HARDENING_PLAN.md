# План усиления безопасности (post-MVP)

Этот документ фиксирует следующие шаги по повышению безопасности после базовой настройки автодеплоя.

## Текущий риск-профиль (кратко)

- CI/CD-схема (GitHub Actions -> GHCR -> VPS) выбрана корректно.
- Основные риски сейчас не в самой схеме деплоя, а в окружении сервера:
  - HTTP без TLS (до настройки домена и сертификата);
  - деплой под `root`;
  - потенциально избыточные права у `VPS_GHCR_TOKEN`;
  - риск дефолтных/слабых секретов в `.env`.

## Что уже сделано в коде

- В `docker-compose.prod.yml` добавлен `nginx` reverse proxy.
- Прямой publish `web:8000` в prod удалён (`web` доступен только внутри docker-сети).
- Внешний трафик идёт через `nginx` на `:80`.
- Добавлен `SECURE_PROXY_SSL_HEADER` в `config/settings/production.py`.
- `restart: unless-stopped` и healthcheck для `db`, `web`, `nginx`, `telegram_bot`.
- Deploy workflow: preflight (`git status`, проверка `DJANGO_DEBUG`), pin образов по `github.sha`, post-deploy health check.
- Операционный runbook: [docs/PRODUCTION_OPERATIONS.md](./docs/PRODUCTION_OPERATIONS.md).

## Срочно (сделать в первую очередь)

1. Ротировать секреты приложения на VPS:
   - `DJANGO_SECRET_KEY`
   - `POSTGRES_PASSWORD`
   - `ADAPTER_API_TOKENS`
2. Проверить на VPS, что после деплоя порт `:8000` больше не слушает внешний интерфейс; оставить снаружи только `80/443`.
3. Включить firewall (`ufw`) и явно разрешить только нужные порты.
4. Отключить SSH password auth и прямой root-login по паролю.
5. Проверить `VPS_GHCR_TOKEN`: минимальные права (`read:packages`), без лишних scope.

## 1) Перевести прод на HTTPS

- Подключить домен к VPS.
- Настроить `nginx` как reverse proxy к `127.0.0.1:8000`.
- Выпустить сертификат Let's Encrypt (`certbot`).
- Принудительно редиректить HTTP -> HTTPS.

После перехода на HTTPS вернуть в `config/settings/production.py`:

- `SESSION_COOKIE_SECURE = True`
- `CSRF_COOKIE_SECURE = True`
- `CSRF_TRUSTED_ORIGINS = ["https://<ваш-домен>"]`

## 2) Снизить риск доступа к серверу

- Не деплоить под `root` в GitHub Actions.
- Создать отдельного пользователя `deploy`.
- Ограничить права пользователя `deploy` только необходимыми командами.
- Оставить вход по SSH только по ключу, отключить password auth.
- Для root оставить `PermitRootLogin prohibit-password` или полностью `no`.

## 3) Защитить ветку `main` в GitHub

- Включить Branch protection rules.
- Запретить прямой push в `main`.
- Разрешить деплой только после merge Pull Request.
- Добавить обязательную проверку успешного workflow перед merge.

## 4) Минимизировать и ротировать секреты

- Использовать отдельный SSH-ключ только для GitHub Actions.
- Ограничить срок действия токена `VPS_GHCR_TOKEN`.
- Переодически ротировать:
  - `VPS_SSH_KEY`
  - `VPS_GHCR_TOKEN`
  - `DJANGO_SECRET_KEY`
  - `POSTGRES_PASSWORD`
  - `ADAPTER_API_TOKENS`
- Хранить `.env` только на VPS (не в Git).
- Не использовать дефолтные значения из `.env.example` в проде.

## 5) Уменьшить внешнюю поверхность атак

- Закрыть наружный доступ к PostgreSQL (порт `5432`) на уровне firewall.
- Открытый наружу `:8000` считать временным техническим режимом.
- После настройки `nginx` и HTTPS закрыть прямой внешний доступ к `:8000` (разрешить только localhost/reverse proxy).
- Оставить снаружи только нужные порты (`22`, `80`, `443`).

## 6) Усилить безопасность Django

- В проде держать `DEBUG=False`.
- Настроить `SECURE_PROXY_SSL_HEADER` при работе за `nginx`.
- Добавить HSTS после перехода на стабильный HTTPS.
- Ограничить `ALLOWED_HOSTS` только нужными доменами/IP.
- После включения HTTPS вернуть:
  - `SESSION_COOKIE_SECURE = True`
  - `CSRF_COOKIE_SECURE = True`
  - `CSRF_TRUSTED_ORIGINS = ["https://<ваш-домен>"]`

## 7) Наблюдаемость и операционная безопасность

- Подключить мониторинг доступности (`/api/health/`) и алерты.
- Хранить и ротировать логи приложения и `nginx`.
- Добавить регулярные бэкапы базы данных и проверку восстановления.
- Обновлять базовые образы и пакеты безопасности (Docker image, ОС VPS).

## Мини-чеклист перед релизом

- [ ] HTTPS включён и проверен.
- [ ] Вход в admin работает только по HTTPS.
- [ ] Порт `8000` закрыт снаружи (доступ только через `nginx`).
- [ ] `main` защищена, прямой push запрещён.
- [ ] Деплой выполняется не от `root`.
- [ ] Секреты ротированы и актуальны.
- [ ] Бэкапы БД настроены и протестированы.
