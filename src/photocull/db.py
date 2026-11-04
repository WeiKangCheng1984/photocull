from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from photocull.models import AssetItem, FileItem


SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  input_dir TEXT NOT NULL,
  output_dir TEXT NOT NULL,
  params_json TEXT NOT NULL,
  summary_json TEXT
);

CREATE TABLE IF NOT EXISTS files (
  file_id TEXT PRIMARY KEY,
  asset_id TEXT,
  path TEXT UNIQUE NOT NULL,
  size INTEGER,
  ext TEXT,
  sha_preview TEXT,
  captured_at TEXT,
  camera TEXT,
  time_unreliable INTEGER NOT NULL DEFAULT 0,
  missing INTEGER NOT NULL DEFAULT 0,
  mtime_ns INTEGER
);

CREATE TABLE IF NOT EXISTS assets (
  asset_id TEXT PRIMARY KEY,
  cluster_id TEXT,
  preview_path TEXT,
  preview_sha256 TEXT,
  dhash TEXT,
  sharp_full REAL,
  sharp_center REAL,
  sharp_used REAL,
  clip_hi REAL,
  clip_lo REAL,
  aesthetic REAL,
  tech_pct REAL,
  aes_pct REAL,
  solo_aes_pct REAL,
  penalty REAL,
  composite REAL,
  defects_json TEXT,
  scene_json TEXT,
  description TEXT,
  vlm_status TEXT,
  bucket TEXT,
  survivor_asset_id TEXT,
  flags TEXT,
  error TEXT
);

CREATE TABLE IF NOT EXISTS metrics (
  preview_sha256 TEXT PRIMARY KEY,
  preview_path TEXT,
  dhash TEXT,
  sharp_full REAL,
  sharp_center REAL,
  clip_hi REAL,
  clip_lo REAL,
  aesthetic REAL,
  embedding BLOB,
  defects_json TEXT,
  scene_json TEXT,
  description TEXT,
  vlm_status TEXT,
  flags TEXT
);

