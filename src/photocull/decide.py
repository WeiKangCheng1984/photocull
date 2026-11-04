from __future__ import annotations

from photocull.cluster import hamming
from photocull.models import AssetItem

HARD_DEFECTS = {"closed_eyes", "blink"}
PENALTY = {
    "closed_eyes": 0.50,
    "blink": 0.50,
    "motion_blur": 0.25,
    "out_of_focus": 0.25,
    "cut_off_subject": 0.15,
    "awkward_crop": 0.15,
    "blown_highlights": 0.10,
    "underexposed": 0.10,
    "distracting_object": 0.10,
}


def penalty_of(defects: list[str]) -> float:
    values = [PENALTY[name] for name in defects if name in PENALTY]
    return max(values) if values else 0.0


def has_hard_defect(defects: list[str]) -> bool:
    return any(name in HARD_DEFECTS for name in defects)


def high_is_one(values: list[float]) -> list[float]:
    """數值愈高，百分位愈接近 1。相同分數共用平均名次。"""
    count = len(values)
    if count == 0:
        return []
    if count == 1:
        return [1.0]
    order = sorted(range(count), key=lambda index: values[index])
    ranks = [0.0] * count
    cursor = 0
    while cursor < count:
        end = cursor
        while end + 1 < count and values[order[end + 1]] == values[order[cursor]]:
            end += 1
        average = (cursor + 1 + end + 1) / 2
        for index in range(cursor, end + 1):
            ranks[order[index]] = average
        cursor = end + 1
    return [(rank - 1) / (count - 1) for rank in ranks]


def _best(members: list[AssetItem]) -> AssetItem:
    return sorted(
        members,
        key=lambda asset: (
            -(asset.composite if asset.composite is not None else -1e9),
            -(asset.sharp_used if asset.sharp_used is not None else -1e9),
            asset.primary_path.casefold(),
        ),
    )[0]


def score_cluster(members: list[AssetItem], use_sharpness: bool = True) -> None:
    if len(members) < 2:
        return
    if use_sharpness:
        tech = high_is_one([asset.sharp_used or 0.0 for asset in members])
        for asset, pct in zip(members, tech):
            asset.tech_pct = pct
    else:
        for asset in members:
            asset.tech_pct = None
    if all(asset.aesthetic is not None for asset in members):
        aes = high_is_one([float(asset.aesthetic) for asset in members])
        for asset, pct in zip(members, aes):
            asset.aes_pct = pct
    else:
        for asset in members:
            asset.aes_pct = None
    for asset in members:
        asset.penalty = penalty_of(asset.defects)
        if asset.tech_pct is not None and asset.aes_pct is not None:
            asset.composite = 0.5 * asset.tech_pct + 0.5 * asset.aes_pct - asset.penalty
        elif asset.aes_pct is not None:
            asset.composite = asset.aes_pct - asset.penalty
        elif asset.tech_pct is not None:
            asset.composite = asset.tech_pct - asset.penalty
        else:
            asset.composite = None


def assign_solo_percentiles(solos: list[AssetItem]) -> int:
    scored = [asset for asset in solos if asset.aesthetic is not None and not asset.eliminated]
    if len(scored) < 2:
        for asset in scored:
            asset.solo_aes_pct = 1.0
        return len(scored)
    pcts = high_is_one([float(asset.aesthetic) for asset in scored])
    for asset, pct in zip(scored, pcts):
        asset.solo_aes_pct = pct
    return len(scored)


def decide_solo(asset: AssetItem, opt, scored_solo_count: int) -> str:
    if "preview_missing" in asset.flags or "preview_tiny" in asset.flags:
        return "review"
    if has_hard_defect(asset.defects):
        return "reject_defect"
    if not getattr(opt, "use_sharpness", True):
        if asset.aesthetic is None:
            return "review"
        bottom = scored_solo_count >= 4 and (asset.solo_aes_pct or 1.0) < 0.25
        if asset.aesthetic >= opt.aes_keep and not bottom:
            return "keep"
        if asset.aesthetic >= opt.aes_keep:
            return "review"
        if asset.aesthetic < opt.aes_drop and opt.strict_aesthetic:
            return "reject_aesthetic"
        return "review"
    subject = "subject_sharp" in asset.flags
    sharp = asset.sharp_used
    if sharp is None:
        return "review"
    if sharp < opt.blur_floor and not subject:
        return "reject_blur"
    if asset.aesthetic is None:
        return "review"
    bottom = False
    if scored_solo_count >= 4:
        bottom = (asset.solo_aes_pct or 1.0) < 0.25
    if sharp >= opt.sharp_ok and asset.aesthetic >= opt.aes_keep and not bottom:
        return "keep"
    if sharp >= opt.sharp_ok and asset.aesthetic >= opt.aes_keep and bottom:
        return "review"
    if sharp >= opt.sharp_ok and asset.aesthetic < opt.aes_drop:
        return "reject_aesthetic" if opt.strict_aesthetic else "review"
    if opt.blur_floor <= sharp < opt.sharp_ok and asset.aesthetic >= opt.aes_keep:
        return "review"
    if opt.blur_floor <= sharp < opt.sharp_ok and asset.aesthetic < opt.aes_keep:
        return "review" if subject else "reject_aesthetic"
    return "review"


