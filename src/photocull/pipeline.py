from __future__ import annotations

import contextvars
import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from photocull.aesthetic import load_scorer
from photocull.cluster import build_clusters, mark_all_soft, mark_exact_duplicates, refresh_sharp_used
from photocull.db import Database
from photocull.decide import (
    assign_burst_buckets,
    assign_solo_percentiles,
    burst_vlm_ids,
    decide_solo,
    keep_candidate,
    needs_aesthetic,
    penalty_of,
    score_cluster,
    solo_needs_vlm,
)
from photocull.ingest import pair_files, scan_files
from photocull.link import bucket_dest, place_link, safe_unlink
from photocull.models import AssetItem
from photocull.preview import preview_for_raster, preview_for_raw
from photocull.report import render
from photocull.sharpness import dhash_file, measure_file
from photocull.vlm import VlmClient


class _AutoType:
    pass


AUTO = _AutoType()
_LOG = contextvars.ContextVar("photocull_on_log", default=None)


def emit(message: str) -> None:
    print(message, flush=True)
    sink = _LOG.get()
    if sink is None:
        return
    try:
        sink(message)
    except Exception:
        return


@dataclass
class RunOptions:
    input_dir: Path
    output_dir: Path
    input_display: str
    output_display: str
    burst_gap: float = 2.0
    blur_floor: float = 20.0
    sharp_ok: float = 80.0
    aes_drop: float = 4.2
    aes_keep: float = 6.0
    vlm: str = "auto"
    strict_aesthetic: bool = False
    use_sharpness: bool = True
    use_burst: bool = True
    use_aesthetic: bool = True
    device: str = "cuda"
    aesthetic_weights: Path | None = None
    vlm_model: str = "qwen2.5vl:7b"
    vlm_base_url: str | None = None
    batch_size: int = 32


def run_job(opt: RunOptions, scorer=AUTO, vlm_client=AUTO, on_log=None) -> str:
    opt.input_dir = Path(opt.input_dir).resolve()
    opt.output_dir = Path(opt.output_dir).resolve()
    _check_dirs(opt)
    db = Database(opt.output_dir / "catalog.sqlite")
    db.open()
    token = _LOG.set(on_log)
    try:
        return _execute(db, opt, scorer, vlm_client)
    finally:
        _LOG.reset(token)
        db.close()


def _check_dirs(opt: RunOptions) -> None:
    if not opt.input_dir.is_dir():
        raise SystemExit(f"找不到輸入資料夾：{opt.input_display}")
    if opt.output_dir == opt.input_dir or opt.input_dir in opt.output_dir.parents:
        raise SystemExit("輸出資料夾不能放在輸入資料夾裡面，否則會把分桶結果再掃進去。")
    opt.output_dir.mkdir(parents=True, exist_ok=True)
    (opt.output_dir / "previews").mkdir(parents=True, exist_ok=True)


