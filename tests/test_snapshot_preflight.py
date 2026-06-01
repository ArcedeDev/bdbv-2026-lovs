# SPDX-License-Identifier: Apache-2.0
"""Tests for snapshot_preflight.py, the pre-run completeness gate."""
from __future__ import annotations

import contextlib
import io
import unittest

import snapshot_preflight


class TestSnapshotPreflight(unittest.TestCase):

    def _run(self, as_of: str) -> tuple[int, str]:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = snapshot_preflight.run(as_of)
        return rc, buf.getvalue()

    def test_ready_for_current_snapshot_date(self):
        rc, out = self._run("2026-05-20")
        self.assertEqual(rc, 0)
        self.assertIn("READY", out)
        # The new cross-border targets must be present and carry centroids.
        self.assertIn("arua-uga", out)
        self.assertIn("nebbi-uga", out)
        self.assertNotIn("MISSING CENTROID", out)
        self.assertIn("staged_observations contract ok", out)
        self.assertIn("watch_signals contract ok", out)

    def test_not_ready_when_as_of_newer_than_manifest(self):
        rc, out = self._run("2099-12-31")
        self.assertEqual(rc, 3)
        self.assertIn("GAP", out)

    def test_may_21_ready_after_official_source_archived(self):
        rc, out = self._run("2026-05-21")
        self.assertEqual(rc, 0)
        self.assertIn("READY", out)

    def test_may_22_ready_after_official_source_archived(self):
        rc, out = self._run("2026-05-22")
        self.assertEqual(rc, 0)
        self.assertIn("READY", out)
        self.assertIn("Official source-zone coverage", out)
        self.assertIn("zone-attributed confirmed total=79", out)

    def test_lists_all_five_leverages(self):
        _, out = self._run("2026-05-20")
        for lever in (
            "zone_attributed_counts",
            "onset_dates",
            "validated_centroids",
            "mobility_transport_flow",
            "confirmation_latency",
        ):
            self.assertIn(lever, out)

    def test_self_edge_target_covered_by_calibration_block_is_not_a_gap(self):
        """Goma-cod is both a source zone (SitRep007 May 22) and a target
        (snapshot_targets.json after PR #20). Because the May-26 calibration
        block pins three Ituri-source -> goma-cod corridors, this overlap is
        legitimate self-edge under the pinned calibration, not a watch-set
        drift gap. See snapshot_contract.py for the matching corridor-count
        self-edge exclusion."""
        _, out = self._run("2026-05-22")
        self.assertIn(
            "self-edge goma-cod: source+target, covered by an active calibration block",
            out,
        )
        # And the legacy hard-gap phrase must NOT appear for goma-cod.
        self.assertNotIn(
            "GAP: goma-cod is both a confirmed source zone and a candidate target",
            out,
        )


if __name__ == "__main__":
    unittest.main()
