FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY bot ./bot

RUN pip install --no-cache-dir -e .

CMD ["python", "-m", "bot"]
