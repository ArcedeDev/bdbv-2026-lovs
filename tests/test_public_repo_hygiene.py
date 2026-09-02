# SPDX-License-Identifier: Apache-2.0
"""Tests for public repository hygiene checks."""
from __future__ import annotations

import unittest
from unittest import mock

from lovs import public_repo_hygiene


class TestPublicRepoHygiene(unittest.TestCase):
    def test_clean_current_tree(self):
        self.assertEqual([], public_repo_hygiene.scan_tracked_files())

    def test_detects_tool_provenance_marker(self):
        marker = "prepared by " + "co" + "dex"
        self.assertTrue(public_repo_hygiene.contains_marker(marker))

    def test_all_hygiene_scans_are_clean(self):
        self.assertEqual([], public_repo_hygiene.scan_all())

    def test_workflow_ref_is_not_treated_as_repository_content(self):
        marker_ref = "refs/heads/" + "co" + "dex" + "/release"
        with mock.patch.dict("os.environ", {"GITHUB_HEAD_REF": marker_ref}):
            self.assertEqual([], public_repo_hygiene.scan_all())
            self.assertNotEqual([], public_repo_hygiene.scan_environment_refs())


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


class TestMaintainerCoauthorship(unittest.TestCase):
    """The gate exists to keep TOOL provenance out, not human co-authorship.

    GitHub appends a co-authorship trailer to every squash merge whose commit
    author differs from the merging account. With the generic trailer treated as
    a provenance marker, main went red on its own hygiene gate after each merge
    and the only remedy was pinning another immutable SHA every cycle. A tool
    co-author is still caught by the vendor and product names, which is what the
    gate is actually for.

    The trailer is assembled from parts throughout, so this file does not itself
    carry a literal marker for the tracked-file scan to find.
    """

    TRAILER = "Co-authored" + "-by"

    def test_maintainer_trailer_is_not_a_provenance_marker(self):
        for address in public_repo_hygiene.MAINTAINER_COAUTHOR_ADDRESSES:
            with self.subTest(address=address):
                self.assertFalse(
                    public_repo_hygiene.contains_marker(
                        f"{self.TRAILER}: A Maintainer <{address}>"
                    )
                )

    def test_tool_coauthor_still_fails(self):
        vendor = "anth" + "ropic"
        product = "Clau" + "de"
        self.assertTrue(
            public_repo_hygiene.contains_marker(
                f"{self.TRAILER}: {product} <noreply@{vendor}.com>"
            )
        )

    def test_unknown_third_party_coauthor_still_fails(self):
        self.assertTrue(
            public_repo_hygiene.contains_marker(
                f"{self.TRAILER}: Someone <someone@example.invalid>"
            )
        )

    def test_exemption_does_not_mask_a_marker_elsewhere_in_the_message(self):
        message = (
            f"{self.TRAILER}: A Maintainer <frans@arcede.com>\n"
            + "Generated" + " with a tool"
        )
        self.assertTrue(public_repo_hygiene.contains_marker(message))
