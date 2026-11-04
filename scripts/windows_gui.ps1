# 在 Windows 開啟照片整理視窗。請先完成 pip install -e ".[gpu]"。
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Root
$env:HF_HUB_DISABLE_XET = "1"
$venv = Join-Path $Root ".venv\Scripts\pythonw.exe"
if (Test-Path -LiteralPath $venv) {
  & $venv -m photocull gui
  exit $LASTEXITCODE
}
py -3.11 -m photocull gui
