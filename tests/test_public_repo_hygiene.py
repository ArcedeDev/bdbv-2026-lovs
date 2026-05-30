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

    def test_sensitive_public_paths_are_blocked(self):
        findings = public_repo_hygiene.scan_sensitive_public_paths(
            [
                ".process/2026-05-30-example/plan.md",
                ".specs/private-methodology.md",
                "run_local.py",
                "VISUAL_CONVENTIONS.md",
                "lovs/lovs_visibility.py",
                "data/calibration-ledger.json",
                "data/evidence-chains.json",
                "data/external_sources/source_registry.json",
                "data/bundibugyo-2026/raw/example",
                "data/live-bdbv-2026-output.json",
                "data/bundibugyo-2026/manifest.json",
                "tests/data/lovs/fixture.json",
                "tests/test_lovs_visibility.py",
                "deliverables/public-health-dataset/model_outputs.csv",
                "brief/brief.html",
            ]
        )
        self.assertEqual(
            [
                ".process/2026-05-30-example/plan.md: internal process artifact",
                ".specs/private-methodology.md: internal methodology spec",
                "run_local.py: partner local-data runner",
                "VISUAL_CONVENTIONS.md: internal visual convention",
                "lovs/lovs_visibility.py: method implementation module",
                "data/calibration-ledger.json: calibration workbench data",
                "data/evidence-chains.json: method evidence chains",
                "data/external_sources/source_registry.json: source-prep registry",
                "data/bundibugyo-2026/raw/example: source archive bytes",
                "data/live-bdbv-2026-output.json: rich internal snapshot",
                "data/bundibugyo-2026/manifest.json: rich source manifest",
                "tests/data/lovs/fixture.json: test fixture data",
                "tests/test_lovs_visibility.py: method test module",
                "deliverables/public-health-dataset/model_outputs.csv: machine-readable dataset export",
            ],
            findings,
        )

    def test_public_support_paths_are_allowed(self):
        findings = public_repo_hygiene.scan_sensitive_public_paths(
            [
                "README.md",
                "lovs/__init__.py",
                "lovs/public_repo_hygiene.py",
                "lovs/public_exports.py",
                "tests/test_public_repo_hygiene.py",
                "tests/test_public_exports.py",
                "deliverables/brief.pdf",
            ]
        )
        self.assertEqual([], findings)


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
