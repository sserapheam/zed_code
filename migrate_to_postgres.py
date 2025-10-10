#!/usr/bin/env python3
"""
Скрипт миграции данных из SQLite в PostgreSQL
"""

import os
import sys
import sqlite3
import json
from datetime import datetime
from dotenv import load_dotenv

# Загружаем переменные окружения из .env файла
load_dotenv()
from typing import Dict, List, Any

# Добавляем текущую директорию в путь для импорта
sys.path.append(os.path.dirname(__file__))

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
    from sqlalchemy import create_engine, text
except ImportError:
    print("Ошибка: Не установлены необходимые библиотеки")
    print("Установите их командой: pip install psycopg2-binary sqlalchemy")
    sys.exit(1)

# Конфигурация PostgreSQL
POSTGRES_CONFIG = {
    'host': os.environ.get('DB_HOST', 'localhost'),
    'port': int(os.environ.get('DB_PORT', 5432)),
    'database': os.environ.get('DB_NAME', 'coding_platform'),
    'user': os.environ.get('DB_USER', 'admin'),
    'password': os.environ.get('DB_PASSWORD', 'Sserapheam17*'),
    'client_encoding': 'utf8'
}

SQLITE_DB_PATH = 'app.db'

def get_sqlite_connection():
    """Подключение к SQLite базе"""
    return sqlite3.connect(SQLITE_DB_PATH)

def get_postgres_connection():
    """Подключение к PostgreSQL базе"""
    return psycopg2.connect(**POSTGRES_CONFIG)

def create_postgres_schema():
    """Создание схемы в PostgreSQL"""
    conn = get_postgres_connection()
    cursor = conn.cursor()
    
    print("Создание схемы базы данных...")
    
    # Создание таблиц
    schema_sql = """
    -- Удаление существующих таблиц (если есть)
    DROP TABLE IF EXISTS solutions CASCADE;
    DROP TABLE IF EXISTS task_tags CASCADE;
    DROP TABLE IF EXISTS testcases CASCADE;
    DROP TABLE IF EXISTS tags CASCADE;
    DROP TABLE IF EXISTS tasks CASCADE;
    DROP TABLE IF EXISTS categories CASCADE;
    DROP TABLE IF EXISTS users CASCADE;

    -- Создание таблицы пользователей
    CREATE TABLE users (
        id SERIAL PRIMARY KEY,
        username VARCHAR(50) UNIQUE NOT NULL,
        password_hash VARCHAR(255) NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
        display_name VARCHAR(100),
        bio TEXT,
        avatar_path VARCHAR(255),
        points INTEGER DEFAULT 0,
        email VARCHAR(255) UNIQUE,
        google_sub VARCHAR(255) UNIQUE
    );

    -- Создание таблицы категорий
    CREATE TABLE categories (
        id SERIAL PRIMARY KEY,
        name VARCHAR(100) UNIQUE NOT NULL
    );

    -- Создание таблицы задач
    CREATE TABLE tasks (
        id SERIAL PRIMARY KEY,
        title VARCHAR(200) NOT NULL,
        description TEXT NOT NULL,
        starter_code TEXT NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
        category_id INTEGER REFERENCES categories(id),
        points INTEGER DEFAULT 100,
        level VARCHAR(20) DEFAULT 'easy'
    );

    -- Создание таблицы тегов
    CREATE TABLE tags (
        id SERIAL PRIMARY KEY,
        name VARCHAR(50) UNIQUE NOT NULL
    );

    -- Создание таблицы связей задач и тегов
    CREATE TABLE task_tags (
        task_id INTEGER REFERENCES tasks(id) ON DELETE CASCADE,
        tag_id INTEGER REFERENCES tags(id) ON DELETE CASCADE,
        PRIMARY KEY (task_id, tag_id)
    );

    -- Создание таблицы тест-кейсов
    CREATE TABLE testcases (
        id SERIAL PRIMARY KEY,
        task_id INTEGER REFERENCES tasks(id) ON DELETE CASCADE,
        input_text TEXT NOT NULL,
        expected_output TEXT NOT NULL
    );

    -- Создание таблицы решений
    CREATE TABLE solutions (
        id SERIAL PRIMARY KEY,
        user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
        task_id INTEGER REFERENCES tasks(id) ON DELETE CASCADE,
        code TEXT NOT NULL,
        passed INTEGER NOT NULL,
        result_json TEXT NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
        duration_ms INTEGER DEFAULT 0
    );

    -- Создание индексов для производительности
    CREATE INDEX idx_users_username ON users(username);
    CREATE INDEX idx_users_email ON users(email) WHERE email IS NOT NULL;
    CREATE INDEX idx_users_points ON users(points);
    CREATE INDEX idx_tasks_category ON tasks(category_id);
    CREATE INDEX idx_tasks_level ON tasks(level);
    CREATE INDEX idx_solutions_user_task ON solutions(user_id, task_id);
    CREATE INDEX idx_solutions_created_at ON solutions(created_at);
    CREATE INDEX idx_testcases_task ON testcases(task_id);
    """
    
    cursor.execute(schema_sql)
    conn.commit()
    print("Схема базы данных создана")
    
    cursor.close()
    conn.close()

