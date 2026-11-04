from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

NS = uuid.UUID("6f9c2a4e-1b7d-4e5a-9c31-0a6d8e2b7f10")

RAW_EXTS = {
    ".cr2", ".cr3", ".nef", ".arw", ".raf", ".dng", ".orf", ".rw2", ".pef", ".srw",
}
RASTER_EXTS = {".jpg", ".jpeg", ".heic"}
ALL_EXTS = RAW_EXTS | RASTER_EXTS

BUCKET_DIR = {
    "keep": "keep",
    "review": "review",
    "reject_blur": "reject/blur",
    "reject_aesthetic": "reject/aesthetic",
    "reject_defect": "reject/defect",
    "reject_exact_dup": "reject/exact_dup",
    "reject_near_dup": "reject/near_dup",
    "reject_burst": "reject/burst",
}

BUCKET_LABEL = {
    "keep": "保留（keep）",
    "review": "覆核（review）",
    "reject_blur": "失焦（reject/blur）",
    "reject_aesthetic": "美學偏低（reject/aesthetic）",
    "reject_defect": "缺陷（reject/defect）",
    "reject_exact_dup": "完全重複（reject/exact_dup）",
    "reject_near_dup": "近重複（reject/near_dup）",
    "reject_burst": "連拍落選（reject/burst）",
}


def file_id_for(path: Path) -> str:
    return str(uuid.uuid5(NS, "file:" + str(path.resolve())))


def asset_id_for(paths: list[Path]) -> str:
    key = "|".join(sorted(str(p.resolve()) for p in paths))
    return str(uuid.uuid5(NS, "asset:" + key))


@dataclass
class FileItem:
    file_id: str
    path: Path
    size: int = 0
    ext: str = ".jpg"
    mtime_ns: int = 0
    captured_at: str | None = None
    captured_dt: datetime | None = None
    camera: str = "unknown"
    time_unreliable: bool = False
    is_raw: bool = False
    sha_preview: str | None = None
    missing: bool = False


@dataclass
class AssetItem:
    asset_id: str
    files: list[FileItem]
    cluster_id: str | None = None
    preview_path: str | None = None
    preview_sha256: str | None = None
    dhash: str | None = None
    sharp_full: float | None = None
    sharp_center: float | None = None
    sharp_used: float | None = None
    clip_hi: float | None = None
    clip_lo: float | None = None
    aesthetic: float | None = None
    tech_pct: float | None = None
    aes_pct: float | None = None
    solo_aes_pct: float | None = None
    penalty: float | None = None
    composite: float | None = None
    defects: list[str] = field(default_factory=list)
    scene: list[str] = field(default_factory=list)
    description: str | None = None
    vlm_status: str | None = None
    bucket: str | None = None
    survivor_asset_id: str | None = None
    flags: set[str] = field(default_factory=set)
    error: str | None = None
    eliminated: bool = False

    @property
    def primary_path(self) -> str:
        return str(sorted((f.path for f in self.files), key=lambda p: str(p).casefold())[0])

    @property
    def has_raw(self) -> bool:
        return any(f.is_raw for f in self.files)

    @property
    def max_size(self) -> int:
        return max((f.size for f in self.files), default=0)

    @property
    def camera(self) -> str:
        for f in self.files:
            if f.camera and f.camera != "unknown":
                return f.camera
        return "unknown"

    @property
    def captured_dt(self) -> datetime | None:
        reliable = [f.captured_dt for f in self.files if f.captured_dt and not f.time_unreliable]
        if reliable:
            return min(reliable)
        any_dt = [f.captured_dt for f in self.files if f.captured_dt]
        return min(any_dt) if any_dt else None

    @property
    def time_unreliable(self) -> bool:
        return not any(f.captured_dt and not f.time_unreliable for f in self.files)

    def active_files(self) -> list[FileItem]:
        return [f for f in self.files if not f.missing]
