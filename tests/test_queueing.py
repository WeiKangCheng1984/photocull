from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from photocull.queueing import iter_pause, rest_seconds, unique_photo_folders


class QueueingTests(unittest.TestCase):
    def test_skip_result_folders_and_duplicates(self) -> None:
        first = Path("G:/活動/過年")
        chosen, notes = unique_photo_folders([first, first, Path("G:/活動/過年_整理")])
        self.assertEqual(chosen, [first.resolve()])
        self.assertEqual(len(notes), 1)
        self.assertIn("整理結果", notes[0])

    def test_pause_stops_before_the_full_wait(self) -> None:
        calls = {"n": 0}

        def stop() -> bool:
            return calls["n"] >= 2

        def sleep(_seconds: int) -> None:
            calls["n"] += 1

        self.assertEqual(list(iter_pause(5, stop, sleep)), [5, 4])
        self.assertEqual(calls["n"], 2)

    def test_rest_minutes_become_seconds(self) -> None:
        self.assertEqual(rest_seconds(4), 240)
        self.assertEqual(rest_seconds(0), 0)
        self.assertEqual(rest_seconds(1.5), 90)
        with self.assertRaises(ValueError):
            rest_seconds(-1)


if __name__ == "__main__":
    unittest.main()
