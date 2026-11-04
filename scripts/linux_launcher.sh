#!/usr/bin/env bash
# 在 Linux 上開啟照片整理視窗。請在專案目錄執行：bash scripts/linux_launcher.sh
cd "$(dirname "$0")/.." || exit 1
export HF_HUB_DISABLE_XET=1
if command -v python3 >/dev/null 2>&1; then
  exec python3 -m photocull gui
fi
echo "找不到 python3。請先安裝 Python 3.11。" >&2
exit 1
