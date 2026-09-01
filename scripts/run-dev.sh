#!/usr/bin/env bash
set -euo pipefail
export AI_PROVIDER="${AI_PROVIDER:-mock}"
export OLLAMA_MODEL="${OLLAMA_MODEL:-evoharness-alert-qwen2.5-7b:latest}"
export DATABASE_URL="${DATABASE_URL:-mysql+pymysql://evoalert:evoalert@127.0.0.1:13306/evoharness_alert?charset=utf8mb4}"
export REDIS_URL="${REDIS_URL:-redis://127.0.0.1:16379/0}"
echo "使用 MySQL: ${DATABASE_URL}"
echo "使用 Redis: ${REDIS_URL}"
uvicorn app.main:app --host "${SERVER_HOST:-127.0.0.1}" --port "${SERVER_PORT:-8080}" --reload
