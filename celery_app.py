"""
Celery приложение для асинхронного выполнения кода
"""
import os
from celery import Celery
import docker
import json
import time

# Создаем Celery приложение
celery_app = Celery(
    'zedcode',
    broker=os.environ.get('REDIS_URL', 'redis://localhost:6379/0'),
    backend=os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
)

# Конфигурация Celery
celery_app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='UTC',
    enable_utc=True,
    task_track_started=True,
    task_time_limit=30,  # Максимум 30 секунд на задачу
    task_soft_time_limit=25,
    worker_prefetch_multiplier=1,  # Обрабатывать по одной задаче за раз
    worker_max_tasks_per_child=50,  # Перезапускать worker после 50 задач
)


@celery_app.task(bind=True, max_retries=3, name='execute_code')
def execute_code_task(self, code: str, language: str, tests: list, timeout: int = 5):
    """
    Выполняет код пользователя в Docker контейнере
    
    Args:
        code: Код пользователя
        language: Язык программирования (python3, javascript, etc.)
        tests: Список тестов
        timeout: Таймаут выполнения (секунды)
    
    Returns:
        Словарь с результатами выполнения
    """
    try:
        client = docker.from_env()
        
        # Запускаем контейнер с ограничениями
        container = client.containers.run(
            image=f'zedcode-{language}:latest',
            command=f'python /app/runner.py',
            stdin_open=True,
            mem_limit='128m',  # Максимум 128MB памяти
            cpu_period=100000,
            cpu_quota=50000,   # 50% одного CPU
            network_disabled=True,  # Отключить сеть
            read_only=True,    # Только чтение файловой системы
            remove=True,       # Удалить после выполнения
            detach=True,
            environment={
                'TIMEOUT': str(timeout),
            }
        )
        
        # Отправляем код в контейнер
        container.put_archive('/app', {
            'code.py': code.encode('utf-8')
        })
        
        # Запускаем выполнение
        container.start()
        
        # Ждем завершения (с таймаутом)
        result = container.wait(timeout=timeout + 2)
        exit_code = result.get('StatusCode', 1)
        
        # Получаем логи
        logs = container.logs().decode('utf-8')
        
        # Парсим результат
        try:
            execution_result = json.loads(logs)
        except json.JSONDecodeError:
            execution_result = {
                'ok': False,
                'error': 'Не удалось распарсить результат',
                'stdout': logs,
                'stderr': '',
                'execution_time': 0,
                'memory_used': 0
            }
        
        return {
            'success': True,
            'result': execution_result,
            'exit_code': exit_code
        }
        
    except docker.errors.ImageNotFound:
        return {
            'success': False,
            'error': f'Образ zedcode-{language}:latest не найден'
        }
    except docker.errors.ContainerError as e:
        return {
            'success': False,
            'error': f'Ошибка контейнера: {str(e)}'
        }
    except Exception as e:
        # Повторяем попытку при временных ошибках
        raise self.retry(exc=e, countdown=2 ** self.request.retries)

