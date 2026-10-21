# SPDX-License-Identifier: Apache-2.0
"""Nigeria v3: the ruleset switch, the ten registered changes, determinism, sums, curve and N6, digests and text."""
from __future__ import annotations

import json
import math
import random
import re
import unittest

from lovs import public_repo_hygiene
from lovs.forecast import kenya_conditioned as kc
from lovs.forecast import nigeria_v2 as v

REPO = v.REPO
ARTIFACT = v.DEFAULT_OUT_V3
V2_ARTIFACT = v.DEFAULT_OUT
MODULE = REPO / "lovs" / "forecast" / "nigeria_v2.py"
THIS_FILE = REPO / "tests" / "test_nigeria_v3.py"
DOC_HEADING = "### Nigeria v2 and v3"
CHANGELOG_BULLET = "- **Nigeria v2"
V3_RESULTS_SHA256: str | None = "eb510ee40157a5cd6d85ebbb18175fc97a0d4a5b12173d24597cee5d34ea8a2f"
V3_FIRST_RUN_PAYLOAD_SHA256: str | None = "c3324be342cb6dacfed68c777e2c6c359601671ac388210a99dac158794521ed"


def _section(text: str, start_marker: str) -> str | None:
    if start_marker not in text:
        return None
    start = text.index(start_marker)
    stops = ("\n### ", "\n## ", "\n- **") if start_marker.startswith("- **") else ("\n### ", "\n## ")
    ends = [i for i in (text.find(s, start + 1) for s in stops) if i > 0]
    return text[start:min(ends)] if ends else text[start:]


def _public_texts() -> dict[str, str]:
    texts = {p.name: p.read_text(encoding="utf-8") for p in (MODULE, THIS_FILE)}
    if ARTIFACT.exists():
        texts[ARTIFACT.name] = ARTIFACT.read_text(encoding="utf-8")
    doc = _section((REPO / "docs" / "international-forecast-validation.md").read_text(encoding="utf-8"), DOC_HEADING)
    if doc is not None:
        texts["doc_subsection"] = doc
    bullet = _section((REPO / "CHANGELOG.md").read_text(encoding="utf-8"), CHANGELOG_BULLET)
    if bullet is not None:
        texts["changelog"] = bullet
    return texts


