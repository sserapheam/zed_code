# Установка и настройка PostgreSQL для платформы программирования

## Шаг 1: Установка PostgreSQL

### Windows
1. Скачайте PostgreSQL с официального сайта: https://www.postgresql.org/download/windows/
2. Запустите установщик и следуйте инструкциям
3. Запомните пароль для пользователя `postgres`

### Linux (Ubuntu/Debian)
```bash
sudo apt update
sudo apt install postgresql postgresql-contrib
```

### macOS
```bash
brew install postgresql
brew services start postgresql
```

## Шаг 2: Создание базы данных

Подключитесь к PostgreSQL как пользователь postgres:

```bash
# Windows (если добавили в PATH)
psql -U postgres

# Linux/macOS
sudo -u postgres psql
```

Создайте базу данных:

```sql
CREATE DATABASE coding_platform;
CREATE USER coding_user WITH PASSWORD 'your_password';
GRANT ALL PRIVILEGES ON DATABASE coding_platform TO coding_user;
\q
```

## Шаг 3: Установка Python зависимостей

```bash
pip install psycopg2-binary sqlalchemy flask
```

## Шаг 4: Настройка переменных окружения

Создайте файл `.env` в корне проекта:

```env
DB_HOST=localhost
DB_PORT=5432
DB_NAME=coding_platform
DB_USER=coding_user
DB_PASSWORD=your_password
FLASK_SECRET=your-secret-key-here
```

## Шаг 5: Запуск миграции

```bash
python migrate_to_postgres.py
```

## Шаг 6: Запуск приложения

```bash
python main_postgres.py
```

Приложение будет доступно по адресу: http://127.0.0.1:5000

## Структура базы данных

После миграции в базе будут следующие таблицы:

- **users** - пользователи системы
- **categories** - категории задач
- **tasks** - задачи программирования
- **testcases** - тест-кейсы для задач
- **solutions** - решения пользователей
- **tags** - теги для задач
- **task_tags** - связь задач и тегов

## Расширенные категории задач

Система включает следующие категории:

1. **Базовый синтаксис** - основы Python
2. **Строки** - работа со строками
3. **Циклы** - циклы и итерации
4. **Списки** - работа с массивами
5. **Алгоритмы** - базовые алгоритмы
6. **Структуры данных** - стеки, очереди
7. **Динамическое программирование** - DP задачи
8. **Графы** - алгоритмы на графах
9. **Математика** - математические задачи
10. **Строковые алгоритмы** - KMP, поиск подстрок
11. **Рекурсия** - рекурсивные алгоритмы

## Устранение неполадок

### Ошибка подключения к базе
- Проверьте, что PostgreSQL запущен
- Убедитесь, что пароль и имя пользователя правильные
- Проверьте настройки файрвола

### Ошибка импорта psycopg2
```bash
# Для Windows может потребоваться:
pip install --upgrade pip
pip install psycopg2-binary

# Для Linux может потребоваться:
sudo apt install python3-dev libpq-dev
pip install psycopg2-binary
```

### Ошибка прав доступа
```sql
-- Подключитесь как postgres и выполните:
GRANT ALL PRIVILEGES ON DATABASE coding_platform TO coding_user;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO coding_user;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO coding_user;
```
