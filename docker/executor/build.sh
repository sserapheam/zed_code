#!/bin/bash
# Скрипт для сборки Docker образа для выполнения кода

docker build -t zedcode-python:latest .
echo "Образ zedcode-python:latest успешно собран"

