# Production operations (VPS)

Операционный runbook для продакшн-стенда WebMarket: диагностика, демо, бэкапы, откат и preflight перед деплоем.

Связанные документы: [SECURITY_HARDENING_PLAN.md](../SECURITY_HARDENING_PLAN.md), [README.md](../README.md) (раздел «Продакшн на VPS»), [docs/TELEGRAM_BOT.md](./TELEGRAM_BOT.md), [docs/VK_BOT.md](./VK_BOT.md).

## Статус стенда

| Критерий | Статус |
|----------|--------|
| Feature demo (внутренняя демонстрация функций) | **Готов** |
| Показ заказчику как рабочий стенд | **Да, можно** |
| Production-эталон без оговорок | **Пока нет** — HTTPS + проверенный backup (финальный этап) |
| CI/CD (Actions → GHCR → VPS) | Работает |
| Схема контейнеров | `nginx`, `web`, `db`, `telegram_bot` — все `healthy`; `vk_bot` — опционально (profile `vk`) |

**Закрыто в checkpoint (коммит `781d626` и последующие деплои):** restart policies, healthcheck, `DJANGO_DEBUG=False`, deploy preflight, pin образов по SHA, nginx security headers, runbook backup/rollback.

**Отложено на финальный этап hardening (не блокирует демо заказчику):**

