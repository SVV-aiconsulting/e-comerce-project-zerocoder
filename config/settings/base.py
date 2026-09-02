"""Базовые настройки Django, общие для всех окружений."""
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env(
    DJANGO_DEBUG=(bool, False),
    ADAPTER_API_PUBLIC_CATALOG=(bool, True),
    CELERY_TASK_ALWAYS_EAGER=(bool, False),
    INTAKE_EVENT_LEASE_SECONDS=(int, 300),
    INTAKE_DISPATCH_BATCH_SIZE=(int, 100),
    INTAKE_MAX_PROCESSING_ATTEMPTS=(int, 5),
    INTAKE_RETRY_BASE_SECONDS=(int, 10),
    INTAKE_RETRY_MAX_SECONDS=(int, 300),
    AI_ORDER_PROCESSING_ENABLED=(bool, False),
    AI_ASSISTANT_ENABLED=(bool, False),
    GIGACHAT_VERIFY_SSL=(bool, True),
    GIGACHAT_TIMEOUT_SECONDS=(float, 30.0),
    GIGACHAT_MAX_TOKENS=(int, 1600),
    GIGACHAT_TEMPERATURE=(float, 0.1),
    CATALOG_MATCH_AUTO_THRESHOLD=(float, 0.75),
    CATALOG_MATCH_MIN_MARGIN=(float, 0.15),
    CATALOG_MATCH_CANDIDATE_THRESHOLD=(float, 0.25),
    INTAKE_MAX_CLARIFICATION_ATTEMPTS=(int, 3),
    EMAIL_CHANNEL_ENABLED=(bool, False),
    EMAIL_POLL_INTERVAL_SECONDS=(int, 60),
    EMAIL_RESPONSE_DISPATCH_INTERVAL_SECONDS=(int, 15),
    EMAIL_POLL_BATCH_SIZE=(int, 50),
    EMAIL_MAX_MESSAGE_BYTES=(int, 5 * 1024 * 1024),
    EMAIL_OUTBOUND_LEASE_SECONDS=(int, 300),
    EMAIL_OUTBOUND_MAX_ATTEMPTS=(int, 5),
    EMAIL_NETWORK_TIMEOUT_SECONDS=(float, 30.0),
    YANDEX_DELIVERY_ENABLED=(bool, False),
    YANDEX_DELIVERY_PRODUCTION_ENABLED=(bool, False),
    YANDEX_DELIVERY_TIMEOUT_SECONDS=(float, 20.0),
    YANDEX_DELIVERY_VAT_CODE=(int, -1),
    YOOKASSA_ENABLED=(bool, False),
    YOOKASSA_PRODUCTION_ENABLED=(bool, False),
    YOOKASSA_TIMEOUT_SECONDS=(float, 20.0),
    YOOKASSA_VERIFY_WEBHOOK_IP=(bool, True),
    YOOKASSA_DEFAULT_VAT_CODE=(int, 1),
)

environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = env("DJANGO_DEBUG")
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost"])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
    "rest_framework",
    "apps.common.apps.CommonConfig",
    "apps.catalog.apps.CatalogConfig",
    "apps.customers.apps.CustomersConfig",
    "apps.carts.apps.CartsConfig",
    "apps.delivery.apps.DeliveryConfig",
    "apps.payments.apps.PaymentsConfig",
    "apps.discounts.apps.DiscountsConfig",
    "apps.orders.apps.OrdersConfig",
    "apps.intake.apps.IntakeConfig",
    "apps.dashboard.apps.DashboardConfig",
    "apps.api.apps.ApiConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("POSTGRES_DB"),
        "USER": env("POSTGRES_USER"),
        "PASSWORD": env("POSTGRES_PASSWORD"),
        "HOST": env("POSTGRES_HOST"),
        "PORT": env("POSTGRES_PORT"),
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "ru-ru"
TIME_ZONE = "Europe/Moscow"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "frontends" / "website" / "static"]

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
    "EXCEPTION_HANDLER": "apps.api.exceptions.shop_exception_handler",
}