def migrate_categories():
    """Миграция категорий"""
    print("Миграция категорий...")
    
    sqlite_conn = get_sqlite_connection()
    sqlite_cursor = sqlite_conn.cursor()
    
    postgres_conn = get_postgres_connection()
    postgres_cursor = postgres_conn.cursor()
    
    # Получаем категории из SQLite
    sqlite_cursor.execute("SELECT id, name FROM categories")
    categories = sqlite_cursor.fetchall()
    
    # Создаем маппинг старых ID на новые
    id_mapping = {}
    
    for old_id, name in categories:
        postgres_cursor.execute(
            "INSERT INTO categories (name) VALUES (%s) RETURNING id",
            (name,)
        )
        new_id = postgres_cursor.fetchone()[0]
        id_mapping[old_id] = new_id
        print(f"  Категория: {name} (ID: {old_id} -> {new_id})")
    
    postgres_conn.commit()
    
    sqlite_cursor.close()
    sqlite_conn.close()
    postgres_cursor.close()
    postgres_conn.close()
    
    return id_mapping

def migrate_tasks(category_mapping):
    """Миграция задач"""
    print("Миграция задач...")
    
    sqlite_conn = get_sqlite_connection()
    sqlite_cursor = sqlite_conn.cursor()
    
    postgres_conn = get_postgres_connection()
    postgres_cursor = postgres_conn.cursor()
    
    # Получаем задачи из SQLite
    sqlite_cursor.execute("""
        SELECT id, title, description, starter_code, created_at, 
               category_id, points, level 
        FROM tasks
    """)
    tasks = sqlite_cursor.fetchall()
    
    task_id_mapping = {}
    
    for old_id, title, description, starter_code, created_at, category_id, points, level in tasks:
        # Конвертируем дату из строки в timestamp
        if created_at:
            try:
                created_at = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                created_at = datetime.now()
        else:
            created_at = datetime.now()
        
        # Получаем новый ID категории
        new_category_id = category_mapping.get(category_id) if category_id else None
        
        postgres_cursor.execute("""
            INSERT INTO tasks (title, description, starter_code, created_at, 
                             category_id, points, level)
            VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id
        """, (title, description, starter_code, created_at, 
              new_category_id, points, level))
        
        new_id = postgres_cursor.fetchone()[0]
        task_id_mapping[old_id] = new_id
        print(f"  Задача: {title} (ID: {old_id} -> {new_id})")
    
    postgres_conn.commit()
    
    sqlite_cursor.close()
    sqlite_conn.close()
    postgres_cursor.close()
    postgres_conn.close()
    
    return task_id_mapping