- HTTPS (Let's Encrypt + secure cookies);
- первый backup + проверка restore;
- ротация прод-секретов;
- мониторинг и алерты.

## Политика VPS-репозитория

VPS — **immutable deploy target**: код и tracked-файлы синхронизируются только из GitHub Actions (`git fetch` + `git reset --hard`).

- **Не правьте** tracked-файлы на VPS вручную (они будут затёрты при следующем деплое).
- **Правьте только** `.env` на VPS (он в `.gitignore` и не перезаписывается).
- Перед деплоем workflow выводит `git status` и предупреждает о незакоммиченных tracked-изменениях.

Привести VPS к чистому состоянию вручную:

```bash
cd /root/Webmarket   # или ваш VPS_APP_DIR
git fetch origin main
git reset --hard origin/main
git clean -fd        # осторожно: удалит неотслеживаемые файлы
```

### Почему `git status` может показывать `ahead N` относительно `origin/main`

Deploy workflow делает `git fetch <URL-with-token> main` и `git reset --hard FETCH_HEAD`, **без** обновления локальной ссылки `origin/main`. Рабочая копия при этом соответствует актуальному `main` на GitHub — на деплой и runtime это **не влияет**.

Чтобы убрать предупреждение `ahead` (косметика):

```bash
cd /root/Webmarket   # или ваш VPS_APP_DIR
git fetch origin main
git branch -f main origin/main
git checkout main
```

Или однократно настроить remote с токеном и дальше использовать обычный `git fetch origin`.

## Preflight перед деплоем

Автоматически (в GitHub Actions deploy workflow):

1. `git status --short --branch` на VPS.
2. Предупреждение, если есть незакоммиченные tracked-изменения.
3. **Остановка деплоя**, если в `.env` на VPS `DJANGO_DEBUG=True`.

Вручную на VPS перед показом заказчику:

```bash
cd /root/Webmarket
docker compose -f docker-compose.prod.yml ps
curl -sf http://localhost/api/health/
grep -E '^DJANGO_DEBUG=' .env
grep -E '^DJANGO_SETTINGS_MODULE=' .env
```

Ожидаемые значения:

- `DJANGO_DEBUG=False`
- `DJANGO_SETTINGS_MODULE=config.settings.production`

## Диагностика (production checklist)

### Статус контейнеров

```bash
docker compose -f docker-compose.prod.yml ps
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
```

Все сервисы (`db`, `web`, `nginx`, `telegram_bot`) должны быть `Up` (желательно `healthy` для `db`, `web`, `nginx`). `vk_bot` — только если включён profile `vk`.

### Логи

```bash
docker compose -f docker-compose.prod.yml logs --tail=200 web
docker compose -f docker-compose.prod.yml logs --tail=200 nginx
docker compose -f docker-compose.prod.yml logs --tail=200 telegram_bot
docker compose -f docker-compose.prod.yml logs --tail=200 vk_bot
docker compose -f docker-compose.prod.yml logs --tail=200 db
```

### Health через nginx

```bash
curl -i http://localhost/api/health/
# Ожидается: HTTP/1.1 200 и {"status":"успешно"}
```

### Порты (снаружи только 80)

```bash
ss -tlnp | grep -E ':80|:8000|:5432'
```

Ожидание: `:80` слушает nginx; `:8000` и `:5432` **не** на внешнем интерфейсе.

### Git на VPS

```bash
git -C /root/Webmarket status --short --branch
```

Ожидание: чистое дерево относительно `main` (кроме игнорируемых файлов вроде `.env`).

### Render compose (без секретов)

```bash
docker compose -f docker-compose.prod.yml config
```

Проверьте, что `DJANGO_DEBUG` не попадает в environment как `True`.

## Backup и restore (PostgreSQL)

### Подготовка каталога бэкапов (один раз)

```bash
mkdir -p /root/backups
chmod 700 /root/backups
```

### Создать бэкап

```bash
cd /root/Webmarket
source .env
BACKUP="/root/backups/webmarket_$(date +%F_%H-%M-%S).sql"
docker compose -f docker-compose.prod.yml exec -T db \
  pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" > "$BACKUP"
ls -lh "$BACKUP"
```

Рекомендация: cron ежедневно + хранение 7–14 копий (настройка cron — на усмотрение администратора VPS).

### Восстановить из бэкапа

**Внимание:** перезапишет текущие данные БД.

```bash
cd /root/Webmarket
source .env
BACKUP="/root/backups/<имя-файла>.sql"
docker compose -f docker-compose.prod.yml exec -T db \
  psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" < "$BACKUP"
```

### Проверка восстановления (рекомендуется раз в квартал)

1. Создать бэкап.
2. На тестовой БД или после `pg_dump`/`psql` в отдельную схему — убедиться, что таблицы и записи на месте.
3. Зафиксировать дату проверки в журнале администрирования.

### Media-файлы

Том `webmarket_media_data` не входит в SQL-бэкап. Для полного DR:

```bash
docker run --rm -v webmarket_media_data:/data -v /root/backups:/backup alpine \
  tar czf /backup/webmarket_media_$(date +%F).tar.gz -C /data .
```

## Rollback (откат на предыдущий коммит)

Образы в GHCR тегируются как `latest` и `<git-sha>`. Для отката используйте SHA из GitHub (коммит, на который нужно вернуться).

### 1) Найти SHA

В GitHub: **Actions** → успешный workflow нужного коммита → в логе build шагов виден `${{ github.sha }}`, или:

```bash
git log --oneline -5
```

### 2) Откатить образы на VPS

```bash
cd /root/Webmarket
export WEBMARKET_IMAGE_TAG=<sha-коммита>
export WEBMARKET_TELEGRAM_BOT_IMAGE_TAG=<sha-коммита>
export WEBMARKET_VK_BOT_IMAGE_TAG=<sha-коммита>

echo "$VPS_GHCR_TOKEN" | docker login ghcr.io -u <github-user> --password-stdin

docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d --no-build
docker compose -f docker-compose.prod.yml exec -T web python manage.py migrate
curl -sf http://localhost/api/health/
```

### 3) (Опционально) Откатить файлы репозитория на VPS

```bash
git fetch origin main
git reset --hard <sha-коммита>
```

**Важно:** `.env` на VPS при этом не меняется. Миграции БД при откате кода могут требовать ручного решения — при сомнении восстановите БД из бэкапа, сделанного до проблемного деплоя.

### Закрепить откат

Чтобы следующий автодеплой не перезаписал SHA, либо исправьте `main` в GitHub и задеплойте заново, либо временно отключите workflow / не пушьте в `main`, пока не убедитесь в стабильности.

## Pre-demo checklist (показ заказчику)

Выполнить **за 30–60 минут до демо**. Оговорка для заказчика: стенд на HTTP (без HTTPS) — осознанно, TLS на финальном этапе.

### Быстрая проверка на VPS (5 мин)

```bash
cd /root/Webmarket   # или ваш VPS_APP_DIR
docker compose -f docker-compose.prod.yml ps
curl -sf http://localhost/api/health/
grep -E '^DJANGO_DEBUG=|^DJANGO_SETTINGS_MODULE=' .env
```

Ожидание: 4 контейнера `Up`/`healthy`, health → `200`, `DJANGO_DEBUG=False`.

### Сценарий в браузере (admin)

- [ ] `http://<VPS_IP>/admin/` — форма логина открывается (при необходимости — инкогнито)
- [ ] Вход под заранее созданным superuser
- [ ] Есть товары с картинками (если пусто: `load_demo_data`)
- [ ] Вкладка заказов открывается

### Сценарий в Telegram (основной demo flow)

- [ ] `/start` — приветствие без ошибки
- [ ] Регистрация по номеру (кнопка `request_contact`)
- [ ] **Каталог** — список и карточка с фото
- [ ] Добавить в корзину → **Корзина** — сумма обновилась
- [ ] **Оформить заказ** → подтверждение с номером заказа
- [ ] **Мои заказы** — заказ в списке
- [ ] Тот же заказ виден в Django Admin

### Сценарий в VK (если включён vk_bot)

- [ ] Написать в сообщество: **Начать** или `/start`
- [ ] Регистрация — ввод телефона `79991234567`
- [ ] **Каталог** — карточки с фото, `+`/`−` без пропадания фото
- [ ] **Корзина** — количество меняется на месте, «Удалить» убирает позицию
- [ ] **Оформить заказ** → подтверждение с номером
- [ ] Заказ виден в Django Admin с `channel=vk`

Подробнее: [docs/VK_BOT.md](./VK_BOT.md).

### Запасной план на демо

| Если сломалось | Что сказать заказчику | Действие |
|----------------|----------------------|----------|
| Бот не отвечает (Telegram) | «Канал Telegram временно недоступен, ядро магазина работает» | Показать admin + API health; `docker compose restart telegram_bot` |
| VK-бот не отвечает | «Канал VK временно недоступен» | Проверить Long Poll и `VK_BOT_TOKEN`; `docker compose --profile vk restart vk_bot` |
| `TelegramNetworkError: timeout` в логах | Не озвучивать, если бот отвечает | Бот восстанавливается сам; при повторении — restart контейнера |
| Admin 400 | — | Проверить `DJANGO_ALLOWED_HOSTS` (IP + `nginx`), `up -d` |
| Нет товаров | «Загрузим демо-каталог» | `load_demo_data` |

### Что **не** обещать на этом этапе

- HTTPS и «банковский» уровень безопасности — **следующий этап**
- Оплата, сайт, MAX — **в roadmap** (Telegram и VK уже реализованы)
- SLA / мониторинг 24/7 — **после hardening**

## Demo checklist (полный, для внутренней приёмки)

Выполните перед демонстрацией. Отметьте каждый пункт.

- [ ] `GET /api/health/` через nginx → **200**
- [ ] Вход в Django Admin (`/admin/`)
- [ ] В admin есть товары / клиенты / заказы (или загружены demo-данные)
- [ ] Telegram: `/start` отрабатывает
- [ ] Регистрация по номеру телефона проходит
- [ ] Каталог, карточки, изображения `/media/` в боте открываются
- [ ] Корзина обновляется
- [ ] Checkout создаёт заказ
- [ ] Заказ виден в admin
- [ ] При остановке `telegram_bot` backend и admin продолжают работать

Команда для проверки изоляции бота:

```bash
docker compose -f docker-compose.prod.yml stop telegram_bot
curl -sf http://localhost/api/health/
# admin в браузере — должен открываться
docker compose -f docker-compose.prod.yml start telegram_bot
```

## Демо-данные на проде

Только для демо-стенда, не для реального production с живыми клиентами:

```bash
docker compose -f docker-compose.prod.yml exec web python manage.py load_demo_data
```

## Типовые проблемы

| Симптом | Причина | Действие |
|---------|---------|----------|
| Admin 400 Bad Request | Нет хоста в `DJANGO_ALLOWED_HOSTS` | Добавить IP/домен и `nginx` в `.env`, `docker compose up -d` |
| Admin без CSS | Статика / кэш | Ctrl+F5; проверить WhiteNoise и `collectstatic` в образе |
| Бот 400 при старте | Backend ещё не готов | Дождаться `healthy` у `web`; перезапустить бота |
| CSRF 403 в admin | HTTP + secure cookies | Ожидаемо до HTTPS; после TLS включить secure cookies |
| Deploy failed: DJANGO_DEBUG | Debug включён в `.env` | `DJANGO_DEBUG=False`, перезапуск не нужен до следующего deploy |
| VK кнопки крутятся | Long Poll: нет «Действие с сообщением» | [docs/VK_BOT.md](./VK_BOT.md) §2.1, пересобрать `vk_bot` |

## Следующие шаги (не блокируют feature demo)

См. [SECURITY_HARDENING_PLAN.md](../SECURITY_HARDENING_PLAN.md): HTTPS, ufw, ротация секретов, деплой не от root, branch protection.
