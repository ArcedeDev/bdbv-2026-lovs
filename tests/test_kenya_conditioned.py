# SPDX-License-Identifier: Apache-2.0
"""Case-conditioned Kenya forecast: identities, determinism, inputs and the committed artifact."""
from __future__ import annotations

import datetime as dt
import json
import math
import random
import unittest

from lovs import public_repo_hygiene
from lovs.forecast import kenya_conditioned as kc

ARTIFACT = kc.DEFAULT_OUT
NEW_FILES = (kc.REPO / "lovs" / "forecast" / "kenya_conditioned.py", kc.FEATURES, ARTIFACT,
             kc.REPO / "tests" / "test_kenya_conditioned.py")


class IdentityTest(unittest.TestCase):
    def test_negative_binomial_zero_probability_matches_gamma_poisson_simulation(self) -> None:
        rng = random.Random(7)
        m, k, n = 0.6, 0.4, 60_000
        zeros = 0
        for _ in range(n):
            nu = rng.gammavariate(k, 1.0 / k)
            lam = m * nu
            # Poisson zero probability given lam, sampled once
            zeros += rng.random() < math.exp(-lam)
        self.assertAlmostEqual(zeros / n, kc.nb_none(m, k), delta=0.006)

    def test_nb_limits(self) -> None:
        self.assertEqual(kc.nb_none(0.0, 0.3), 1.0)
        self.assertEqual(kc.nb_none(0.5, 0.3, 0.0), 1.0)
        self.assertAlmostEqual(kc.nb_none(0.5, 1e9), math.exp(-0.5), places=6)
        self.assertGreater(kc.nb_none(0.5, 0.18), kc.nb_none(0.5, 1.0))  # more dispersion, more zeros

    def test_survival_update_matches_brute_force_simulation(self) -> None:
        # Simulate the generative story: pick a draw by weight, draw a count, place each case in
        # time, then keep the runs with nothing reported by the cutoff and see how many ever report.
        draws = [{"w": 0.7, "m": 0.5, "k": 0.3, "f": [0.0, 0.4, 1.0], "lam": 0.0},
                 {"w": 0.3, "m": 2.0, "k": 1.0, "f": [0.0, 0.2, 1.0], "lam": 0.0}]
        rng = random.Random(11)
        kept = hits = 0
        for _ in range(200_000):
            d = draws[0] if rng.random() < 0.7 else draws[1]
            nu = rng.gammavariate(d["k"], 1.0 / d["k"])
            n_cases = 0
            lam = d["m"] * nu
            # Poisson count by inversion
            u, acc, term = rng.random(), math.exp(-lam), math.exp(-lam)
            while u > acc:
                n_cases += 1
                term *= lam / n_cases
                acc += term
            early = sum(1 for _ in range(n_cases) if rng.random() < d["f"][1])
            if early:
                continue
            kept += 1
            hits += 1 if n_cases else 0
        self.assertAlmostEqual(hits / kept, kc._conditional(draws, [0.0, 1.0, 2.0], [0.0] * 3, 1)[0],
                               delta=0.004)

    def test_survival_update_matches_direct_bayes(self) -> None:
        # Two parameter draws with known m, k and report-time CDF values at three points.
        draws = [{"w": 0.7, "m": 0.5, "k": 0.3, "f": [0.0, 0.4, 1.0], "lam": 0.0},
                 {"w": 0.3, "m": 2.0, "k": 1.0, "f": [0.0, 0.2, 1.0], "lam": 0.0}]
        points, bg = [0.0, 1.0, 2.0], [0.0, 0.0, 0.0]
        got = kc._conditional(draws, points, bg, 1)[0]
        none_by = [d["w"] * kc.nb_none(d["m"], d["k"], d["f"][1]) for d in draws]
        none_end = [d["w"] * kc.nb_none(d["m"], d["k"], 1.0) for d in draws]
        self.assertAlmostEqual(got, 1 - sum(none_end) / sum(none_by), places=12)
        # With nothing observed yet the update equals the prior probability of any case.
        self.assertAlmostEqual(kc._conditional(draws, points, bg, 0)[0],
                               1 - sum(none_end) / sum(d["w"] for d in draws), places=12)

    def test_hospital_exposure_times_stay_in_the_stay(self) -> None:
        rng = random.Random(3)
        for g in kc.GROWTH_RATES:
            times = [kc._hospital_exposure(rng, g) for _ in range(2000)]
            self.assertTrue(all(kc.T_HOSPITAL <= t <= kc.T_DEATH for t in times))
            # later times are more likely (infectiousness rises toward death)
            mid = (kc.T_HOSPITAL + kc.T_DEATH) / 2
            self.assertGreater(sum(t > mid for t in times), sum(t <= mid for t in times))


