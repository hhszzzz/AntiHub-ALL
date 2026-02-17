#!/bin/sh
set -e

cd /app

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8045}"

exec uvicorn app.main:app --host "$HOST" --port "$PORT"

