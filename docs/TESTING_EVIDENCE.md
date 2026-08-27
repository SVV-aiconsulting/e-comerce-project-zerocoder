# Тестирование и доказательства готовности MVP

Дата актуализации: 26.08.2026.

## Автоматические проверки

В локальном окружении PostgreSQL выполнены следующие проверки после добавления
дашборда менеджера:

| Набор | Команда | Результат |
|---|---|---:|
| Django проверка конфигурации | `py -3.14 manage.py check` | успешно |
| Контроль миграций | `py -3.14 manage.py makemigrations --check --dry-run` | изменений нет |
| Django-приложения | `py -3.14 -m pytest apps -q` | 182 passed |
| Telegram-адаптер | `py -3.14 -m pytest frontends/telegram_bot/tests -q` | 31 passed |
| VK-адаптер | `py -3.14 -m pytest frontends/vk_bot/tests -q` | 30 passed |
| Всего | три набора выше | **243 passed** |

## Проверка CI/CD

27.08.2026 GitHub Actions успешно выполнил workflow для коммита `7c9ceee`:

| Job | Результат |
|---|---|
| Backend tests | success |
| Telegram adapter tests | success |
| VK adapter tests | success |
| Build and publish container images | success |
| Deploy immutable release to VPS | skipped по защите `DEPLOY_ENABLED` |

Таким образом, GitHub самостоятельно подтвердил тесты и собрал/published три
контейнерных образа. VPS не использовался для сборки и ещё не получил deployment:
для этого владелец сначала добавляет Secrets и включает переменную
`DEPLOY_ENABLED=true` по инструкции `docs/GITHUB_ACTIONS_DEPLOYMENT.md`.

Предупреждения тестовой среды не относятся к логике MVP: отсутствующий пока каталог
`staticfiles`, предстоящее изменение значения по умолчанию Django `URLField` и
невозможность создать `.pytest_cache` на пути Windows с кириллицей. Ошибок и
неприменённых миграций нет.

## Сценарии демонстрации для защиты

После развёртывания на VPS нужно записать единый скринкаст в этой последовательности:

1. Отправить свободный текстовый заказ в Telegram или через веб-форму.
2. Показать уточняющий вопрос при отсутствии адреса/контакта или при неоднозначном
   товаре.
3. Подтвердить server-side preview и открыть созданный заказ в Django Admin.
4. Показать расчёт и создание тестовой доставки Яндекс Доставки.
5. Открыть тестовую ссылку ЮKassa и завершить оплату тестовой картой.
6. Показать webhook-событие, статус оплаты в заказе и платёжный аудит.
7. Открыть `/manager/dashboard/`, отфильтровать период и показать очередь исключений.

Для каждого пункта сохранить один скриншот в отдельную папку `docs/screenshots/`
только после появления реальных данных. В снимки нельзя включать `.env`, API-ключи,
номера карт, полные адреса и персональные контакты клиентов.

## Внешние проверки, ожидающие домен

- DNS для `webmarket.apernova.ru` должен указывать на VPS проекта.
- На VPS должны быть настроены HTTPS, redirect HTTP → HTTPS, `ALLOWED_HOSTS` и
  `CSRF_TRUSTED_ORIGINS`.
- Затем в test shop ЮKassa указывается реальный URL
  `https://webmarket.apernova.ru/api/webhooks/payments/yookassa/` и выполняется
  тестовый платёж. До этого указывать фиктивный URL нельзя.
- Выполняется реальный sandbox-flow Яндекс Доставки без переключения в production.

Подробные шаги оплаты находятся в `docs/YOOKASSA.md`, а эксплуатационные шаги VPS —
в `docs/PRODUCTION_OPERATIONS.md`.
