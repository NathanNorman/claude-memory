#!/usr/bin/env bash
# Start the webhook auto-indexing server.
#
# Runs FastAPI + background index worker in a single process.
# The webhook receiver listens for GitHub push events and the worker
# thread processes index jobs from the SQLite queue.
#
# Configuration (env vars):
#   WEBHOOK_PORT     — Port to listen on (default: 8787)
#   WEBHOOK_HOST     — Host to bind to (default: 0.0.0.0)
#   WEBHOOK_SECRET   — GitHub webhook secret (fallback; prefer Keychain)
#   MIRROR_DIR       — Directory for bare git mirrors (default: ~/.claude-memory/mirrors/)
#   WORKER_POLL_INTERVAL — Seconds between worker polls (default: 5)
#   MEMORY_EMBEDDING_MODEL — Embedding model name (default: bge-base-en-v1.5)
#
# The webhook secret is read from macOS Keychain (service: webhook-github-secret)
# and falls back to the WEBHOOK_SECRET env var.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

PYTHON="${HOME}/.claude-memory/graphiti-venv/bin/python3"
PORT="${WEBHOOK_PORT:-8787}"
HOST="${WEBHOOK_HOST:-0.0.0.0}"

if [ ! -f "$PYTHON" ]; then
    echo "Error: Python venv not found at $PYTHON" >&2
    echo "Expected the graphiti-venv at ~/.claude-memory/graphiti-venv/" >&2
    exit 1
fi

echo "[webhook-server] Starting on ${HOST}:${PORT}" >&2
exec "$PYTHON" -m uvicorn \
    webhook_server:app \
    --host "$HOST" \
    --port "$PORT" \
    --app-dir "$PROJECT_DIR/src" \
    --log-level info
