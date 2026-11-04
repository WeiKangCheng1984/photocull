# 本機照片整理

把一個資料夾裡沒整理的 RAW / JPEG，分成保留、覆核、廢片。程式只在結果資料夾建立硬連結，**不會刪除、修改或搬移原始照片**。

完整說明（架構、概念、Windows / Linux 環境與操作）見 [docs/說明書.md](docs/說明書.md)。

## 版本怎麼分

同一套原始碼。整理流程共用；介面差異在：

- `src/photocull/platform_windows.py`：Windows 連選資料夾、開啟資料夾
- `src/photocull/platform_linux.py`：Linux zenity 連選、`xdg-open`

| | Windows | Linux |
| --- | --- | --- |
| 啟動視窗 | 雙擊 `照片整理.exe`，或 `scripts\windows_gui.ps1` | `./scripts/linux_launch.sh` |
| 硬連結檔案系統 | NTFS | ext4 / XFS / Btrfs |
| 批次連選 | 系統對話框 | zenity（建議安裝） |

## 快速安裝

### Windows

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
pip install -e ".[gpu]"
```

### Linux

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e .
pip install -e ".[gpu]"
# 建議：sudo apt install zenity fonts-noto-cjk exiftool
```

顯示卡、CUDA、Ollama、美學權重、ExifTool **怎麼安裝、怎麼掛進本程式**（含檢查指令）見 [docs/說明書.md 第 3 節](docs/說明書.md)。摘要：驅動給 PyTorch 用；Ollama 開在本機 `11434` 即可被自動連上；權重放 `models/`。

## 先用一小疊複本試跑

結果資料夾要與原檔**同一磁碟**，且不要放在輸入資料夾裡面。

```powershell
# Windows
py -3.11 -m photocull run --input "D:\照片樣本" --output "D:\照片樣本_整理" --vlm fast
```

```bash
# Linux
python -m photocull run --input "/照片/樣本" --output "/照片/樣本_整理" --vlm fast
```

視窗：

```powershell
py -3.11 -m photocull gui
```

```bash
./scripts/linux_launch.sh
```

## 結果怎麼看

| 位置 | 意思 |
| --- | --- |
| `keep` | 建議留下 |
| `review` | 請人看 |
| `reject\...` | 建議不用（失焦、美學、缺陷、重複、連拍落選） |
| `catalog.sqlite` | 分數紀錄，重跑會沿用 |
| `manifest.jsonl` | 硬連結失敗清單 |

刪掉結果資料夾裡的連結，原始照片還在。細節與門檻說明見 [docs/說明書.md](docs/說明書.md)。
