FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY . .

RUN pip install --no-cache-dir -e .

# Статика admin/CSS собирается в образ на этапе build (в проде DEBUG=False).
ENV DJANGO_SETTINGS_MODULE=config.settings.production \
    DJANGO_SECRET_KEY=collectstatic-build-only \
    DJANGO_DEBUG=False \
    DJANGO_ALLOWED_HOSTS=localhost \
    POSTGRES_DB=build \
    POSTGRES_USER=build \
    POSTGRES_PASSWORD=build \
    POSTGRES_HOST=localhost \
    POSTGRES_PORT=5432

RUN python manage.py collectstatic --noinput

EXPOSE 8000

# Локальная разработка: python manage.py runserver 0.0.0.0:8000
# Продакшн: gunicorn config.wsgi:application --bind 0.0.0.0:8000
