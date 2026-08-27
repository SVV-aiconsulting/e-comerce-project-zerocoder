# GitHub Actions → GHCR → VPS

Дата: 27.08.2026. Репозиторий проекта:
`SVV-aiconsulting/e-comerce-project-zerocoder`.

## Что делает pipeline

При каждом push в `main` workflow `.github/workflows/deploy.yml` последовательно:

1. поднимает временные PostgreSQL и Redis в GitHub Actions;
2. выполняет проверку Django, контроль миграций и backend-тесты;
3. отдельно запускает тесты Telegram- и VK-адаптеров;
4. только если все тесты зелёные, собирает три Docker-образа в GitHub;
5. публикует образы в GHCR с тегами `latest` и точным SHA коммита;
6. по SSH обновляет код на VPS, скачивает только образы SHA, применяет миграции и
   проверяет health endpoint по HTTPS.

Пока вы не включите переменную репозитория `DEPLOY_ENABLED=true`, deploy-job
намеренно помечается `skipped`: тесты и публикация образов остаются зелёными, а
попытки подключиться к VPS не выполняются.

VPS не собирает Python- или Docker-образы. Его роль — хранить `.env`, volumes базы
и media, а также запускать уже проверенный образ.

## Одноразовая подготовка VPS

Пример использует путь `/opt/webmarket`; это значение затем указывается в
`VPS_APP_DIR`.

```bash
sudo mkdir -p /opt/webmarket
sudo chown "$USER":"$USER" /opt/webmarket
cd /opt/webmarket
touch .env
chmod 600 .env
```

Первый deployment сам инициализирует Git-репозиторий в этом каталоге совместимой
командой `git init` и получает актуальный `main`; вручную выполнять `git clone` не
требуется. Существующий `.env` остаётся нетронутым. После заполнения в `.env` VPS
обязательно должны быть как минимум:

```dotenv
DJANGO_DEBUG=False
DJANGO_SETTINGS_MODULE=config.settings.production
DJANGO_ALLOWED_HOSTS=webmarket.apernova.ru,localhost,127.0.0.1,web,nginx
DJANGO_CSRF_TRUSTED_ORIGINS=https://webmarket.apernova.ru
YOOKASSA_RETURN_URL=https://webmarket.apernova.ru/payment/return/
```

Остальные значения — реальные секреты и реквизиты вашего проекта из локального
`.env`; их копируют на VPS вручную, не в GitHub Actions и не в Git. Перед запуском
также откройте firewall для TCP-портов 80 и 443. Порт PostgreSQL наружу не открывают.

## GitHub Secrets

В репозитории откройте **Settings → Secrets and variables → Actions** и создайте:

| Secret | Значение |
|---|---|
| `VPS_HOST` | публичный IP-адрес VPS (хранится только в Secret) |
| `VPS_USER` | пользователь SSH на VPS |
| `VPS_SSH_KEY` | приватный SSH-ключ этого пользователя (весь блок PEM/OpenSSH) |
| `VPS_PORT` | обычно `22` |
| `VPS_APP_DIR` | `/opt/webmarket` или выбранный путь |
| `LETSENCRYPT_EMAIL` | рабочий email для уведомлений Let's Encrypt |

Репозиторий и три GHCR-образа для этого MVP публичны. Поэтому VPS клонирует код и
загружает образы анонимно — отдельный GitHub PAT не нужен. Если в будущем репозиторий
или package visibility станет private, потребуется вернуть отдельный read-only токен
с `read:packages` и доступом к репозиторию.

После создания и проверки всех Secrets откройте вкладку **Variables** того же
раздела, создайте переменную `DEPLOY_ENABLED` со значением `true`. Это единственный
включатель автоматического deployment и renewal TLS. При любом подозрении на
проблему поменяйте значение на `false` — тесты и сборка образов продолжатся, но VPS
не будет затронут.

Для ключа SSH на VPS публичная часть должна быть в `~/.ssh/authorized_keys` того же
пользователя. Рекомендуется отдельный ключ только для deployment.

## Первый запуск

1. Убедитесь, что DNS-запись `webmarket.apernova.ru` указывает на ваш VPS.
2. Выполните подготовку VPS и заполните `.env`.
3. Создайте Secrets.
4. Запушьте код в `main` или запустите workflow вручную: **Actions → Test, build and
   deploy WebMarket → Run workflow**.

Во время первого успешного deployment workflow получает сертификат Let's Encrypt
через временный standalone Certbot на 80-м порту, затем запускает nginx на 80/443.
Сертификат сохраняется на VPS в игнорируемом Git каталоге `deploy/certbot/conf/`.
Отдельный workflow `renew-certificate.yml` запускается раз в месяц и безопасно
перечитывает сертификат nginx.

## Контроль после deployment

```bash
curl -I https://webmarket.apernova.ru/api/health/
curl -I http://webmarket.apernova.ru/api/health/
# Второй запрос должен вернуть redirect на HTTPS.

cd /opt/webmarket
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml logs --tail=100 web nginx
```

После зелёного health-check в личном кабинете тестового магазина ЮKassa укажите
webhook URL:

```text
https://webmarket.apernova.ru/api/webhooks/payments/yookassa/
```

Затем выполните тестовую оплату: только она проверяет полный путь оплаты и webhook.

## Откат

GitHub Container Registry сохраняет образ каждого коммита по SHA. Для отката на VPS
выберите SHA последнего рабочего workflow и выполните команды из
`docs/PRODUCTION_OPERATIONS.md`; данные PostgreSQL и `.env` при этом не меняются.
