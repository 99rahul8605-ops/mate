#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "$0")"
if ! command -v ffmpeg >/dev/null 2>&1; then
  echo 'ffmpeg missing; install with apt install ffmpeg'
  exit 1
fi
if [[ ! -d venv ]]; then python3 -m venv venv; fi
venv/bin/python -m pip install -r requirements.txt
set -a
source .env
set +a
exec venv/bin/uvicorn server:app --host 0.0.0.0 --port "${PORT:-8094}"
