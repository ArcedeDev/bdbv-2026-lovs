# SPDX-License-Identifier: Apache-2.0
"""Tests for release_snapshot.py release gates."""
from __future__ import annotations

import unittest

import release_snapshot


class TestReleaseGates(unittest.TestCase):
    def test_public_artifact_leak_scan_is_clean(self):
        self.assertEqual([], release_snapshot.scan_public_artifacts_for_leaks())


if __name__ == "__main__":
    unittest.main()
