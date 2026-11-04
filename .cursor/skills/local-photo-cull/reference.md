# 照片整理管線參考

實作 `local-photo-cull` 時讀本檔。SKILL.md 的硬性約束優先於這裡的起始數字。起始數字都要能由 CLI 覆寫。

## 內嵌預覽

對每個 RAW，依序嘗試，取到非空 JPEG 位元組就停：

```
exiftool -b -PreviewImage <file>
exiftool -b -JpgFromRaw <file>
exiftool -b -ThumbnailImage <file>
```

`ThumbnailImage` 長邊小於 320 時標 `preview_tiny`，仍可做雜湊，但不做清晰度與美學分，資產進 `review`。

未安裝 exiftool：

```python
thumb = rawpy.imread(path).extract_thumb()
# thumb.format == rawpy.ThumbFormat.JPEG 才採用其 data
```

預覽寫入 `<output>/previews/<sha256>.jpg`。指標一律對這個快取檔計算，避免各階段重複抽 RAW。

JPEG 與 HEIC 沒有「內嵌預覽」步驟：解碼後長邊縮到 1600，存成快取，再算 SHA-256。

## EXIF

最少欄位：`DateTimeOriginal`、`SubSecTimeOriginal`、`Model`、`BodySerialNumber`、`ImageUniqueID`。時間缺漏時用檔案修改時間，並標 `time_unreliable`。這種資產不進連拍簇，各自成簇。

RAW+JPEG 配對：主檔名去掉副檔名後相同，且 `|t_raw - t_jpeg| <= 2` 秒。配對成功的兩筆檔案共用一個 `asset_id`。評分欄位寫在資產上。

## 雜湊與連拍簇

- `preview_sha256`：快取 JPEG 檔案位元組。
- `dhash`：`imagehash.dhash(image, hash_size=8)`，64 bit。
- 精確重複群組：同一 `preview_sha256`。保留順序：有 RAW 的資產 > 檔案較大 > 路徑字典序。其餘 `reject_exact_dup`，`survivor_asset_id` 指向留下者。精確重複不再做清晰度、美學、VLM。

連拍簇，對每個機身分組後按時間排序：

1. 第一張開新簇，並成為代表。
2. 下一張若 `dt > burst_gap_sec`（預設 2.0）或與代表的 dHash 漢明距離 `> 12`，開新簇。
3. 否則加入該簇。

`burst_gap_sec` 是 CLI 參數。連拍簇與精確重複群組是兩回事，不要合併成一種「重複」。

## 清晰度

1. 預覽長邊縮到 1024（`INTER_AREA`），轉灰階，得到 `gray`。
2. `sharp_full = cv2.Laplacian(gray, cv2.CV_64F).var()`。
3. 取 `gray` 正中央 50% 寬高（每邊去掉 25%），同一像素尺度計算 `sharp_center`。不要先把中心塊放大。
4. 若 `sharp_center >= 1.8 * sharp_full` 且 `sharp_center >= blur_floor`，標 `subject_sharp`，`sharp_used = sharp_center`。否則 `sharp_used = sharp_full`。
5. 曝光：像素值 `>= 250` 的比例為 `clip_hi`，`<= 5` 的比例為 `clip_lo`。`clip_hi > 0.25` 或 `clip_lo > 0.40` 只記旗標，不因此淘汰（日落、舞台光會觸發）。

簇內清晰度百分位只對簇大小 `>= 2` 計算：平均名次百分位 `(rank - 1) / (n - 1)`，最清晰為 1。散張沒有簇內百分位。

兩條起始線都作用在 `sharp_used`（長邊 1024 的 Laplacian variance）：

- `blur_floor` 預設 20：極端失焦。
- `sharp_ok` 預設 80：散張的技術過關線。

兩者都不是通用常數。使用者給出約 30 張已標好／壞的樣本（含一張故意淺景深）後再寫進設定。在那之前：