def _gap_bucket(champion: AssetItem, asset: AssetItem) -> str:
    if champion.composite is None or asset.composite is None:
        return "review"
    gap = champion.composite - asset.composite
    distance = hamming(champion.dhash, asset.dhash)
    if gap <= 0.08:
        return "review"
    if distance is not None and distance <= 6 and gap > 0.15:
        return "reject_near_dup"
    return "reject_burst"


def assign_burst_buckets(members: list[AssetItem]) -> None:
    for asset in members:
        asset.bucket = None
        asset.tech_pct = None if len(members) < 2 else asset.tech_pct
    if any("all_soft" in asset.flags for asset in members):
        champion = _best(members)
        champion.bucket = "review"
        for asset in members:
            if asset.asset_id != champion.asset_id:
                asset.bucket = "reject_blur"
        return
    score_leader = _best(members)
    if has_hard_defect(score_leader.defects):
        score_leader.bucket = "review"
        pool = [
            asset
            for asset in members
            if asset.asset_id != score_leader.asset_id and not has_hard_defect(asset.defects)
        ]
        if pool:
            keep = _best(pool)
            keep.bucket = "keep"
            reference = keep
        else:
            reference = score_leader
    else:
        score_leader.bucket = "keep"
        reference = score_leader
    for asset in members:
        if asset.bucket is not None:
            continue
        asset.bucket = _gap_bucket(reference, asset)


def keep_candidate(members: list[AssetItem]) -> AssetItem | None:
    """目前若要留代表，會是哪一張。供 VLM 補看改判後的新代表。"""
    if not members or any("all_soft" in asset.flags for asset in members):
        return None
    leader = _best(members)
    if not has_hard_defect(leader.defects):
        return leader
    pool = [asset for asset in members if not has_hard_defect(asset.defects)]
    return _best(pool) if pool else None


def is_extreme_blur(asset: AssetItem, blur_floor: float) -> bool:
    if "subject_sharp" in asset.flags or asset.sharp_used is None:
        return False
    if "all_soft" in asset.flags:
        return True
    return asset.sharp_used < blur_floor


def needs_aesthetic(asset: AssetItem, blur_floor: float, solo: bool, use_sharpness: bool = True) -> bool:
    if asset.eliminated or not asset.preview_path:
        return False
    if "preview_missing" in asset.flags or "preview_tiny" in asset.flags:
        return False
    if "all_soft" in asset.flags:
        return False
    if use_sharpness and solo and is_extreme_blur(asset, blur_floor):
        return False
    return True


def solo_needs_vlm(asset: AssetItem, opt, scored_solo_count: int, mode: str) -> bool:
    if mode == "off" or asset.eliminated:
        return False
    if "preview_missing" in asset.flags or "preview_tiny" in asset.flags:
        return False
    if is_extreme_blur(asset, opt.blur_floor) and "all_soft" not in asset.flags and getattr(opt, "use_sharpness", True):
        return False
    if mode == "all":
        return True
    if mode == "fast":
        return decide_solo(asset, opt, scored_solo_count) == "keep"
    if asset.sharp_used is None or asset.aesthetic is None:
        return False
    if decide_solo(asset, opt, scored_solo_count) == "keep":
        return False
    weak_tech = opt.blur_floor <= asset.sharp_used < opt.sharp_ok
    mid_aes = opt.aes_drop <= asset.aesthetic < opt.aes_keep
    low_aes = asset.sharp_used >= opt.sharp_ok and asset.aesthetic < opt.aes_drop
    return weak_tech or mid_aes or low_aes


def burst_vlm_ids(members: list[AssetItem], mode: str) -> list[str]:
    if mode == "off" or not members or any("all_soft" in asset.flags for asset in members):
        return []
    if mode == "all":
        return [asset.asset_id for asset in members]
    if mode not in {"auto", "fast"}:
        return []
    ranked = sorted(
        members,
        key=lambda asset: (
            -(asset.composite if asset.composite is not None else -1e9),
            -(asset.sharp_used if asset.sharp_used is not None else -1e9),
            asset.primary_path.casefold(),
        ),
    )
    chosen = [ranked[0].asset_id]
    for asset in ranked[:3]:
        if asset.tech_pct is None or asset.aes_pct is None:
            continue
        if abs(asset.tech_pct - asset.aes_pct) > 0.35:
            chosen.append(asset.asset_id)
    unique: list[str] = []
    for asset_id in chosen:
        if asset_id not in unique:
            unique.append(asset_id)
    return unique
