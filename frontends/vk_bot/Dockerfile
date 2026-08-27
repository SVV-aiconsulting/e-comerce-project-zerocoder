FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY vk_bot ./vk_bot

RUN pip install --no-cache-dir -e .

HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=40s \
  CMD python -c "import os, urllib.request; urllib.request.urlopen(os.environ.get('BACKEND_API_BASE_URL','http://web:8000') + '/api/health/')"

CMD ["python", "-m", "vk_bot"]
