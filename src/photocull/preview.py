from __future__ import annotations

import hashlib
import shutil
import subprocess
from io import BytesIO
from pathlib import Path

from PIL import Image

_HEIF_READY = False


def _ensure_heif() -> None:
    global _HEIF_READY
    if _HEIF_READY:
        return
    try:
        import pillow_heif

        pillow_heif.register_heif_opener()
    except Exception:
        pass
    _HEIF_READY = True


def _no_window() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def encode_raster(path: Path, long_edge: int = 1600) -> bytes:
    _ensure_heif()
    with Image.open(path) as image:
        rgb = image.convert("RGB")
        width, height = rgb.size
        long_side = max(width, height)
        if long_side > long_edge:
            scale = long_edge / long_side
            rgb = rgb.resize(
                (max(1, round(width * scale)), max(1, round(height * scale))),
                Image.Resampling.LANCZOS,
            )
        buffer = BytesIO()
        rgb.save(buffer, format="JPEG", quality=95)
        return buffer.getvalue()


def jpeg_long_edge(data: bytes) -> int | None:
    try:
        with Image.open(BytesIO(data)) as image:
            return max(image.size)
    except Exception:
        return None


def _exiftool_tag(path: Path, tag: str) -> bytes | None:
    if shutil.which("exiftool") is None:
        return None
    try:
        completed = subprocess.run(
            ["exiftool", "-b", f"-{tag}", str(path)],
            capture_output=True,
            timeout=60,
            check=False,
            creationflags=_no_window(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    data = completed.stdout
    if len(data) >= 2 and data[:2] == b"\xff\xd8":
        return data
    return None


def _rawpy_thumb(path: Path) -> bytes | None:
    try:
        import rawpy
    except Exception:
        return None
    try:
        with rawpy.imread(str(path)) as raw:
            thumb = raw.extract_thumb()
    except Exception:
        return None
    if thumb.format != rawpy.ThumbFormat.JPEG:
        return None
    data = bytes(thumb.data)
    if len(data) >= 2 and data[:2] == b"\xff\xd8":
        return data
    return None


def extract_raw_jpeg(path: Path) -> tuple[bytes | None, str]:
    """只拿內嵌 JPEG。不呼叫 demosaic / postprocess。"""
    for tag, source in (
        ("PreviewImage", "preview"),
        ("JpgFromRaw", "jpgfromraw"),
        ("ThumbnailImage", "thumbnail"),
    ):
        data = _exiftool_tag(path, tag)
        if data:
            return data, source
    data = _rawpy_thumb(path)
    if data:
        return data, "rawpy"
    return None, "missing"


def store_preview(output: Path, data: bytes) -> tuple[str, Path]:
    digest = hashlib.sha256(data).hexdigest()
    dest = output / "previews" / f"{digest}.jpg"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        dest.write_bytes(data)
    return digest, dest


def preview_for_raster(path: Path, output: Path) -> tuple[str, Path, str]:
    digest, dest = store_preview(output, encode_raster(path))
    return digest, dest, "jpeg"


def preview_for_raw(path: Path, output: Path) -> tuple[str | None, Path | None, str]:
    data, source = extract_raw_jpeg(path)
    if not data:
        return None, None, "missing"
    long_edge = jpeg_long_edge(data) or 0
    kind = "tiny" if source in {"thumbnail", "rawpy"} and long_edge < 320 else "embedded"
    digest, dest = store_preview(output, data)
    return digest, dest, kind