class InputTest(unittest.TestCase):
    def test_weights_sum_to_one(self) -> None:
        self.assertAlmostEqual(sum(s["weight"] for s in kc.SCENARIOS.values()), 1.0)
        self.assertAlmostEqual(sum(c["weight"] for c in kc.COURSES.values()), 1.0)

    def test_window_and_clock(self) -> None:
        self.assertEqual(kc.T_CONFIRM_END, 33.0)
        self.assertEqual(kc.CURVE_DATES[0], dt.date(2026, 10, 8))
        self.assertEqual(kc.CURVE_DATES[-1], dt.date(2026, 11, 4))
        self.assertEqual(len(kc.CURVE_DATES), 28)
        self.assertIn(kc.CONDITIONING_DATE, kc.CURVE_DATES)
        self.assertLess(kc.T_FLIGHT, kc.T_HOSPITAL)
        self.assertLess(kc.T_HOSPITAL, kc.T_DEATH)

    def test_benchmarks_are_derived_and_match_block11(self) -> None:
        from lovs.forecast import pins_block11 as p11
        ref = p11.reference_probability(p11.load_reference())
        marks = kc.benchmarks()
        self.assertAlmostEqual(marks["block11_pooled"], ref["probability"], places=12)
        self.assertAlmostEqual(marks["traveller_subgroup"], 5.5 / 10, places=12)
        self.assertAlmostEqual(marks["responder_subgroup"], 0.5 / 5, places=12)

    def test_every_input_is_labelled_sourced_or_judgement(self) -> None:
        artifact_inputs = kc.build_artifact(kc.run(n=50), [])["inputs"]
        for key in artifact_inputs:
            self.assertIn(key, kc.PROVENANCE, key)
            label = kc.PROVENANCE[key]
            self.assertTrue(label.startswith(("source:", "judgement", "case evidence", "the published", "this map")), label)

    def test_disputed_episodes_exist_in_the_reference_class(self) -> None:
        ids = {e["episode_id"] for e in kc.load_features()}
        for eid in kc.DISPUTED_YES:
            self.assertIn(eid, ids)

    def test_episode_features_match_reference_class(self) -> None:
        reference = json.loads(kc.REFERENCE.read_text(encoding="utf-8"))
        outcomes = {e["episode_id"]: e["outcome_local_transmission"] for e in reference["episodes"]}
        features = kc.load_features()
        self.assertEqual({e["episode_id"]: e["outcome"] for e in features}, outcomes)
        for ep in features:
            for key in ("e_comm", "e_hosp", "f_pre", "e_fun"):
                lo, hi = ep[key]
                self.assertTrue(0.0 <= lo <= hi <= 1.0, (ep["episode_id"], key))
            self.assertIn(ep["pre_level"], kc.LEVELS)
            self.assertIn(ep["post_level"], kc.LEVELS)
            self.assertIn(ep["funeral_level"], kc.FUNERAL)
            self.assertTrue(0.0 <= ep["p_death"] <= 1.0)
        self.assertEqual(sum(e["responder"] for e in features), 4)

    def test_every_literature_input_is_cited(self) -> None:
        for key in ("faye2015", "althaus2015", "dean2016", "who_ert2014", "macneil2010", "lovs_priors",
                    "who_don619"):
            self.assertIn(key, kc.CITATIONS)


class RunTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.res = kc.run(n=1500)

    def test_deterministic(self) -> None:
        again = kc.run(n=1500)
        self.assertEqual(json.dumps(kc._rounded(self.res), sort_keys=True),
                         json.dumps(kc._rounded(again), sort_keys=True))

    def test_curve_is_a_falling_probability(self) -> None:
        ps = [row["p"] for row in self.res["curve"]]
        self.assertTrue(all(0.0 <= p <= 1.0 for p in ps))
        self.assertTrue(all(a >= b - 1e-12 for a, b in zip(ps, ps[1:])))
        for row in self.res["curve"]:
            self.assertLessEqual(row["p10"], row["p90"])
        self.assertLess(ps[-1], 0.01)
        self.assertGreaterEqual(self.res["window_open"], self.res["headline"]["p"])

    def test_scenarios_order_by_precaution(self) -> None:
        rows = {(r["scenario"], r["course"]): r for r in self.res["scenarios"]}
        self.assertLess(rows[("S1", "D1")]["median_m_calibrated"], rows[("S3", "D1")]["median_m_calibrated"])
        self.assertLess(rows[("S1", "D1")]["p_conditioning_date"], rows[("S3", "D1")]["p_conditioning_date"])
        self.assertAlmostEqual(sum(r["share_given_no_report"] for r in self.res["scenarios"]), 1.0, places=9)

    def test_conditioning_order_and_evidence_time(self) -> None:
        self.assertGreater(self.res["window_open"], self.res["headline"]["p"])
        self.assertGreater(self.res["headline"]["p"], self.res["at_evidence_time"])

    def test_paired_differences_report_their_own_uncertainty(self) -> None:
        pairs = self.res["validation"]["paired_differences_vs_calibrated"]
        for key in ("base_rate_loo", "subgroup_loo", "literature_only"):
            row = pairs[key]
            self.assertGreater(row["standard_error"], 0.0)
            self.assertEqual(row["distinguishable"],
                             abs(row["mean_brier_difference"]) > 2 * row["standard_error"])

    def test_multi_index_option_raises_merged_episode_probability(self) -> None:
        rng = random.Random(5)
        p = kc.draw_shared(random.Random(5), {})
        ep = next(e for e in kc.load_features() if e["multi_import"])
        one = kc.episode_probability(random.Random(9), p, ep, multi_index=False)
        many = kc.episode_probability(random.Random(9), p, ep, multi_index=True)
        self.assertGreater(many, one)

    def test_background_off_is_zero(self) -> None:
        res = kc.run({"background": False, "full": True}, n=300)
        self.assertEqual(res["headline"]["background_only"], 0.0)

    def test_validation_rows_cover_all_episodes(self) -> None:
        rows = self.res["validation"]["episodes"]
        self.assertEqual(len(rows), 13)
        for r in rows:
            for key in ("literature_only", "calibrated_loo", "base_rate_loo", "subgroup_loo"):
                self.assertTrue(0.0 < r[key] < 1.0)
        yes = sum(r["outcome"] for r in rows)
        for r in rows:  # Jeffreys leave-one-out base rate
            self.assertAlmostEqual(r["base_rate_loo"], (yes - r["outcome"] + 0.5) / 13)


class ArtifactTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.artifact = json.loads(ARTIFACT.read_text(encoding="utf-8"))

    def test_payload_digest_verifies(self) -> None:
        self.assertEqual(self.artifact["payload_sha256"], kc.payload_digest(self.artifact))

    def test_required_sections(self) -> None:
        for key in ("headline", "curve", "scenarios", "validation", "sensitivity", "inputs", "case_evidence",
                    "disclosures", "uganda_transit", "method"):
            self.assertIn(key, self.artifact)
        self.assertEqual([r["date"] for r in self.artifact["curve"]], [d.isoformat() for d in kc.CURVE_DATES])
        head = self.artifact["headline"]
        row = next(r for r in self.artifact["curve"] if r["date"] == self.artifact["conditioning_date"])
        self.assertEqual(head["probability"], row["p"])
        self.assertEqual(len(self.artifact["sensitivity"]), len(kc.SENSITIVITY))

    def test_committed_artifact_regenerates(self) -> None:
        # Every number outside the sensitivity table regenerates exactly, so a hand edit fails here.
        # The sensitivity rows are carried over rather than recomputed, to keep this test quick;
        # `python3 -m lovs.forecast.kenya_conditioned` recomputes them.
        fresh = kc.build_artifact(kc.run(), self.artifact["sensitivity"])
        self.assertEqual(fresh, self.artifact)

    def test_sensitivity_table_covers_every_registered_variant(self) -> None:
        self.assertEqual([r["variant"] for r in self.artifact["sensitivity"]],
                         [name for name, _, _ in kc.SENSITIVITY])
        for row in self.artifact["sensitivity"]:
            self.assertTrue(row["label"].strip())

    def test_scoring_plan_is_present_and_specific(self) -> None:
        plan = self.artifact["scoring_plan"]
        for key in ("scored_value", "scored_against", "resolution", "excluded", "ambiguity",
                    "if_a_case_occurs", "if_no_case_occurs"):
            self.assertTrue(plan[key])

    def test_no_identifying_or_non_material_detail(self) -> None:
        # Privacy: the forecast needs none of these, and publishing them could expose individuals.
        # The needles are assembled from fragments so that this test file does not itself carry them.
        forbidden = [a + b for a, b in (("JM", "8523"), ("13", ":10"), ("Nairobi ", "Hospital"),
                                        ("East ", "Wing"), ("National Police ", "Service"),
                                        ("Bu", "ta, DRC"), ("Bu", "ri"), ("and a ", "friend"),
                                        ("Kenyan ", "citizen"), ("Jambo", "jet"), ("Jambo", "Jet"))]
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
