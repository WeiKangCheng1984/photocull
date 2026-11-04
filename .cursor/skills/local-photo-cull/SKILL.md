---
name: local-photo-cull
description: >-
  設計並實作本機照片整理管線：掃描 RAW/JPEG 資料夾，以 CPU 抽取內嵌預覽、雜湊去重、OpenCV
  清晰度快篩，再以 GPU 的 CLIP-Aesthetic 做 1～10 美學評分，並用本地 VLM（Qwen2.5-VL，Ollama
  或 vLLM）產生場景標籤與缺陷。連拍在簇內選代表；間隔較長的散張隨手拍改用絕對清晰度、絕對美學分
  與本批百分位分出保留、覆核、廢片。Use when building, extending, or running this photo culling
  tool, or when the user mentions 照片整理、廢片、散張、隨手拍、RAW、連拍去重、清晰度、美學評分、
  CLIP、Qwen2.5-VL、Ollama.
---

# 本機照片整理

把指定資料夾裡未整理的 RAW / JPEG，在本機 CPU 與 GPU 上分成保留、覆核、廢片。原檔不刪、不改、不搬移。

使用者給的兩段流程是骨幹。實作時補上第零階段（盤點與配對）與第三階段（簇內決策），否則去重和評分沒有歸屬單位。

```
[ 原始照片資料夾 (RAW / JPEG) ]
              │
              ▼
    【第零階段：盤點與配對 (CPU)】
    - 列檔、讀 EXIF 時間與機身、RAW+JPEG 成對
              │
              ▼
    【第一階段：極速快篩 (CPU / 輕量運算)】
    - 抽取 RAW 內嵌預覽圖 (無需完整解碼 RAW)
    - 雜湊比對（去除連拍完全重複項）
    - OpenCV 邊緣清晰度檢測（抓出脫焦、手震廢片）
              │
              ▼ (通過清晰度門檻者)
    【第二階段：GPU 美學評分與視覺語意分析】
    - CLIP-Aesthetic 模型：輸出 1～10 分的美學構圖評分
    - 本地 VLM (如 Qwen2.5-VL-7B via Ollama / vLLM)：產生分類標籤與場景描述
              │
              ▼
    【第三階段：分級】
    - 連拍簇：簇內綜合分選一張代表
    - 散張：用絕對清晰度、絕對美學分、本批百分位分好壞
    - 硬連結到 keep / review / reject，寫入 SQLite
```

細節演算法、資料表、VLM 提示詞見 [reference.md](reference.md)。實作前先讀該檔。

## 硬性約束

1. 不刪除、不覆寫、不搬移原檔。分桶只建硬連結；硬連結失敗才改寫清單，不可改複製整份 RAW。
2. 第一階段禁止完整解碼 RAW（禁止 demosaic）。只用內嵌預覽。沒有預覽就標 `preview_missing` 送覆核，不可改走全解碼。
3. 兩張以上的連拍簇只在簇內排名。簇大小為 1 的散張禁止使用簇內百分位（那會恆等於 1），改走下方「散張」絕對分級。
4. 同一快門的 RAW+JPEG 是一筆資產，只評一次分，決定套用到兩個檔。
5. VLM 預設不跑全庫。連拍只跑領先候選與互相矛盾的前幾名；散張只跑好壞不明顯的灰帶。
6. CLIP 與 VLM 不要同時佔 VRAM。CLIP 批次結束後釋放模型，再呼叫 Ollama。
7. 每步結果寫入 SQLite。中斷後重跑必須跳過已完成且預覽雜湊未變的項目。

## 預設選型

只在使用者明示時偏離預設。

| 工作 | 預設 | 改道條件 |
| --- | --- | --- |
| 語言與路徑 | Python 3.11+、`pathlib` | — |
| 內嵌預覽 | `exiftool -b -PreviewImage`，沒有再 `-JpgFromRaw`，再 `-ThumbnailImage` | 未安裝 exiftool 時改 `rawpy.extract_thumb()` |
| JPEG / HEIC | 直接解碼，長邊縮到 1600 再算指標 | HEIC 用 `pillow-heif` |
| 精確重複 | 預覽位元組 SHA-256 | — |
| 近似重複與連拍 | `imagehash.dhash`（64-bit） | — |
| 清晰度 | OpenCV 灰階 Laplacian variance，長邊先縮到 1024；另算中心 50% | — |
| 美學分 | OpenCLIP ViT-L/14（LAION）+ aesthetic 線性頭，輸出約 1～10 | 權重檔路徑由設定指定 |
| VLM | Ollama 的 `qwen2.5vl:7b` | 使用者要求批次吞吐時改 vLLM |
| 狀態 | `<output>/catalog.sqlite` | — |
| 分桶 | 硬連結（`os.link`） | — |

