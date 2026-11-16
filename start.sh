#!/usr/bin/env bash
# Mate API: one-command launcher, use `bash start.sh`.
set -Eeuo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
DIR="$(pwd -P)"
SCREEN="mate-api"
LOG="$DIR/server.log"

info() { printf '%s\n' "$*"; }
fail() { printf '❌ %s\n' "$*" >&2; exit 1; }
have_screen() { screen -ls 2>/dev/null | grep -Eq "[[:digit:]]+\.${SCREEN}[[:space:]]"; }

# Only launched in the detached screen; no git/pip/restart recursion.
if [[ "${1:-}" == "--serve" ]]; then
  [[ -f .env ]] || fail "Missing .env"
  set -a
  source .env
  set +a
  exec "$DIR/venv/bin/uvicorn" server:app --host 0.0.0.0 --port "${PORT:-8094}"
fi

# Explicit restart: stop ONLY the previous mate-api screen and launch a new one.
# Normal invocation remains idempotent and does not interrupt active uploads.
case "${1:-}" in
  "") ;;
  restart) FORCE_RESTART=1 ;;
  *) fail "Usage: bash start.sh [restart]" ;;
esac

for cmd in screen git curl python3 ffmpeg ffprobe; do
  command -v "$cmd" >/dev/null 2>&1 || fail "Missing $cmd. Install it first."
done
[[ -f .env ]] || fail "Missing $DIR/.env"

# Fast: don't touch a running, healthy API unless user explicitly requested restart.
# This prevents an accidental bot/session collision or interrupted Telegram upload.
if [[ ! -x venv/bin/python ]]; then
  info "📦 Creating virtual environment..."
  python3 -m venv venv || fail "Could not create venv (install python3-venv)"
fi
PORT="$(venv/bin/python - <<'PY'
try:
    from dotenv import dotenv_values
    value = dotenv_values('.env').get('PORT')
    port = int(value or 8094)
    print(port if 1 <= port <= 65535 else 8094)
except Exception:
    print(8094)
PY
)"
healthy() { curl -fsS --max-time 3 "http://127.0.0.1:${PORT}/health" | grep -Eq '"ok"[[:space:]]*:[[:space:]]*true'; }

if have_screen && healthy && [[ "${FORCE_RESTART:-0}" != 1 ]]; then
  info "✅ mate-api already running in screen on port $PORT. No restart needed."
  info "   Logs: tail -f $LOG"
  info "   To update/restart later: bash start.sh restart (interrupts active uploads)"
  exit 0
fi
if ! have_screen && healthy; then
  fail "API already running outside mate-api screen. Stop that foreground process first (Ctrl+C); refusing duplicate Pyrogram session."
fi

if [[ "${1:-}" == "restart" ]]; then
  info "♻️ Restart requested: old mate-api screen will be replaced (active uploads interrupted)."
fi
info "🔄 Pulling GitHub updates..."
git pull --ff-only origin main || fail "Git pull failed. Resolve local git changes first."
info "📦 Installing/checking dependencies..."
venv/bin/python -m pip install -q -r requirements.txt || fail "pip install failed"
venv/bin/python -m py_compile server.py browser_upload.py video_metadata.py || fail "Python syntax check failed"

if have_screen; then
  info "♻️ Stopping old mate-api screen..."
  screen -S "$SCREEN" -X quit || true
  for _ in $(seq 1 15); do
    have_screen || break
    sleep 1
  done
  have_screen && fail "Old mate-api screen still present. Refusing duplicate session."
fi
# A separate foreground process could still hold the port/session.
if healthy; then
  fail "API remains live outside mate-api screen. Stop it first to avoid session lock."
fi

info "🚀 Starting mate-api in detached screen..."
screen -dmS "$SCREEN" bash -c 'cd "$1" && exec bash start.sh --serve >> server.log 2>&1' _ "$DIR"
for _ in $(seq 1 30); do
  if healthy; then
    info "✅ API + Telegram bot + upload queue running. Port: $PORT"
    info "📺 Screen: screen -r $SCREEN"
    info "📋 Logs: tail -f $LOG"
    exit 0
  fi
  if ! have_screen; then
    tail -n 30 "$LOG" >&2 || true
    fail "mate-api exited before becoming ready."
  fi
  sleep 1
done
tail -n 30 "$LOG" >&2 || true
fail "Server did not become healthy; see $LOG"
