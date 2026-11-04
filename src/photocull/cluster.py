from __future__ import annotations

import uuid

from photocull.models import NS, AssetItem
from photocull.sharpness import apply_floor


def hamming(left: str | None, right: str | None) -> int | None:
    if not left or not right:
        return None
    try:
        return (int(left, 16) ^ int(right, 16)).bit_count()
    except ValueError:
        return None


def mark_exact_duplicates(assets: list[AssetItem]) -> None:
    groups: dict[str, list[AssetItem]] = {}
    for asset in assets:
        if not asset.active_files() or not asset.preview_sha256:
            continue
        groups.setdefault(asset.preview_sha256, []).append(asset)
    for group in groups.values():
        if len(group) < 2:
            continue
        survivor = min(
            group,
            key=lambda asset: (
                0 if asset.has_raw else 1,
                -asset.max_size,
                asset.primary_path.casefold(),
            ),
        )
        for asset in group:
            if asset.asset_id == survivor.asset_id:
                continue
            asset.eliminated = True
            asset.bucket = "reject_exact_dup"
            asset.survivor_asset_id = survivor.asset_id


def _cluster_id(members: list[AssetItem]) -> str:
    key = ",".join(sorted(member.asset_id for member in members))
    return str(uuid.uuid5(NS, "cluster:" + key))


def _can_burst(asset: AssetItem) -> bool:
    if asset.eliminated or not asset.active_files():
        return False
    if asset.time_unreliable or asset.captured_dt is None or not asset.dhash:
        return False
    if "preview_missing" in asset.flags or "preview_tiny" in asset.flags:
        return False
    return True


def build_clusters(assets: list[AssetItem], burst_gap: float) -> tuple[list[list[AssetItem]], list[AssetItem]]:
    bursts: list[list[AssetItem]] = []
    solos: list[AssetItem] = []
    by_camera: dict[str, list[AssetItem]] = {}
    for asset in assets:
        if asset.eliminated or not asset.active_files():
            continue
        if not _can_burst(asset):
            solos.append(asset)
            continue
        by_camera.setdefault(asset.camera, []).append(asset)
    for group in by_camera.values():
        ordered = sorted(group, key=lambda asset: (asset.captured_dt, asset.primary_path.casefold()))
        current: list[AssetItem] = []
        representative: AssetItem | None = None
        for asset in ordered:
            if representative is None or asset.captured_dt is None or representative.captured_dt is None:
                if current:
                    _flush(current, bursts, solos)
                current = [asset]
                representative = asset
                continue
            delta = (asset.captured_dt - representative.captured_dt).total_seconds()
            distance = hamming(asset.dhash, representative.dhash)
            if delta > burst_gap or distance is None or distance > 12:
                _flush(current, bursts, solos)
                current = [asset]
                representative = asset
            else:
                current.append(asset)
        if current:
            _flush(current, bursts, solos)
    for members in bursts:
        cid = _cluster_id(members)
        for asset in members:
            asset.cluster_id = cid
    for asset in solos:
        asset.cluster_id = _cluster_id([asset])
    return bursts, solos


def _flush(current: list[AssetItem], bursts: list[list[AssetItem]], solos: list[AssetItem]) -> None:
    if len(current) >= 2:
        bursts.append(list(current))
    elif current:
        solos.append(current[0])


def mark_all_soft(bursts: list[list[AssetItem]], blur_floor: float) -> None:
    for members in bursts:
        values = [asset.sharp_used for asset in members if asset.sharp_used is not None]
        if values and max(values) < blur_floor:
            for asset in members:
                asset.flags.add("all_soft")


def refresh_sharp_used(assets: list[AssetItem], blur_floor: float) -> None:
    for asset in assets:
        used, subject = apply_floor(asset.sharp_full, asset.sharp_center, blur_floor)
        asset.sharp_used = used
        if subject:
            asset.flags.add("subject_sharp")
        else:
            asset.flags.discard("subject_sharp")