def _execute(db: Database, opt: RunOptions, scorer, vlm_client) -> str:
    notes: list[str] = []
    started = datetime.now().astimezone().isoformat(timespec="seconds")
    run_id = str(uuid.uuid4())
    db.start_run(
        run_id,
        started,
        opt.input_display,
        opt.output_display,
        {
            "burst_gap": opt.burst_gap,
            "blur_floor": opt.blur_floor,
            "sharp_ok": opt.sharp_ok,
            "aes_drop": opt.aes_drop,
            "aes_keep": opt.aes_keep,
            "vlm": opt.vlm,
            "strict_aesthetic": opt.strict_aesthetic,
            "use_sharpness": opt.use_sharpness,
            "use_burst": opt.use_burst,
            "use_aesthetic": opt.use_aesthetic,
            "device": opt.device,
            "input_resolved": str(opt.input_dir),
            "output_resolved": str(opt.output_dir),
        },
    )
    emit("盤點檔案…")
    files = scan_files(opt.input_dir, notes)
    old_files = {str(Path(path).resolve()): row for path, row in db.load_files().items()}
    seen = {str(item.path) for item in files}
    for path, row in old_files.items():
        if path not in seen and not row["missing"]:
            db.mark_missing(path)
            previous = db.load_links().get(str(row["file_id"]))
            if previous is not None:
                safe_unlink(Path(previous["dest_path"]))
                db.delete_link(str(row["file_id"]))
    assets = pair_files(files)
    metrics = {key: _row_dict(row) for key, row in db.load_metrics().items()}
    emit(f"抽預覽與清晰度（{len(files)} 個檔）…")
    for index, asset in enumerate(assets, start=1):
        _ensure_preview(db, opt, asset, old_files, metrics)
        if index % 25 == 0 or index == len(assets):
            emit(f"  已處理 {index}/{len(assets)}")
            db.commit()
    db.commit()
    refresh_sharp_used(assets, opt.blur_floor)
    if opt.use_burst:
        mark_exact_duplicates(assets)
        emit("分群…")
        bursts, solos = build_clusters(assets, opt.burst_gap)
        if opt.use_sharpness:
            mark_all_soft(bursts, opt.blur_floor)
    else:
        emit("分群…")
        bursts, solos = [], [asset for asset in assets if not asset.eliminated]
    if not opt.use_aesthetic:
        for asset in assets:
            asset.aesthetic = None
    active_scorer = None
    close_scorer = False
    if not opt.use_aesthetic:
        pass
    elif scorer is AUTO:
        active_scorer, note = load_scorer(opt.aesthetic_weights, opt.device, opt.batch_size)
        close_scorer = active_scorer is not None
        if note:
            notes.append(note)
    else:
        active_scorer = scorer
        close_scorer = scorer is not None
    try:
        if active_scorer is not None:
            _score_aesthetic(db, opt, assets, bursts, solos, metrics, active_scorer, notes)
            db.commit()
        if close_scorer and active_scorer is not None:
            active_scorer.close()
            close_scorer = False
        _prepare_scores(bursts, solos, opt)
        if opt.vlm != "off":
            emit("視覺語意…")
            client = vlm_client
            if client is AUTO:
                num_ctx = 8192 if opt.vlm == "fast" else None
                client = VlmClient(opt.vlm_model, opt.vlm_base_url, num_ctx=num_ctx)
            _run_vlm(db, opt, assets, bursts, solos, metrics, client, notes)
            db.commit()
        _prepare_scores(bursts, solos, opt)
        _assign_buckets(opt, bursts, solos)
        emit("建立硬連結…")
        failures = _link_all(db, opt, assets, notes)
        for asset in assets:
            for item in asset.files:
                db.save_file(item, asset.asset_id)
            db.save_asset(asset)
        db.set_meta("notes", json.dumps(notes, ensure_ascii=False))
        db.set_meta("link_failures", str(failures))
        db.set_meta("input_display", opt.input_display)
        db.set_meta("output_display", opt.output_display)
        db.commit()
        text = render(db)
        db.finish_run(run_id, datetime.now().astimezone().isoformat(timespec="seconds"), {"text": text})
        return text
    finally:
        if close_scorer and active_scorer is not None:
            active_scorer.close()


def _row_dict(row) -> dict:
    return {key: row[key] for key in row.keys()}


def _metric_flags(asset: AssetItem) -> set[str]:
    return {"preview_tiny"} if "preview_tiny" in asset.flags else set()


def _apply_metric(asset: AssetItem, data: dict) -> None:
    asset.dhash = data.get("dhash")
    asset.sharp_full = data.get("sharp_full")
    asset.sharp_center = data.get("sharp_center")
    asset.clip_hi = data.get("clip_hi")
    asset.clip_lo = data.get("clip_lo")
    asset.aesthetic = data.get("aesthetic")
    asset.defects = json.loads(data["defects_json"]) if data.get("defects_json") else []
    asset.scene = json.loads(data["scene_json"]) if data.get("scene_json") else []
    asset.description = data.get("description")
    asset.vlm_status = data.get("vlm_status")
    stored = set(json.loads(data.get("flags") or "[]"))
    if "preview_tiny" in stored:
        asset.flags.add("preview_tiny")


def _write_metric(db: Database, metrics: dict, asset: AssetItem, **overrides) -> None:
    current = metrics.get(asset.preview_sha256 or "", {})
    payload = {
        "preview_path": asset.preview_path,
        "dhash": asset.dhash if asset.dhash is not None else current.get("dhash"),
        "sharp_full": asset.sharp_full if asset.sharp_full is not None else current.get("sharp_full"),
        "sharp_center": asset.sharp_center if asset.sharp_center is not None else current.get("sharp_center"),
        "clip_hi": asset.clip_hi if asset.clip_hi is not None else current.get("clip_hi"),
        "clip_lo": asset.clip_lo if asset.clip_lo is not None else current.get("clip_lo"),
        "aesthetic": current.get("aesthetic"),
        "embedding": current.get("embedding"),
        "defects": json.loads(current["defects_json"]) if current.get("defects_json") else None,
        "scene": json.loads(current["scene_json"]) if current.get("scene_json") else None,
        "description": current.get("description"),
        "vlm_status": current.get("vlm_status"),
        "flags": _metric_flags(asset),
    }
    payload.update(overrides)
    db.save_metrics(asset.preview_sha256, **payload)
    metrics[asset.preview_sha256] = {
        "preview_sha256": asset.preview_sha256,
        "preview_path": payload["preview_path"],
        "dhash": payload["dhash"],
        "sharp_full": payload["sharp_full"],
        "sharp_center": payload["sharp_center"],
        "clip_hi": payload["clip_hi"],
        "clip_lo": payload["clip_lo"],
        "aesthetic": payload["aesthetic"],
        "embedding": payload["embedding"],
        "defects_json": None if payload["defects"] is None else json.dumps(payload["defects"], ensure_ascii=False),
        "scene_json": None if payload["scene"] is None else json.dumps(payload["scene"], ensure_ascii=False),
        "description": payload["description"],
        "vlm_status": payload["vlm_status"],
        "flags": json.dumps(sorted(payload["flags"]), ensure_ascii=False),
    }


