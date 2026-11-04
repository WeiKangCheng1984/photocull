#!/usr/bin/env bash
# 在 Linux 開啟照片整理視窗。請先在專案目錄完成 pip install -e ".[gpu]"。
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export HF_HUB_DISABLE_XET="${HF_HUB_DISABLE_XET:-1}"
if [[ -x "$ROOT/.venv/bin/python" ]]; then
  exec "$ROOT/.venv/bin/python" -m photocull gui
fi
if command -v python3.11 >/dev/null 2>&1; then
  exec python3.11 -m photocull gui
fi
exec python3 -m photocull gui
