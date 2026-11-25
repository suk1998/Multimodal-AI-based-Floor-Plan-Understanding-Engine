#!/usr/bin/env bash
set -euo pipefail
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"
exec "$project_root/backend/.venv/bin/python" -B -m uvicorn backend.app:app --host 127.0.0.1 --port 8010 "$@"
