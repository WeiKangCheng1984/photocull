"""Linux 專用介面：資料夾連選、開啟資料夾。"""

from __future__ import annotations

import shutil
import subprocess
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk


def open_folder(path: Path) -> None:
    opener = shutil.which("xdg-open")
    if opener is None:
        raise OSError("找不到 xdg-open，無法開啟資料夾。")
    subprocess.Popen([opener, str(Path(path))])


def pick_folders() -> list[Path]:
    zenity = shutil.which("zenity")
    if zenity:
        completed = subprocess.run(
            [
                zenity,
                "--file-selection",
                "--directory",
                "--multiple",
                "--separator=|",
                "--title=按住 Ctrl 或 Shift 連選資料夾",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            return []
        return [Path(item) for item in completed.stdout.strip().split("|") if item]
    return _pick_from_parent()


def _pick_from_parent() -> list[Path]:
    parent = filedialog.askdirectory(title="先選上層資料夾，下一步再連選裡面的資料夾")
    if not parent:
        return []
    root = Path(parent)
    children = sorted(item for item in root.iterdir() if item.is_dir() and not item.name.startswith("."))
    if not children:
        messagebox.showinfo("沒有子資料夾", f"{root} 裡面沒有可以連選的資料夾。")
        return []
    host = tk._default_root
    if host is None:
        raise OSError("請安裝 zenity，或先開啟照片整理視窗再批次加入。")
    dialog = tk.Toplevel(host)
    dialog.title("連選資料夾")
    dialog.transient(host)
    dialog.grab_set()
    dialog.geometry("640x420")
    ttk.Label(dialog, text=f"在 {root} 裡按住 Ctrl 或 Shift 連選。").pack(anchor="w", padx=12, pady=(12, 6))
    box = tk.Listbox(dialog, selectmode="extended", exportselection=False)
    box.pack(fill="both", expand=True, padx=12)
    for child in children:
        box.insert("end", child.name)
    chosen: list[Path] = []

    def accept() -> None:
        chosen.extend(children[index] for index in box.curselection())
        dialog.destroy()

    actions = ttk.Frame(dialog)
    actions.pack(anchor="e", padx=12, pady=12)
    ttk.Button(actions, text="取消", command=dialog.destroy).pack(side="left", padx=(0, 6))
    ttk.Button(actions, text="加入", command=accept).pack(side="left")
    dialog.wait_window()
    return chosen
