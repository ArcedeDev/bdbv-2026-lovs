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


class TestPublicationStateGuard(unittest.TestCase):
    def test_flags_not_for_publication_subjects(self):
        subjects = [
            "Release LOVS snapshot 2026-05-24 (review-only; not published)",
            "Add read-only calibration resolver and cycle-status composer",
            "do not publish: scratch",
            "Prepare May 24 publication surface",
        ]
        flagged = public_repo_hygiene.find_publication_state_markers(subjects)
        self.assertEqual(
            [
                "Release LOVS snapshot 2026-05-24 (review-only; not published)",
                "do not publish: scratch",
            ],
            flagged,
        )

    def test_read_only_is_not_review_only(self):
        # The calibration commit subject uses "read-only"; it must not trip "review-only".
        self.assertEqual(
            [],
            public_repo_hygiene.find_publication_state_markers(
                ["Add read-only calibration resolver"]
            ),
        )

    def test_clean_subjects_pass(self):
        self.assertEqual(
            [],
            public_repo_hygiene.find_publication_state_markers(
                ["Release LOVS snapshot 2026-05-24", "Add calibration resolver"]
            ),
        )

    def test_live_tree_has_no_unpublished_markers(self):
        self.assertEqual([], public_repo_hygiene.scan_new_commit_publication_state())


if __name__ == "__main__":
    unittest.main()
