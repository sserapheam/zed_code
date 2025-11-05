# Инструкция для Windows

## Вариант 1: Установить Docker Desktop (для локальной разработки)

1. Скачайте Docker Desktop для Windows: https://www.docker.com/products/docker-desktop/
2. Установите и запустите Docker Desktop
3. Откройте PowerShell в папке `docker/executor`
4. Выполните команду:

```powershell
docker build -t zedcode-python:latest .
```

Или используйте готовый скрипт:
```powershell
.\build.bat
```

## Вариант 2: Собрать на сервере (рекомендуется для production)

Если у вас есть Linux сервер (VPS), выполните там:

```bash
# На Linux сервере
cd docker/executor
chmod +x build.sh
./build.sh
```

## Вариант 3: Использовать Docker Hub

Если у вас есть Docker Hub аккаунт, можно собрать образ и загрузить:

```bash
# На Linux сервере или в CI/CD
docker build -t ваш-username/zedcode-python:latest .
docker push ваш-username/zedcode-python:latest
```

Затем на любом сервере:
```bash
docker pull ваш-username/zedcode-python:latest
```

## Проверка наличия Docker

Проверьте, установлен ли Docker:

```powershell
docker --version
```

Если команда не найдена, установите Docker Desktop.

## Важно

Для production использования Docker образ нужно собирать на Linux сервере, так как:
- Docker Desktop на Windows использует виртуализацию (медленнее)
- На сервере обычно уже установлен Docker Engine
- Проще управлять и масштабировать