ADAPTER_API_TOKENS = env.list("ADAPTER_API_TOKENS", default=[])
ADAPTER_API_PUBLIC_CATALOG = env("ADAPTER_API_PUBLIC_CATALOG")

# Яндекс Почта: IMAP/SMTP email-адаптер. Пароль приложения хранится только в env.
EMAIL_CHANNEL_ENABLED = env("EMAIL_CHANNEL_ENABLED")
YANDEX_EMAIL_ADDRESS = env("YANDEX_EMAIL_ADDRESS", default="")
YANDEX_EMAIL_APP_PASSWORD = env("YANDEX_EMAIL_APP_PASSWORD", default="")
YANDEX_IMAP_HOST = env("YANDEX_IMAP_HOST", default="imap.yandex.ru")
YANDEX_IMAP_PORT = env.int("YANDEX_IMAP_PORT", default=993)
YANDEX_IMAP_FOLDER = env("YANDEX_IMAP_FOLDER", default="INBOX")
YANDEX_SMTP_HOST = env("YANDEX_SMTP_HOST", default="smtp.yandex.ru")
YANDEX_SMTP_PORT = env.int("YANDEX_SMTP_PORT", default=465)
EMAIL_POLL_INTERVAL_SECONDS = env("EMAIL_POLL_INTERVAL_SECONDS")
EMAIL_RESPONSE_DISPATCH_INTERVAL_SECONDS = env(
    "EMAIL_RESPONSE_DISPATCH_INTERVAL_SECONDS"
)
EMAIL_POLL_BATCH_SIZE = env("EMAIL_POLL_BATCH_SIZE")
EMAIL_MAX_MESSAGE_BYTES = env("EMAIL_MAX_MESSAGE_BYTES")
EMAIL_OUTBOUND_LEASE_SECONDS = env("EMAIL_OUTBOUND_LEASE_SECONDS")
EMAIL_OUTBOUND_MAX_ATTEMPTS = env("EMAIL_OUTBOUND_MAX_ATTEMPTS")
EMAIL_NETWORK_TIMEOUT_SECONDS = env("EMAIL_NETWORK_TIMEOUT_SECONDS")

# Яндекс Доставка по России. Test и production используют разные переменные,
# а хост выбирается кодом, чтобы токен одного контура не ушёл в другой.
YANDEX_DELIVERY_ENABLED = env("YANDEX_DELIVERY_ENABLED")
YANDEX_DELIVERY_ENVIRONMENT = env("YANDEX_DELIVERY_ENVIRONMENT", default="test")
YANDEX_DELIVERY_TEST_TOKEN = env("YANDEX_DELIVERY_TEST_TOKEN", default="")
YANDEX_DELIVERY_TEST_STATION_ID = env(
    "YANDEX_DELIVERY_TEST_STATION_ID",
    default="fbed3aa1-2cc6-4370-ab4d-59c5cc9bb924",
)
YANDEX_DELIVERY_PRODUCTION_ENABLED = env("YANDEX_DELIVERY_PRODUCTION_ENABLED")
YANDEX_DELIVERY_PRODUCTION_TOKEN = env(
    "YANDEX_DELIVERY_PRODUCTION_TOKEN",
    default="",
)
YANDEX_DELIVERY_PRODUCTION_STATION_ID = env(
    "YANDEX_DELIVERY_PRODUCTION_STATION_ID",
    default="",
)
YANDEX_DELIVERY_TIMEOUT_SECONDS = env("YANDEX_DELIVERY_TIMEOUT_SECONDS")
YANDEX_DELIVERY_QUOTE_TTL_SECONDS = env.int(
    "YANDEX_DELIVERY_QUOTE_TTL_SECONDS",
    default=900,
)
YANDEX_DELIVERY_MERCHANT_INN = env("YANDEX_DELIVERY_MERCHANT_INN", default="")
YANDEX_DELIVERY_VAT_CODE = env("YANDEX_DELIVERY_VAT_CODE")

