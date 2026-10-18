# SPDX-License-Identifier: Apache-2.0
import json
import random
import unittest

from lovs.forecast import kenya_escape as ke
from lovs.forecast import kenya_escape_fp as fp
from lovs.forecast import kenya_escape_v3 as v3

EARLIER_ARTIFACTS = {
    "data/case-conditioned/kenya-2026-10-17-escape-forecast.json": "1d4f4fab3c3060b65dcf9443c1c707a6a7835f42d8368ac44394bccbe2e55959",
    "data/case-conditioned/kenya-2026-10-17-incubation.json": "fa3cbf4aef33f5b3ae8d249faa39a5d6f2a2df3f7c45db4b957bd811bcc794d5",
    "data/case-conditioned/kenya-2026-10-17-escape-forecast-fp.json": "5680b7ae7fe68aa421a67eda0df2a4664bcd9ddc50f9566e8cbd776852583341",
}


class MachineryTests(unittest.TestCase):
    def test_reference_share_is_an_equal_episode_mixture_with_range_counts(self) -> None:
        episodes = (("a", 10, 0, 4, ()), ("b", 2, 1, 1, ()))
        rng = random.Random(31)
        draws = [v3.reference_share(rng, episodes) for _ in range(60_000)]
        expected_a = sum((x + 0.5) / 11.0 for x in range(5)) / 5
        expected = 0.5 * expected_a + 0.5 * (1.5 / 3.0)
        self.assertAlmostEqual(sum(draws) / len(draws), expected, delta=0.005)
        upper = [v3.reference_share(rng, episodes, upper=True) for _ in range(60_000)]
        self.assertAlmostEqual(sum(upper) / len(upper), 0.5 * 4.5 / 11.0 + 0.5 * 0.5, delta=0.005)

    def test_mechanistic_intensity_matches_its_parts(self) -> None:
        k, mu = 3.0, 8.0
        groups = v3.KENYA_GROUPS
        conf = {"groups": groups, "no_touch": v3.NO_TOUCH_HOUSEHOLD, "low_fraction": (0.0, 1.0), "high": (0.0, 1.0)}
        rng = random.Random(32)
        n = 80_000
        mean = sum(v3.mechanistic_lambda(rng, k, mu, conf) for _ in range(n)) / n
        al, bl = v3.attack_shapes(k, mu, "low", groups)
        ah, bh = v3.attack_shapes(k, mu, "high", groups)
        listed_low = sum(g[2] for g in groups if g[1] == "low")
        expected = 0.5 * listed_low * al / (al + bl) + 0.5 * ah / (ah + bh)
        self.assertAlmostEqual(mean, expected, delta=0.03 * expected)

    def test_attack_denominators_match_simulated_onsets(self) -> None:
        # Simulate each group's infections (uniform exposure, gamma incubation) and count those
        # with onset by 17 October; the trials in attack_shapes must match, level by level.
        from lovs.forecast import kenya_incubation as ki
        k, mu = 3.0, 8.0
        rng = random.Random(33)
        n = 40_000
        trials = {"low": float(v3.NO_TOUCH_HOUSEHOLD), "high": 0.0}
        for _, level, listed, _, window in v3.KENYA_GROUPS:
            seen = sum(rng.uniform(*window) + rng.gammavariate(k, mu / k) <= ki.T_OBS for _ in range(n)) / n
            trials[level] += listed * seen
        for level in ("low", "high"):
            cases = sum(g[3] for g in v3.KENYA_GROUPS if g[1] == level)
            a, b = v3.attack_shapes(k, mu, level)
            self.assertAlmostEqual(a, cases + 0.5)
            self.assertAlmostEqual(b, trials[level] - cases + 0.5, delta=0.01 * trials[level])

    def test_low_contact_rate_approximates_exact_posterior(self) -> None:
        # With x observed infections among N exposed, each visible with probability F, the exact
        # posterior from a Jeffreys prior is proportional to a^-0.5 (1-a)^-0.5 (1 - aF)^N for x = 0;
        # the Beta with N*F trials must give nearly the same mean when infections are rare.
        N, F = 99, 0.9
        grid = [(i + 0.5) / 200_000 for i in range(200_000)]
        dens = [a ** -0.5 * (1 - a) ** -0.5 * (1 - a * F) ** N for a in grid]
        exact = sum(a * d for a, d in zip(grid, dens)) / sum(dens)
        a, b = 0.5, 0.5 + N * F
        self.assertAlmostEqual(a / (a + b), exact, delta=0.1 * exact)

    def test_reference_only_runs_never_use_the_mechanism(self) -> None:
        rec: list = []
        ke.run(v3.options({"w_reference": 1.0}, rec), n=300)
        self.assertEqual({r[2] for r in rec}, {"V1"})
        rec = []
        ke.run(v3.options({"w_reference": 0.0}, rec), n=300)
        self.assertEqual({r[2] for r in rec}, {"V2"})

    def test_triangulation_and_weight_after_silence(self) -> None:
        tri = v3.triangulation(n=400)
        for name in ("V1_reference_class", "V2_kenya_mechanism"):
            lo, mid, hi = tri[name]["unlisted_infections_10_50_90"]
            self.assertLessEqual(lo, mid)
            self.assertLessEqual(mid, hi)
        self.assertIsInstance(tri["intervals_overlap"], bool)
        rec: list = []
        draws = ke.run(v3.options({}, rec), n=400)
        w = v3.reference_weight_after_silence(draws, rec)
        self.assertTrue(0.0 < w < 1.0)

    def test_incubating_contacts_are_discounted(self) -> None:
        early = v3.attack_shapes(3.0, 14.0, "low")   # long incubation: few would show by 17 October
        late = v3.attack_shapes(3.0, 4.0, "low")
        self.assertLess(early[1], late[1])
        self.assertGreaterEqual(min(early + late), 0.5)

    def test_lag_grid_and_bad_inputs(self) -> None:
        self.assertEqual(v3.lag_grid(2.0, 3.5), (2.0, 2.5, 3.0, 3.5))
        with self.assertRaises(ValueError):
            v3.lag_grid(3.0, 2.0)
        with self.assertRaises(ValueError):
            v3.options({"episodes": ()})
        bad = [{"no_tuch": 0}, {"episodes": (("x", 5, 3, 2, ()),)}, {"episodes": (("x", 5, 0, 6, ()),)},
               {"groups": (("g", "medium", 10, 0, (0.5, 2.0)),)}, {"groups": (("g", "low", 3, 4, (0.5, 2.0)),)},
               {"low_fraction": (1.0, 0.0)}, {"high": (-1.0, 1.0)}, {"w_reference": 1.5}, {"no_touch": -1}]
        for over in bad:
            with self.assertRaises(ValueError, msg=str(over)):
                v3.options(over)

    def test_hook_records_one_value_per_draw(self) -> None:
        rec: list = []
        ke.run(v3.options({}, rec), n=200)
        self.assertEqual(len(rec), 200)
        self.assertTrue(all(lam >= 0.0 and listed > 0.0 for lam, listed, _ in rec))

    def test_earlier_artifacts_unchanged(self) -> None:
        for path, digest in EARLIER_ARTIFACTS.items():
            self.assertEqual(json.loads((ke.REPO / path).read_text(encoding="utf-8"))["payload_sha256"], digest)


class ForecastTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.artifact = json.loads(v3.DEFAULT_OUT.read_text(encoding="utf-8"))

    def test_questions_reconcile(self) -> None:
        h = self.artifact["headline"]
        a, b, both = h["A"]["probability"], h["B"]["probability"], h["combined"]["probability"]
        self.assertAlmostEqual(1 - (1 - a) * (1 - b), both, delta=0.01)

    def test_curves_are_non_increasing(self) -> None:
        for name, rows in self.artifact["curves"].items():
            ps = [r["p"] for r in rows]
            for x, y in zip(ps, ps[1:]):
                self.assertGreaterEqual(x + 1e-9, y, name)

    def test_rules_rows_and_disclosures_present(self) -> None:
        self.assertEqual([r["variant"] for r in self.artifact["sensitivity"]], [n for n, _, _ in v3.SENSITIVITY])
        inputs = self.artifact["inputs"]
        for key in ("V1_reference_class", "V2_kenya_groups", "V3_weight_on_reference_class", "V4_detection_lag_grid_days"):
            self.assertIn(key, inputs)
        for episode in inputs["V1_reference_class"]["episodes"]:
            for key in episode["sources"]:
                self.assertIn(key, inputs["citations"])
        self.assertIn(v3.PLAN_SHA256, self.artifact["disclosures"][0])
        self.assertIn("intervals_overlap", self.artifact["triangulation"]["unlisted_transmission"])

    def test_second_forecast_reproduced_by_row_e7(self) -> None:
        e7 = next(r for r in self.artifact["sensitivity"] if r["variant"] == "E7_second_forecast_inputs")
        v2 = json.loads(fp.DEFAULT_OUT.read_text(encoding="utf-8"))["headline"]["A"]["probability"]
        self.assertAlmostEqual(e7["p_A"], v2, places=3)

    def test_payload_digest_verifies(self) -> None:
        self.assertEqual(self.artifact["payload_sha256"], v3.kc.payload_digest(self.artifact))

    def test_committed_artifact_regenerates_byte_for_byte(self) -> None:
        self.assertEqual(v3.DEFAULT_OUT.read_text(encoding="utf-8"),
                         json.dumps(v3.build(), indent=1, sort_keys=True) + "\n")


if __name__ == "__main__":
    unittest.main()