- `sharp_used < blur_floor` 且沒有 `subject_sharp` → `reject_blur`。`subject_sharp` 不得進 `reject_blur`。
- 簇大小 `>= 2` 且簇內 `sharp_used` 最大值仍 `< blur_floor` → 整簇標 `all_soft`，分最高者進 `review`，其餘 `reject_blur`。
- 簇內有人 `>= blur_floor` → 不要用地板淘汰其他人，改看連拍分差。

## CLIP-Aesthetic

- 影像編碼器：OpenCLIP `ViT-L-14`，預訓練 `laion2b_s32b_b82k`。
- 線性頭：LAION improved aesthetic predictor 的 ViT-L/14 MSE 權重（分數約 1～10）。權重路徑放設定檔，不寫死版本網址。
- 輸入用預覽快取，交給該模型的標準前處理。
- 裝置 `cuda`。批次 32。分數與 float16 embedding 寫入 SQLite。
- 推論結束：`del` 模型與特徵，`torch.cuda.empty_cache()`，確認沒有殘留參考再進入 VLM。
- 連拍的美學百分位與清晰度百分位相同，只在簇大小 `>= 2` 時於簇內計算。
- 散張另算 `solo_aes_pct`：這一趟全部散張的美學原始分百分位，最高為 1。連拍成員不進這個分布。
- 已知偏差：分數偏好高飽和、主體置中、接近圖庫的畫面，壓低夜景、底片顆粒、紀實與非常規構圖。連拍裡它只排次序，不用來淘汰簇內最清晰的那張。散張會用絕對分數分帶，但低分預設進 `review`，只有 `--strict-aesthetic` 才把「技術過關、美學低於 `aes_drop`」送進 `reject_aesthetic`。

VRAM 低於 12GB 時仍採預設的「先 CLIP、釋放、再 VLM」，不要並行載入。

## VLM

預設模型：Ollama `qwen2.5vl:7b`。呼叫前若 `ollama list` 沒有該模型，標 `vlm_skipped`，不下載、不讓整個 `run` 失敗。

閘門（`--vlm auto`，此為預設）：

- 每個連拍簇依當時的 composite 取第一名（美學分還沒有時用清晰度百分位）。
- 另加簇內前三名中，`|清晰度百分位 - 美學百分位| > 0.35` 者。
- 散張只在灰帶呼叫：`sharp_used` 介於 `blur_floor` 與 `sharp_ok`，或美學原始分介於 `aes_drop` 與 `aes_keep`，或技術過關但美學低於 `aes_drop`。
- 已是 `reject_exact_dup` 或 `reject_blur` 的不呼叫。散張若已由決策表命中 `keep`，預設也不呼叫。

`--vlm off` 跳過。`--vlm all` 對所有第二階段通過者呼叫。vLLM 只在使用者指定 base URL 時使用，請求體改成該服務的 vision API，提示詞不變。

提示詞要求只回 JSON 物件，不要 markdown：

```json
{
  "scene": ["portrait"],
  "description": "一句繁體中文",
  "defects": ["closed_eyes"],
  "recommend": "keep"
}
```

`scene` 短英文詞即可。`defects` 只允許：`closed_eyes`、`blink`、`motion_blur`、`out_of_focus`、`cut_off_subject`、`awkward_crop`、`blown_highlights`、`underexposed`、`distracting_object`。無法判斷就回空陣列。`recommend` 為 `keep`、`review`、`reject` 之一。解析失敗則該筆 `vlm_error`，罰分為 0，不重試超過一次。

罰分：

| 缺陷 | 罰分 |
| --- | --- |
| `closed_eyes`、`blink` | 0.50，且禁止 `keep` |
| `motion_blur`、`out_of_focus` | 0.25 |
| `cut_off_subject`、`awkward_crop` | 0.15 |
| 其餘允許值 | 0.10 |

多個缺陷取最高一項，不累加。連拍裡，硬缺陷（閉眼、眨眼）的第一名改送 `review`；若簇內還有沒有硬缺陷的成員，改由該成員爭 `keep`。散張沒有替代張，硬缺陷直接 `reject_defect`。