# ЮKassa: test и production используют разные реквизиты. Коммерческий контур
# требует отдельного предохранителя, чтобы тестовый ключ не был случайно заменён.
YOOKASSA_ENABLED = env("YOOKASSA_ENABLED")
YOOKASSA_ENVIRONMENT = env("YOOKASSA_ENVIRONMENT", default="test")
YOOKASSA_TEST_SHOP_ID = env("YOOKASSA_TEST_SHOP_ID", default="")
YOOKASSA_TEST_SECRET_KEY = env("YOOKASSA_TEST_SECRET_KEY", default="")
YOOKASSA_PRODUCTION_ENABLED = env("YOOKASSA_PRODUCTION_ENABLED")
YOOKASSA_PRODUCTION_SHOP_ID = env("YOOKASSA_PRODUCTION_SHOP_ID", default="")
YOOKASSA_PRODUCTION_SECRET_KEY = env(
    "YOOKASSA_PRODUCTION_SECRET_KEY",
    default="",
)
YOOKASSA_RETURN_URL = env(
    "YOOKASSA_RETURN_URL",
    default="http://localhost:8000/payment/return/",
)
YOOKASSA_TIMEOUT_SECONDS = env("YOOKASSA_TIMEOUT_SECONDS")
YOOKASSA_VERIFY_WEBHOOK_IP = env("YOOKASSA_VERIFY_WEBHOOK_IP")
YOOKASSA_DEFAULT_VAT_CODE = env("YOOKASSA_DEFAULT_VAT_CODE")
PAYMENT_SYNC_INTERVAL_SECONDS = env.int("PAYMENT_SYNC_INTERVAL_SECONDS", default=60)
PAYMENT_SYNC_BATCH_SIZE = env.int("PAYMENT_SYNC_BATCH_SIZE", default=50)
TELEGRAM_BOT_TOKEN = env("TELEGRAM_BOT_TOKEN", default="")
TELEGRAM_NOTIFICATION_TIMEOUT_SECONDS = env.float(
    "TELEGRAM_NOTIFICATION_TIMEOUT_SECONDS",
    default=10.0,
)

# Celery/Redis: единая очередь для событий Telegram, VK, email и web.
CELERY_BROKER_URL = env("CELERY_BROKER_URL", default="redis://127.0.0.1:6379/0")
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE
CELERY_ENABLE_UTC = True
CELERY_TASK_IGNORE_RESULT = True
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_TASK_ALWAYS_EAGER = env("CELERY_TASK_ALWAYS_EAGER")
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_TASK_ROUTES = {
    "intake.*": {"queue": "intake"},
    "payments.*": {"queue": "intake"},
}
CELERY_BEAT_SCHEDULE = {
    "dispatch-pending-intake-events": {
        "task": "intake.dispatch_pending_events",
        "schedule": 30.0,
    },
    "poll-yandex-email": {
        "task": "intake.poll_yandex_mail",
        "schedule": float(EMAIL_POLL_INTERVAL_SECONDS),
    },
    "dispatch-email-responses": {
        "task": "intake.dispatch_email_responses",
        "schedule": float(EMAIL_RESPONSE_DISPATCH_INTERVAL_SECONDS),
    },
    "sync-pending-yookassa-payments": {
        "task": "payments.sync_pending",
        "schedule": float(PAYMENT_SYNC_INTERVAL_SECONDS),
    },
    "dispatch-paid-payment-notifications": {
        "task": "payments.dispatch_paid_notifications",
        "schedule": 60.0,
    },
}