class InputTest(unittest.TestCase):
    def test_registered_inputs(self) -> None:
        self.assertEqual(v.MODEL_VERSION_V3, "nigeria_v3-v1.0.0")
        self.assertEqual(v.SEED_V3, 2026102103)
        self.assertEqual((v.RULESETS["v3"]["n_draws"], v.N_DRAWS_SENSITIVITY), (40_000, 20_000))
        self.assertEqual(v.REGISTRATION_V3["rules_v3_sha256"],
                         "f775452a466ff9a20ab85de5f414e94d77c3512a18476472be2dbb96f29b7566")
        self.assertEqual(v.REGISTRATION_V3["rules_v2_sha256"],
                         "acf07202acb0dcf07ab2ac0ebd41a94ef6dc3737817d471540e59e21a44a0a3f")
        self.assertEqual(v.Q_V3, (0.03, 0.30))                      # C3
        self.assertEqual(v.X_V3, (0.0, 30.0))                       # C2
        self.assertAlmostEqual(v.INFO_WEIGHT, 2 / 7, places=15)     # C1
        self.assertEqual((v.R_C, v.R_H), ((0.2, 1.5), (0.5, 2.0)))  # C1, C6
        self.assertEqual(v.K_R_V3, (0.3, 0.5))                      # C4
        self.assertEqual(v.P_DETECT_V3, (0.95, 1.0))                # C7
        self.assertEqual(v.P_DEATH, (0.10, 0.35))                   # C8
        self.assertEqual(v.T_LIST_V3, 13.5)                         # C9
        b = v.ANCHOR_B_V3                                           # C5
        self.assertEqual((b["numerator_discount_a"], b["facility_days_d_t"], b["mean_shape_value_g_bar"],
                          b["case_relative_q_b"]), ((0.3, 0.8), (2.0, 6.0), (0.5, 0.9), (0.3, 0.8)))
        self.assertEqual(b["tshopo"]["fatality"], "34%")             # C10

    def test_sensitivity_list_is_the_registered_sixteen(self) -> None:
        self.assertEqual(len(v.SENSITIVITY_V3), 16)
        self.assertEqual([r[0].split("_")[0] for r in v.SENSITIVITY_V3], [f"R{i}" for i in range(1, 17)])
        for _, _, over in v.SENSITIVITY_V3:
            v.options_v3(over)
        with self.assertRaises(ValueError):
            v.options_v3({"bogus": 1})

    def test_anchor_a_counts_health_workers_only(self) -> None:
        # lambda_W ~ Gamma(7 w + 0.5, rate (3 + D_K + X) w), w = 2/7: mean (7 + 0.5 / w) / (3 + D_K + X).
        rng = random.Random(4)
        opts = v.options_v3({"anchor_a_weight": 1.0, "x": (0.0, 0.0), "q": (1.0, 1.0)})
        lam = []
        for _ in range(20_000):
            p = v.draw_parameters_v3(rng, opts)
            fac = p["g"]["facility"]
            lam.append(p["means"]["W_facility"] / p["z"] / fac)
        # E[lambda_W M_A] with D_K ~ U(2, 2.5), X = 0: 8.75 / (3 + D_K) times E[M_A] = 0.9.
        target = sum(8.75 / (3.0 + 2.0 + 0.5 * (i + 0.5) / 400) for i in range(400)) / 400 * 0.9
        self.assertAlmostEqual(sum(lam) / len(lam), target, delta=0.03 * target)

    def test_others_scale_with_health_workers(self) -> None:
        rng = random.Random(5)
        for over in ({}, {"anchor_a_weight": 0.0}):
            opts = v.options_v3(over)
            for _ in range(500):
                p = v.draw_parameters_v3(rng, opts)
                m = p["means"]
                ratio = m["C_facility"] / m["W_facility"]
                self.assertTrue(0.2 <= ratio <= 1.5)
                self.assertAlmostEqual(m["C_isolation"] / m["W_isolation"], ratio, places=9)
                self.assertEqual(p["t_list"], 13.5)
                self.assertTrue(0.10 <= p["p_death"] <= 0.35)

    def test_row_15_restores_all_eight(self) -> None:
        rng = random.Random(6)
        opts = v.options_v3({"kenya_all_linked": True, "anchor_a_weight": 1.0})
        ratios = []
        for _ in range(2_000):
            m = v.draw_parameters_v3(rng, opts)["means"]
            ratios.append(m["C_facility"] / m["W_facility"])
        # s_W ~ Beta(8, 6): C / W = (1 - s_W) / s_W, no longer bounded by r_C's range.
        self.assertGreater(max(ratios), 1.5)

    def test_leave_one_out(self) -> None:
        loo = v.leave_one_out_v3(grid=40)
        self.assertEqual(loo["uganda"]["observed_health_workers"], 4)
        self.assertEqual(loo["kenya"]["observed_health_workers"], 3)
        for key in ("uganda", "kenya"):
            self.assertGreater(loo[key]["predictive_mean"], 0.0)
            self.assertTrue(0.0 <= loo[key]["p_at_least_observed"] <= 1.0)
        self.assertEqual(loo["france"]["predictive_mean"], 0.0)

    def test_earlier_records_beside(self) -> None:
        beside = v.earlier_records_beside_v3()
        self.assertEqual(beside["v1_forecast"]["headline"], 0.3262)
        self.assertEqual(beside["v1_nowcast"]["N7_p_at_least_one_report_by_2026-11-17"], 0.7856)
        self.assertEqual(beside["v2"]["payload_sha256"], json.loads(V2_ARTIFACT.read_text(encoding="utf-8"))[
            "payload_sha256"])
        self.assertEqual(beside["v2"]["results_sha256"],
                         "fbccbe504717e1415ad979aca83a440c40f643c2dedd613bfb58c54d0c85e7d9")


class SwitchTest(unittest.TestCase):
    def test_v2_path_is_unchanged(self) -> None:
        # The v2 draw never sees a funeral term or the 13.5 listing day.
        p = v.draw_parameters(random.Random(1), v.options())
        self.assertNotIn("p_death", p)
        self.assertNotIn("t_list", p)
        self.assertEqual(v.run(n=200, seed=3, full=False), v.run(n=200, seed=3, full=False, ruleset="v2"))
        self.assertNotEqual(v.run(n=200, seed=3, full=False), v.run(n=200, seed=3, full=False, ruleset="v3"))

    def test_v2_artifact_still_regenerates(self) -> None:
        text = V2_ARTIFACT.read_text(encoding="utf-8")
        art = json.loads(text)
        fresh = v.build_artifact(v.run(), art["results"]["sensitivity"])
        self.assertEqual(v.render(fresh), text)


