from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from photocull.cluster import hamming
from photocull.ingest import read_pillow_capture
from photocull.pipeline import RunOptions, run_job
from photocull.sharpness import apply_floor, measure_file, measure_gray


def write_jpeg(path: Path, array: np.ndarray, when: datetime, serial: str = "BODY1") -> None:
    image = Image.fromarray(array, "RGB")
    exif = image.getexif()
    exif[0x010F] = "TestMake"
    exif[0x0110] = "TestBody"
    ifd = exif.get_ifd(0x8769)
    ifd[0x9003] = when.strftime("%Y:%m:%d %H:%M:%S")
    ifd[0x9291] = f"{when.microsecond // 1000:03d}"
    ifd[0xA431] = serial
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="JPEG", quality=95, exif=exif.tobytes())


def moment(seconds: float = 0) -> datetime:
    base = datetime(2024, 6, 1, 15, 0, 0, tzinfo=datetime.now().astimezone().tzinfo)
    return base + timedelta(seconds=seconds)


def checker(tile: int, width: int = 1280, height: int = 960) -> np.ndarray:
    yy, xx = np.ogrid[0:height, 0:width]
    mask = ((yy // tile) + (xx // tile)) % 2 == 0
    image = np.zeros((height, width, 3), dtype=np.uint8)
    image[mask] = (230, 230, 230)
    image[~mask] = (25, 25, 25)
    return image


def snapshot(root: Path) -> dict[str, str]:
    found = {}
    for path in root.rglob("*"):
        if path.is_file():
            found[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return found


def names(root: Path) -> set[str]:
    if not root.exists():
        return set()
    return {path.name for path in root.rglob("*") if path.is_file()}


class Scorer:
    def __init__(self):
        self.calls = 0

    def score_paths(self, paths, names=None):
        self.calls += len(paths)
        return [(3.0 if name and "lowaes" in name else 8.0, None) for name in (names or [])]

    def close(self):
        return None


class SharpnessTests(unittest.TestCase):
    def test_center_subject_is_not_treated_as_blur(self):
        gray = np.full((1024, 1024), 128, np.uint8)
        center = gray[256:768, 256:768]
        yy, xx = np.ogrid[0:512, 0:512]
        mask = ((yy // 8) + (xx // 8)) % 2 == 0
        center[mask] = 255
        center[~mask] = 0
        measured = measure_gray(gray)
        used, subject = apply_floor(measured["sharp_full"], measured["sharp_center"], 20)
        self.assertTrue(subject, measured)
        self.assertEqual(used, measured["sharp_center"])
        self.assertGreaterEqual(used, 20)


class PipelineTests(unittest.TestCase):
    def test_exif_roundtrip(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "exif.jpg"
            when = moment(1.25)
            write_jpeg(path, checker(32), when)
            captured, unreliable, camera = read_pillow_capture(path)
        self.assertFalse(unreliable)
        self.assertEqual(camera, "BODY1")
        self.assertIsNotNone(captured)
        self.assertAlmostEqual(abs((captured - when).total_seconds()), 0, delta=0.002)

    def test_cull_sample_folder(self):
        with tempfile.TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            source = root / "photos"
            output = root / "out"
            source.mkdir()
            burst = checker(16)
            sigma = self._burst_sigma(root, burst)
            write_jpeg(source / "burst_sharp.jpg", burst, moment(0))
            blurred = cv2.GaussianBlur(burst, (0, 0), sigma)
            for index in range(4):
                variant = np.clip(blurred.astype(np.int16) + index + 1, 0, 255).astype(np.uint8)
                write_jpeg(source / f"burst_soft_{index}.jpg", variant, moment(0.4 * (index + 1)))
            write_jpeg(source / "good.jpg", checker(32), moment(600))
            write_jpeg(source / "lowaes.jpg", checker(48), moment(1200))
            write_jpeg(source / "blur.jpg", np.full((960, 1280, 3), 140, np.uint8), moment(1800))
            height, width = 960, 1280
            shallow = np.full((height, width, 3), 128, np.uint8)
            y0, x0 = height // 4, width // 4
            patch = shallow[y0 : y0 + height // 2, x0 : x0 + width // 2]
            yy, xx = np.ogrid[0 : height // 2, 0 : width // 2]
            mask = ((yy // 8) + (xx // 8)) % 2 == 0
            patch[mask] = (255, 255, 255)
            patch[~mask] = (0, 0, 0)
            write_jpeg(source / "dof.jpg", shallow, moment(2400))
            bars = np.zeros((height, width, 3), dtype=np.uint8)
            tone = np.where((np.arange(width) // 12) % 2 == 0, 240, 15).astype(np.uint8)
            bars[:] = tone[None, :, None]
            write_jpeg(source / "dup_a.jpg", bars, moment(3000))
            write_jpeg(source / "dup_b.jpg", bars, moment(3000))
            (source / "no_preview.nef").write_bytes(b"not-a-raw-file")
            before = snapshot(source)
            scorer = Scorer()
            options = RunOptions(
                input_dir=source,
                output_dir=output,
                input_display=str(source),
                output_display=str(output),
                vlm="off",
            )
            summary = run_job(options, scorer=scorer)
            self.assertEqual(before, snapshot(source))
            keep = names(output / "keep")
            review = names(output / "review")
            blur = names(output / "reject" / "blur")
            exact = names(output / "reject" / "exact_dup")
            self.assertIn("burst_sharp.jpg", keep, summary)
            for index in range(4):
                self.assertNotIn(f"burst_soft_{index}.jpg", keep, summary)
            self.assertIn("good.jpg", keep, summary)
            self.assertNotIn("lowaes.jpg", keep, summary)
            self.assertIn("blur.jpg", blur, summary)
            self.assertNotIn("dof.jpg", blur, summary)
            self.assertEqual(len({"dup_a.jpg", "dup_b.jpg"} & keep), 1, summary)
            self.assertEqual({"dup_a.jpg", "dup_b.jpg"} - keep, {"dup_a.jpg", "dup_b.jpg"} & exact, summary)
            self.assertIn("no_preview.nef", review, summary)
            first_calls = scorer.calls
            self.assertGreater(first_calls, 0)
            run_job(options, scorer=scorer)
            self.assertEqual(scorer.calls, first_calls)
            self.assertEqual(before, snapshot(source))
            self.assertEqual(keep, names(output / "keep"))

    def _burst_sigma(self, root: Path, burst: np.ndarray) -> float:
        probe_sharp = root / "probe_sharp.jpg"
        probe_soft = root / "probe_soft.jpg"
        write_jpeg(probe_sharp, burst, moment())
        sharp_measure = measure_file(probe_sharp)
        for candidate in (1.5, 2.5, 4, 6):
            softened = cv2.GaussianBlur(burst, (0, 0), candidate)
            write_jpeg(probe_soft, softened, moment(0.4))
            soft_measure = measure_file(probe_soft)
            dist = hamming(str(sharp_measure["dhash"]), str(soft_measure["dhash"]))
            if dist is not None and dist <= 12 and soft_measure["sharp_full"] < sharp_measure["sharp_full"]:
                return candidate
        self.fail("找不到能留在同一連拍簇的模糊程度")
        return 0


if __name__ == "__main__":
    unittest.main()
