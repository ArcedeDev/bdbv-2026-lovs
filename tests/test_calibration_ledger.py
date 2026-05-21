# SPDX-License-Identifier: Apache-2.0
"""Tests for the pre-commitment calibration ledger and its carry-forward.

These tests encode the contract the whole calibration exercise rests on: once a
calibration point is pinned on a date, a later data refresh must carry it forward
UNCHANGED and must never re-derive it from the current run's corridor ranking.

The headline test (`test_data_refresh_cannot_move_pinned_points`) is the one that
would have caught the original landmine: it perturbs the snapshot so the live
corridor ranking moves, then proves the carried-forward calibration set does not.
"""
from __future__ import annotations

import dataclasses
import json
import pathlib
import tempfile
import unittest
from unittest import mock

import refresh_pipeline
from lovs import lovs_next_zone
from lovs import lovs_visibility


# The four points pinned in the 2026-05-20 block, in ledger order. Hard-coded
# here on purpose: the test is the independent witness to what was committed, so
# it must not read the value it is checking from the artifact under test.
PINNED_20MAY_RANGES = [
    [0.229, 0.523],
    [0.227, 0.523],
    [0.218, 0.522],
    [0.209, 0.515],
]
PIN_RESOLVES_AT = "2026-06-19T23:59:59Z"
TARGET_ZONES = ("kasese-uga", "kampala-uga", "bundibugyo-uga", "beni-cod")


class TestCarryForward(unittest.TestCase):

    def test_carry_forward_reproduces_committed_snapshot(self):
        """At the pin date, carry-forward equals what the snapshot shipped."""
        committed = json.loads(
            (refresh_pipeline.OUT_PATH).read_text()
        )
        carried = refresh_pipeline.carry_forward_calibration("2026-05-20T23:59:59Z")
        self.assertEqual(
            carried["mode_b_hypotheses"], committed["mode_b_hypotheses"]
        )
        self.assertEqual(carried["resolves_at"], committed["resolves_at"])
        self.assertEqual(carried["resolves_at"], PIN_RESOLVES_AT)
        self.assertEqual(
            [p["risk_adj_50"] for p in carried["mode_b_hypotheses"]],
            PINNED_20MAY_RANGES,
        )

    def test_carry_forward_is_invariant_to_as_of(self):
        """Pre-commitment: the pinned set is identical on every later refresh."""
        at_pin = refresh_pipeline.carry_forward_calibration("2026-05-20T23:59:59Z")
        for later in ("2026-05-21T00:00:00Z", "2026-05-31T12:00:00Z",
                      "2026-06-18T23:59:59Z"):
            self.assertEqual(
                refresh_pipeline.carry_forward_calibration(later), at_pin,
                msg=f"calibration set drifted at as_of={later}",
            )

    def test_carry_forward_excludes_pins_in_the_future(self):
        """A snapshot before the pin date has no calibration to carry."""
        with self.assertRaises(ValueError):
            refresh_pipeline.carry_forward_calibration("2026-05-19T23:59:59Z")

    def test_ledger_ids_are_content_addressed(self):
        """Every point id and corridor label matches its source/target/pin date."""
        ledger = json.loads(refresh_pipeline.LEDGER_PATH.read_text())
        seen = 0
        for block in ledger["blocks"]:
            for point in block["points"]:
                expected = refresh_pipeline._calibration_point_id(
                    point["source"], point["target"],
                    point["horizon_days"], block["pinned_at"],
                )
                self.assertEqual(point["hypothesis_id"], expected)
                self.assertEqual(
                    point["corridor"],
                    f"{point['source']} -> {point['target']}",
                )
                seen += 1
        self.assertGreaterEqual(seen, 4)

    def test_integrity_guard_rejects_desynced_id(self):
        """A hand-edit that desyncs id from corridor is rejected at load time."""
        tampered = json.loads(refresh_pipeline.LEDGER_PATH.read_text())
        # Move the target but keep the old id; the content-addressed id no longer
        # matches the corridor, which the loader must refuse to ship.
        tampered["blocks"][0]["points"][0]["target"] = "goma-cod"
        tampered["blocks"][0]["points"][0]["corridor"] = "bunia -> goma-cod"
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "ledger.json"
            path.write_text(json.dumps(tampered))
            # patch.object restores LEDGER_PATH even if the assertion fails, and
            # keeps the swap local so a parallel test runner cannot see it.
            with mock.patch.object(refresh_pipeline, "LEDGER_PATH", path):
                with self.assertRaises(ValueError):
                    refresh_pipeline.carry_forward_calibration(
                        "2026-05-20T23:59:59Z"
                    )

    def test_data_refresh_cannot_move_pinned_points(self):
        """The anti-landmine test.

        Perturb the snapshot so the LIVE corridor ranking produces different
        ascertainment-adjusted ranges (this is what the old code fed into the
        calibration set). Then prove the carried-forward calibration set is
        unchanged. This is the regression that locks the pre-commitment contract:
        re-running with new data must not overwrite points pinned on an earlier
        date.
        """
        base = refresh_pipeline.build_snapshot()
        # A later snapshot with materially higher confirmed cases and deaths,
        # which moves the corridor export hazard and so the live ranking.
        perturbed = dataclasses.replace(
            base,
            as_of="2026-05-27T23:59:59Z",
            reported_counts={
                "suspected": base.reported_counts["suspected"],
                "confirmed": dataclasses.replace(
                    base.reported_counts["confirmed"],
                    minimum=51, maximum=240, primary_value=240,
                ),
            },
            reported_deaths=dataclasses.replace(
                base.reported_deaths, minimum=144, maximum=300, primary_value=300,
            ),
        )

        # Reproduce the OLD derive-from-top-4 path on the perturbed snapshot.
        vp = lovs_visibility.nowcast(perturbed, history=(), n_samples=200)
        corridors = lovs_next_zone.next_zone_risk(
            snapshot=perturbed, visibility=vp, candidate_targets=TARGET_ZONES,
            horizon_days=30, edge_weights=None, n_samples=200,
        )
        sorted_corridors = sorted(
            corridors, key=lambda c: c.risk_visibility_adjusted.upper_50,
            reverse=True,
        )
        derived_top4 = [
            [round(c.risk_visibility_adjusted.lower_50, 3),
             round(c.risk_visibility_adjusted.upper_50, 3)]
            for c in sorted_corridors[:4]
        ]

        # Sanity: the data change actually moved the would-be-derived set.
        self.assertNotEqual(
            derived_top4, PINNED_20MAY_RANGES,
            msg="perturbation did not move the live ranking; test is not "
                "exercising the landmine",
        )

        # The contract: carry-forward is blind to the new data and holds the pins.
        carried = refresh_pipeline.carry_forward_calibration(perturbed.as_of)
        self.assertEqual(
            [p["risk_adj_50"] for p in carried["mode_b_hypotheses"]],
            PINNED_20MAY_RANGES,
        )
        self.assertEqual(carried["resolves_at"], PIN_RESOLVES_AT)


if __name__ == "__main__":
    unittest.main()
