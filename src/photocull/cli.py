from __future__ import annotations

import argparse
import sys
from pathlib import Path

from photocull.db import Database
from photocull.pipeline import RunOptions, run_job
from photocull.report import render


def _utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8")
        except Exception:
            pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="photocull",
        description="用本機 CPU / GPU 整理 RAW 與 JPEG。只建立硬連結，不刪除、不搬移原檔。",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="整理一個資料夾")
    run.add_argument("--input", required=True, help="原始照片資料夾")
    run.add_argument("--output", required=True, help="結果資料夾，需與原檔在同一磁碟")
    run.add_argument("--burst-gap", type=float, default=2.0, help="連拍簇以第一張為基準的秒數，預設 2")
    run.add_argument("--blur-floor", type=float, default=20.0, help="極端失焦地板，預設 20")
    run.add_argument("--sharp-ok", type=float, default=80.0, help="散張技術過關線，預設 80")
    run.add_argument("--aes-drop", type=float, default=4.2, help="散張美學偏低線，預設 4.2")
    run.add_argument("--aes-keep", type=float, default=6.0, help="散張美學保留線，預設 6.0")
    run.add_argument("--vlm", choices=("auto", "fast", "off", "all"), default="auto", help="語意分析範圍。fast 只看準備留下的照片")
    run.add_argument("--no-sharpness", action="store_true", help="這次不用清晰度決定好壞")
    run.add_argument("--no-burst", action="store_true", help="這次不做連拍與完全重複")
    run.add_argument("--no-aesthetic", action="store_true", help="這次不算美學分")
    run.add_argument("--strict-aesthetic", action="store_true", help="技術過關但美學很低的散張直接列為美學廢片")
    run.add_argument("--device", default="cuda", help="美學模型裝置，預設 cuda")
    run.add_argument("--aesthetic-weights", default=None, help="CLIP 美學線性頭 .pth")
    run.add_argument("--vlm-model", default="qwen2.5vl:7b", help="Ollama 或 vLLM 的模型名稱")
    run.add_argument("--vlm-base-url", default=None, help="指定後改走 vLLM，例如 http://127.0.0.1:8000")
    run.add_argument("--batch-size", type=int, default=32, help="美學評分批次，預設 32")
    commands.add_parser("gui", help="開啟視窗，自己加入資料夾")
    report = commands.add_parser("report", help="查看這個結果資料夾的整理摘要")
    report.add_argument("--output", required=True, help="先前 run 使用的結果資料夾")
    return parser


def main(argv: list[str] | None = None) -> None:
    _utf8_stdio()
    args = build_parser().parse_args(argv)
    if args.command == "gui":
        from photocull.gui import main as gui_main

        gui_main()
        return
    if args.command == "report":
        catalog = Path(args.output).resolve() / "catalog.sqlite"
        if not catalog.exists():
            raise SystemExit(f"找不到整理紀錄：{catalog}")
        db = Database(catalog)
        db.open()
        try:
            sys.stdout.write(render(db))
        finally:
            db.close()
        return
    weights = None if args.aesthetic_weights is None else Path(args.aesthetic_weights)
    text = run_job(
        RunOptions(
            input_dir=Path(args.input),
            output_dir=Path(args.output),
            input_display=args.input,
            output_display=args.output,
            burst_gap=args.burst_gap,
            blur_floor=args.blur_floor,
            sharp_ok=args.sharp_ok,
            aes_drop=args.aes_drop,
            aes_keep=args.aes_keep,
            vlm=args.vlm,
            strict_aesthetic=args.strict_aesthetic,
            use_sharpness=not args.no_sharpness,
            use_burst=not args.no_burst,
            use_aesthetic=not args.no_aesthetic,
            device=args.device,
            aesthetic_weights=weights,
            vlm_model=args.vlm_model,
            vlm_base_url=args.vlm_base_url,
            batch_size=args.batch_size,
        )
    )
    sys.stdout.write(text)


if __name__ == "__main__":
    main()
