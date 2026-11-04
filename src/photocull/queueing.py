from __future__ import annotations

from pathlib import Path

REST_SECONDS = 4 * 60


def rest_seconds(minutes: float) -> int:
    if minutes < 0:
        raise ValueError("休息時間不能是負數")
    return int(round(minutes * 60))


def unique_photo_folders(paths: list[Path]) -> tuple[list[Path], list[str]]:
    chosen: list[Path] = []
    notes: list[str] = []
    seen: set[Path] = set()
    for raw in paths:
        path = Path(raw)
        if path.name.endswith("_整理"):
            notes.append(f"{path} 是整理結果，未加入。")
            continue
        try:
            resolved = path.resolve()
        except OSError:
            notes.append(f"{path} 無法讀取，未加入。")
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        chosen.append(resolved)
    return chosen, notes


def iter_pause(seconds: int, stop, sleep):
    """每秒產生剩餘時間。stop() 為真時立刻結束，不再往下等。"""
    for remaining in range(max(0, seconds), 0, -1):
        if stop():
            return
        yield remaining
        sleep(1)
