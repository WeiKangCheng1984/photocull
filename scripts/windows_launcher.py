"""雙擊啟動視窗。這個檔只尋找已安裝的 Python，不把模型打包進去。"""

from __future__ import annotations

import os
import subprocess
import sys
import winreg
from pathlib import Path

CREATE_NO_WINDOW = 0x08000000


def project_root() -> Path:
    start = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
    for candidate in (start, *start.parents):
        if (candidate / "pyproject.toml").is_file() and (candidate / "src" / "photocull").is_dir():
            return candidate
    return start


def python_install_dir() -> Path | None:
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, r"Software\Python\PythonCore\3.11\InstallPath") as key:
                folder, _ = winreg.QueryValueEx(key, "")
        except OSError:
            continue
        path = Path(folder)
        if (path / "python.exe").is_file():
            return path
    local = os.environ.get("LOCALAPPDATA")
    if local:
        fallback = Path(local) / "Programs" / "Python" / "Python311"
        if (fallback / "python.exe").is_file():
            return fallback
    return None


def tell(message: str) -> None:
    import ctypes

    ctypes.windll.user32.MessageBoxW(0, message, "照片整理", 0x10)


def launch() -> int:
    root = project_root()
    install = python_install_dir()
    if install is None:
        tell("找不到 Python 3.11。請先安裝 Python 3.11，再雙擊這個程式。")
        return 1
    python = install / "python.exe"
    pythonw = install / "pythonw.exe"
    if not pythonw.is_file():
        pythonw = python
    probe = subprocess.run(
        [str(python), "-c", "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('photocull') else 1)"],
        cwd=str(root),
        capture_output=True,
        creationflags=CREATE_NO_WINDOW,
        check=False,
    )
    if probe.returncode != 0:
        detail = probe.stderr.decode("utf-8", "replace").strip()
        tell("這台的 Python 3.11 還沒有照片整理程式。\n" + (detail or f"請在 {root} 執行 pip install -e ."))
        return 1
    subprocess.Popen(
        [str(pythonw), "-m", "photocull", "gui"],
        cwd=str(root),
        env={**os.environ, "HF_HUB_DISABLE_XET": "1"},
        creationflags=CREATE_NO_WINDOW,
    )
    return 0


if __name__ == "__main__":
    if "--check" in sys.argv:
        folder = python_install_dir()
        print(folder or "")
        print(project_root())
        raise SystemExit(0 if folder else 1)
    raise SystemExit(launch())
