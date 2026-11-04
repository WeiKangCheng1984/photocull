from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path

from PIL import Image

from photocull.models import ALL_EXTS, RAW_EXTS, FileItem, asset_id_for, file_id_for

PAIR_SECONDS = 2.0


def iter_images(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if not name.startswith(".")]
        for name in filenames:
            if name.startswith("."):
                continue
            path = Path(dirpath) / name
            if path.suffix.casefold() in ALL_EXTS:
                yield path


def parse_exif_datetime(text: str, subsec: str | None) -> datetime | None:
    raw = text.strip().replace("-", ":")
    if len(raw) < 19:
        return None
    try:
        captured = datetime.strptime(raw[:19], "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None
    if subsec and subsec.strip().isdigit():
        fraction = subsec.strip()
        captured += timedelta(seconds=int(fraction) / (10 ** len(fraction)))
    return captured.replace(tzinfo=datetime.now().astimezone().tzinfo)


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    return str(value).strip().strip("\x00")


def _from_mtime(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime).astimezone()


def read_pillow_capture(path: Path) -> tuple[datetime | None, bool, str]:
    """回傳拍攝時間、時間是否不可靠、機身識別。"""
    try:
        with Image.open(path) as image:
            exif = image.getexif()
            try:
                ifd = exif.get_ifd(0x8769)
            except Exception:
                ifd = {}
            make = _text(exif.get(0x010F))
            model = _text(exif.get(0x0110)) or _text(ifd.get(0x0110))
            serial = _text(ifd.get(0xA431))
            taken = _text(ifd.get(0x9003)) or _text(exif.get(0x9003))
            subsec = _text(ifd.get(0x9291))
    except Exception:
        return None, True, "unknown"
    camera = serial or " ".join(part for part in (make, model) if part) or "unknown"
    if not taken:
        try:
            return _from_mtime(path), True, camera
        except OSError:
            return None, True, camera
    captured = parse_exif_datetime(taken, subsec)
    if captured is None:
        try:
            return _from_mtime(path), True, camera
        except OSError:
            return None, True, camera
    return captured, False, camera


def exiftool_capture_map(paths: list[Path]) -> dict[str, tuple[datetime | None, bool, str]]:
    import json
    import shutil
    import subprocess

    if not paths or shutil.which("exiftool") is None:
        return {}
    found: dict[str, tuple[datetime | None, bool, str]] = {}
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    for start in range(0, len(paths), 40):
        chunk = paths[start : start + 40]
        try:
            completed = subprocess.run(
                [
                    "exiftool",
                    "-json",
                    "-DateTimeOriginal",
                    "-SubSecTimeOriginal",
                    "-Make",
                    "-Model",
                    "-BodySerialNumber",
                    *[str(path) for path in chunk],
                ],
                capture_output=True,
                timeout=120,
                check=False,
                creationflags=flags,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        try:
            payload = json.loads(completed.stdout.decode("utf-8", "replace") or "[]")
        except json.JSONDecodeError:
            continue
        for row in payload:
            source = row.get("SourceFile")
            if not source:
                continue
            taken = _text(row.get("DateTimeOriginal"))
            subsec = _text(row.get("SubSecTimeOriginal"))
            serial = _text(row.get("BodySerialNumber"))
            make = _text(row.get("Make"))
            model = _text(row.get("Model"))
            camera = serial or " ".join(part for part in (make, model) if part) or "unknown"
            captured = parse_exif_datetime(taken, subsec) if taken else None
            if captured is None:
                try:
                    captured = _from_mtime(Path(source))
                except OSError:
                    captured = None
                found[str(Path(source).resolve())] = (captured, True, camera)
            else:
                found[str(Path(source).resolve())] = (captured, False, camera)
    return found


def scan_files(root: Path, notes: list[str]) -> list[FileItem]:
    paths = []
    for path in iter_images(root):
        try:
            path.stat()
        except OSError as exc:
            notes.append(f"無法讀取 {path}：{exc}")
            continue
        paths.append(path.resolve())
    raw_paths = [path for path in paths if path.suffix.casefold() in RAW_EXTS]
    raw_meta = exiftool_capture_map(raw_paths)
    items: list[FileItem] = []
    for path in paths:
        try:
            stat = path.stat()
        except OSError as exc:
            notes.append(f"無法讀取 {path}：{exc}")
            continue
        is_raw = path.suffix.casefold() in RAW_EXTS
        meta = raw_meta.get(str(path))
        if meta is None and not is_raw:
            captured, unreliable, camera = read_pillow_capture(path)
        elif meta is None:
            try:
                captured, unreliable, camera = _from_mtime(path), True, "unknown"
            except OSError:
                captured, unreliable, camera = None, True, "unknown"
        else:
            captured, unreliable, camera = meta
        iso = None if captured is None else captured.isoformat(timespec="milliseconds")
        items.append(
            FileItem(
                file_id=file_id_for(path),
                path=path,
                size=stat.st_size,
                ext=path.suffix.casefold(),
                mtime_ns=stat.st_mtime_ns,
                captured_at=iso,
                captured_dt=captured,
                camera=camera or "unknown",
                time_unreliable=unreliable,
                is_raw=is_raw,
            )
        )
    return items


def pair_files(files: list[FileItem]):
    from photocull.models import AssetItem

    raws: dict[tuple[str, str], list[FileItem]] = {}
    rasters: dict[tuple[str, str], list[FileItem]] = {}
    for item in files:
        key = (str(item.path.parent).casefold(), item.path.stem.casefold())
        if item.is_raw:
            raws.setdefault(key, []).append(item)
        else:
            rasters.setdefault(key, []).append(item)
    used: set[str] = set()
    assets: list[AssetItem] = []
    for key, raw_list in raws.items():
        for raw in raw_list:
            match = None
            if raw.captured_dt is not None:
                for raster in rasters.get(key, []):
                    if raster.file_id in used or raster.captured_dt is None:
                        continue
                    delta = abs((raw.captured_dt - raster.captured_dt).total_seconds())
                    if delta <= PAIR_SECONDS:
                        match = raster
                        break
            group = [raw] if match is None else [raw, match]
            if match is not None:
                used.add(match.file_id)
            assets.append(AssetItem(asset_id=asset_id_for([item.path for item in group]), files=group))
    for item in files:
        if not item.is_raw and item.file_id not in used:
            assets.append(AssetItem(asset_id=asset_id_for([item.path]), files=[item]))
    return assets
