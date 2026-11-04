"""依作業系統分開的小功能。整理流程兩邊共用。"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

HARD_LINK_FS = {"NTFS", "REFS", "EXT2", "EXT3", "EXT4", "XFS", "BTRFS", "ZFS"}
_UI_FONTS = (
    "Microsoft JhengHei UI",
    "Noto Sans CJK TC",
    "Noto Sans CJK JP",
    "Source Han Sans TC",
    "WenQuanYi Micro Hei",
    "PingFang TC",
)
_MONO_FONTS = ("Consolas", "DejaVu Sans Mono", "Noto Sans Mono CJK TC", "Liberation Mono")


def is_windows() -> bool:
    return os.name == "nt"


def supports_hardlinks(fs_name: str) -> bool:
    return fs_name.upper() in HARD_LINK_FS


def pick_folders(owner: int = 0) -> list[Path]:
    if is_windows():
        from photocull.platform_windows import pick_folders as pick

        return pick(owner)
    from photocull.platform_linux import pick_folders as pick

    return pick()


def open_folder(path: Path) -> None:
    folder = Path(path)
    if is_windows():
        from photocull.platform_windows import open_folder as open_it

        open_it(folder)
        return
    from photocull.platform_linux import open_folder as open_it

    open_it(folder)


def ui_font(root) -> tuple[str, int]:
    return (_first_family(root, _UI_FONTS, "TkDefaultFont"), 10)


def mono_font(root) -> tuple[str, int]:
    return (_first_family(root, _MONO_FONTS, "TkFixedFont"), 10)


def enable_dpi() -> None:
    if not is_windows():
        return
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        return


def volume_fs_name(path: Path) -> str:
    if is_windows():
        return _windows_fs_name(path)
    return _linux_fs_name(path)


def _first_family(root, names: tuple[str, ...], fallback: str) -> str:
    import tkinter.font as tkfont

    families = set(tkfont.families(root))
    for name in names:
        if name in families:
            return name
    return fallback


def _existing(path: Path) -> Path:
    current = path
    while not current.exists():
        parent = current.parent
        if parent == current:
            return current
        current = parent
    return current


def _windows_fs_name(path: Path) -> str:
    import ctypes

    root = Path(_existing(path).anchor)
    name = ctypes.create_unicode_buffer(256)
    ok = ctypes.windll.kernel32.GetVolumeInformationW(
        str(root),
        None,
        0,
        None,
        None,
        None,
        name,
        len(name),
    )
    return name.value if ok else ""


def _linux_fs_name(path: Path) -> str:
    target = _existing(path)
    findmnt = shutil.which("findmnt")
    if findmnt:
        try:
            output = subprocess.check_output(
                [findmnt, "-no", "FSTYPE", str(target)],
                text=True,
                timeout=5,
            )
            found = output.strip().splitlines()
            if found and found[0].strip():
                return found[0].strip()
        except (OSError, subprocess.SubprocessError):
            pass
    return _fstype_from_mounts(target)


def _fstype_from_mounts(path: Path) -> str:
    try:
        lines = Path("/proc/mounts").read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    resolved = str(path.resolve())
    best = ""
    best_len = -1
    for line in lines:
        parts = line.split()
        if len(parts) < 3:
            continue
        mountpoint = parts[1].replace("\\040", " ")
        fstype = parts[2]
        if resolved == mountpoint or resolved.startswith(mountpoint.rstrip("/") + "/"):
            if len(mountpoint) > best_len:
                best = fstype
                best_len = len(mountpoint)
    return best
