# Dockerfile для Flask приложения
FROM python:3.11-slim

WORKDIR /app

# Устанавливаем зависимости системы
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    gcc \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Копируем requirements
COPY requirements_postgres.txt .
RUN pip install --no-cache-dir -r requirements_postgres.txt

# Устанавливаем gunicorn
RUN pip install gunicorn

# Копируем код приложения
COPY . .

# Создаем непривилегированного пользователя
RUN useradd -m -u 1000 appuser && \
    chown -R appuser:appuser /app

USER appuser

EXPOSE 5000

CMD ["gunicorn", "-w", "4", "-b", "0.0.0.0:5000", "--timeout", "120", "main_postgres:app"]