def _reusable_sha(asset: AssetItem, old_files: dict) -> str | None:
    shas = []
    for item in asset.files:
        old = old_files.get(str(item.path))
        if old is None or old["sha_preview"] is None:
            return None
        if int(old["size"] or -1) != item.size or int(old["mtime_ns"] or -1) != item.mtime_ns:
            return None
        shas.append(str(old["sha_preview"]))
    if len(set(shas)) == 1:
        return shas[0]
    return None


def _ensure_preview(db: Database, opt: RunOptions, asset: AssetItem, old_files: dict, metrics: dict) -> None:
    sha = _reusable_sha(asset, old_files)
    dest = None if sha is None else opt.output_dir / "previews" / f"{sha}.jpg"
    if sha is not None and dest is not None and dest.exists():
        asset.preview_sha256 = sha
        asset.preview_path = str(dest)
        for item in asset.files:
            item.sha_preview = sha
    else:
        _extract_preview(opt, asset)
    if not asset.preview_sha256 or not asset.preview_path:
        asset.flags.add("preview_missing")
        asset.bucket = "review"
        return
    data = metrics.get(asset.preview_sha256)
    if data and "preview_tiny" in set(json.loads(data.get("flags") or "[]")):
        asset.flags.add("preview_tiny")
    tiny = "preview_tiny" in asset.flags
    complete = data is not None and data.get("dhash") and (tiny or data.get("sharp_full") is not None)
    if not complete:
        try:
            if tiny:
                asset.dhash = dhash_file(Path(asset.preview_path))
                _write_metric(db, metrics, asset)
            else:
                measured = measure_file(Path(asset.preview_path))
                asset.dhash = str(measured["dhash"])
                asset.sharp_full = float(measured["sharp_full"])
                asset.sharp_center = float(measured["sharp_center"])
                asset.clip_hi = float(measured["clip_hi"])
                asset.clip_lo = float(measured["clip_lo"])
                _write_metric(db, metrics, asset)
        except Exception as exc:
            asset.flags.add("preview_missing")
            asset.error = str(exc)
            asset.bucket = "review"
            return
        data = metrics.get(asset.preview_sha256)
    if data is not None:
        _apply_metric(asset, data)


def _extract_preview(opt: RunOptions, asset: AssetItem) -> None:
    raster = next((item for item in asset.active_files() if not item.is_raw), None)
    raw = next((item for item in asset.active_files() if item.is_raw), None)
    try:
        if raster is not None:
            sha, dest, kind = preview_for_raster(raster.path, opt.output_dir)
        elif raw is not None:
            sha, dest, kind = preview_for_raw(raw.path, opt.output_dir)
            if sha is None or dest is None:
                asset.flags.add("preview_missing")
                return
        else:
            asset.flags.add("preview_missing")
            return
    except Exception as exc:
        asset.flags.add("preview_missing")
        asset.error = str(exc)
        return
    asset.preview_sha256 = sha
    asset.preview_path = str(dest)
    for item in asset.files:
        item.sha_preview = sha
    if kind == "tiny":
        asset.flags.add("preview_tiny")


def _solo_ids(solos: list[AssetItem]) -> set[str]:
    return {asset.asset_id for asset in solos}


def _score_aesthetic(db, opt, assets, bursts, solos, metrics, scorer, notes: list[str]) -> None:
    solo_ids = _solo_ids(solos)
    pending = [
        asset
        for asset in assets
        if asset.aesthetic is None and needs_aesthetic(
            asset, opt.blur_floor, asset.asset_id in solo_ids, opt.use_sharpness
        )
    ]
    if not pending:
        return
    emit(f"美學評分（{len(pending)} 張）…")
    try:
        scored = scorer.score_paths(
            [Path(asset.preview_path) for asset in pending],
            [Path(asset.primary_path).name for asset in pending],
        )
    except Exception as exc:
        notes.append(f"美學評分中斷：{exc}")
        return
    if len(scored) != len(pending):
        notes.append("美學評分回傳數量不符，這次略過。")
        return
    for asset, (score, embedding) in zip(pending, scored):
        asset.aesthetic = float(score)
        _write_metric(db, metrics, asset, aesthetic=float(score), embedding=embedding)
    notes.append(f"這次新評美學分 {len(pending)} 張。")