支援副檔名：`jpg jpeg heic cr2 cr3 nef arw raf dng orf rw2 pef srw`。大小寫不拘。

## 模組與目錄

```
src/photocull/
  cli.py              # run、report、gui
  db.py               # SQLite schema 與增量查詢
  ingest.py           # 第零階段：列檔、EXIF、RAW+JPEG 配對
  preview.py          # 內嵌預覽抽取與快取
  cluster.py          # 精確重複、連拍簇
  sharpness.py        # 全圖與中心 Laplacian、曝光裁切比例
  aesthetic.py        # CLIP-Aesthetic，跑完即釋放
  vlm.py              # Ollama / vLLM，有閘門
  decide.py           # 連拍簇與散張兩套分桶
  link.py             # 硬連結
  gui.py              # 共用視窗
  platform.py         # 依 OS 轉呼叫
  platform_windows.py # Windows 連選與開啟資料夾
  platform_linux.py   # Linux zenity 連選與 xdg-open
```

使用說明見專案根目錄 `docs/說明書.md`。Windows 與 Linux 共用整理流程，只拆開介面適配。

預覽快取放在 `<output>/previews/`，檔名用預覽 SHA-256，不放回原始資料夾。

## 實作順序

依序做完，每步可單獨重跑。不要先接 GPU。

```
進度：
- [ ] 1. SQLite 與 cli run/report 骨架
- [ ] 2. 第零階段：列檔、EXIF、RAW+JPEG 配對
- [ ] 3. 內嵌預覽抽取與 SHA-256
- [ ] 4. 精確重複與連拍簇
- [ ] 5. 全圖與中心清晰度、極端失焦標記
- [ ] 6. 只用 CPU 訊號的暫時分桶。散張除極端失焦外先放 review，等美學分再改桶
- [ ] 7. CLIP-Aesthetic，只評尚未被精確重複或極端失焦淘汰者
- [ ] 8. VLM 閘門
- [ ] 9. 最終分桶硬連結與中文摘要
```

第 6 步就要能產出 `keep / review / reject`。GPU 步驟是加分，不是管線能否跑完的前提。Ollama 沒裝或模型不在時，標 `vlm_skipped` 並繼續，用清晰度與美學分做決定。

## 三階段怎麼接

**第零階段。** 遞迴掃描輸入資料夾。每檔記錄路徑、大小、副檔名、`DateTimeOriginal`、機身序號（沒有則用廠牌+型號）。檔名主檔名相同且拍攝時間差在 2 秒內的 RAW 與 JPEG 合成一筆資產。

**第一階段。** 只對資產的預覽做事。

- 預覽 SHA-256 相同 → 精確重複。留下 RAW（沒有 RAW 則留較大檔），其餘標 `reject_exact_dup`，指向留下的那筆。
- 同一機身、拍攝時間排序。與當前連拍簇代表的時間差大於 `burst_gap_sec`（預設 2.0）或 dHash 漢明距離大於 12，就開新簇。
- Laplacian 在長邊 1024 的灰階預覽上計算，並另算中心 50% 區域。中心明顯高於全圖時標 `subject_sharp`（主體清楚、背景虛化），技術分改用中心值。
- 連拍簇內用百分位。散張不用百分位。失焦地板與 `sharp_ok` 見 reference.md。整簇都糊時，分最高的那張送 `review`。

通過者：不是精確重複，且不是極端失焦。這些才進入第二階段。散張的極端失焦在這一階就進 `reject_blur`。

**第二階段。**

