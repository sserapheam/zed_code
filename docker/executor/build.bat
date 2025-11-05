@echo off
REM Скрипт для сборки Docker образа для выполнения кода (Windows)
echo Сборка Docker образа zedcode-python:latest...

docker build -t zedcode-python:latest .

if %ERRORLEVEL% EQU 0 (
    echo Образ zedcode-python:latest успешно собран
) else (
    echo Ошибка при сборке образа
    exit /b 1
)