CREATE TABLE IF NOT EXISTS links (
  file_id TEXT PRIMARY KEY,
  dest_path TEXT NOT NULL,
  bucket TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT
);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.conn: sqlite3.Connection | None = None

    def open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        if self.conn is not None:
            self.conn.close()
            self.conn = None

    def commit(self) -> None:
        assert self.conn is not None
        self.conn.commit()

    def start_run(self, run_id: str, started_at: str, input_dir: str, output_dir: str, params: dict) -> None:
        assert self.conn is not None
        self.conn.execute(
            """INSERT INTO runs (run_id, started_at, input_dir, output_dir, params_json)
               VALUES (?, ?, ?, ?, ?)""",
            (run_id, started_at, input_dir, output_dir, json.dumps(params, ensure_ascii=False)),
        )
        self.commit()

    def finish_run(self, run_id: str, finished_at: str, summary: dict) -> None:
        assert self.conn is not None
        self.conn.execute(
            "UPDATE runs SET finished_at = ?, summary_json = ? WHERE run_id = ?",
            (finished_at, json.dumps(summary, ensure_ascii=False), run_id),
        )
        self.commit()

    def set_meta(self, key: str, value: str) -> None:
        assert self.conn is not None
        self.conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    def get_meta(self, key: str) -> str | None:
        assert self.conn is not None
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return None if row is None else str(row["value"])

    def load_files(self) -> dict[str, sqlite3.Row]:
        assert self.conn is not None
        rows = self.conn.execute("SELECT * FROM files").fetchall()
        return {str(row["path"]): row for row in rows}

    def mark_missing(self, path: str) -> None:
        assert self.conn is not None
        self.conn.execute("UPDATE files SET missing = 1 WHERE path = ?", (path,))

    def save_file(self, item: FileItem, asset_id: str) -> None:
        assert self.conn is not None
        self.conn.execute(
            """INSERT INTO files (
                 file_id, asset_id, path, size, ext, sha_preview, captured_at, camera,
                 time_unreliable, missing, mtime_ns
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(file_id) DO UPDATE SET
                 asset_id = excluded.asset_id,
                 path = excluded.path,
                 size = excluded.size,
                 ext = excluded.ext,
                 sha_preview = excluded.sha_preview,
                 captured_at = excluded.captured_at,
                 camera = excluded.camera,
                 time_unreliable = excluded.time_unreliable,
                 missing = excluded.missing,
                 mtime_ns = excluded.mtime_ns
            """,
            (
                item.file_id,
                asset_id,
                str(item.path),
                item.size,
                item.ext,
                item.sha_preview,
                item.captured_at,
                item.camera,
                1 if item.time_unreliable else 0,
                1 if item.missing else 0,
                item.mtime_ns,
            ),
        )

    def save_asset(self, asset: AssetItem) -> None:
        assert self.conn is not None
        self.conn.execute(
            """INSERT INTO assets (
                 asset_id, cluster_id, preview_path, preview_sha256, dhash,
                 sharp_full, sharp_center, sharp_used, clip_hi, clip_lo, aesthetic,
                 tech_pct, aes_pct, solo_aes_pct, penalty, composite,
                 defects_json, scene_json, description, vlm_status, bucket,
                 survivor_asset_id, flags, error
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(asset_id) DO UPDATE SET
                 cluster_id = excluded.cluster_id,
                 preview_path = excluded.preview_path,
                 preview_sha256 = excluded.preview_sha256,
                 dhash = excluded.dhash,
                 sharp_full = excluded.sharp_full,
                 sharp_center = excluded.sharp_center,
                 sharp_used = excluded.sharp_used,
                 clip_hi = excluded.clip_hi,
                 clip_lo = excluded.clip_lo,
                 aesthetic = excluded.aesthetic,
                 tech_pct = excluded.tech_pct,
                 aes_pct = excluded.aes_pct,
                 solo_aes_pct = excluded.solo_aes_pct,
                 penalty = excluded.penalty,
                 composite = excluded.composite,
                 defects_json = excluded.defects_json,
                 scene_json = excluded.scene_json,
                 description = excluded.description,
                 vlm_status = excluded.vlm_status,
                 bucket = excluded.bucket,
                 survivor_asset_id = excluded.survivor_asset_id,
                 flags = excluded.flags,
                 error = excluded.error
            """,
            (
                asset.asset_id,
                asset.cluster_id,
                asset.preview_path,
                asset.preview_sha256,
                asset.dhash,
                asset.sharp_full,
                asset.sharp_center,
                asset.sharp_used,
                asset.clip_hi,
                asset.clip_lo,
                asset.aesthetic,
                asset.tech_pct,
                asset.aes_pct,
                asset.solo_aes_pct,
                asset.penalty,
                asset.composite,
                json.dumps(asset.defects, ensure_ascii=False),
                json.dumps(asset.scene, ensure_ascii=False),
                asset.description,
                asset.vlm_status,
                asset.bucket,
                asset.survivor_asset_id,
                json.dumps(sorted(asset.flags), ensure_ascii=False),
                asset.error,
            ),
        )

    def load_metrics(self) -> dict[str, sqlite3.Row]:
        assert self.conn is not None
        rows = self.conn.execute("SELECT * FROM metrics").fetchall()
        return {str(row["preview_sha256"]): row for row in rows}

    def save_metrics(
        self,
        sha: str,
        *,
        preview_path: str | None,
        dhash: str | None,
        sharp_full: float | None,
        sharp_center: float | None,
        clip_hi: float | None,
        clip_lo: float | None,
        aesthetic: float | None,
        embedding: bytes | None,
        defects: list[str] | None,
        scene: list[str] | None,
        description: str | None,
        vlm_status: str | None,
        flags: set[str],
    ) -> None:
        assert self.conn is not None
        self.conn.execute(
            """INSERT INTO metrics (
                 preview_sha256, preview_path, dhash, sharp_full, sharp_center,
                 clip_hi, clip_lo, aesthetic, embedding, defects_json, scene_json,
                 description, vlm_status, flags
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(preview_sha256) DO UPDATE SET
                 preview_path = excluded.preview_path,
                 dhash = COALESCE(excluded.dhash, metrics.dhash),
                 sharp_full = COALESCE(excluded.sharp_full, metrics.sharp_full),
                 sharp_center = COALESCE(excluded.sharp_center, metrics.sharp_center),
                 clip_hi = COALESCE(excluded.clip_hi, metrics.clip_hi),
                 clip_lo = COALESCE(excluded.clip_lo, metrics.clip_lo),
                 aesthetic = COALESCE(excluded.aesthetic, metrics.aesthetic),
                 embedding = COALESCE(excluded.embedding, metrics.embedding),
                 defects_json = COALESCE(excluded.defects_json, metrics.defects_json),
                 scene_json = COALESCE(excluded.scene_json, metrics.scene_json),
                 description = COALESCE(excluded.description, metrics.description),
                 vlm_status = COALESCE(excluded.vlm_status, metrics.vlm_status),
                 flags = excluded.flags
            """,
            (
                sha,
                preview_path,
                dhash,
                sharp_full,
                sharp_center,
                clip_hi,
                clip_lo,
                aesthetic,
                embedding,
                None if defects is None else json.dumps(defects, ensure_ascii=False),
                None if scene is None else json.dumps(scene, ensure_ascii=False),
                description,
                vlm_status,
                json.dumps(sorted(flags), ensure_ascii=False),
            ),
        )

    def load_links(self) -> dict[str, sqlite3.Row]:
        assert self.conn is not None
        rows = self.conn.execute("SELECT * FROM links").fetchall()
        return {str(row["file_id"]): row for row in rows}

    def save_link(self, file_id: str, dest: str, bucket: str) -> None:
        assert self.conn is not None
        self.conn.execute(
            """INSERT INTO links (file_id, dest_path, bucket) VALUES (?, ?, ?)
               ON CONFLICT(file_id) DO UPDATE SET dest_path = excluded.dest_path, bucket = excluded.bucket""",
            (file_id, dest, bucket),
        )

    def delete_link(self, file_id: str) -> None:
        assert self.conn is not None
        self.conn.execute("DELETE FROM links WHERE file_id = ?", (file_id,))

    def file_id_for_path(self, path: str) -> str | None:
        assert self.conn is not None
        row = self.conn.execute("SELECT file_id FROM files WHERE path = ?", (path,)).fetchone()
        return None if row is None else str(row["file_id"])
