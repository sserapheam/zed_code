#!/usr/bin/env python3
"""
Продакшен версия Flask приложения
"""

import os
from dotenv import load_dotenv
from main_postgres import create_app

# Загружаем переменные из .env файла
load_dotenv()

# Создаем приложение
app = create_app()

if __name__ == "__main__":
    # Получаем настройки из переменных окружения
    host = os.environ.get('HOST', '0.0.0.0')
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_DEBUG', 'False').lower() == 'true'
    
    print(f"Запуск Flask приложения:")
    print(f"  Host: {host}")
    print(f"  Port: {port}")
    print(f"  Debug: {debug}")
    print(f"  Environment: {os.environ.get('FLASK_ENV', 'development')}")
    print()
    print("Для доступа из локальной сети используйте:")
    print(f"  http://ВАШ_ЛОКАЛЬНЫЙ_IP:{port}")
    print()
    print("Для доступа из интернета используйте:")
    print(f"  http://ВАШ_ВНЕШНИЙ_IP:{port}")
    print()
    print("Нажмите Ctrl+C для остановки")
    print("-" * 50)
    
    # Запускаем приложение
    app.run(
        host=host,
        port=port,
        debug=debug,
        threaded=True  # Поддержка множественных подключений
    )
