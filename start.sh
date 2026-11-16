#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "$0")"
if ! command -v ffmpeg >/dev/null; then echo 'ffmpeg missing; sudo apt install ffmpeg'; exit 1; fi
if [[ ! -d venv ]]; then python3 -m venv venv; fi
venv/bin/pip install -r requirements.txt
set -a; source .env; set +a
exec venv/bin/uvicorn server:app --host 127.0.0.1 --port "${PORT:-8094}"
