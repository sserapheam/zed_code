# Быстрый старт zedcode

## 🚀 Запуск приложения

### 1. Установка зависимостей

Убедитесь, что у вас установлен Python 3.8+ и активировано виртуальное окружение:

```bash
# Windows
venv\Scripts\activate

# Linux/Mac
source venv/bin/activate
```

Установите зависимости:

```bash
pip install -r requirements_postgres.txt
```

### 2. Настройка базы данных

Убедитесь, что PostgreSQL запущен и настроен. Создайте файл `.env` на основе `.env_template`:

```bash
# Windows
copy .env_template .env

# Linux/Mac
cp .env_template .env
```

Отредактируйте `.env` и укажите правильные данные для подключения к базе данных.

### 3. Запуск приложения

#### Режим 1: Обычный режим (без Docker)

Приложение будет использовать обычный `subprocess` для выполнения кода:

```bash
python main_postgres.py
```

Приложение будет доступно по адресу: http://localhost:8080

#### Режим 2: Docker режим (рекомендуется для production)

**Шаг 1:** Соберите Docker образ для выполнения кода:

```bash
# Windows (PowerShell)
cd docker\executor
docker build -t zedcode-python:latest .

# Linux/Mac
cd docker/executor
docker build -t zedcode-python:latest .
```

**Шаг 2:** Включите Docker режим в `.env`:

```env
USE_DOCKER=true
```

**Шаг 3:** Запустите приложение:

```bash
python main_postgres.py
```

### 4. Проверка работы

1. Откройте браузер и перейдите на http://localhost:8080
2. Зарегистрируйтесь или войдите в систему
3. Попробуйте решить задачу - код должен выполняться

## 🔍 Проверка Docker режима

Чтобы проверить, что Docker режим работает:

1. Убедитесь, что Docker Desktop запущен (на Windows)
2. Проверьте, что образ собран:
   ```bash
   docker images | grep zedcode-python
   ```
3. Включите `USE_DOCKER=true` в `.env`
4. Попробуйте отправить решение задачи
5. Если код выполняется в Docker, в логах не будет ошибок, и код будет изолирован

## ⚠️ Важные замечания

- **Безопасность:** Docker режим обеспечивает изоляцию выполнения кода. Без Docker код выполняется на вашем сервере без ограничений.
- **Производительность:** Docker режим немного медленнее из-за создания контейнеров, но намного безопаснее.
- **Требования:** Для Docker режима нужен установленный Docker Desktop (Windows) или Docker Engine (Linux).

## 🐛 Решение проблем

### Ошибка "Docker образ не найден"

Убедитесь, что образ собран:
```bash
docker build -t zedcode-python:latest docker/executor/
```

### Ошибка "Docker недоступен"

1. Проверьте, что Docker Desktop запущен
2. Проверьте доступность Docker:
   ```bash
   docker ps
   ```
3. Если Docker недоступен, приложение автоматически переключится на обычный режим

### Ошибка подключения к базе данных

Проверьте настройки в `.env` файле и убедитесь, что PostgreSQL запущен.

## 📚 Дополнительная информация

Для масштабирования приложения на большое количество пользователей смотрите `SCALING_GUIDE.md`.

