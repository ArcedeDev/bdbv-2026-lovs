# SPDX-License-Identifier: Apache-2.0
"""Kenya further-linked-case forecast: identities, determinism, inputs and the committed artifact."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import random
import unittest

from lovs import public_repo_hygiene
from lovs.forecast import kenya_conditioned as kc
from lovs.forecast import kenya_second_case as k2

ARTIFACT = k2.DEFAULT_OUT
NEW_FILES = (k2.REPO / "lovs" / "forecast" / "kenya_second_case.py", ARTIFACT,
             k2.REPO / "tests" / "test_kenya_second_case.py")
# The 9 October artifact as precommitted and merged; this forecast must leave it untouched.
KENYA_2026_10_09_SHA256 = "d5d2da9fdd5e41e8182c73d610375a641c3276234463cb28133a67400a90adf3"


def _poisson(rng: random.Random, lam: float) -> int:
    n, acc, term, u = 0, math.exp(-lam), math.exp(-lam), rng.random()
    while u > acc:
        n += 1
        term *= lam / n
        acc += term
    return n


class IdentityTest(unittest.TestCase):
    def test_residual_after_one_observed_case_matches_simulation(self) -> None:
        # Gamma-Poisson offspring; keep runs with exactly one case reported by the conditioning
        # fraction, then count runs with no further report by a later fraction.
        m, k, f_c, f_d = 0.6, 0.4, 0.55, 0.8
        rng = random.Random(21)
        kept = none = 0
        for _ in range(400_000):
            lam = rng.gammavariate(k, m / k)
            early = _poisson(rng, lam * f_c)
            if early != 1:
                continue
            kept += 1
            none += _poisson(rng, lam * (f_d - f_c)) == 0
        b = k / m
        self.assertAlmostEqual(none / kept, ((b + f_c) / (b + f_d)) ** (k + 1), delta=0.01)

    def test_observation_likelihood_matches_simulation(self) -> None:
        # P(exactly one by f_c) = m * (b / (b + f_c)) ** (k + 1) * f_c for a uniform report density,
        # so the likelihood used per draw is proportional to the simulated probability.
        m, k, f_c = 0.8, 0.5, 0.6
        rng = random.Random(5)
        n = 300_000
        one = sum(_poisson(rng, rng.gammavariate(k, m / k) * f_c) == 1 for _ in range(n)) / n
        b = k / m
        self.assertAlmostEqual(one, m * f_c * (b / (b + f_c)) ** (k + 1), delta=0.004)

    def test_none_by_is_one_at_the_conditioning_time_for_the_first_case(self) -> None:
        d = {"m1": 0.5, "m2": 0.0, "k": 0.4, "f1": [0.3, 0.5, 0.9], "f2": [0.0, 0.1, 0.4]}
        self.assertAlmostEqual(k2._none_by(d, 0, "case1", True), 1.0, places=12)
        self.assertAlmostEqual(k2._none_by(d, 0, "case1", False), 1.0, places=12)
        self.assertLess(k2._none_by(d, 2, "case1", True), k2._none_by(d, 1, "case1", True))

    def test_case2_exposures_start_after_onset(self) -> None:
        tables = k2.case2_timing_tables(k2.SEED, n=2000)
        earliest = min(s[0] for s in tables.values())
        self.assertGreater(earliest, k2.ONSET[0])


class InputTest(unittest.TestCase):
    def test_weights_sum_to_one(self) -> None:
        self.assertAlmostEqual(sum(q["weight"] for q in k2.QUARANTINE.values()), 1.0)

    def test_window_and_clock(self) -> None:
        self.assertEqual(k2.CURVE_DATES[0], dt.date(2026, 10, 14))
        self.assertEqual(k2.CURVE_DATES[-1], dt.date(2026, 11, 10))
        self.assertEqual(k2.T_COND, 12.0)
        self.assertEqual(k2.T_CONFIRM_END, 39.0)
        self.assertLess(k2.ONSET[1], k2.T_OBSERVED)
        self.assertLess(k2.T_OBSERVED, k2.T_COND)

    def test_benchmark_is_derived_from_the_reference_class(self) -> None:
        mark = k2.further_local_benchmark()
        self.assertEqual(mark["episodes_with_a_local_case"], 5)
        self.assertAlmostEqual(mark["probability"], (mark["episodes_with_two_or_more"] + 0.5) / 6, places=12)

    def test_every_input_group_is_labelled(self) -> None:
        inputs = k2.build_artifact(k2.run(n=60), [])["inputs"]
        labels = inputs["provenance"]
        for key in ("clock", "quarantine_scenarios", "case2", "imports_per_week", "inherited"):
            self.assertIn(key, labels)
            self.assertTrue(labels[key].startswith(("source:", "judgement", "case evidence")), key)

    def test_the_9_october_artifact_is_untouched(self) -> None:
        digest = hashlib.sha256(kc.DEFAULT_OUT.read_bytes()).hexdigest()
        self.assertEqual(digest, KENYA_2026_10_09_SHA256)


class RunTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.res = k2.run(n=1500)

    def test_deterministic(self) -> None:
        again = k2.run(n=1500)
        self.assertEqual(json.dumps(kc._rounded(self.res), sort_keys=True),
                         json.dumps(kc._rounded(again), sort_keys=True))

    def test_curve_is_a_falling_probability(self) -> None:
        ps = [row["p"] for row in self.res["curve"]]
        self.assertTrue(all(0.0 <= p <= 1.0 for p in ps))
        self.assertTrue(all(a >= b - 1e-12 for a, b in zip(ps, ps[1:])))
        for row in self.res["curve"]:
            self.assertLessEqual(row["p10"], row["p90"])
            self.assertLessEqual(max(row["case1_only"], row["case2_only"]), row["p"] + 1e-12)
        self.assertLess(ps[-1], 0.02)

    def test_any_case_is_at_least_the_linked_case_at_the_conditioning_date(self) -> None:
        # Only there do both share the same conditioning; later rows condition on different events.
        self.assertGreater(self.res["any_further_case_in_kenya"]["headline"], self.res["headline"]["p"])

    def test_dropping_the_observation_lowers_the_first_case_part(self) -> None:
        without = k2.run({"case2_update": False}, n=1500)
        self.assertLess(without["headline"]["case1_only"], self.res["headline"]["case1_only"])

    def test_home_quarantine_is_the_riskier_scenario(self) -> None:
        rows = {r["quarantine"]: r for r in self.res["quarantine_scenarios"]}
        self.assertLess(rows["Q1"]["median_m2"], rows["Q2"]["median_m2"])


class ArtifactTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.artifact = json.loads(ARTIFACT.read_text(encoding="utf-8"))

    def test_payload_digest_verifies(self) -> None:
        self.assertEqual(self.artifact["payload_sha256"], k2.payload_digest(self.artifact))

    def test_required_sections(self) -> None:
        for key in ("question", "headline", "curve", "case1_scenarios", "quarantine_scenarios", "sensitivity",
                    "scoring_plan", "inputs", "case_evidence", "disclosures", "method"):
            self.assertIn(key, self.artifact)
        self.assertEqual([r["date"] for r in self.artifact["curve"]], [d.isoformat() for d in k2.CURVE_DATES])
        self.assertEqual(self.artifact["headline"]["probability"], self.artifact["curve"][0]["p"])
        self.assertEqual([r["variant"] for r in self.artifact["sensitivity"]],
                         [name for name, _, _ in k2.SENSITIVITY])

    def test_committed_artifact_regenerates(self) -> None:
        # Sensitivity rows are carried over to keep this test quick; the module recomputes them.
        fresh = k2.build_artifact(k2.run(), self.artifact["sensitivity"])
        self.assertEqual(fresh, self.artifact)

    def test_scoring_plan_is_present_and_specific(self) -> None:
        plan = self.artifact["scoring_plan"]
        for key in ("scored_value", "scored_against", "resolution", "excluded", "ambiguity",
                    "if_a_case_occurs", "if_no_case_occurs"):
            self.assertTrue(plan[key])

    def test_no_identifying_or_non_material_detail(self) -> None:
        # The needles are assembled from fragments so that this file does not itself carry them.
        forbidden = [a + b for a, b in (("JM", "8523"), ("Nairobi ", "Hospital"), ("Bu", "ta, DRC"),
                                        ("Jambo", "jet"), ("Jambo", "Jet"), ("Kenyan ", "citizen"),
                                        ("39", "-year"), ("39 ", "year"), ("and a ", "friend"),
                                        ("drove ", "him"), ("Ken", "yatta"), ("Mba", "gathi"))]
        for path in NEW_FILES:
            text = path.read_text(encoding="utf-8")
            for needle in forbidden:
                self.assertNotIn(needle, text, f"{path.name}: {needle}")

    def test_new_files_pass_public_hygiene_and_style(self) -> None:
        for path in NEW_FILES:
            text = path.read_text(encoding="utf-8")
            self.assertFalse(public_repo_hygiene.contains_marker(text), path.name)
            self.assertNotIn(chr(0x2014), text, path.name)


if __name__ == "__main__":
    unittest.main()
