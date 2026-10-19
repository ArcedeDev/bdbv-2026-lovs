# SPDX-License-Identifier: Apache-2.0
import json
import math
import random
import unittest

from lovs.forecast import kenya_incubation as ki


class GammaMachineryTests(unittest.TestCase):
    def test_table_matches_exact_cdf(self) -> None:
        for k, mu in ((1.5, 5.0), (2.0, 11.4), (3.0, 7.39), (4.0, 6.67), (8.0, 16.0)):
            for x in (0.3, 1.7, 5.2, 9.99, 10.01, 14.3, 21.0, 33.3, 60.0):
                self.assertAlmostEqual(ki.cdf(k, mu, x), ki.gamma_cdf_exact(x, k, mu), delta=1e-5)

    def test_exact_cdf_matches_closed_forms(self) -> None:
        for mu in (5.0, 11.4):
            for x in (2.0, 12.0, 30.0):
                z = x / mu
                self.assertAlmostEqual(ki.gamma_cdf_exact(x, 1.0, mu), 1 - math.exp(-z), places=12)
                z2 = 2 * x / mu
                self.assertAlmostEqual(ki.gamma_cdf_exact(x, 2.0, mu), 1 - math.exp(-z2) * (1 + z2), places=12)

    def test_exact_cdf_matches_simulation(self) -> None:
        rng = random.Random(11)
        for k, mu in ((1.5, 5.0), (3.0, 7.39), (6.0, 12.0)):
            xs = [rng.gammavariate(k, mu / k) for _ in range(60_000)]
            for x in (5.0, 12.0, 21.0):
                self.assertAlmostEqual(sum(v <= x for v in xs) / len(xs), ki.gamma_cdf_exact(x, k, mu), delta=0.008)

    def test_uniform_exposure_integral_matches_simulation(self) -> None:
        rng = random.Random(12)
        k, mu, exposure = 3.0, 9.0, (0.5, 2.98)
        s = [rng.uniform(*exposure) + rng.gammavariate(k, mu / k) for _ in range(60_000)]
        for t in (8.0, 12.0, 15.0, 24.0):
            self.assertAlmostEqual(sum(v <= t for v in s) / len(s), ki.onset_cdf(k, mu, exposure, t), delta=0.008)

    def test_truncated_window_probability_matches_simulation(self) -> None:
        # Given that an onset is observed by T, its window probability is P(W) / P(onset <= T).
        rng = random.Random(13)
        for k, mu in ((4.0, 6.67), (2.0, 11.4)):
            exposure, t_obs, window = ki.HOSP_EXPOSURE, ki.T_OBS, ki.ONSET_17OCT
            seen = [o for o in (rng.uniform(*exposure) + rng.gammavariate(k, mu / k) for _ in range(120_000))
                    if o <= t_obs]
            empirical = sum(window[0] < o <= window[1] for o in seen) / len(seen)
            p = ki.onset_cdf(k, mu, exposure, window[1]) - ki.onset_cdf(k, mu, exposure, window[0])
            self.assertAlmostEqual(empirical, p / ki.onset_cdf(k, mu, exposure, t_obs), delta=0.01)


class AnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.artifact = ki.build_artifact()

    def test_posterior_is_normalised(self) -> None:
        for combined in (False, True):
            self.assertAlmostEqual(sum(w for _, _, w in ki.posterior(combined=combined)), 1.0, places=9)

    def test_prior_reproduces_its_summary(self) -> None:
        prior = [(k, mu, w) for (k, mu), w in ki.prior_weights().items()]
        q_mu = ki._marginal_quantiles(prior, 1)
        self.assertLessEqual(abs(q_mu[1] - ki.PRIOR_MU[0]), 0.25)

    def test_every_registered_output_and_row(self) -> None:
        a = self.artifact
        self.assertEqual(set(a["candidates"]), set(ki.CANDIDATES))
        self.assertEqual([r["variant"] for r in a["sensitivity"]], [n for n, _ in ki.SENSITIVITY])
        for block in (a["kenya_only"], a["combined"]):
            for key in ("mean_days_10_50_90", "shape_10_50_90", "predictive_p_over_21_days",
                        "predictive_p_over_28_days", "p_onset_after_release_if_still_incubating"):
                self.assertIn(key, block)

    def test_payload_digest_verifies(self) -> None:
        self.assertEqual(self.artifact["payload_sha256"], ki.payload_digest(self.artifact))

    def test_committed_artifact_regenerates_byte_for_byte(self) -> None:
        self.assertEqual(ki.DEFAULT_OUT.read_text(encoding="utf-8"),
                         json.dumps(self.artifact, indent=1, sort_keys=True) + "\n")

    def test_release_risk_is_the_posterior_predictive(self) -> None:
        # Draw an incubation distribution from the posterior, then an onset; keep contacts with
        # no onset by the end of 17 October and count those whose onset comes after release.
        rng = random.Random(14)
        post = ki.posterior(combined=True)
        cells, weights = [(k, mu) for k, mu, _ in post], [w for _, _, w in post]
        for group, exposure in (("hospital", ki.HOSP_EXPOSURE), ("relatives", ki.RELATIVES_EXPOSURE)):
            late = after = 0
            for k, mu in rng.choices(cells, weights, k=400_000):
                onset = rng.uniform(*exposure) + rng.gammavariate(k, mu / k)
                if onset > ki.T_OBS:
                    late += 1
                    after += onset > ki.RELEASE[group]
            # 0.004 separates this from the average of per-distribution ratios (lower by about 0.008).
            self.assertAlmostEqual(after / late, ki.release_risk(post, group), delta=0.004)


if __name__ == "__main__":
    unittest.main()