## 決策

先分模式。簇大小 `>= 2` 用連拍規則。簇大小為 1 用散張規則。精確重複已淘汰的成員兩套都不進。

### 連拍

```
tech = 清晰度百分位
aes  = 美學百分位   # 尚未計算時，公式改為 composite = tech
composite = 0.5 * tech + 0.5 * aes - penalty
```

- `all_soft`：最高分 → `review`，其餘 → `reject_blur`。
- 否則最高 composite 且無硬缺陷 → `keep`。
- 其餘成員：與冠軍 dHash 漢明距離 `<= 6` 且 composite 差距 `> 0.15` → `reject_near_dup`。
- composite 與冠軍差距 `<= 0.08` → `review`。
- 其他 → `reject_burst`。

### 散張

不計算 composite，依技術帶與美學原始分查表。起始值：`blur_floor = 20`、`sharp_ok = 80`、`aes_drop = 4.2`、`aes_keep = 6.0`。美學分尚未寫入時，除 `reject_blur` 外桶一律 `review`。

1. 有硬缺陷 → `reject_defect`。
2. `sharp_used < blur_floor` 且沒有 `subject_sharp` → `reject_blur`。
3. `sharp_used >= sharp_ok` 且美學分 `>= aes_keep` 且 `solo_aes_pct >= 0.25` → `keep`。
4. `sharp_used >= sharp_ok` 且美學分 `>= aes_keep` 且 `solo_aes_pct < 0.25` → `review`。
5. `sharp_used >= sharp_ok` 且美學分 `< aes_drop` → `review`；`--strict-aesthetic` 時改 `reject_aesthetic`。
6. `blur_floor <= sharp_used < sharp_ok` 且美學分 `>= aes_keep` → `review`。
7. `blur_floor <= sharp_used < sharp_ok` 且美學分 `< aes_keep` → `reject_aesthetic`；有 `subject_sharp` 則改 `review`。
8. 其餘（技術過關、美學落在兩條線之間）→ `review`。

`solo_aes_pct` 用這一批散張的平均名次百分位。散張少於 4 張時不做墊底檢查，步驟 4 視為通過。VLM 若在灰帶把 `recommend` 設為 `reject` 且缺陷非空，維持查表結果，不另開新桶；查表已是 `review` 時不升級成 `keep`。

## SQLite

主鍵用文字 UUID。時間用 ISO 8601。

`files`：`file_id`、`asset_id`、`path`、`size`、`ext`、`sha_preview`、`captured_at`、`camera`、`time_unreliable`。

`assets`：`asset_id`、`cluster_id`、`preview_path`、`preview_sha256`、`dhash`、`sharp_full`、`sharp_center`、`sharp_used`、`clip_hi`、`clip_lo`、`aesthetic`、`tech_pct`、`aes_pct`、`solo_aes_pct`、`penalty`、`composite`、`defects_json`、`scene_json`、`description`、`vlm_status`、`bucket`、`survivor_asset_id`、`flags`。`flags` 含 `subject_sharp`、`all_soft`。散張的 `tech_pct`、`aes_pct`、`composite` 留空。

`runs`：`run_id`、`started_at`、`input_dir`、`output_dir`、`params_json`。

增量：`preview_sha256` 已有清晰度、美學分或 VLM 結果就不要重算該欄。檔案路徑消失時不刪歷史列，標 `missing`。

## CLI

```
photocull run --input <dir> --output <dir> [--burst-gap 2] [--blur-floor 20] [--sharp-ok 80] [--aes-drop 4.2] [--aes-keep 6.0] [--vlm auto|off|all] [--strict-aesthetic] [--device cuda]
photocull report --output <dir>
```

終端摘要用繁體中文。路徑照使用者輸入顯示。

## 硬連結

`os.link(src, dst)`。目標已存在且指向同一檔則略過。跨磁碟失敗（Windows 錯誤 17）時不要複製 RAW，改把該筆寫入 `<output>/manifest.jsonl` 並在摘要說明有多少筆未能連結。
