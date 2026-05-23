# SPDX-License-Identifier: Apache-2.0
"""Tests for public repository hygiene checks."""
from __future__ import annotations

import unittest

from lovs import public_repo_hygiene


class TestPublicRepoHygiene(unittest.TestCase):
    def test_clean_current_tree(self):
        self.assertEqual([], public_repo_hygiene.scan_tracked_files())

    def test_detects_tool_provenance_marker(self):
        marker = "prepared by " + "co" + "dex"
        self.assertTrue(public_repo_hygiene.contains_marker(marker))

    def test_all_hygiene_scans_are_clean(self):
        self.assertEqual([], public_repo_hygiene.scan_all())


if __name__ == "__main__":
    unittest.main()
