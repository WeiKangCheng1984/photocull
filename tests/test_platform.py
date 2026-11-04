from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from photocull.platform import is_windows, open_folder, supports_hardlinks


class PlatformTests(unittest.TestCase):
    def test_hardlink_filesystems(self) -> None:
        self.assertTrue(supports_hardlinks("NTFS"))
        self.assertTrue(supports_hardlinks("ext4"))
        self.assertTrue(supports_hardlinks("XFS"))
        self.assertTrue(supports_hardlinks("btrfs"))
        self.assertFalse(supports_hardlinks("exFAT"))
        self.assertFalse(supports_hardlinks("vfat"))

    def test_open_folder_routes_by_os(self) -> None:
        target = Path("C:/photos") if is_windows() else Path("/photos")
        if is_windows():
            with mock.patch("photocull.platform_windows.open_folder") as opener:
                open_folder(target)
                opener.assert_called_once_with(target)
        else:
            with mock.patch("photocull.platform_linux.open_folder") as opener:
                open_folder(target)
                opener.assert_called_once_with(target)


if __name__ == "__main__":
    unittest.main()