class RunTest(unittest.TestCase):
    N = 1500

    @classmethod
    def setUpClass(cls) -> None:
        cls.res = v.run(n=cls.N, seed=7, ruleset="v3")

    def test_deterministic(self) -> None:
        a = json.dumps(kc._rounded(v.run(n=300, seed=5, ruleset="v3")), sort_keys=True)
        b = json.dumps(kc._rounded(v.run(n=300, seed=5, ruleset="v3")), sort_keys=True)
        self.assertEqual(a, b)
        self.assertNotEqual(a, json.dumps(kc._rounded(v.run(n=300, seed=6, ruleset="v3")), sort_keys=True))

    def test_poisson_counts_match_their_means(self) -> None:
        rng = random.Random(9)
        opts = v.options_v3()
        expected = got = 0.0
        for _ in range(4_000):
            p = v.draw_parameters_v3(rng, opts)
            expected += sum(p["means"].values()) + p["p_death"] * p["funeral_mean"]
            got += len(v.simulate_run(rng, p)["first"])
        self.assertAlmostEqual(got / expected, 1.0, delta=0.05)

    def test_funeral_cases_follow_a_death(self) -> None:
        rng = random.Random(10)
        opts = v.options_v3()
        seen = 0
        for _ in range(6_000):
            p = v.draw_parameters_v3(rng, opts)
            p = {**p, "p_death": 1.0, "funeral_mean": 1.0}
            run = v.simulate_run(rng, p)
            for key, t_inf, onset, _ in run["first"]:
                if key == "funeral":
                    seen += 1
                    self.assertTrue(v.DEATH[0] <= t_inf <= v.DEATH[1] + v.BURIAL_SPAN)
                    self.assertGreater(onset, t_inf)
        self.assertAlmostEqual(seen / 6_000, 1.0, delta=0.05)
        p = {**v.draw_parameters_v3(rng, opts), "p_death": 0.0, "funeral_mean": 5.0}
        self.assertFalse(any(k == "funeral" for k, *_ in v.simulate_run(rng, p)["first"]))

    def test_no_linked_confirmation_before_listing(self) -> None:
        rng = random.Random(11)
        opts = v.options_v3()
        for _ in range(3_000):
            run = v.simulate_run(rng, v.draw_parameters_v3(rng, opts))
            self.assertTrue(all(c > 13.5 for *_, c in run["first"]))
            self.assertTrue(all(c > 13.5 for *_, c in run["second"]))

    def test_groups_sum_to_total(self) -> None:
        for kind in ("unconditioned", "conditioned"):
            n1 = self.res["nowcast"][kind]["N1_infected_by_group_by_end_of_20_october"]
            self.assertAlmostEqual(n1["total"]["mean"], sum(n1[g]["mean"] for g in "HWC"), places=9)
            self.assertAlmostEqual(n1["total"]["mean"] + n1["after_20_october_mean"],
                                   n1["first_generation_all_time_mean"], places=9)
        for kind in ("forecast_conditioned", "forecast_unconditioned"):
            s3 = self.res[kind]
            self.assertEqual(list(s3["by_pathway"]), ["H", "W", "C", "funeral", "G2", "P6"])
            self.assertAlmostEqual(sum(r["expected_qualifying_cases"] for r in s3["by_pathway"].values()),
                                   s3["expected_qualifying_cases"], places=9)
            self.assertAlmostEqual(sum(s3["shares_of_yes_by_first_qualifying_case"].values()), 1.0, places=9)
            self.assertLessEqual(s3["p_at_least_10"], s3["p_at_least_3"])
            self.assertLessEqual(s3["p_at_least_3"], s3["p_yes"])

    def test_headline_agrees_with_the_decomposition(self) -> None:
        self.assertAlmostEqual(self.res["conditioned"], self.res["forecast_conditioned"]["p_yes"], places=9)
        self.assertAlmostEqual(self.res["unconditioned"], self.res["forecast_unconditioned"]["p_yes"], places=9)

    def test_curve_non_increasing(self) -> None:
        ps = [row["p"] for row in self.res["curve"]]
        self.assertTrue(all(0.0 <= p <= 1.0 for p in ps))
        self.assertTrue(all(a >= b - 1e-12 for a, b in zip(ps, ps[1:])))

    def test_n6_non_decreasing(self) -> None:
        for kind in ("unconditioned", "conditioned"):
            rows = self.res["nowcast"][kind]["N6_cumulative_reports"]["by_date"]
            for a, b in zip(rows, rows[1:]):
                self.assertLessEqual(a["expected_cumulative"], b["expected_cumulative"])
                self.assertLessEqual(a["p_at_least_one"], b["p_at_least_one"])

    def test_rows_run(self) -> None:
        rows = v.sensitivity_rows(n=40, ruleset="v3")
        self.assertEqual([r["variant"] for r in rows], [r[0] for r in v.SENSITIVITY_V3])
        self.assertEqual(rows[13]["headline"], rows[13]["unconditioned"])