def _prepare_scores(bursts, solos, opt) -> int:
    for members in bursts:
        score_cluster(members, use_sharpness=opt.use_sharpness)
    return assign_solo_percentiles(solos)


def _run_vlm(db, opt, assets, bursts, solos, metrics, client, notes: list[str]) -> None:
    scored_n = assign_solo_percentiles(solos)
    targets = _vlm_targets(bursts, solos, opt, scored_n)
    if not targets:
        return
    ready, reason = client.available()
    if not ready:
        notes.append(reason)
        for asset in targets:
            if asset.vlm_status not in {"ok", "vlm_error"}:
                asset.vlm_status = "vlm_skipped"
                _write_metric(db, metrics, asset, vlm_status="vlm_skipped")
        return
    called: set[str] = set()
    _call_targets(db, metrics, targets, client, called)
    _prepare_scores(bursts, solos, opt)
    if opt.vlm == "auto":
        extra = []
        for members in bursts:
            candidate = keep_candidate(members)
            if candidate is None or candidate.asset_id in called:
                continue
            if candidate.vlm_status in {"ok", "vlm_error"}:
                continue
            extra.append(candidate)
        _call_targets(db, metrics, extra, client, called)
    selected = {asset.asset_id for asset in targets} | called
    for asset in assets:
        if asset.eliminated or "preview_missing" in asset.flags:
            continue
        if asset.asset_id in selected:
            continue
        if asset.vlm_status in {"ok", "vlm_error", "vlm_skipped"}:
            continue
        asset.vlm_status = "not_gated"


def _vlm_targets(bursts, solos, opt, scored_n) -> list[AssetItem]:
    by_id = {asset.asset_id: asset for members in bursts for asset in members}
    by_id.update({asset.asset_id: asset for asset in solos})
    ordered: list[AssetItem] = []
    seen: set[str] = set()
    for members in bursts:
        for asset_id in burst_vlm_ids(members, opt.vlm):
            asset = by_id.get(asset_id)
            if asset is not None and asset_id not in seen:
                seen.add(asset_id)
                ordered.append(asset)
    for asset in solos:
        if solo_needs_vlm(asset, opt, scored_n, opt.vlm) and asset.asset_id not in seen:
            seen.add(asset.asset_id)
            ordered.append(asset)
    return ordered


def _call_targets(db, metrics, targets: list[AssetItem], client, called: set[str]) -> None:
    for asset in targets:
        if asset.asset_id in called or asset.vlm_status in {"ok", "vlm_error"}:
            called.add(asset.asset_id)
            continue
        if not asset.preview_path:
            continue
        result = client.describe(Path(asset.preview_path))
        called.add(asset.asset_id)
        asset.vlm_status = result["status"]
        if result["status"] == "ok":
            asset.defects = result["defects"]
            asset.scene = result["scene"]
            asset.description = result["description"] or None
            _write_metric(
                db,
                metrics,
                asset,
                defects=asset.defects,
                scene=asset.scene,
                description=asset.description,
                vlm_status="ok",
            )
        elif result["status"] == "vlm_error":
            asset.defects = []
            _write_metric(db, metrics, asset, defects=[], vlm_status="vlm_error")
        else:
            _write_metric(db, metrics, asset, vlm_status="vlm_skipped")


def _assign_buckets(opt, bursts, solos) -> None:
    scored_n = assign_solo_percentiles(solos)
    for members in bursts:
        assign_burst_buckets(members)
    for asset in solos:
        asset.bucket = decide_solo(asset, opt, scored_n)
        asset.penalty = penalty_of(asset.defects)
        asset.tech_pct = None
        asset.aes_pct = None
        asset.composite = None


def _link_all(db: Database, opt: RunOptions, assets: list[AssetItem], notes: list[str]) -> int:
    failures = 0
    manifest = opt.output_dir / "manifest.jsonl"
    previous_links = db.load_links()
    for asset in assets:
        if asset.bucket is None and asset.active_files():
            asset.bucket = "review"
        if not asset.bucket:
            continue
        for item in asset.active_files():
            try:
                dest = bucket_dest(opt.output_dir, asset.bucket, item.path, opt.input_dir)
            except ValueError as exc:
                failures += 1
                notes.append(f"無法計算連結路徑 {item.path}：{exc}")
                continue
            previous = previous_links.get(item.file_id)
            if previous is not None and Path(previous["dest_path"]) != dest:
                if not safe_unlink(Path(previous["dest_path"])):
                    notes.append(f"舊連結仍留在 {previous['dest_path']}")
            if place_link(item.path, dest, manifest):
                db.save_link(item.file_id, str(dest), asset.bucket)
            else:
                failures += 1
    return failures
