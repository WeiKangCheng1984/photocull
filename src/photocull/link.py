from __future__ import annotations

import json
import os
from pathlib import Path

from photocull.models import BUCKET_DIR


def safe_unlink(path: Path) -> bool:
    """只移除仍有其他硬連結的檔，避免誤刪僅存的一份。"""
    if not path.exists():
        return True
    try:
        if path.is_dir() or path.stat().st_nlink < 2:
            return False
        path.unlink()
        return True
    except OSError:
        return False


def place_link(src: Path, dest: Path, manifest: Path) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        if dest.exists():
            if os.path.samefile(src, dest):
                return True
            if not safe_unlink(dest):
                _manifest(manifest, src, dest, "目標已存在且無法取代")
                return False
        os.link(src, dest)
        return True
    except OSError as exc:
        _manifest(manifest, src, dest, str(exc))
        return False


def _manifest(manifest: Path, src: Path, dest: Path, error: str) -> None:
    manifest.parent.mkdir(parents=True, exist_ok=True)
    row = {"src": str(src), "dest": str(dest), "error": error}
    with manifest.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def bucket_dest(output: Path, bucket: str, src: Path, input_dir: Path) -> Path:
    relative = src.resolve().relative_to(input_dir.resolve())
    return output / BUCKET_DIR[bucket] / relative
