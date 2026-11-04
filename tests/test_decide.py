from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from photocull.cluster import build_clusters, hamming
from photocull.decide import (
    assign_burst_buckets,
    burst_vlm_ids,
    decide_solo,
    high_is_one,
    score_cluster,
    solo_needs_vlm,
)
from photocull.ingest import pair_files
from photocull.models import AssetItem, FileItem, file_id_for
from photocull.vlm import ollama_limits, parse_vlm_text


def _when(minutes: float = 0) -> datetime:
    base = datetime(2024, 1, 1, 12, 0, tzinfo=datetime.now().astimezone().tzinfo)
    return base + timedelta(minutes=minutes)


def _asset(name: str, *, sharp: float, aesthetic: float | None = None, flags: set[str] | None = None, defects=None, dhash="0000000000000000", when=None):
    path = Path(name)
    item = FileItem(
        file_id=file_id_for(path),
        path=path,
        size=100,
        ext=path.suffix or ".jpg",
        captured_dt=when or _when(),
        camera="BODY",
    )
    asset = AssetItem(
        asset_id=name,
        files=[item],
        sharp_used=sharp,
        sharp_full=sharp,
        sharp_center=sharp,
        aesthetic=aesthetic,
        dhash=dhash,
        flags=set(flags or []),
        defects=list(defects or []),
    )
    return asset


class _Opt:
    def __init__(self, **kwargs):
        self.blur_floor = kwargs.get("blur_floor", 20)
        self.sharp_ok = kwargs.get("sharp_ok", 80)
        self.aes_drop = kwargs.get("aes_drop", 4.2)
        self.aes_keep = kwargs.get("aes_keep", 6.0)
        self.strict_aesthetic = kwargs.get("strict_aesthetic", False)
        self.vlm = kwargs.get("vlm", "auto")


