#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/zed_code"
BRANCH="${1:-feature}"

cd "$APP_DIR"

echo "==> Deploy branch: $BRANCH"
git fetch origin
git checkout "$BRANCH"
git reset --hard "origin/$BRANCH"

# Сохраняем локальный .env (он в .gitignore)
if [ ! -f .env ]; then
  echo "ERROR: /opt/zed_code/.env missing"
  exit 1
fi

echo "==> Rebuild executor image"
docker build -f docker/executor/Dockerfile -t zedcode-python:latest .

echo "==> Restart stack"
docker compose up -d --build

# Nginx кэширует IP upstream при старте — перезапускаем после web
echo "==> Restart nginx (refresh upstream DNS)"
docker compose restart nginx

echo "==> Status"
docker compose ps
echo "Deploy OK"
