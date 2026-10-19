# SPDX-License-Identifier: Apache-2.0
import json
import math
import random
import unittest

from lovs.forecast import kenya_escape as ke
from lovs.forecast import kenya_escape_fp as fp
from lovs.forecast import kenya_incubation as ki

V1_ARTIFACTS = {
    "data/case-conditioned/kenya-2026-10-17-escape-forecast.json": "1d4f4fab3c3060b65dcf9443c1c707a6a7835f42d8368ac44394bccbe2e55959",
    "data/case-conditioned/kenya-2026-10-17-incubation.json": "fa3cbf4aef33f5b3ae8d249faa39a5d6f2a2df3f7c45db4b957bd811bcc794d5",
}


class MachineryTests(unittest.TestCase):
    def test_beta_sampler_moments_including_small_shapes(self) -> None:
        rng = random.Random(21)
        for a, b in ((0.5, 9.5), (2.5, 20.5), (1.5, 0.5)):
            xs = [ke._beta(rng, a, b) for _ in range(40_000)]
            mean = sum(xs) / len(xs)
            var = sum((x - mean) ** 2 for x in xs) / len(xs)
            self.assertAlmostEqual(mean, a / (a + b), delta=0.006)
            self.assertAlmostEqual(var, a * b / ((a + b) ** 2 * (a + b + 1)), delta=0.004)

    def test_lag_aware_timing_matches_simulation(self) -> None:
        k, mu, lag = 3.0, 8.0, 2.5
        tm = ke.timing(k, mu, lag)
        rng = random.Random(22)
        n = 60_000
        xs = []
        for _ in range(n):
            onset = rng.uniform(*ke.UNLISTED_EXPOSURE) + rng.gammavariate(k, mu / k)
            xs.append(onset - math.log(1 - rng.random()) * lag / 2 - math.log(1 - rng.random()) * lag / 2
                      + ke.REPORT_DELAY)
        for i in (0, 7, ke.I_OPEN, ke.I_END):
            self.assertAlmostEqual(tm["unlisted"][i], sum(v <= ke.POINTS[i] for v in xs) / n, delta=0.01)

    def test_mixture_likelihood_matches_simulation(self) -> None:
        # One relative confirmed on 17 October: source is case 2 with probability p_q, else the
        # first case. Among simulated relatives whose onset is observable by T, the share with
        # onset in the widened window must equal the likelihood term.
        k, mu, p_q, widen = 3.0, 8.0, 0.3, 2.0
        rng = random.Random(23)
        window = fp._widen(ki.ONSET_17OCT, widen)
        seen = hit = 0
        for _ in range(300_000):
            exposure = fp.CASE2_EXPOSURE if rng.random() < p_q else ki.RELATIVES_EXPOSURE
            onset = rng.uniform(*exposure) + rng.gammavariate(k, mu / k)
            if onset <= ki.T_OBS:
                seen += 1
                hit += window[0] < onset <= window[1]
        mixed = ((1 - p_q, ki.RELATIVES_EXPOSURE), (p_q, fp.CASE2_EXPOSURE))
        self.assertAlmostEqual(hit / seen, fp._p_case(k, mu, mixed, window, ki.T_OBS), delta=0.006)

    def test_posterior_is_normalised(self) -> None:
        for widen, p_q in ((0.0, 0.0), (fp.ONSET_WIDEN_DAYS, fp.P_QUARANTINE), (2.0, 0.9)):
            self.assertAlmostEqual(sum(w for _, _, w in fp.posterior(widen, p_q)), 1.0, places=9)

    def test_bad_inputs_are_refused(self) -> None:
        with self.assertRaises(ValueError):
            fp.posterior(p_q=1.5)
        with self.assertRaises(ValueError):
            fp.options({"lag_grid": ()})

    def test_v1_artifacts_unchanged(self) -> None:
        for path, digest in V1_ARTIFACTS.items():
            self.assertEqual(json.loads((ke.REPO / path).read_text(encoding="utf-8"))["payload_sha256"], digest)


class ForecastTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.artifact = json.loads(fp.DEFAULT_OUT.read_text(encoding="utf-8"))

    def test_questions_reconcile(self) -> None:
        h = self.artifact["headline"]
        a, b, both = h["A"]["probability"], h["B"]["probability"], h["combined"]["probability"]
        self.assertAlmostEqual(1 - (1 - a) * (1 - b), both, delta=0.01)

    def test_curves_are_non_increasing(self) -> None:
        for name, rows in self.artifact["curves"].items():
            ps = [r["p"] for r in rows]
            for x, y in zip(ps, ps[1:]):
                self.assertGreaterEqual(x + 1e-9, y, name)

    def test_rules_and_rows_present(self) -> None:
        self.assertEqual([r["variant"] for r in self.artifact["sensitivity"]], [n for n, _, _ in fp.SENSITIVITY])
        inputs = self.artifact["inputs"]
        for key in ("R1_unlisted_share_beta", "R2_detection_lag_grid_days", "R3_onset_widening_days",
                    "R4_p_infected_by_case_2", "R5_importation_scale"):
            self.assertIn(key, inputs)
        for sources in inputs["R1_unlisted_share_beta"]["episode_sources"].values():
            for key in sources:
                self.assertIn(key, inputs["citations"])
        self.assertIn(fp.PLAN_SHA256, self.artifact["disclosures"][0])

    def test_payload_digest_verifies(self) -> None:
        self.assertEqual(self.artifact["payload_sha256"], fp.kc.payload_digest(self.artifact))

    def test_committed_artifact_regenerates_byte_for_byte(self) -> None:
        v1 = json.loads(ke.DEFAULT_OUT.read_text(encoding="utf-8"))["headline"]["probability"]
        rebuilt = fp.build_artifact(fp.summarise(ke.run(fp.options())), fp.sensitivity_rows(), v1)
        self.assertEqual(fp.DEFAULT_OUT.read_text(encoding="utf-8"),
                         json.dumps(rebuilt, indent=1, sort_keys=True) + "\n")


if __name__ == "__main__":
    unittest.main()
