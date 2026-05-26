# SPDX-License-Identifier: Apache-2.0
"""Tests for the higher-of-valid-primaries reconciliation-invariant gate."""
from __future__ import annotations

import json
import pathlib
import unittest

import release_snapshot

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


class TestReconciliationInvariants(unittest.TestCase):
    def test_current_snapshot_satisfies_invariants(self):
        summary = json.loads(
            (REPO_ROOT / "data" / "live-bdbv-2026-output.json").read_text(encoding="utf-8")
        )
        self.assertEqual([], release_snapshot.check_reconciliation_invariants(summary))

    def test_source_review_primary_is_rejected(self):
        # Promoting the source_review DRC MoH sitrep-009 aggregate (the 179-over-177
        # defect class) must fail the guard.
        summary = {
            "reported_counts": {
                "deaths": {
                    "min": 106,
                    "max": 179,
                    "primary": 179,
                    "primary_source_id": "drc-moh-epidemie-dashboard-sitrep-009-graphql-2026-05-24",
                    "conflicting_source_ids": ["cdc-current-situation-2026-05-25"],
                }
            }
        }
        problems = release_snapshot.check_reconciliation_invariants(summary)
        self.assertTrue(any("source_review" in p for p in problems), problems)

    def test_primary_below_band_ceiling_is_rejected(self):
        summary = {
            "reported_counts": {
                "deaths": {
                    "min": 106,
                    "max": 223,
                    "primary": 179,
                    "primary_source_id": "cdc-current-situation-2026-05-25",
                    "conflicting_source_ids": ["who-dg-remarks-bdbv-2026-05-22"],
                }
            }
        }
        problems = release_snapshot.check_reconciliation_invariants(summary)
        self.assertTrue(any("band ceiling" in p for p in problems), problems)

    def test_self_conflict_is_rejected(self):
        summary = {
            "reported_counts": {
                "deaths": {
                    "min": 106,
                    "max": 223,
                    "primary": 223,
                    "primary_source_id": "cdc-current-situation-2026-05-25",
                    "conflicting_source_ids": ["cdc-current-situation-2026-05-25"],
                }
            }
        }
        problems = release_snapshot.check_reconciliation_invariants(summary)
        self.assertTrue(any("own conflict trail" in p for p in problems), problems)

    def test_empty_conflict_trail_is_rejected(self):
        summary = {
            "reported_counts": {
                "deaths": {
                    "min": 106,
                    "max": 223,
                    "primary": 223,
                    "primary_source_id": "cdc-current-situation-2026-05-25",
                    "conflicting_source_ids": [],
                }
            }
        }
        problems = release_snapshot.check_reconciliation_invariants(summary)
        self.assertTrue(any("conflict trail" in p for p in problems), problems)


if __name__ == "__main__":
    unittest.main()