class DecideTests(unittest.TestCase):
    def test_percentile_highest_is_one(self):
        pct = high_is_one([8, 8, 8, 3])
        self.assertEqual(pct[3], 0.0)
        self.assertAlmostEqual(pct[0], 2 / 3)

    def test_solo_table(self):
        opt = _Opt()
        self.assertEqual(decide_solo(_asset("a.jpg", sharp=10, aesthetic=9), opt, 1), "reject_blur")
        self.assertEqual(decide_solo(_asset("b.jpg", sharp=100, aesthetic=7), opt, 1), "keep")
        self.assertEqual(decide_solo(_asset("c.jpg", sharp=100, aesthetic=3), opt, 1), "review")
        strict = _Opt(strict_aesthetic=True)
        self.assertEqual(decide_solo(_asset("d.jpg", sharp=100, aesthetic=3), strict, 1), "reject_aesthetic")
        self.assertEqual(decide_solo(_asset("e.jpg", sharp=40, aesthetic=7), opt, 1), "review")
        self.assertEqual(decide_solo(_asset("f.jpg", sharp=40, aesthetic=3), opt, 1), "reject_aesthetic")
        subject = _asset("g.jpg", sharp=40, aesthetic=3, flags={"subject_sharp"})
        self.assertEqual(decide_solo(subject, opt, 1), "review")
        bottom = _asset("h.jpg", sharp=100, aesthetic=7)
        bottom.solo_aes_pct = 0.1
        self.assertEqual(decide_solo(bottom, opt, 4), "review")
        self.assertEqual(decide_solo(_asset("i.jpg", sharp=100, aesthetic=None), opt, 1), "review")
        missing = _asset("j.jpg", sharp=100, aesthetic=9, flags={"preview_missing"})
        self.assertEqual(decide_solo(missing, opt, 1), "review")
        eyes = _asset("k.jpg", sharp=200, aesthetic=9, defects=["closed_eyes"])
        self.assertEqual(decide_solo(eyes, opt, 1), "reject_defect")

    def test_burst_keeps_the_sharp_one(self):
        members = [
            _asset(f"soft{i}.jpg", sharp=30, dhash="0000000000000000", when=_when(i / 60))
            for i in range(4)
        ]
        members.append(_asset("sharp.jpg", sharp=200, dhash="0000000000000001", when=_when(0.02)))
        score_cluster(members)
        assign_burst_buckets(members)
        buckets = {Path(asset.primary_path).name: asset.bucket for asset in members}
        self.assertEqual(buckets["sharp.jpg"], "keep")
        self.assertTrue(all(bucket != "keep" for name, bucket in buckets.items() if name != "sharp.jpg"))

    def test_hard_defect_does_not_keep_burst_leader(self):
        leader = _asset("eyes.jpg", sharp=200, defects=["blink"], dhash="0000000000000000")
        other = _asset("ok.jpg", sharp=80, dhash="0000000000000001", when=_when(0.01))
        members = [leader, other]
        score_cluster(members)
        assign_burst_buckets(members)
        self.assertEqual(leader.bucket, "review")
        self.assertEqual(other.bucket, "keep")

    def test_unreliable_time_is_not_a_burst(self):
        left = _asset("a.jpg", sharp=100, dhash="0000000000000000")
        right = _asset("b.jpg", sharp=90, dhash="0000000000000000", when=_when(0.01))
        right.files[0].time_unreliable = True
        bursts, solos = build_clusters([left, right], 2.0)
        self.assertEqual(bursts, [])
        self.assertEqual(len(solos), 2)

    def test_pair_raw_and_jpeg_within_two_seconds(self):
        moment = _when()
        raw = FileItem(
            file_id=file_id_for(Path("D:/roll/IMG_001.NEF")),
            path=Path("D:/roll/IMG_001.NEF"),
            size=10,
            ext=".nef",
            captured_dt=moment,
            is_raw=True,
            camera="BODY",
        )
        jpeg = FileItem(
            file_id=file_id_for(Path("D:/roll/IMG_001.JPG")),
            path=Path("D:/roll/IMG_001.JPG"),
            size=5,
            ext=".jpg",
            captured_dt=moment + timedelta(seconds=1),
            camera="BODY",
        )
        late = FileItem(
            file_id=file_id_for(Path("D:/roll/IMG_002.JPG")),
            path=Path("D:/roll/IMG_002.JPG"),
            size=5,
            ext=".jpg",
            captured_dt=moment + timedelta(seconds=30),
            camera="BODY",
        )
        assets = pair_files([raw, jpeg, late])
        self.assertEqual(len(assets), 2)
        paired = next(asset for asset in assets if len(asset.files) == 2)
        self.assertEqual({item.ext for item in paired.files}, {".nef", ".jpg"})

    def test_vlm_parser_filters_unknown_defects(self):
        parsed = parse_vlm_text('說明\n{"scene":["night"],"description":"夜景","defects":["blink","nope"],"recommend":"reject"}')
        self.assertEqual(parsed["defects"], ["blink"])
        self.assertEqual(parsed["recommend"], "reject")

    def test_fast_vlm_looks_at_keep_candidates_only(self):
        opt = _Opt(vlm="fast")
        keep = _asset("keep.jpg", sharp=100, aesthetic=7)
        gray = _asset("gray.jpg", sharp=100, aesthetic=5)
        self.assertTrue(solo_needs_vlm(keep, opt, 2, "fast"))
        self.assertFalse(solo_needs_vlm(gray, opt, 2, "fast"))
        soft = _asset("soft.jpg", sharp=30, aesthetic=3, dhash="0000000000000000")
        sharp = _asset("sharp.jpg", sharp=200, aesthetic=8, dhash="0000000000000001", when=_when(0.01))
        members = [soft, sharp]
        score_cluster(members)
        self.assertEqual(burst_vlm_ids(members, "fast"), [sharp.asset_id])

    def test_sharpness_off_uses_aesthetic_only(self):
        opt = _Opt()
        opt.use_sharpness = False
        self.assertEqual(decide_solo(_asset("blur.jpg", sharp=5, aesthetic=8), opt, 1), "keep")
        self.assertEqual(decide_solo(_asset("plain.jpg", sharp=200, aesthetic=3), opt, 1), "review")

    def test_fast_context_is_short(self):
        self.assertEqual(ollama_limits(8192), {"num_ctx": 8192, "num_predict": 256})
        self.assertIsNone(ollama_limits(None))

    def test_hamming(self):
        self.assertEqual(hamming("0000000000000000", "0000000000000001"), 1)


if __name__ == "__main__":
    unittest.main()
