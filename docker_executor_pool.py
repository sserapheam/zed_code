"""
Менеджер пула Docker контейнеров для выполнения кода
Переиспользует контейнеры для ускорения выполнения
"""
import os
import threading
import time
import logging
from typing import Optional, Dict, Any
from queue import Queue, Empty
from docker import DockerClient
from docker import errors as docker_errors

logger = logging.getLogger(__name__)

class DockerExecutorPool:
    """Пул долгоживущих Docker контейнеров для выполнения кода"""
    
    def __init__(self, pool_size: int = 3, max_containers: int = 10, warmup: bool = True):
        """
        Args:
            pool_size: Количество контейнеров в пуле
            max_containers: Максимальное количество контейнеров (для масштабирования)
            warmup: Прогревать контейнеры при создании
        """
        self.pool_size = pool_size
        self.max_containers = max_containers
        self.warmup = warmup
        self.client = DockerClient.from_env()
        self.container_pool: Queue = Queue(maxsize=pool_size)
        self.active_containers: Dict[str, threading.Lock] = {}
        self.lock = threading.Lock()
        self._initialized = False
        self._shutdown = False
        
        # Статистика для мониторинга
        self.stats = {
            "total_executions": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "avg_execution_time": 0.0,
            "errors": 0
        }
        
    def _create_container(self, container_id: Optional[str] = None) -> Optional[Any]:
        """Создает долгоживущий контейнер"""
        try:
            # Создаем контейнер, который будет работать постоянно
            # Используем уникальное имя, чтобы избежать конфликтов
            unique_name = f"zedcode-executor-{container_id or int(time.time() * 1000000)}"
            try:
                # Пытаемся удалить старый контейнер с таким именем, если он существует
                try:
                    old_container = self.client.containers.get(unique_name)
                    old_container.remove(force=True)
                except:
                    pass
            except:
                pass
            
            container = self.client.containers.create(
                image="zedcode-python:latest",
                command=["sh", "-c", "tail -f /dev/null"],  # Долгоживущий процесс
                mem_limit="256m",
                network_disabled=True,
                read_only=False,  # Нужен для записи временных файлов
                detach=True,
                name=unique_name,
                # Оптимизации:
                tmpfs={'/tmp': 'size=50m,mode=1777'},  # Tmpfs для /tmp - быстрее диска
                cpu_quota=50000,  # Ограничение CPU для справедливости
                cpu_period=100000,
                pids_limit=50,  # Ограничение процессов
            )
            container.start()
            logger.info(f"✅ Создан контейнер для пула: {container.id[:12]}")
            
            # Прогрев контейнера (опционально)
            if self.warmup:
                self._warmup_container(container)
            
            return container
        except Exception as e:
            logger.error(f"❌ Ошибка создания контейнера: {e}")
            return None
    
    def _warmup_container(self, container: Any):
        """Прогревает контейнер, запуская простой код для инициализации Python"""
        try:
            # Простой код для инициализации интерпретатора
            warmup_code = "print('warmup')"
            
            # Создаем tar с файлом прогрева
            import tarfile
            import io
            
            tar_stream = io.BytesIO()
            tar = tarfile.TarFile(fileobj=tar_stream, mode='w')
            
            code_bytes = warmup_code.encode('utf-8')
            code_tarinfo = tarfile.TarInfo(name='warmup.py')
            code_tarinfo.size = len(code_bytes)
            code_tarinfo.mtime = int(time.time())
            code_tarinfo.mode = 0o644
            
            tar.addfile(code_tarinfo, io.BytesIO(code_bytes))
            tar.close()
            
            tar_stream.seek(0)
            container.put_archive('/app', tar_stream.read())
            
            # Выполняем прогрев
            result = container.exec_run(
                ["python", "-u", "/app/warmup.py"],
                stdout=True,
                stderr=True
            )
            
            # Очищаем
            container.exec_run("rm -f /app/warmup.py")
            
            logger.debug(f"🔥 Контейнер {container.id[:12]} прогрет")
        except Exception as e:
            logger.warning(f"⚠️ Ошибка прогрева контейнера: {e}")
    
    def _check_container_health(self, container: Any) -> bool:
        """Проверяет здоровье контейнера"""
        try:
            container.reload()
            if container.status != 'running':
                return False
            
            # Пробуем выполнить простую команду
            result = container.exec_run(["echo", "ok"], stdout=True, stderr=True)
            return result.exit_code == 0
        except Exception:
            return False
    
    def _cleanup_container(self, container: Any):
        """Очищает и удаляет контейнер"""
        try:
            container.stop(timeout=1)
            container.remove()
            logger.debug(f"🗑️ Контейнер удален: {container.id[:12]}")
        except Exception as e:
            logger.warning(f"⚠️ Ошибка удаления контейнера: {e}")
    
    def initialize(self):
        """Инициализирует пул контейнеров"""
        if self._initialized:
            return
        
        logger.info(f"🚀 Инициализация пула контейнеров (размер: {self.pool_size})...")
        
        # Создаем контейнеры для пула
        for i in range(self.pool_size):
            container = self._create_container(f"pool-{i}")
            if container:
                self.container_pool.put(container)
                self.active_containers[container.id] = threading.Lock()
        
        self._initialized = True
        logger.info(f"✅ Пул контейнеров инициализирован: {self.container_pool.qsize()} контейнеров")
    
    def execute_code(self, code: str, time_limit_sec: float) -> Dict[str, Any]:
        """
        Выполняет код в переиспользуемом контейнере
        
        Args:
            code: Код для выполнения
            time_limit_sec: Лимит времени выполнения
            
        Returns:
            Результат выполнения в формате словаря
        """
        if not self._initialized:
            self.initialize()
        
        container = None
        start_time = time.time()
        
        # Профилирование
        timings = {
            "get_container": 0,
            "health_check": 0,
            "create_tar": 0,
            "put_archive": 0,
            "exec_run": 0,
            "parse_result": 0,
            "cleanup": 0
        }
        checkpoint = start_time
        
        try:
            # Получаем контейнер из пула (с таймаутом)
            from_pool = False
            try:
                container = self.container_pool.get(timeout=5)
                from_pool = True
            except Empty:
                # Если пул пуст, создаем новый контейнер
                logger.warning("⚠️ Пул пуст, создаем новый контейнер...")
                container = self._create_container()
                if not container:
                    raise Exception("Не удалось создать контейнер")
            
            timings["get_container"] = (time.time() - checkpoint) * 1000
            checkpoint = time.time()
            
            container_id = container.id[:12]
            
            # Проверяем здоровье ТОЛЬКО для новых контейнеров (не из пула)
            # Контейнеры из пула уже проверены и работают
            if not from_pool and not self._check_container_health(container):
                logger.warning(f"⚠️ Контейнер {container_id} нездоров, пересоздаем...")
                try:
                    self._cleanup_container(container)
                except:
                    pass
                container = self._create_container()
                if not container:
                    self.stats["errors"] += 1
                    raise Exception("Не удалось создать контейнер")
                container_id = container.id[:12]
                self.stats["cache_misses"] += 1
            else:
                self.stats["cache_hits"] += 1
            
            timings["health_check"] = (time.time() - checkpoint) * 1000
            checkpoint = time.time()
            
            # Создаем временный файл с кодом внутри контейнера
            import json
            import tarfile
            import io
            
            # Используем put_archive для копирования файла в контейнер
            # Создаем tar архив в памяти с файлом code.py
            tar_stream = io.BytesIO()
            tar = tarfile.TarFile(fileobj=tar_stream, mode='w')
            
            # Создаем файл в tar архиве
            code_bytes = code.encode('utf-8')
            code_tarinfo = tarfile.TarInfo(name='code.py')
            code_tarinfo.size = len(code_bytes)
            code_tarinfo.mtime = int(time.time())
            code_tarinfo.mode = 0o644
            
            tar.addfile(code_tarinfo, io.BytesIO(code_bytes))
            tar.close()
            
            timings["create_tar"] = (time.time() - checkpoint) * 1000
            checkpoint = time.time()
            
            # Копируем файл в контейнер
            tar_stream.seek(0)
            success = container.put_archive('/app', tar_stream.read())
            
            timings["put_archive"] = (time.time() - checkpoint) * 1000
            checkpoint = time.time()
            
            if not success:
                raise Exception("Не удалось скопировать код в контейнер")
            
            # Выполняем код через runner.py с таймаутом
            timeout_seconds = int(time_limit_sec) + 2
            
            # Читаем код из файла и передаем через stdin
            # Важно: используем простое перенаправление, так как exec_run не поддерживает параметр input
            exec_result = container.exec_run(
                ["sh", "-c", f"cat /app/code.py | python -u /app/runner.py"],
                stdout=True,
                stderr=True,
                demux=False
            )
            
            timings["exec_run"] = (time.time() - checkpoint) * 1000
            checkpoint = time.time()
            
            # Получаем результат
            exit_code = exec_result.exit_code
            output_bytes = exec_result.output
            output_text = output_bytes.decode('utf-8', errors='replace') if isinstance(output_bytes, bytes) else str(output_bytes)
            
            # Логируем для отладки с тайминга�ми
            logger.info(f"Profiling (container {container_id}): "
                       f"get={timings['get_container']:.1f}ms, "
                       f"health={timings['health_check']:.1f}ms, "
                       f"tar={timings['create_tar']:.1f}ms, "
                       f"upload={timings['put_archive']:.1f}ms, "
                       f"exec={timings['exec_run']:.1f}ms")
            
            if exit_code != 0:
                logger.warning(f"⚠️ Контейнер завершился с кодом {exit_code}")
                if not output_text.strip():
                    # Если вывод пустой, возвращаем ошибку
                    return {
                        "ok": False,
                        "output": "",
                        "error": f"Контейнер завершился с кодом ошибки {exit_code}",
                        "execution_time": int((time.time() - start_time) * 1000),
                        "memory_mb": 0.0
                    }
            
            # Парсим JSON
            try:
                result = json.loads(output_text.strip())
            except json.JSONDecodeError as e:
                # Если не JSON, возможно это ошибка выполнения
                logger.error(f"❌ Не удалось распарсить JSON. Ошибка: {e}")
                logger.error(f"   Вывод (первые 500 символов): {output_text[:500]}")
                
                # Возвращаем сырой вывод как ошибку
                return {
                    "ok": False,
                    "output": "",
                    "error": f"Ошибка парсинга JSON: {str(e)}. Вывод: {output_text[:200]}",
                    "execution_time": int((time.time() - start_time) * 1000),
                    "memory_mb": 0.0
                }
            
            # Извлекаем stdout
            stdout = result.get("stdout", "") or ""
            if not stdout:
                stdout = result.get("output", "")
            
            execution_time = int((time.time() - start_time) * 1000)
            
            # Обновляем статистику
            self.stats["total_executions"] += 1
            if self.stats["total_executions"] > 0:
                self.stats["avg_execution_time"] = (
                    (self.stats["avg_execution_time"] * (self.stats["total_executions"] - 1) + execution_time) / 
                    self.stats["total_executions"]
                )
            
            return {
                "ok": result.get("ok", False),
                "output": stdout,
                "error": result.get("error", "") or result.get("stderr", ""),
                "execution_time": execution_time,
                "memory_mb": round(result.get("memory_used", 0), 2)
            }
            
        except json.JSONDecodeError as e:
            execution_time = int((time.time() - start_time) * 1000)
            output_text = output_bytes.decode('utf-8') if isinstance(output_bytes, bytes) else str(output_bytes)
            logger.error(f"❌ Ошибка парсинга JSON: {e}, вывод: {output_text[:200]}")
            return {
                "ok": False,
                "output": output_text,
                "error": f"Ошибка парсинга ответа: {str(e)}",
                "execution_time": execution_time,
                "memory_mb": 0.0
            }
        except Exception as e:
            execution_time = int((time.time() - start_time) * 1000)
            logger.error(f"❌ Ошибка выполнения кода: {e}", exc_info=True)
            
            # Обновляем статистику ошибок
            self.stats["errors"] += 1
            
            # Если контейнер сломан, пересоздаем его
            if container:
                try:
                    container.reload()
                    if container.status != 'running':
                        logger.warning(f"⚠️ Контейнер {container.id[:12]} сломан, пересоздаем...")
                        self._cleanup_container(container)
                        container = None
                except:
                    container = None
            
            return {
                "ok": False,
                "error": f"Ошибка выполнения: {str(e)}",
                "execution_time": execution_time,
                "memory_mb": 0.0
            }
        finally:
            # Возвращаем контейнер в пул
            if container:
                try:
                    # Очищаем временные файлы
                    container.exec_run("rm -f /app/code.py", stdout=True, stderr=True)
                    
                    # Возвращаем в пул
                    if not self._shutdown:
                        self.container_pool.put(container)
                except Exception as e:
                    logger.warning(f"⚠️ Ошибка возврата контейнера в пул: {e}")
                    # Если не удалось вернуть, пересоздаем
                    try:
                        self._cleanup_container(container)
                    except:
                        pass
    
    def get_stats(self) -> Dict[str, Any]:
        """Возвращает статистику работы пула"""
        return {
            **self.stats,
            "pool_size": self.pool_size,
            "containers_in_pool": self.container_pool.qsize(),
            "cache_hit_rate": (
                self.stats["cache_hits"] / (self.stats["cache_hits"] + self.stats["cache_misses"])
                if (self.stats["cache_hits"] + self.stats["cache_misses"]) > 0 else 0
            ) * 100,
            "error_rate": (
                self.stats["errors"] / self.stats["total_executions"]
                if self.stats["total_executions"] > 0 else 0
            ) * 100
        }
    
    def shutdown(self):
        """Останавливает и очищает пул контейнеров"""
        self._shutdown = True
        logger.info("🛑 Остановка пула контейнеров...")
        
        # Логируем финальную статистику
        stats = self.get_stats()
        logger.info(f"📊 Финальная статистика:")
        logger.info(f"   Всего выполнений: {stats['total_executions']}")
        logger.info(f"   Среднее время: {stats['avg_execution_time']:.2f}ms")
        logger.info(f"   Cache hit rate: {stats['cache_hit_rate']:.2f}%")
        logger.info(f"   Error rate: {stats['error_rate']:.2f}%")
        
        # Очищаем все контейнеры из пула
        while not self.container_pool.empty():
            try:
                container = self.container_pool.get_nowait()
                self._cleanup_container(container)
            except:
                pass
        
        # Очищаем активные контейнеры
        for container_id in list(self.active_containers.keys()):
            try:
                container = self.client.containers.get(container_id)
                self._cleanup_container(container)
            except:
                pass
        
        self.active_containers.clear()
        logger.info("✅ Пул контейнеров остановлен")


# Глобальный экземпляр пула
_executor_pool: Optional[DockerExecutorPool] = None
_pool_lock = threading.Lock()

def get_executor_pool() -> DockerExecutorPool:
    """Получает или создает глобальный экземпляр пула"""
    global _executor_pool
    
    with _pool_lock:
        if _executor_pool is None:
            pool_size = int(os.environ.get("DOCKER_POOL_SIZE", "5"))  # Увеличен с 3 до 5
            warmup = os.environ.get("DOCKER_WARMUP", "true").lower() in ("true", "1", "yes")
            _executor_pool = DockerExecutorPool(pool_size=pool_size, warmup=warmup)
            _executor_pool.initialize()
        
        return _executor_pool

