from __future__ import annotations

import ctypes
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from photocull.aesthetic import resolve_weights_path
from photocull.models import ALL_EXTS
from photocull.platform import supports_hardlinks, volume_fs_name
from photocull.vlm import VlmClient

OK = "ok"
WARN = "warn"
BLOCK = "block"
GIB = 1024**3


@dataclass(frozen=True)
class Check:
    key: str
    title: str
    level: str
    detail: str


def format_gib(num_bytes: int) -> str:
    return f"{num_bytes / GIB:.1f} GB"


def memory_bytes() -> tuple[int, int]:
    if os.name != "nt":
        total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        avail = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_AVPHYS_PAGES")
        return int(total), int(avail)

    class MemoryStatus(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    status = MemoryStatus()
    status.dwLength = ctypes.sizeof(status)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        raise OSError("無法讀取記憶體資訊")
    return int(status.ullTotalPhys), int(status.ullAvailPhys)


def existing_ancestor(path: Path) -> Path:
    current = path
    while not current.exists():
        parent = current.parent
        if parent == current:
            return current
        current = parent
    return current


def same_disk(left: Path, right: Path) -> bool:
    a = existing_ancestor(left)
    b = existing_ancestor(right)
    try:
        return a.stat().st_dev == b.stat().st_dev
    except OSError:
        return a.anchor.casefold() == b.anchor.casefold() and bool(a.anchor)


def count_entries(root: Path) -> tuple[int, int]:
    photos = 0
    skipped = 0
    if not root.is_dir():
        return 0, 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if not name.startswith(".")]
        for name in filenames:
            if name.startswith("."):
                continue
            suffix = Path(name).suffix.casefold()
            if suffix in ALL_EXTS:
                photos += 1
            elif suffix:
                skipped += 1
    return photos, skipped


def default_output_for(source: Path) -> Path:
    name = source.name or "照片"
    return source.parent / f"{name}_整理"


def machine_checks(vlm: str, model: str) -> list[Check]:
    checks = [
        _memory_check(),
        _gpu_check(),
        _ollama_check(vlm, model),
        _weights_check(),
        _exiftool_check(),
    ]
    return checks


def folder_checks(
    source: Path,
    output: Path,
    *,
    fs_name: str | None = None,
    image_count: tuple[int, int] | None = None,
) -> list[Check]:
    source = Path(source)
    output = Path(output)
    checks: list[Check] = []
    if not source.is_dir():
        checks.append(Check("input", "原始資料夾", BLOCK, f"找不到資料夾：{source}"))
        return checks
    resolved_in = source.resolve()
    resolved_out = output.resolve()
    if resolved_out == resolved_in or resolved_in in resolved_out.parents:
        checks.append(Check("nest", "結果位置", BLOCK, "結果資料夾不能放在原始資料夾裡面，否則會把分桶再掃進去。"))
    elif not same_disk(resolved_in, resolved_out):
        checks.append(Check("disk", "磁碟", BLOCK, "結果必須和原檔在同一個磁碟，硬連結才能建立。"))
    else:
        checks.append(Check("disk", "磁碟", OK, "原檔與結果在同一個磁碟。"))
    detected_fs = volume_fs_name(resolved_out) if fs_name is None else fs_name
    fs_name = detected_fs
    if supports_hardlinks(fs_name):
        checks.append(Check("fs", "檔案系統", OK, f"{fs_name} 可以建立硬連結。原檔不會被搬移或刪除。"))
    elif fs_name:
        checks.append(
            Check(
                "fs",
                "檔案系統",
                BLOCK,
                f"{fs_name} 不能建立硬連結。Windows 請用 NTFS，Linux 請用 ext4、XFS 或 Btrfs。",
            )
        )
    else:
        checks.append(Check("fs", "檔案系統", WARN, "讀不到檔案系統名稱。若硬連結失敗，結果會寫進 manifest.jsonl。"))
    anchor = existing_ancestor(resolved_out)
    try:
        free = shutil.disk_usage(anchor).free
    except OSError as exc:
        checks.append(Check("space", "剩餘空間", WARN, f"無法讀取剩餘空間：{exc}"))
    else:
        detail = f"結果磁碟剩餘 {format_gib(free)}。"
        if free < GIB:
            checks.append(Check("space", "剩餘空間", WARN, detail + "低於 1 GB，預覽快取可能寫不下。"))
        else:
            checks.append(Check("space", "剩餘空間", OK, detail))
    photos, skipped = count_entries(resolved_in) if image_count is None else image_count
    if photos == 0:
        checks.append(Check("photos", "照片", BLOCK, "這個資料夾沒有支援的 RAW、JPEG 或 HEIC。"))
    else:
        checks.append(Check("photos", "照片", OK, f"可整理 {photos} 張。"))
    if skipped:
        checks.append(
            Check("other", "其他檔案", WARN, f"另有 {skipped} 個檔案不是支援的照片，這次不會整理。")
        )
    catalog = resolved_out / "catalog.sqlite"
    if catalog.is_file():
        checks.append(Check("catalog", "既有紀錄", OK, "結果資料夾已有評分紀錄，重跑會沿用未改過的分數。"))
    return checks