def migrate_testcases(task_mapping):
    """Миграция тест-кейсов"""
    print("Миграция тест-кейсов...")
    
    sqlite_conn = get_sqlite_connection()
    sqlite_cursor = sqlite_conn.cursor()
    
    postgres_conn = get_postgres_connection()
    postgres_cursor = postgres_conn.cursor()
    
    sqlite_cursor.execute("SELECT task_id, input_text, expected_output FROM testcases")
    testcases = sqlite_cursor.fetchall()
    
    for task_id, input_text, expected_output in testcases:
        new_task_id = task_mapping.get(task_id)
        if new_task_id:
            postgres_cursor.execute("""
                INSERT INTO testcases (task_id, input_text, expected_output)
                VALUES (%s, %s, %s)
            """, (new_task_id, input_text, expected_output))
    
    postgres_conn.commit()
    print(f"  Перенесено {len(testcases)} тест-кейсов")
    
    sqlite_cursor.close()
    sqlite_conn.close()
    postgres_cursor.close()
    postgres_conn.close()

def migrate_users():
    """Миграция пользователей"""
    print("Миграция пользователей...")
    
    sqlite_conn = get_sqlite_connection()
    sqlite_cursor = sqlite_conn.cursor()
    
    postgres_conn = get_postgres_connection()
    postgres_cursor = postgres_conn.cursor()
    
    sqlite_cursor.execute("""
        SELECT id, username, password_hash, created_at, display_name, 
               bio, avatar_path, points, email, google_sub
        FROM users
    """)
    users = sqlite_cursor.fetchall()
    
    user_id_mapping = {}
    
    for (old_id, username, password_hash, created_at, display_name, 
         bio, avatar_path, points, email, google_sub) in users:
        
        # Конвертируем дату
        if created_at:
            try:
                created_at = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                created_at = datetime.now()
        else:
            created_at = datetime.now()
        
        postgres_cursor.execute("""
            INSERT INTO users (username, password_hash, created_at, display_name,
                             bio, avatar_path, points, email, google_sub)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
        """, (username, password_hash, created_at, display_name,
              bio, avatar_path, points, email, google_sub))
        
        new_id = postgres_cursor.fetchone()[0]
        user_id_mapping[old_id] = new_id
        print(f"  Пользователь: {username} (ID: {old_id} -> {new_id})")
    
    postgres_conn.commit()
    
    sqlite_cursor.close()
    sqlite_conn.close()
    postgres_cursor.close()
    postgres_conn.close()
    
    return user_id_mapping

