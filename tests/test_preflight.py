from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from photocull.pipeline import emit
from photocull.preflight import BLOCK, OK, WARN, folder_checks, ready_state
from photocull.summary import line_tag, result_badge


REPORT = """輸入：G:\\照片
輸出：G:\\照片_整理
掃描檔案：6
資產：6
精確重複：0
連拍簇：1
散張：3
分桶（硬連結檔案數）：
  保留（keep）：2
  覆核（review）：1
  失焦（reject/blur）：1
  美學偏低（reject/aesthetic）：0
  缺陷（reject/defect）：1
  完全重複（reject/exact_dup）：0
  近重複（reject/near_dup）：1
  連拍落選（reject/burst）：0
VLM 實際呼叫：2
硬連結失敗：0
"""


class SummaryTests(unittest.TestCase):
    def test_badge_and_tags(self) -> None:
        self.assertEqual(result_badge(REPORT), "保留 2 · 覆核 1 · 廢片 3")
        self.assertEqual(line_tag("  保留（keep）：2"), "keep")
        self.assertEqual(line_tag("  覆核（review）：1"), "review")
        self.assertEqual(line_tag("  缺陷（reject/defect）：1"), "reject")
        self.assertEqual(line_tag("硬連結失敗：0"), "plain")
        self.assertEqual(line_tag("硬連結失敗：2"), "fail")

    def test_failure_badge(self) -> None:
        text = REPORT.replace("硬連結失敗：0", "硬連結失敗：4")
        self.assertIn("連結失敗 4", result_badge(text))


class FolderCheckTests(unittest.TestCase):
    def test_nested_output_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "photos"
            source.mkdir()
            checks = folder_checks(source, source / "inside", fs_name="NTFS", image_count=(3, 0))
            self.assertTrue(any(item.key == "nest" and item.level == BLOCK for item in checks))

    def test_exfat_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "photos"
            source.mkdir()
            checks = folder_checks(source, root / "photos_整理", fs_name="exFAT", image_count=(3, 0))
            self.assertTrue(any(item.key == "fs" and item.level == BLOCK for item in checks))

    def test_ntfs_folder_can_run(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "photos"
            source.mkdir()
            checks = folder_checks(source, root / "photos_整理", fs_name="NTFS", image_count=(3, 1))
            levels = {item.key: item.level for item in checks}
            self.assertEqual(levels["fs"], OK)
            self.assertEqual(levels["photos"], OK)
            self.assertEqual(levels["other"], WARN)
            can_start, _text = ready_state(checks)
            self.assertTrue(can_start)

    def test_ext4_folder_can_run(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "photos"
            source.mkdir()
            checks = folder_checks(source, root / "photos_整理", fs_name="ext4", image_count=(2, 0))
            self.assertTrue(any(item.key == "fs" and item.level == OK for item in checks))
            can_start, _text = ready_state(checks)
            self.assertTrue(can_start)

    def test_missing_folder_blocks(self) -> None:
        missing = Path(tempfile.gettempdir()) / "photocull-missing-source"
        checks = folder_checks(missing, missing.parent / "out", fs_name="NTFS")
        self.assertEqual(checks[0].level, BLOCK)
        can_start, _text = ready_state(checks)
        self.assertFalse(can_start)


class EmitTests(unittest.TestCase):
    def test_log_callback_receives_progress(self) -> None:
        seen: list[str] = []
        from photocull.pipeline import _LOG

        token = _LOG.set(seen.append)
        try:
            emit("盤點檔案…")
        finally:
            _LOG.reset(token)
        self.assertEqual(seen, ["盤點檔案…"])


class WindowTests(unittest.TestCase):
    def test_window_lists_checks(self) -> None:
        from photocull.gui import App
        from photocull.preflight import Check

        app = App(
            loader=lambda _vlm: [Check("ram", "記憶體", OK, "總共 32.0 GB，目前可用 16.0 GB。")],
            visible=False,
        )
        try:
            app.root.update()
            self.assertEqual(app.root.title(), "本機照片整理")
            self.assertIn("新增資料夾", app.add_button.cget("text"))
        finally:
            app.root.destroy()


if __name__ == "__main__":
    unittest.main()
