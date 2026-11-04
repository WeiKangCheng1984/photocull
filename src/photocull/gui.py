from __future__ import annotations

import os
import queue
import threading
import time
import tkinter as tk
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from photocull.pipeline import RunOptions, run_job
from photocull.platform import enable_dpi, mono_font, open_folder, pick_folders, ui_font
from photocull.preflight import BLOCK, OK, WARN, Check, default_output_for, folder_checks, machine_checks, ready_state
from photocull.queueing import iter_pause, rest_seconds, unique_photo_folders
from photocull.summary import line_tag, result_badge
VLM_MODEL = "qwen2.5vl:7b"
LEVEL_COLOR = {OK: "#1b7f3a", WARN: "#9a6700", BLOCK: "#a12622"}
TAG_COLOR = {"keep": "#1b7f3a", "review": "#9a6700", "reject": "#a12622", "fail": "#a12622", "plain": "#222222"}


@dataclass
class Job:
    source: Path
    output: Path
    photos: int = 0
    status: str = "等待中"
    badge: str = ""
    report: str = ""
    iid: str = field(default="")


class App:
    def __init__(self, loader=None, visible: bool = True) -> None:
        self._loader = loader or (lambda vlm: machine_checks(vlm, VLM_MODEL))
        self.jobs: list[Job] = []
        self.events: queue.Queue = queue.Queue()
        self._check_gen = 0
        self._worker: threading.Thread | None = None
        self._stop_later = False
        self._checks: list[Check] = []
        self._checks_ready = False
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("本機照片整理")
        self.root.geometry("1080x820")
        self.root.minsize(920, 700)
        self.font = ui_font(self.root)
        self.mono = mono_font(self.root)
        self.root.option_add("*Font", self.font)
        self._build()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(80, self._poll)
        self.refresh_checks()
        if visible:
            self.root.deiconify()

    def _build(self) -> None:
        style = ttk.Style()
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Treeview", font=self.font, rowheight=26)
        style.configure("Treeview.Heading", font=self.font)
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)
        self.root.rowconfigure(3, weight=1)
        self.root.rowconfigure(4, weight=2)

        checks = ttk.LabelFrame(self.root, text="執行前檢查", padding=8)
        checks.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 6))
        checks.columnconfigure(0, weight=1)
        self.readiness = tk.StringVar(value="正在檢查記憶體、顯示卡與 Ollama…")
        ttk.Label(checks, textvariable=self.readiness).grid(row=0, column=0, sticky="w")
        ttk.Button(checks, text="重新檢查", command=self.refresh_checks).grid(row=0, column=1, padx=(8, 0))
        self.check_box = ttk.Frame(checks)
        self.check_box.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))

        jobs = ttk.LabelFrame(self.root, text="資料夾", padding=8)
        jobs.grid(row=1, column=0, sticky="nsew", padx=12, pady=6)
        jobs.columnconfigure(0, weight=1)
        jobs.rowconfigure(0, weight=1)
        columns = ("source", "output", "photos", "status", "badge")
        self.tree = ttk.Treeview(jobs, columns=columns, show="headings", selectmode="browse")
        self.tree.heading("source", text="原始資料夾")
        self.tree.heading("output", text="結果資料夾")
        self.tree.heading("photos", text="張數")
        self.tree.heading("status", text="狀態")
        self.tree.heading("badge", text="結果標示")
        self.tree.column("source", width=280, stretch=True)
        self.tree.column("output", width=280, stretch=True)
        self.tree.column("photos", width=60, anchor="e", stretch=False)
        self.tree.column("status", width=80, stretch=False)
        self.tree.column("badge", width=280, stretch=True)
        self.tree.tag_configure("run", foreground="#0b5394")
        self.tree.tag_configure("rest", foreground="#9a6700")
        self.tree.tag_configure("done", foreground="#1b7f3a")
        self.tree.tag_configure("fail", foreground="#a12622")
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(jobs, orient="vertical", command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)
        buttons = ttk.Frame(jobs)
        buttons.grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self.add_button = ttk.Button(buttons, text="新增資料夾", command=self.add_job)
        self.add_button.grid(row=0, column=0, padx=(0, 6))
        self.batch_button = ttk.Button(buttons, text="批次加入", command=self.add_jobs_batch)
        self.batch_button.grid(row=0, column=1, padx=(0, 6))
        self.remove_button = ttk.Button(buttons, text="移除選取", command=self.remove_job)
        self.remove_button.grid(row=0, column=2, padx=(0, 6))
        self.start_button = ttk.Button(buttons, text="開始整理", command=self.confirm_and_start)
        self.start_button.grid(row=0, column=3, padx=(0, 6))
        self.stop_button = ttk.Button(buttons, text="這批結束後停止", command=self.request_stop, state="disabled")
        self.stop_button.grid(row=0, column=4)

        options = ttk.LabelFrame(self.root, text="功能", padding=8)
        options.grid(row=2, column=0, sticky="ew", padx=12, pady=6)
        self.burst_gap = self._number(options, 0, "連拍間隔（秒）", "2")
        self.blur_floor = self._number(options, 2, "失焦地板", "20")
        self.sharp_ok = self._number(options, 4, "技術過關", "80")
        self.aes_drop = self._number(options, 6, "美學偏低", "4.2")
        self.aes_keep = self._number(options, 8, "美學保留", "6.0")
        switches = ttk.Frame(options)
        switches.grid(row=1, column=0, columnspan=10, sticky="w", pady=(8, 0))
        self.use_sharpness = tk.BooleanVar(value=True)
        self.use_burst = tk.BooleanVar(value=True)
        self.use_aesthetic = tk.BooleanVar(value=True)
        self.use_vlm = tk.BooleanVar(value=True)
        ttk.Checkbutton(switches, text="清晰度", variable=self.use_sharpness).pack(side="left", padx=(0, 12))
        ttk.Checkbutton(switches, text="連拍與重複", variable=self.use_burst).pack(side="left", padx=(0, 12))
        ttk.Checkbutton(switches, text="美學分", variable=self.use_aesthetic).pack(side="left", padx=(0, 12))
        ttk.Checkbutton(
            switches,
            text="視覺語意（只看準備留下的，以及連拍分數矛盾的少數張）",
            variable=self.use_vlm,
            command=self.refresh_checks,
        ).pack(side="left")
        self.strict = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            options,
            text="嚴格美學：技術過關但分數很低時，直接放進美學廢片",
            variable=self.strict,
        ).grid(row=2, column=0, columnspan=8, sticky="w", pady=(8, 0))
        ttk.Label(options, text="休息（分鐘）").grid(row=2, column=8, sticky="e", padx=(8, 4), pady=(8, 0))
        self.rest_minutes = tk.StringVar(value="4")
        ttk.Entry(options, textvariable=self.rest_minutes, width=7).grid(row=2, column=9, sticky="w", pady=(8, 0))

        progress = ttk.LabelFrame(self.root, text="進度", padding=8)
        progress.grid(row=3, column=0, sticky="nsew", padx=12, pady=6)
        progress.columnconfigure(0, weight=1)
        progress.rowconfigure(1, weight=1)
        self.stage = tk.StringVar(value="尚未開始")
        ttk.Label(progress, textvariable=self.stage).grid(row=0, column=0, sticky="w")
        self.log = self._text(progress, 1, height=6)

        result = ttk.LabelFrame(self.root, text="結果標示", padding=8)
        result.grid(row=4, column=0, sticky="nsew", padx=12, pady=6)
        result.columnconfigure(0, weight=1)
        result.rowconfigure(0, weight=1)
        self.report = self._text(result, 0, height=10)
        for name, color in TAG_COLOR.items():
            self.report.tag_configure(name, foreground=color)
        opens = ttk.Frame(result)
        opens.grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Button(opens, text="開啟結果資料夾", command=lambda: self.open_bucket(None)).grid(row=0, column=0, padx=(0, 6))
        ttk.Button(opens, text="開啟保留", command=lambda: self.open_bucket("keep")).grid(row=0, column=1, padx=(0, 6))
        ttk.Button(opens, text="開啟覆核", command=lambda: self.open_bucket("review")).grid(row=0, column=2, padx=(0, 6))
        ttk.Button(opens, text="開啟廢片", command=lambda: self.open_bucket("reject")).grid(row=0, column=3)

        hint = ttk.Label(
            self.root,
            text="原檔只會被讀取。分桶是硬連結，刪掉結果資料夾裡的連結不會刪掉原檔。一次只跑一個資料夾，間隔休息可自己填，填 0 則不休息。",
        )
        hint.grid(row=5, column=0, sticky="w", padx=12, pady=(0, 12))

    def _number(self, parent, column: int, label: str, value: str) -> tk.StringVar:
        ttk.Label(parent, text=label).grid(row=0, column=column, sticky="w", padx=(0 if column == 0 else 10, 4))
        var = tk.StringVar(value=value)
        ttk.Entry(parent, textvariable=var, width=7).grid(row=0, column=column + 1, sticky="w")
        return var

    def _text(self, parent, row: int, height: int) -> tk.Text:
        widget = tk.Text(parent, height=height, wrap="word", font=self.mono, relief="flat", borderwidth=0)
        widget.grid(row=row, column=0, sticky="nsew")
        widget.configure(state="disabled")
        return widget

    def refresh_checks(self) -> None:
        self._check_gen += 1
        generation = self._check_gen
        self.readiness.set("正在檢查記憶體、顯示卡與 Ollama…")

        def work() -> None:
            try:
                checks = self._loader(self._vlm_mode())
            except Exception as exc:
                checks = [Check("machine", "檢查", BLOCK, str(exc))]
            self.events.put(("checks", generation, checks))

        threading.Thread(target=work, daemon=True).start()

    def _show_checks(self, checks: list[Check]) -> None:
        self._checks = checks
        for child in self.check_box.winfo_children():
            child.destroy()
        for index, item in enumerate(checks):
            mark = {OK: "通過", WARN: "注意", BLOCK: "不足"}[item.level]
            label = ttk.Label(
                self.check_box,
                text=f"{mark}  {item.title}：{item.detail}",
                foreground=LEVEL_COLOR[item.level],
            )
            label.grid(row=index, column=0, sticky="w")
        self._checks_ready = True
        _, text = ready_state(checks)
        self.readiness.set(text)

    def add_job(self) -> None:
        if self._running():
            return
        dialog = tk.Toplevel(self.root)
        dialog.title("新增資料夾")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.columnconfigure(1, weight=1)
        source_var = tk.StringVar()
        output_var = tk.StringVar()
        follow = {"on": True}
        ttk.Label(dialog, text="原始資料夾").grid(row=0, column=0, sticky="w", padx=12, pady=(12, 4))
        ttk.Entry(dialog, textvariable=source_var, width=64).grid(row=0, column=1, sticky="ew", pady=(12, 4))
        ttk.Label(dialog, text="結果資料夾").grid(row=1, column=0, sticky="w", padx=12, pady=4)
        output_entry = ttk.Entry(dialog, textvariable=output_var, width=64)
        output_entry.grid(row=1, column=1, sticky="ew", pady=4)
        notice = tk.StringVar(value="結果會放在同磁碟，資料夾名稱以 _整理 結尾。")
        ttk.Label(dialog, textvariable=notice, wraplength=560).grid(row=2, column=0, columnspan=3, sticky="w", padx=12, pady=4)

        def browse_source() -> None:
            dialog.grab_release()
            chosen = filedialog.askdirectory(title="選擇原始照片資料夾", parent=dialog)
            dialog.grab_set()
            if not chosen:
                return
            source_var.set(chosen)
            if follow["on"]:
                output_var.set(str(default_output_for(Path(chosen))))

        def browse_output() -> None:
            initial = output_var.get() or source_var.get() or str(Path.home())
            initial_path = Path(initial)
            initial_dir = initial_path if initial_path.is_dir() else initial_path.parent
            dialog.grab_release()
            chosen = filedialog.askdirectory(
                title="選擇結果資料夾的上層位置",
                parent=dialog,
                initialdir=str(initial_dir if initial_dir.exists() else Path.home()),
                mustexist=True,
            )
            dialog.grab_set()
            if not chosen:
                return
            parent = Path(chosen)
            source_name = Path(source_var.get()).name or "照片"
            output_var.set(str(parent if parent.name.endswith("_整理") else parent / f"{source_name}_整理"))
            follow["on"] = False

        output_entry.bind("<Key>", lambda _event: follow.update(on=False))
        ttk.Button(dialog, text="瀏覽", command=browse_source).grid(row=0, column=2, padx=12, pady=(12, 4))
        ttk.Button(dialog, text="瀏覽", command=browse_output).grid(row=1, column=2, padx=12, pady=4)

        def accept() -> None:
            source = Path(source_var.get().strip())
            output = Path(output_var.get().strip())
            if not source_var.get().strip() or not output_var.get().strip():
                notice.set("請選擇原始資料夾和結果資料夾。")
                return
            checks = folder_checks(source, output)
            blocked = [item.detail for item in checks if item.level == BLOCK]
            if blocked:
                notice.set("\n".join(blocked))
                return
            photos = next((item for item in checks if item.key == "photos"), None)
            count = int("".join(ch for ch in photos.detail if ch.isdigit()) or "0") if photos else 0
            warned = [item.detail for item in checks if item.level == WARN]
            if warned and not messagebox.askokcancel("請確認這些情況", "\n".join(warned), parent=dialog):
                return
            job = Job(source=source, output=output, photos=count)
            self.jobs.append(job)
            self._insert_job(job)
            dialog.destroy()

        actions = ttk.Frame(dialog)
        actions.grid(row=3, column=0, columnspan=3, sticky="e", padx=12, pady=12)
        ttk.Button(actions, text="取消", command=dialog.destroy).grid(row=0, column=0, padx=(0, 6))
        ttk.Button(actions, text="加入清單", command=accept).grid(row=0, column=1)
        dialog.wait_window()

    def add_jobs_batch(self) -> None:
        if self._running():
            return
        try:
            chosen = pick_folders(int(self.root.winfo_id()))
        except OSError as exc:
            messagebox.showerror("無法開啟資料夾選擇", str(exc))
            return
        if not chosen:
            return
        sources, notes = unique_photo_folders(chosen)
        existing = {job.source.resolve() for job in self.jobs}
        accepted: list[tuple[Path, Path, int]] = []
        self.root.configure(cursor="watch")
        self.root.update_idletasks()
        try:
            for source in sources:
                if source in existing:
                    notes.append(f"{source} 已在清單中。")
                    continue
                output = default_output_for(source)
                checks = folder_checks(source, output)
                blocked = [item.detail for item in checks if item.level == BLOCK]
                if blocked:
                    notes.append(f"{source}：{blocked[0]}")
                    continue
                accepted.append((source, output, _photo_count(checks)))
                existing.add(source)
        finally:
            self.root.configure(cursor="")
        if not accepted:
            messagebox.showwarning("沒有加入資料夾", "\n".join(notes) or "沒有選到可整理的資料夾。")
            return
        if not self._confirm_batch(accepted, notes):
            return
        for source, output, count in accepted:
            job = Job(source=source, output=output, photos=count)
            self.jobs.append(job)
            self._insert_job(job)

    def _confirm_batch(self, accepted: list[tuple[Path, Path, int]], notes: list[str]) -> bool:
        dialog = tk.Toplevel(self.root)
        dialog.title("批次加入")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.geometry("720x420")
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(1, weight=1)
        ttk.Label(dialog, text=f"將加入 {len(accepted)} 個資料夾。結果放在同磁碟、名稱以 _整理 結尾。").grid(
            row=0, column=0, sticky="w", padx=12, pady=(12, 6)
        )
        body = tk.Text(dialog, wrap="word", font=self.font, relief="flat")
        body.grid(row=1, column=0, sticky="nsew", padx=12)
        for source, output, count in accepted:
            body.insert("end", f"{source}\n  → {output}（{count} 張）\n")
        if notes:
            body.insert("end", "\n未加入：\n")
            for note in notes:
                body.insert("end", note + "\n")
        body.configure(state="disabled")
        answer = {"ok": False}

        def accept() -> None:
            answer["ok"] = True
            dialog.destroy()

        actions = ttk.Frame(dialog)
        actions.grid(row=2, column=0, sticky="e", padx=12, pady=12)
        ttk.Button(actions, text="取消", command=dialog.destroy).grid(row=0, column=0, padx=(0, 6))
        ttk.Button(actions, text="加入清單", command=accept).grid(row=0, column=1)
        dialog.wait_window()
        return answer["ok"]

    def _insert_job(self, job: Job) -> None:
        job.iid = self.tree.insert(
            "",
            "end",
            values=(str(job.source), str(job.output), job.photos, job.status, job.badge),
        )

    def _refresh_job(self, job: Job) -> None:
        tag = ""
        if job.status == "整理中":
            tag = "run"
        elif job.status == "休息中":
            tag = "rest"
        elif job.status == "完成":
            tag = "done"
        elif job.status == "失敗":
            tag = "fail"
        self.tree.item(
            job.iid,
            values=(str(job.source), str(job.output), job.photos, job.status, job.badge),
            tags=(tag,) if tag else (),
        )

    def remove_job(self) -> None:
        if self._running():
            return
        selected = self.tree.selection()
        if not selected:
            return
        iid = selected[0]
        self.jobs = [job for job in self.jobs if job.iid != iid]
        self.tree.delete(iid)

    def _settings(self) -> RunOptions | None:
        try:
            burst_gap = float(self.burst_gap.get())
            blur_floor = float(self.blur_floor.get())
            sharp_ok = float(self.sharp_ok.get())
            aes_drop = float(self.aes_drop.get())
            aes_keep = float(self.aes_keep.get())
        except ValueError:
            messagebox.showerror("數字不正確", "連拍間隔、清晰度與美學分必須是數字。")
            return None
        try:
            pause = rest_seconds(float(self.rest_minutes.get()))
        except ValueError:
            messagebox.showerror("數字不正確", "休息時間要是 0 以上的分鐘數。填 0 表示不休息。")
            return None
        if burst_gap <= 0 or blur_floor < 0 or sharp_ok < 0:
            messagebox.showerror("數字不正確", "連拍間隔要大於 0，清晰度不能是負數。")
            return None
        if aes_keep < aes_drop:
            messagebox.showerror("數字不正確", "美學保留線要高於或等於美學偏低線。")
            return None
        if not any((self.use_sharpness.get(), self.use_burst.get(), self.use_aesthetic.get(), self.use_vlm.get())):
            messagebox.showerror("沒有篩選", "清晰度、連拍與重複、美學分、視覺語意，至少要留一種。")
            return None
        self._rest_seconds = pause
        return RunOptions(
            input_dir=Path("."),
            output_dir=Path("."),
            input_display="",
            output_display="",
            burst_gap=burst_gap,
            blur_floor=blur_floor,
            sharp_ok=sharp_ok,
            aes_drop=aes_drop,
            aes_keep=aes_keep,
            vlm=self._vlm_mode(),
            strict_aesthetic=bool(self.strict.get()),
            use_sharpness=bool(self.use_sharpness.get()),
            use_burst=bool(self.use_burst.get()),
            use_aesthetic=bool(self.use_aesthetic.get()),
            vlm_model=VLM_MODEL,
        )

    def _vlm_mode(self) -> str:
        return "fast" if self.use_vlm.get() else "off"

    def confirm_and_start(self) -> None:
        if self._running():
            return
        if not self._checks_ready:
            messagebox.showinfo("還在檢查", "記憶體、顯示卡與 Ollama 的檢查還沒結束。")
            return
        if not self.jobs:
            messagebox.showinfo("還沒有資料夾", "先按「新增資料夾」，選原始照片和結果要放的位置。")
            return
        template = self._settings()
        if template is None:
            return
        pending = [job for job in self.jobs if job.status in {"等待中", "失敗", "略過"}]
        if not pending:
            messagebox.showinfo("這份清單已經跑完", "要再跑一次，先移除後重新加入，或加入新的資料夾。")
            return
        job_checks: list[tuple[Job, list[Check]]] = []
        for job in pending:
            job_checks.append((job, folder_checks(job.source, job.output)))
        blocked = [item for item in self._checks if item.level == BLOCK]
        blocked.extend(item for _, checks in job_checks for item in checks if item.level == BLOCK)
        warned = [item for item in self._checks if item.level == WARN]
        warned.extend(item for _, checks in job_checks for item in checks if item.level == WARN)
        if not self._ask_confirm(template, job_checks, blocked, warned):
            return
        self._stop_later = False
        self._set_busy(True)
        self._write(self.log, "", replace=True)
        self.stage.set("準備開始…")
        self._worker = threading.Thread(
            target=self._run_queue,
            args=(pending, template, self._rest_seconds),
            daemon=True,
        )
        self._worker.start()

    def _ask_confirm(self, template: RunOptions, job_checks, blocked: list[Check], warned: list[Check]) -> bool:
        dialog = tk.Toplevel(self.root)
        dialog.title("開始前確認")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.geometry("720x560")
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(1, weight=1)
        headline = "還不能開始。" if blocked else "請確認這台電腦與資料夾都符合這次整理。"
        ttk.Label(dialog, text=headline).grid(row=0, column=0, sticky="w", padx=12, pady=(12, 6))
        body = tk.Text(dialog, wrap="word", font=self.font, relief="flat")
        body.grid(row=1, column=0, sticky="nsew", padx=12)
        lines = []
        for item in self._checks:
            lines.append((item.level, f"{item.title}：{item.detail}"))
        lines.append(("plain", ""))
        for job, checks in job_checks:
            lines.append(("plain", f"{job.source}  →  {job.output}"))
            for item in checks:
                lines.append((item.level, f"  {item.title}：{item.detail}"))
        chosen = [name for name, enabled in (
            ("清晰度", template.use_sharpness),
            ("連拍與重複", template.use_burst),
            ("美學分", template.use_aesthetic),
            ("視覺語意", template.vlm == "fast"),
        ) if enabled]
        strict = "開啟" if template.strict_aesthetic else "關閉"
        lines.append(("plain", ""))
        lines.append(
            (
                "plain",
                f"篩選：{'、'.join(chosen)}。連拍間隔 {template.burst_gap:g} 秒，"
                f"失焦地板 {template.blur_floor:g}，技術過關 {template.sharp_ok:g}，"
                f"美學 {template.aes_drop:g}～{template.aes_keep:g}，嚴格美學：{strict}。",
            )
        )
        if template.vlm == "fast":
            lines.append(("plain", "視覺語意只看準備留下的照片，以及連拍裡清晰度和美學分差很多的少數張。"))
        lines.append(("plain", "原檔不會被刪除、修改或搬移。"))
        if len(job_checks) > 1:
            if self._rest_seconds == 0:
                lines.append(("plain", "每個資料夾處理完會立刻開始下一個。"))
            else:
                lines.append(("plain", f"每個資料夾處理完會休息 {self._rest_seconds / 60:g} 分鐘，再開始下一個。"))
        if warned and not blocked:
            lines.append(("warn", "有注意項目。仍要開始的話，那些項目會沿用程式原本的略過方式。"))
        for level, text in lines:
            tag = {"ok": "keep", "warn": "review", "block": "fail"}.get(level, "plain")
            body.insert("end", text + "\n", tag)
        for name, color in TAG_COLOR.items():
            body.tag_configure(name, foreground=color)
        body.configure(state="disabled")
        accepted = {"value": False}

        def accept() -> None:
            accepted["value"] = True
            dialog.destroy()

        actions = ttk.Frame(dialog)
        actions.grid(row=2, column=0, sticky="e", padx=12, pady=12)
        ttk.Button(actions, text="返回", command=dialog.destroy).grid(row=0, column=0, padx=(0, 6))
        start = ttk.Button(actions, text="開始整理", command=accept)
        start.grid(row=0, column=1)
        if blocked:
            start.state(["disabled"])
        dialog.wait_window()
        return accepted["value"]

    def request_stop(self) -> None:
        self._stop_later = True
        self.stage.set("休息會立刻結束，後面的資料夾不再開始。正在整理的這一批會先跑完。")
        self.stop_button.state(["disabled"])

    def _run_queue(self, pending: list[Job], template: RunOptions, rest: int) -> None:
        for index, job in enumerate(pending):
            if self._stop_later:
                self.events.put(("skip", job))
                continue
            self.events.put(("begin", job))
            options = RunOptions(
                input_dir=job.source,
                output_dir=job.output,
                input_display=str(job.source),
                output_display=str(job.output),
                burst_gap=template.burst_gap,
                blur_floor=template.blur_floor,
                sharp_ok=template.sharp_ok,
                aes_drop=template.aes_drop,
                aes_keep=template.aes_keep,
                vlm=template.vlm,
                strict_aesthetic=template.strict_aesthetic,
                use_sharpness=template.use_sharpness,
                use_burst=template.use_burst,
                use_aesthetic=template.use_aesthetic,
                device=template.device,
                aesthetic_weights=template.aesthetic_weights,
                vlm_model=template.vlm_model,
                vlm_base_url=template.vlm_base_url,
                batch_size=template.batch_size,
            )
            try:
                report = run_job(options, on_log=lambda line: self.events.put(("log", line)))
            except SystemExit as exc:
                self.events.put(("fail", job, str(exc)))
            except Exception as exc:
                self.events.put(("fail", job, f"{type(exc).__name__}：{exc}"))
            else:
                self.events.put(("done", job, report))
            if self._stop_later or index == len(pending) - 1 or rest <= 0:
                continue
            self.events.put(("log", f"休息 {rest / 60:g} 分鐘，再處理下一個資料夾。"))
            for remaining in iter_pause(rest, lambda: self._stop_later, time.sleep):
                self.events.put(("rest", pending[index + 1], remaining))
        self.events.put(("idle",))

    def _poll(self) -> None:
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            kind = event[0]
            if kind == "checks":
                _, generation, checks = event
                if generation == self._check_gen:
                    self._show_checks(checks)
            elif kind == "log":
                self.stage.set(event[1].strip() or event[1])
                self._write(self.log, event[1] + "\n")
            elif kind == "begin":
                job = event[1]
                job.status = "整理中"
                job.badge = ""
                self._refresh_job(job)
                self.tree.selection_set(job.iid)
                self.tree.see(job.iid)
                self._write(self.report, f"—— {job.source} ——\n正在整理…\n\n")
            elif kind == "done":
                _, job, report = event
                job.status = "完成"
                job.report = report
                job.badge = result_badge(report)
                self._refresh_job(job)
                self._show_report(job)
            elif kind == "fail":
                _, job, message = event
                job.status = "失敗"
                job.report = message
                job.badge = "失敗"
                self._refresh_job(job)
                self._write(self.report, f"—— {job.source} ——\n{message}\n\n", tag="fail")
            elif kind == "skip":
                job = event[1]
                job.status = "略過"
                job.badge = "這批已停止"
                self._refresh_job(job)
            elif kind == "rest":
                _, job, remaining = event
                minutes, seconds = divmod(remaining, 60)
                job.status = "休息中"
                job.badge = f"{minutes}:{seconds:02d}"
                self._refresh_job(job)
                self.stage.set(f"休息中，{minutes}:{seconds:02d} 後處理下一個資料夾。")
            elif kind == "idle":
                self._set_busy(False)
                if self._stop_later:
                    self.stage.set("已停止，尚未開始的資料夾標成略過。")
                else:
                    self.stage.set("清單跑完。")
        self.root.after(80, self._poll)

    def _show_report(self, job: Job) -> None:
        self.report.configure(state="normal")
        self.report.insert("end", f"—— {job.source} ——\n", "plain")
        for line in job.report.splitlines(keepends=True):
            self.report.insert("end", line if line.endswith("\n") else line + "\n", line_tag(line))
        self.report.insert("end", "\n")
        self.report.see("end")
        self.report.configure(state="disabled")

    def _write(self, widget: tk.Text, text: str, replace: bool = False, tag: str = "plain") -> None:
        widget.configure(state="normal")
        if replace:
            widget.delete("1.0", "end")
        widget.insert("end", text, tag)
        widget.see("end")
        widget.configure(state="disabled")

    def _selected_job(self) -> Job | None:
        selected = self.tree.selection()
        if selected:
            iid = selected[0]
            for job in self.jobs:
                if job.iid == iid:
                    return job
        for job in reversed(self.jobs):
            if job.status == "完成":
                return job
        return self.jobs[-1] if self.jobs else None

    def open_bucket(self, bucket: str | None) -> None:
        job = self._selected_job()
        if job is None:
            messagebox.showinfo("還沒有資料夾", "先加入並整理一個資料夾。")
            return
        path = job.output if bucket is None else job.output / bucket
        if not path.is_dir():
            messagebox.showinfo("資料夾還不在", f"找不到 {path}")
            return
        try:
            open_folder(path)
        except OSError as exc:
            messagebox.showerror("打不開資料夾", str(exc))

    def _running(self) -> bool:
        return self._worker is not None and self._worker.is_alive()

    def _set_busy(self, busy: bool) -> None:
        state = ["disabled"] if busy else ["!disabled"]
        for button in (self.add_button, self.batch_button, self.remove_button, self.start_button):
            button.state(state)
        self.stop_button.state(["!disabled"] if busy else ["disabled"])

    def _on_close(self) -> None:
        if self._running() and not messagebox.askokcancel(
            "整理還在進行",
            "關閉視窗會中斷目前這一批。原檔不會被刪除。確定要關閉嗎？",
        ):
            return
        self.root.destroy()


def _photo_count(checks: list[Check]) -> int:
    photos = next((item for item in checks if item.key == "photos" and item.level == OK), None)
    if photos is None:
        return 0
    return int("".join(ch for ch in photos.detail if ch.isdigit()) or "0")


def main() -> None:
    enable_dpi()
    App().root.mainloop()