INTAKE_EVENT_LEASE_SECONDS = env("INTAKE_EVENT_LEASE_SECONDS")
INTAKE_DISPATCH_BATCH_SIZE = env("INTAKE_DISPATCH_BATCH_SIZE")
INTAKE_MAX_PROCESSING_ATTEMPTS = env("INTAKE_MAX_PROCESSING_ATTEMPTS")
INTAKE_RETRY_BASE_SECONDS = env("INTAKE_RETRY_BASE_SECONDS")
INTAKE_RETRY_MAX_SECONDS = env("INTAKE_RETRY_MAX_SECONDS")

# LLM используется только для NLU. Он не получает доступ к ORM/SQL и не
# выполняет цены, оплату, доставку или создание финального заказа.
# AI_ORDER_PROCESSING_ENABLED сохранён как совместимое имя старой конфигурации.
_LEGACY_AI_ENABLED = env("AI_ORDER_PROCESSING_ENABLED")
AI_ASSISTANT_ENABLED = env("AI_ASSISTANT_ENABLED", default=_LEGACY_AI_ENABLED)
AI_ORDER_PROCESSING_ENABLED = AI_ASSISTANT_ENABLED
AI_ASSISTANT_PROVIDER = env("AI_ASSISTANT_PROVIDER", default="gigachat").strip().lower()
AI_ASSISTANT_PROMPT_PROFILE = env(
    "AI_ASSISTANT_PROMPT_PROFILE",
    default="ecommerce_sales_v1",
).strip()
_GIGACHAT_CREDENTIALS = env("GIGACHAT_CREDENTIALS", default="").strip()
GIGACHAT_CREDENTIALS = _GIGACHAT_CREDENTIALS or env(
    "GIGACHAT_TOKEN",
    default="",
).strip()
GIGACHAT_SCOPE = env("GIGACHAT_SCOPE", default="GIGACHAT_API_PERS")
GIGACHAT_MODEL = env("GIGACHAT_MODEL", default="GigaChat-2")
GIGACHAT_BASE_URL = env("GIGACHAT_BASE_URL", default="https://api.giga.chat/v1")
GIGACHAT_AUTH_URL = env(
    "GIGACHAT_AUTH_URL",
    default="https://ngw.devices.sberbank.ru:9443/api/v2/oauth",
)
_PACKAGED_GIGACHAT_CA_BUNDLE = (
    BASE_DIR / "deploy" / "certs" / "russian_trusted_root_ca_pem.crt"
)
_LOCAL_GIGACHAT_CA_BUNDLE = (
    BASE_DIR / ".local" / "certs" / "russian_trusted_root_ca_pem.crt"
)
_DEFAULT_GIGACHAT_CA_BUNDLE = next(
    (
        str(path)
        for path in (_PACKAGED_GIGACHAT_CA_BUNDLE, _LOCAL_GIGACHAT_CA_BUNDLE)
        if path.is_file()
    ),
    "",
)
GIGACHAT_CA_BUNDLE = (
    env("GIGACHAT_CA_BUNDLE", default="").strip()
    or _DEFAULT_GIGACHAT_CA_BUNDLE
)
GIGACHAT_VERIFY_SSL = env("GIGACHAT_VERIFY_SSL")
GIGACHAT_TIMEOUT_SECONDS = env("GIGACHAT_TIMEOUT_SECONDS")
GIGACHAT_MAX_TOKENS = env("GIGACHAT_MAX_TOKENS")
GIGACHAT_TEMPERATURE = env("GIGACHAT_TEMPERATURE")
CATALOG_MATCH_AUTO_THRESHOLD = env("CATALOG_MATCH_AUTO_THRESHOLD")
CATALOG_MATCH_MIN_MARGIN = env("CATALOG_MATCH_MIN_MARGIN")
CATALOG_MATCH_CANDIDATE_THRESHOLD = env("CATALOG_MATCH_CANDIDATE_THRESHOLD")
INTAKE_MAX_CLARIFICATION_ATTEMPTS = env("INTAKE_MAX_CLARIFICATION_ATTEMPTS")
