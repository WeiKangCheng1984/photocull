from __future__ import annotations

from pathlib import Path

import cv2
import imagehash
import numpy as np
from PIL import Image


def dhash_file(path: Path) -> str:
    with Image.open(path) as image:
        return str(imagehash.dhash(image.convert("RGB"), hash_size=8))


def measure_file(path: Path) -> dict[str, float | str]:
    with Image.open(path) as image:
        rgb = image.convert("RGB")
        digest = str(imagehash.dhash(rgb, hash_size=8))
        gray = np.asarray(rgb.convert("L"))
    measured = measure_gray(gray)
    measured["dhash"] = digest
    return measured


def measure_gray(gray: np.ndarray) -> dict[str, float]:
    if gray.ndim != 2:
        raise ValueError("灰階圖必須是二維陣列")
    height, width = gray.shape[:2]
    long_side = max(height, width)
    if long_side == 0:
        return {"sharp_full": 0.0, "sharp_center": 0.0, "clip_hi": 0.0, "clip_lo": 0.0}
    scale = 1024 / long_side
    new_w = max(1, round(width * scale))
    new_h = max(1, round(height * scale))
    interpolation = cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR
    resized = cv2.resize(gray, (new_w, new_h), interpolation=interpolation)
    if resized.dtype != np.uint8:
        resized = np.clip(resized, 0, 255).astype(np.uint8)
    sharp_full = float(cv2.Laplacian(resized, cv2.CV_64F).var())
    y0, x0 = new_h // 4, new_w // 4
    center = resized[y0 : y0 + new_h // 2, x0 : x0 + new_w // 2]
    if center.size == 0:
        sharp_center = sharp_full
    else:
        sharp_center = float(cv2.Laplacian(center, cv2.CV_64F).var())
    return {
        "sharp_full": sharp_full,
        "sharp_center": sharp_center,
        "clip_hi": float((resized >= 250).mean()),
        "clip_lo": float((resized <= 5).mean()),
    }


def apply_floor(sharp_full: float | None, sharp_center: float | None, blur_floor: float) -> tuple[float | None, bool]:
    if sharp_full is None or sharp_center is None:
        return None, False
    subject = sharp_center >= 1.8 * sharp_full and sharp_center >= blur_floor
    return (sharp_center if subject else sharp_full), subject
