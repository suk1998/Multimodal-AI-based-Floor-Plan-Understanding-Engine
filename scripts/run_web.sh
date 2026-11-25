#!/usr/bin/env bash
set -euo pipefail
umask 077
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd -- "$project_root"
npm --prefix frontend run build
exec backend/.venv/bin/python -B -m uvicorn backend.public_app:create_app --factory --host 127.0.0.1 --port 8020 --workers 1 --proxy-headers --forwarded-allow-ips 127.0.0.1
