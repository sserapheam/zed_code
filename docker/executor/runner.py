#!/usr/bin/env python3
"""
Безопасный runner для выполнения пользовательского кода
"""
import sys
import json
import time
import resource
import psutil
import os

# Устанавливаем ограничения ресурсов
def set_limits():
    """Устанавливает жесткие ограничения на ресурсы"""
    # Максимум 128MB памяти (RSS)
    resource.setrlimit(resource.RLIMIT_AS, (128 * 1024 * 1024, 128 * 1024 * 1024))
    
    # Максимум 5 секунд CPU времени
    resource.setrlimit(resource.RLIMIT_CPU, (5, 5))
    
    # Максимум 50MB файлов
    resource.setrlimit(resource.RLIMIT_FSIZE, (50 * 1024 * 1024, 50 * 1024 * 1024))
    
    # Запрещаем создание новых процессов
    resource.setrlimit(resource.RLIMIT_NPROC, (1, 1))

# Отключаем опасные функции
def disable_imports():
    """Отключает опасные модули"""
    import builtins
    original_import = builtins.__import__
    
    BLOCKED_MODULES = {
        'os', 'sys', 'subprocess', 'socket', 'threading', 
        'multiprocessing', 'ctypes', 'importlib'
    }
    
    def restricted_import(name, *args, **kwargs):
        if name in BLOCKED_MODULES:
            raise ImportError(f"Модуль {name} заблокирован")
        return original_import(name, *args, **kwargs)
    
    builtins.__import__ = restricted_import

def execute_code(code: str, timeout: int = 5) -> dict:
    """Выполняет код пользователя с ограничениями"""
    start_time = time.time()
    max_memory = 0.0
    stdout = ""
    stderr = ""
    error = None
    traceback_text = None
    
    try:
        # Устанавливаем ограничения
        set_limits()
        
        # Перенаправляем stdout/stderr
        import io
        from contextlib import redirect_stdout, redirect_stderr
        
        stdout_capture = io.StringIO()
        stderr_capture = io.StringIO()
        
        # Мониторинг памяти
        process = psutil.Process()
        
        with redirect_stdout(stdout_capture), redirect_stderr(stderr_capture):
            # Выполняем код
            exec(code, {"__builtins__": __builtins__})
        
        stdout = stdout_capture.getvalue()
        stderr = stderr_capture.getvalue()
        max_memory = process.memory_info().rss / 1024 / 1024  # MB
        
    except TimeoutError:
        error = "Превышено время выполнения"
    except MemoryError:
        error = "Превышен лимит памяти"
    except Exception as e:
        error = str(e)
        import traceback
        traceback_text = traceback.format_exc()
    
    execution_time = time.time() - start_time
    
    return {
        "ok": error is None,
        "stdout": stdout,
        "stderr": stderr,
        "error": error,
        "traceback": traceback_text,
        "execution_time": execution_time,
        "memory_used": max_memory
    }

if __name__ == "__main__":
    # Читаем код из stdin
    code = sys.stdin.read()
    
    # Получаем timeout из переменной окружения (по умолчанию 5 сек)
    timeout = int(os.environ.get("TIMEOUT", "5"))
    
    # Выполняем код
    result = execute_code(code, timeout)
    
    # Выводим результат в JSON формате
    print(json.dumps(result, ensure_ascii=False))

