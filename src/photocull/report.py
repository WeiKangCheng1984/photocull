from __future__ import annotations

import json
from pathlib import Path

from photocull.db import Database
from photocull.models import BUCKET_LABEL


def render(db: Database) -> str:
    assert db.conn is not None
    notes = json.loads(db.get_meta("notes") or "[]")
    link_failures = int(db.get_meta("link_failures") or "0")
    input_dir = db.get_meta("input_display") or ""
    output_dir = db.get_meta("output_display") or ""
    active = "asset_id IN (SELECT asset_id FROM files WHERE missing = 0)"
    files = db.conn.execute("SELECT COUNT(*) AS n FROM files WHERE missing = 0").fetchone()["n"]
    assets = db.conn.execute(f"SELECT COUNT(DISTINCT asset_id) AS n FROM files WHERE missing = 0").fetchone()["n"]
    exact = db.conn.execute(
        f"SELECT COUNT(*) AS n FROM assets WHERE bucket = 'reject_exact_dup' AND {active}"
    ).fetchone()["n"]
    bursts = db.conn.execute(
        f"""SELECT COUNT(*) AS n FROM (
              SELECT cluster_id FROM assets
              WHERE cluster_id IS NOT NULL AND {active}
              GROUP BY cluster_id HAVING COUNT(*) >= 2
            )"""
    ).fetchone()["n"]
    solos = db.conn.execute(
        f"""SELECT COUNT(*) AS n FROM (
              SELECT cluster_id FROM assets
              WHERE cluster_id IS NOT NULL AND {active}
              GROUP BY cluster_id HAVING COUNT(*) = 1
            )"""
    ).fetchone()["n"]
    buckets = {
        row["bucket"]: row["n"]
        for row in db.conn.execute(
            """SELECT links.bucket AS bucket, COUNT(*) AS n
               FROM links JOIN files ON files.file_id = links.file_id
               WHERE files.missing = 0 GROUP BY links.bucket"""
        )
    }
    vlm_called = db.conn.execute(
        f"SELECT COUNT(*) AS n FROM assets WHERE vlm_status IN ('ok', 'vlm_error') AND {active}"
    ).fetchone()["n"]
    lines = [
        f"輸入：{input_dir}",
        f"輸出：{output_dir}",
        f"掃描檔案：{files}",
        f"資產：{assets}",
        f"精確重複：{exact}",
        f"連拍簇：{bursts}",
        f"散張：{solos}",
        "分桶（硬連結檔案數）：",
    ]
    for bucket, label in BUCKET_LABEL.items():
        lines.append(f"  {label}：{buckets.get(bucket, 0)}")
    lines.append(f"VLM 實際呼叫：{vlm_called}")
    lines.append(f"硬連結失敗：{link_failures}")
    if link_failures:
        lines.append(f"未能連結的清單：{Path(output_dir) / 'manifest.jsonl'}")
    for note in notes:
        lines.append(f"說明：{note}")
    return "\n".join(lines) + "\n"
