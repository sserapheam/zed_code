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
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Копируем скрипт выполнения
COPY runner.py /app/runner.py
RUN chmod +x /app/runner.py && \
    chown runner:runner /app/runner.py

# Переключаемся на непривилегированного пользователя
USER runner

# Запускаем скрипт
CMD ["python", "/app/runner.py"]

