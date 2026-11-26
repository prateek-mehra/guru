#!/bin/bash
# One-time setup + run. Needs SARVAM_API_KEY in .env (see .env.example).
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt
echo "Carpathy chal raha hai: http://localhost:8765"
exec .venv/bin/uvicorn server:app --port 8765
