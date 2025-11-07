# Dockerfile для безопасного выполнения Python кода
FROM python:3.11-slim

# Создаем непривилегированного пользователя
RUN useradd -m -u 1000 runner && \
    mkdir -p /app && \
    chown runner:runner /app

# Устанавливаем только необходимые зависимости
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    gcc \
    coreutils \
    && rm -rf /var/lib/apt/lists/*

# Устанавливаем Python зависимости для worker
COPY requirements_postgres.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
RUN pip install --no-cache-dir celery

WORKDIR /app

# Копируем скрипт выполнения
COPY docker/executor/runner.py /app/runner.py
RUN chmod +x /app/runner.py && \
    chown runner:runner /app/runner.py

# Переключаемся на непривилегированного пользователя
USER runner

# Запускаем скрипт
CMD ["python", "/app/runner.py"]

