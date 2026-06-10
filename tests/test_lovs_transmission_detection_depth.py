"""Tests for the rebuilt detection-depth estimator in lovs_transmission.

The legacy single-R back-calc was degenerate under the live Stage-Two effective-R
prior (mean 1.33): subcritical samples diverge and pile into the censored bin at
any cap. The rebuild draws the back-projection R from an uncontrolled early-phase
prior truncated to the supercritical region (R>1), while the forward latent-chains
sim keeps the effective-R prior. It separates two metrics:

  - silent generations before detection  (back-calc from the detection-era anchor)
  - total generations elapsed to date     (back-calc from the live count)

each reported as median + 50/95 CI + censored_fraction. This file pins that
behaviour: non-degenerate, separated, finite (no divergence), deterministic.
"""
from __future__ import annotations

import unittest

from lovs import lovs_priors_bundibugyo
from lovs import lovs_transmission


class _Count:
    """Minimal duck-typed ReconciledCount: the estimator only reads primary_value."""

    def __init__(self, value: int) -> None:
        self.primary_value = value


class _Snap:
    """Minimal duck-typed OutbreakSnapshot for transmission_plausibility.

    The estimator reads reported_counts['confirmed'].primary_value, outbreak_id,
    affected_zones, as_of, and sources. A fixed seed is always passed so
    snapshot_content_seed (which would need the full dataclass) is never invoked.
    """

    def __init__(self, confirmed: int) -> None:
        self.reported_counts = {"confirmed": _Count(confirmed)}
        self.outbreak_id = "ebv-bdb-2026"
        self.affected_zones = ("ituri",)
        self.as_of = "2026-06-07"
        self.sources = ("test",)


PRIORS = lovs_priors_bundibugyo.BUNDIBUGYO_PRIORS_STAGE_TWO
LIVE = 569      # confirmed as of 2026-06-07
ANCHOR = 10     # detection-era confirmed (16 May, who-pheic-2026-05-17)


class TestDetectionDepthRebuild(unittest.TestCase):
    def _run(self, confirmed: int = LIVE, anchor: int | None = ANCHOR, seed: int = 99, n: int = 2000):
        return lovs_transmission.transmission_plausibility(
            _Snap(confirmed),
            n_trajectories=n,
            seed=seed,
            priors=PRIORS,
            detection_anchor_confirmed=anchor,
        )

    def test_both_summaries_present_when_anchor_given(self):
        out = self._run()
        self.assertIsNotNone(out.silent_generations)
        self.assertIsNotNone(out.elapsed_generations)

    def test_silent_median_in_expected_range(self):
        out = self._run()
        self.assertGreaterEqual(out.silent_generations.median, 3)
        self.assertLessEqual(out.silent_generations.median, 7)

    def test_elapsed_median_in_expected_range(self):
        out = self._run()
        self.assertGreaterEqual(out.elapsed_generations.median, 7)
        self.assertLessEqual(out.elapsed_generations.median, 17)

    def test_silent_shallower_than_elapsed(self):
        out = self._run()
        self.assertLess(
            out.silent_generations.median,
            out.elapsed_generations.median,
            "silent-before-detection must be shallower than total-elapsed",
        )

    def test_silent_not_degenerate(self):
        """The headline-relevant silent metric must not pile into the censored bin."""
        out = self._run()
        self.assertLess(out.silent_generations.censored_fraction, 0.10)

    def test_censored_fraction_is_a_fraction(self):
        out = self._run()
        for summary in (out.silent_generations, out.elapsed_generations):
            self.assertGreaterEqual(summary.censored_fraction, 0.0)
            self.assertLessEqual(summary.censored_fraction, 1.0)

    def test_summaries_carry_anchor_count(self):
        out = self._run()
        self.assertEqual(out.silent_generations.anchor_confirmed, ANCHOR)
        self.assertEqual(out.elapsed_generations.anchor_confirmed, LIVE)

    def test_ci_ordering(self):
        out = self._run()
        for s in (out.silent_generations, out.elapsed_generations):
            self.assertLessEqual(s.ci_95[0], s.ci_50[0])
            self.assertLessEqual(s.ci_50[0], s.median)
            self.assertLessEqual(s.median, s.ci_50[1])
            self.assertLessEqual(s.ci_50[1], s.ci_95[1])

    def test_no_anchor_preserves_backward_compat(self):
        out = lovs_transmission.transmission_plausibility(
            _Snap(LIVE), n_trajectories=500, seed=7, priors=PRIORS
        )
        self.assertIsNone(out.silent_generations)
        self.assertIsNotNone(out.elapsed_generations)
        self.assertTrue(out.generations_before_detection)  # legacy histogram still emitted

    def test_deterministic_under_fixed_seed(self):
        a = self._run(seed=123)
        b = self._run(seed=123)
        self.assertEqual(a.elapsed_generations, b.elapsed_generations)
        self.assertEqual(a.silent_generations, b.silent_generations)
        self.assertEqual(a.generations_before_detection, b.generations_before_detection)

    def test_back_calc_terminates_no_divergence_even_for_huge_counts(self):
        """Truncated R>1 guarantees the back-calc terminates; mass stays a valid pmf."""
        out = self._run(confirmed=5000, anchor=ANCHOR, n=1000)
        keys = [int(k) for k in out.generations_before_detection]
        self.assertTrue(all(1 <= k <= lovs_transmission.MAX_GENERATIONS for k in keys))
        self.assertAlmostEqual(sum(out.generations_before_detection.values()), 1.0, places=6)

    def test_cap_raised_above_legacy_six(self):
        self.assertGreater(lovs_transmission.MAX_GENERATIONS, 6)


if __name__ == "__main__":
    unittest.main()