def ready_state(checks: list[Check]) -> tuple[bool, str]:
    blocked = [item for item in checks if item.level == BLOCK]
    warned = [item for item in checks if item.level == WARN]
    if blocked:
        return False, "還不能開始：" + blocked[0].detail
    if warned:
        return True, f"可以開始，但有 {len(warned)} 項要確認。"
    return True, "條件足夠，可以開始。"


def _memory_check() -> Check:
    try:
        total, avail = memory_bytes()
    except OSError as exc:
        return Check("ram", "記憶體", WARN, str(exc))
    detail = f"總共 {format_gib(total)}，目前可用 {format_gib(avail)}。"
    if avail < 8 * GIB:
        return Check("ram", "記憶體", WARN, detail + "可用低於 8 GB，載入模型時可能變慢。")
    return Check("ram", "記憶體", OK, detail)


def _gpu_check() -> Check:
    try:
        import torch
    except Exception:
        return Check("gpu", "顯示卡", WARN, "未安裝 PyTorch。清晰度與連拍仍可整理，美學評分會略過。")
    if not torch.cuda.is_available():
        return Check("gpu", "顯示卡", WARN, "沒有可用的 CUDA。美學評分會改走 CPU，速度會慢很多。")
    index = torch.cuda.current_device()
    name = torch.cuda.get_device_name(index)
    total = int(torch.cuda.get_device_properties(index).total_memory)
    detail = f"{name}，顯示記憶體 {format_gib(total)}。"
    if total < 12 * GIB:
        return Check("gpu", "顯示卡", WARN, detail + "低於 12 GB，視覺模型可能放不下；美學分仍會先算。")
    return Check("gpu", "顯示卡", OK, detail)


def _ollama_check(vlm: str, model: str) -> Check:
    if vlm == "off":
        return Check("ollama", "Ollama", OK, "這次關閉視覺語意，不需要 Ollama。")
    ready, reason = VlmClient(model).available()
    if ready:
        return Check("ollama", "Ollama", OK, f"已啟動，模型 {model} 可用。")
    return Check("ollama", "Ollama", WARN, reason or "Ollama 未就緒。視覺語意會略過，其餘整理仍會完成。")


def _weights_check() -> Check:
    path = resolve_weights_path(None)
    if path is None or not path.is_file():
        return Check(
            "weights",
            "美學權重",
            WARN,
            "找不到美學權重。散張除明顯失焦外會先進覆核，連拍仍用清晰度選代表。",
        )
    return Check("weights", "美學權重", OK, f"已找到 {path.name}。")


def _exiftool_check() -> Check:
    if shutil.which("exiftool"):
        return Check("exiftool", "ExifTool", OK, "已安裝。RAW 的拍攝時間與內嵌預覽可以讀取。")
    return Check(
        "exiftool",
        "ExifTool",
        WARN,
        "未安裝。JPEG 不受影響；RAW 若抽不到內嵌預覽會送覆核，不會完整解碼。",
    )