def migrate_solutions(user_mapping, task_mapping):
    """Миграция решений"""
    print("Миграция решений...")
    
    sqlite_conn = get_sqlite_connection()
    sqlite_cursor = sqlite_conn.cursor()
    
    postgres_conn = get_postgres_connection()
    postgres_cursor = postgres_conn.cursor()
    
    sqlite_cursor.execute("""
        SELECT user_id, task_id, code, passed, result_json, created_at, duration_ms
        FROM solutions
    """)
    solutions = sqlite_cursor.fetchall()
    
    for user_id, task_id, code, passed, result_json, created_at, duration_ms in solutions:
        new_user_id = user_mapping.get(user_id)
        new_task_id = task_mapping.get(task_id)
        
        if new_user_id and new_task_id:
            # Конвертируем дату
            if created_at:
                try:
                    created_at = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    created_at = datetime.now()
            else:
                created_at = datetime.now()
            
            postgres_cursor.execute("""
                INSERT INTO solutions (user_id, task_id, code, passed, result_json, 
                                     created_at, duration_ms)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (new_user_id, new_task_id, code, passed, result_json, 
                  created_at, duration_ms))
    
    postgres_conn.commit()
    print(f"  Перенесено {len(solutions)} решений")
    
    sqlite_cursor.close()
    sqlite_conn.close()
    postgres_cursor.close()
    postgres_conn.close()

def add_comprehensive_tasks():
    """Добавление расширенного набора задач"""
    print("Добавление расширенного набора задач...")
    
    conn = get_postgres_connection()
    cursor = conn.cursor()
    
    # Получаем ID категорий
    cursor.execute("SELECT id, name FROM categories")
    categories = {name: id for id, name in cursor.fetchall()}
    
    # Создаем недостающие категории
    new_categories = [
        "Алгоритмы", "Структуры данных", "Динамическое программирование", 
        "Графы", "Математика", "Строковые алгоритмы", "Рекурсия"
    ]
    
    for cat_name in new_categories:
        if cat_name not in categories:
            cursor.execute("INSERT INTO categories (name) VALUES (%s) RETURNING id", (cat_name,))
            categories[cat_name] = cursor.fetchone()[0]
            print(f"  Создана категория: {cat_name}")
    
    # Расширенный набор задач
    extended_tasks = [
        # Алгоритмы
        ("Линейный поиск", "Найдите индекс первого вхождения элемента в массиве.", 
         "arr = list(map(int, input().split()))\ntarget = int(input())\nfor i, x in enumerate(arr):\n    if x == target:\n        print(i)\n        break\nelse:\n    print(-1)", 
         "Алгоритмы", 60, "easy", [("1 2 3 4 5\n3\n", "2\n"), ("5 4 3 2 1\n6\n", "-1\n")]),
        
        ("Бинарный поиск", "Найдите индекс элемента в отсортированном массиве.", 
         "arr = list(map(int, input().split()))\ntarget = int(input())\nleft, right = 0, len(arr) - 1\nwhile left <= right:\n    mid = (left + right) // 2\n    if arr[mid] == target:\n        print(mid)\n        break\n    elif arr[mid] < target:\n        left = mid + 1\n    else:\n        right = mid - 1\nelse:\n    print(-1)", 
         "Алгоритмы", 120, "medium", [("1 3 5 7 9\n5\n", "2\n"), ("1 3 5 7 9\n4\n", "-1\n")]),
        
        ("Сортировка пузырьком", "Отсортируйте массив методом пузырька.", 
         "arr = list(map(int, input().split()))\nn = len(arr)\nfor i in range(n):\n    for j in range(0, n - i - 1):\n        if arr[j] > arr[j + 1]:\n            arr[j], arr[j + 1] = arr[j + 1], arr[j]\nprint(' '.join(map(str, arr)))", 
         "Алгоритмы", 100, "medium", [("64 34 25 12 22 11 90\n", "11 12 22 25 34 64 90\n"), ("5 2 8 1 9\n", "1 2 5 8 9\n")]),
        
        # Структуры данных
        ("Стек", "Реализуйте стек с операциями push, pop, top.", 
         "stack = []\nwhile True:\n    try:\n        line = input().strip()\n        if line == 'exit':\n            break\n        elif line.startswith('push'):\n            val = int(line.split()[1])\n            stack.append(val)\n        elif line == 'pop':\n            if stack:\n                print(stack.pop())\n            else:\n                print('error')\n        elif line == 'top':\n            if stack:\n                print(stack[-1])\n            else:\n                print('error')\n    except EOFError:\n        break", 
         "Структуры данных", 150, "hard", [("push 1\npush 2\ntop\npop\ntop\npop\npop\nexit\n", "2\n2\n1\nerror\n"), ("push 5\ntop\npush 3\ntop\npop\ntop\nexit\n", "5\n3\n3\n5\n")]),
        
        ("Очередь", "Реализуйте очередь с операциями enqueue, dequeue.", 
         "from collections import deque\nqueue = deque()\nwhile True:\n    try:\n        line = input().strip()\n        if line == 'exit':\n            break\n        elif line.startswith('enqueue'):\n            val = int(line.split()[1])\n            queue.append(val)\n        elif line == 'dequeue':\n            if queue:\n                print(queue.popleft())\n            else:\n                print('error')\n    except EOFError:\n        break", 
         "Структуры данных", 140, "hard", [("enqueue 1\nenqueue 2\ndequeue\ndequeue\ndequeue\nexit\n", "1\n2\nerror\n"), ("enqueue 5\ndequeue\nenqueue 3\ndequeue\nexit\n", "5\n3\n")]),
        
        # Динамическое программирование
        ("Числа Фибоначчи (DP)", "Вычислите n-е число Фибоначчи с использованием динамического программирования.", 
         "n = int(input())\nif n <= 1:\n    print(n)\nelse:\n    dp = [0] * (n + 1)\n    dp[1] = 1\n    for i in range(2, n + 1):\n        dp[i] = dp[i-1] + dp[i-2]\n    print(dp[n])", 
         "Динамическое программирование", 120, "medium", [("10\n", "55\n"), ("20\n", "6765\n")]),
        
        ("Задача о рюкзаке", "Решите задачу о рюкзаке с целыми весами.", 
         "n, capacity = map(int, input().split())\nweights = list(map(int, input().split()))\nvalues = list(map(int, input().split()))\n\n# Создаем таблицу DP\ndp = [[0 for _ in range(capacity + 1)] for _ in range(n + 1)]\n\nfor i in range(1, n + 1):\n    for w in range(capacity + 1):\n        if weights[i-1] <= w:\n            dp[i][w] = max(dp[i-1][w], dp[i-1][w-weights[i-1]] + values[i-1])\n        else:\n            dp[i][w] = dp[i-1][w]\n\nprint(dp[n][capacity])", 
         "Динамическое программирование", 200, "hard", [("3 10\n2 3 4\n1 4 5\n", "9\n"), ("4 7\n1 3 4 5\n1 4 5 7\n", "9\n")]),
        
        # Графы
        ("Обход графа в глубину", "Найдите количество связных компонент в неориентированном графе.", 
         "def dfs(node, visited, graph):\n    visited[node] = True\n    for neighbor in graph[node]:\n        if not visited[neighbor]:\n            dfs(neighbor, visited, graph)\n\nn, m = map(int, input().split())\ngraph = [[] for _ in range(n)]\nfor _ in range(m):\n    u, v = map(int, input().split())\n    graph[u].append(v)\n    graph[v].append(u)\n\nvisited = [False] * n\ncomponents = 0\n\nfor i in range(n):\n    if not visited[i]:\n        dfs(i, visited, graph)\n        components += 1\n\nprint(components)", 
         "Графы", 180, "hard", [("4 2\n0 1\n2 3\n", "2\n"), ("5 4\n0 1\n1 2\n2 3\n3 4\n", "1\n")]),
        
        # Математика
        ("Наибольший общий делитель", "Найдите НОД двух чисел алгоритмом Евклида.", 
         "def gcd(a, b):\n    while b:\n        a, b = b, a % b\n    return a\n\na, b = map(int, input().split())\nprint(gcd(a, b))", 
         "Математика", 80, "easy", [("48 18\n", "6\n"), ("100 25\n", "25\n")]),
        
        ("Проверка простого числа", "Проверьте, является ли число простым.", 
         "def is_prime(n):\n    if n < 2:\n        return False\n    for i in range(2, int(n ** 0.5) + 1):\n        if n % i == 0:\n            return False\n    return True\n\nn = int(input())\nprint('yes' if is_prime(n) else 'no')", 
         "Математика", 90, "medium", [("17\n", "yes\n"), ("15\n", "no\n")]),
        
        # Строковые алгоритмы
        ("Поиск подстроки (KMP)", "Найдите все вхождения подстроки в строку.", 
         "def kmp_search(text, pattern):\n    def build_lps(pattern):\n        lps = [0] * len(pattern)\n        length = 0\n        i = 1\n        while i < len(pattern):\n            if pattern[i] == pattern[length]:\n                length += 1\n                lps[i] = length\n                i += 1\n            else:\n                if length != 0:\n                    length = lps[length - 1]\n                else:\n                    lps[i] = 0\n                    i += 1\n        return lps\n    \n    lps = build_lps(pattern)\n    i = j = 0\n    positions = []\n    \n    while i < len(text):\n        if pattern[j] == text[i]:\n            i += 1\n            j += 1\n        if j == len(pattern):\n            positions.append(i - j)\n            j = lps[j - 1]\n        elif i < len(text) and pattern[j] != text[i]:\n            if j != 0:\n                j = lps[j - 1]\n            else:\n                i += 1\n    return positions\n\ntext = input()\npattern = input()\npositions = kmp_search(text, pattern)\nprint(' '.join(map(str, positions)) if positions else -1)", 
         "Строковые алгоритмы", 250, "hard", [("ababcababc\nabc\n", "2 6\n"), ("hello\nworld\n", "-1\n")]),
        
        # Рекурсия
        ("Ханойские башни", "Решите задачу о ханойских башнях.", 
         "def hanoi(n, source, destination, auxiliary):\n    if n == 1:\n        print(f'Move disk 1 from {source} to {destination}')\n        return\n    hanoi(n-1, source, auxiliary, destination)\n    print(f'Move disk {n} from {source} to {destination}')\n    hanoi(n-1, auxiliary, destination, source)\n\nn = int(input())\nhanoi(n, 'A', 'C', 'B')", 
         "Рекурсия", 120, "medium", [("3\n", "Move disk 1 from A to C\nMove disk 2 from A to B\nMove disk 1 from C to B\nMove disk 3 from A to C\nMove disk 1 from B to A\nMove disk 2 from B to C\nMove disk 1 from A to C\n"), ("2\n", "Move disk 1 from A to B\nMove disk 2 from A to C\nMove disk 1 from B to C\n")]),
        
        ("Быстрая сортировка", "Реализуйте алгоритм быстрой сортировки.", 
         "def quicksort(arr):\n    if len(arr) <= 1:\n        return arr\n    pivot = arr[len(arr) // 2]\n    left = [x for x in arr if x < pivot]\n    middle = [x for x in arr if x == pivot]\n    right = [x for x in arr if x > pivot]\n    return quicksort(left) + middle + quicksort(right)\n\narr = list(map(int, input().split()))\nsorted_arr = quicksort(arr)\nprint(' '.join(map(str, sorted_arr)))", 
         "Рекурсия", 150, "hard", [("64 34 25 12 22 11 90\n", "11 12 22 25 34 64 90\n"), ("5 2 8 1 9\n", "1 2 5 8 9\n")])
    ]
    
    # Добавляем задачи
    for title, description, starter_code, category_name, points, level, testcases in extended_tasks:
        category_id = categories.get(category_name)
        if category_id:
            cursor.execute("""
                INSERT INTO tasks (title, description, starter_code, created_at, 
                                 category_id, points, level)
                VALUES (%s, %s, %s, NOW(), %s, %s, %s) RETURNING id
            """, (title, description, starter_code, category_id, points, level))
            
            task_id = cursor.fetchone()[0]
            
            # Добавляем тест-кейсы
            for input_text, expected_output in testcases:
                cursor.execute("""
                    INSERT INTO testcases (task_id, input_text, expected_output)
                    VALUES (%s, %s, %s)
                """, (task_id, input_text, expected_output))
            
            print(f"  Добавлена задача: {title} (категория: {category_name})")
    
    conn.commit()
    cursor.close()
    conn.close()
    
    print(f"Добавлено {len(extended_tasks)} новых задач")

def main():
    """Основная функция миграции"""
    print("Начинаем миграцию из SQLite в PostgreSQL")
    print("=" * 50)
    
    # Проверяем существование SQLite базы
    if not os.path.exists(SQLITE_DB_PATH):
        print(f"Файл {SQLITE_DB_PATH} не найден!")
        return
    
    try:
        # Создаем схему PostgreSQL
        create_postgres_schema()
        
        # Мигрируем данные
        category_mapping = migrate_categories()
        task_mapping = migrate_tasks(category_mapping)
        migrate_testcases(task_mapping)
        user_mapping = migrate_users()
        migrate_solutions(user_mapping, task_mapping)
        
        # Добавляем расширенный набор задач
        add_comprehensive_tasks()
        
        print("\n" + "=" * 50)
        print("Миграция завершена успешно!")
        print("\nСтатистика:")
        
        # Показываем статистику
        conn = get_postgres_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) FROM users")
        users_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM categories")
        categories_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM tasks")
        tasks_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM testcases")
        testcases_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM solutions")
        solutions_count = cursor.fetchone()[0]
        
        print(f"  Пользователи: {users_count}")
        print(f"  Категории: {categories_count}")
        print(f"  Задачи: {tasks_count}")
        print(f"  Тест-кейсы: {testcases_count}")
        print(f"  Решения: {solutions_count}")
        
        cursor.close()
        conn.close()
        
        print(f"\nПодключение к базе: postgresql://{POSTGRES_CONFIG['user']}@localhost:5432/{POSTGRES_CONFIG['database']}")
        
    except Exception as e:
        print(f"Ошибка при миграции: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