- CLIP-Aesthetic 對通過者批次推論（預設 batch 32）。分數與 embedding 寫入 SQLite。
- VLM 閘門有四種範圍。`fast` 只看準備留下的散張，以及連拍第一名和簇內前三名裡清晰度與美學百分位差距超過 0.35 的照片，並把 Ollama 上下文收到 8192。`auto` 仍會多看散張灰帶。`all` 看全部通過者。`off` 全跳過。視窗預設四項都開，視覺語意用 `fast`。
- VLM 回傳場景標籤、一句描述、缺陷清單。`closed_eyes` 與 `blink` 是硬缺陷，美學分再高也不得進 `keep`。連拍改由簇內其他張爭代表；散張進 `reject_defect`。
- VLM 的 keep/reject 建議只當參考，不覆蓋數值排名。

**第三階段，先看簇大小。** 兩張以上走連拍。只有一張走散張。兩套規則不要混用。

連拍簇內：

```
composite = 0.5 * 清晰度百分位 + 0.5 * 美學百分位 - 缺陷罰分
```

美學分尚未計算時，清晰度百分位權重改為 1。第一名進 `keep`（除非整簇都糊或有硬缺陷）。其餘依與第一名的分差與漢明距離分到 `reject_near_dup`、`reject_burst` 或 `review`。

散張（拍攝間隔超過連拍窗、或畫面差異大，因而自己一簇）用絕對分數，門檻都能由 CLI 覆寫：

| 技術 `sharp_used` | 美學原始分 | 桶 |
| --- | --- | --- |
| `< blur_floor`（20），且沒有 `subject_sharp` | 任何 | `reject_blur` |
| `>= sharp_ok`（80） | `>= aes_keep`（6.0），且本批散張美學百分位 `>= 0.25` | `keep` |
| `>= sharp_ok` | `>= aes_keep`，但本批百分位 `< 0.25` | `review` |
| `>= sharp_ok` | `< aes_drop`（4.2） | `review`；`--strict-aesthetic` 時改 `reject_aesthetic` |
| 介於地板與 `sharp_ok` | `>= aes_keep` | `review` |
| 介於地板與 `sharp_ok` | `< aes_keep` | `reject_aesthetic`；有 `subject_sharp` 則改 `review` |
| `>= sharp_ok` | 介於 `aes_drop` 與 `aes_keep` | `review`（灰帶，預設送 VLM） |

本批百分位只在這一趟的散張之間計算，用來抓住「絕對分數看起來還行、但在這疊照片裡墊底」的張。硬缺陷（閉眼、眨眼）的散張改 `reject_defect`，因為沒有連拍替代張。美學分還沒算出時，散張除 `reject_blur` 外全部留在 `review`。

## 輸出

```
<output>/
  catalog.sqlite
  previews/
  keep/
  review/
  reject/blur/
  reject/aesthetic/
  reject/defect/
  reject/exact_dup/
  reject/near_dup/
  reject/burst/
```

`report` 用繁體中文印出：掃描張數、資產數、精確重複、連拍簇數、各桶數量、VLM 實際呼叫次數、跳過原因。

## 驗收

用暫時複製的小樣本測，不要對使用者的正式照片庫做第一次試驗。

- 兩張位元組相同的 JPEG：一張 `keep`，一張 `reject_exact_dup`。
- 五張連拍只有一張清晰：清晰的那張 `keep`，其餘不進 `keep`。
- 三張彼此相隔數分鐘、畫面不同的散張：糊的進 `reject_blur`，清晰且美學分高的進 `keep`，清晰但美學分低的不進 `keep`。
- 全圖 Laplacian 低、中心區域高的淺景深：不得進 `reject_blur`。
- 沒有內嵌預覽的 RAW：進 `review`，行程不中斷，且沒有呼叫全解碼。
- 原資料夾檔案數量與路徑在執行前後不變。
- 同一輸入重跑：不重複推論，硬連結不衝突。

## 不要做

- 不要用單一全域 Laplacian 門檻一次砍掉夜景、人像、長焦。散張的 `sharp_ok` 只決定技術帶，灰帶進覆核。
- 不要因為散張落在連拍時間窗之外就自動 `keep`。
- 不要把連拍近重複直接刪到只剩檔案雜湊相同者；連拍要先評分再留代表。
- 不要讓 VLM 單獨決定好壞。
- 不要在第一版做星級寫回 XMP、上傳雲端、或自動刪檔。