@unittest.skipUnless(ARTIFACT.exists(), "artifact not built yet")
class ArtifactTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = ARTIFACT.read_text(encoding="utf-8")
        cls.artifact = json.loads(cls.text)

    def test_digests_verify(self) -> None:
        self.assertEqual(self.artifact["payload_sha256"], kc.payload_digest(self.artifact))
        self.assertEqual(self.artifact["results_sha256"], v.results_digest(self.artifact["results"]))
        self.assertEqual(self.artifact["model_version"], "nigeria_v3-v1.0.0")
        self.assertEqual(self.artifact["method"]["seed"], 2026102103)
        entry = self.artifact["registration"]["entry_3_first_run"]
        if entry is not None:
            self.assertEqual(entry["results_sha256"], self.artifact["results_sha256"])

    @unittest.skipIf(V3_RESULTS_SHA256 is None, "first run not yet registered")
    def test_results_are_the_precommitted_ones(self) -> None:
        self.assertEqual(self.artifact["results_sha256"], V3_RESULTS_SHA256)
        self.assertEqual(self.artifact["registration"]["entry_3_first_run"]["payload_sha256"],
                         V3_FIRST_RUN_PAYLOAD_SHA256)

    def test_required_sections(self) -> None:
        res = self.artifact["results"]
        for key in ("headline", "S1_curve", "S2_count_tiers", "S3_decomposition", "nowcast", "leave_one_out",
                    "sensitivity", "first_generation_unconditioned"):
            self.assertIn(key, res)
        self.assertEqual([r["variant"] for r in res["sensitivity"]], [r[0] for r in v.SENSITIVITY_V3])
        self.assertIn("funeral", res["S3_decomposition"]["conditioned"]["by_pathway"])
        self.assertEqual(self.artifact["headline"]["probability"], res["headline"]["probability"])

    def test_earlier_records_match_their_artifacts(self) -> None:
        self.assertEqual(self.artifact["earlier_records_beside_v3"], kc._rounded(v.earlier_records_beside_v3()))

    def test_committed_artifact_regenerates(self) -> None:
        fresh = v.build_artifact_v3(v.run(ruleset="v3"), self.artifact["results"]["sensitivity"])
        self.assertEqual(v.render(fresh), self.text)

    def test_never_verdict_words(self) -> None:
        for name in (ARTIFACT.name, "doc_subsection"):
            text = _public_texts().get(name)
            if text is not None:
                self.assertIsNone(re.search(r"\b(hit|hits|miss|missed|misses)\b", text, re.IGNORECASE), name)


class PublicTextTest(unittest.TestCase):
    def test_no_personal_or_facility_detail(self) -> None:
        forbidden = [a + b for a, b in (("Niza", "miye"), ("NIZA", "MIYE"), ("Tur", "kish"), ("Ugan", "dan "),
                                        ("Niger", "ian national"), ("Congo", "lese"), ("air", "line"),
                                        ("fli", "ght"), ("Abu", "ja"), ("-year", "-old"), ("ye", "ars old"),
                                        ("busi", "ness trip"), ("hus", "band"), ("wi", "fe"), ("tran", "sit"),
                                        ("stated ", "PPE"), ("about ", "half"))]
        words = re.compile(r"\b(" + "|".join(("wo" + "man", "fe" + "male", "s" + "he", "h" + "er")) + r")\b",
                           re.IGNORECASE)
        for name, text in _public_texts().items():
            for needle in forbidden:
                self.assertNotIn(needle, text, f"{name}: {needle}")
            self.assertIsNone(words.search(text), f"{name}: {words.search(text)}")

    def test_public_hygiene_and_style(self) -> None:
        for name, text in _public_texts().items():
            self.assertFalse(public_repo_hygiene.contains_marker(text), name)
            self.assertNotIn(chr(0x2014), text, name)


if __name__ == "__main__":
    unittest.main()
