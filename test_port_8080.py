#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тест порта 8080
"""

import socket
import time

def test_connection(host, port, timeout=10):
    """Тест подключения к хосту и порту"""
    try:
        print(f"Тестируем подключение к {host}:{port}...")
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        
        start_time = time.time()
        result = sock.connect_ex((host, port))
        end_time = time.time()
        
        sock.close()
        
        if result == 0:
            print(f"OK - Подключение успешно! Время: {end_time - start_time:.2f} сек")
            return True
        else:
            print(f"ERROR - Подключение не удалось. Код ошибки: {result}")
            return False
            
    except socket.timeout:
        print(f"ERROR - Таймаут подключения ({timeout} сек)")
        return False
    except Exception as e:
        print(f"ERROR - Ошибка: {e}")
        return False

def main():
    print("=" * 50)
    print("ТЕСТ ПОРТА 8080")
    print("=" * 50)
    
    # Тест локального подключения на порту 8080
    print("1. Тест локального подключения (порт 8080):")
    local_ok = test_connection('127.0.0.1', 8080, 5)
    
    if not local_ok:
        print("ПРОБЛЕМА: Flask приложение не запущено на порту 8080")
        print("РЕШЕНИЕ: Перезапустите Flask приложение")
        print("  .\\venv\\Scripts\\python.exe main_postgres.py")
        return
    
    print()
    
    # Тест сетевого подключения
    print("2. Тест сетевого подключения (порт 8080):")
    network_ok = test_connection('192.168.1.54', 8080, 5)
    
    print()
    
    # Тест внешнего подключения
    print("3. Тест внешнего подключения (порт 8080):")
    external_ok = test_connection('79.139.139.134', 8080, 15)
    
    print()
    print("=" * 50)
    
    if external_ok:
        print("SUCCESS - ВСЕ РАБОТАЕТ! Сайт доступен из интернета")
        print("http://79.139.139.134:8080")
    else:
        print("PROBLEM - Сайт недоступен из интернета")
        print()
        print("СЛЕДУЮЩИЕ ШАГИ:")
        print("1. Обновите настройки Port Forwarding в роутере:")
        print("   - Внешний порт: 8080")
        print("   - Внутренний порт: 8080")
        print("   - Внутренний IP: 192.168.1.54")
        print("2. Перезагрузите роутер")
        print("3. Если не поможет, попробуйте порт 80 или 443")
        print("4. Обратитесь в техподдержку МТС")
    
    print("=" * 50)

if __name__ == "__main__":
    main()
