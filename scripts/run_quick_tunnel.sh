#!/usr/bin/env bash
set -euo pipefail
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd -- "$project_root"
cloudflared_bin="${FPUE_CLOUDFLARED_BIN:-$HOME/.local/bin/cloudflared}"
backend/.venv/bin/python -B - <<'PY'
import json,urllib.request
with urllib.request.urlopen('http://127.0.0.1:8020/api/health',timeout=5) as response:data=json.load(response)
assert data.get('mode')=='public_pdf_workspace' and data.get('capabilities',{}).get('pdf_upload') is True and data.get('server_GT_storage') is False,'Start the FPUE PDF upload server first; refusing to tunnel another API.'
PY
if pgrep -f '[c]loudflared tunnel.*--url http://127.0.0.1:8020' >/dev/null; then
  echo '기존 FPUE Quick Tunnel이 실행 중입니다. 기존 프로세스와 URL을 먼저 확인하세요.' >&2
  exit 1
fi
exec "$cloudflared_bin" tunnel --url http://127.0.0.1:8020
