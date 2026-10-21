# SPDX-License-Identifier: Apache-2.0
"""Nigeria further-case forecast: identities, determinism, window arithmetic, inputs and the committed artifact."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import random
import re
import unittest

from lovs import public_repo_hygiene
from lovs.forecast import kenya_conditioned as kc
from lovs.forecast import nigeria_conditioned as nc

REPO = nc.REPO
ARTIFACT = nc.DEFAULT_OUT
MODULE = REPO / "lovs" / "forecast" / "nigeria_conditioned.py"
THIS_FILE = REPO / "tests" / "test_nigeria_conditioned.py"
# Registered files this forecast must leave byte-identical.
FROZEN = {
    "lovs/forecast/kenya_conditioned.py": "ff4d8577912acf04961b587809627d55748db5d00e6757b0966f3c7d27502e43",
    "data/case-conditioned/reference-episode-features.json": "21ad0252d9419f90e84e280b5f2764ef9adf9c539130a781276e9c5a408e277e",
    "data/importation-reference-class.json": "87905b2fa15fa79717130fb40ac0834d048f5504e2ae3a357afd5d464d29a1ea",
    "data/case-conditioned/kenya-2026-10-09-forecast.json": "d5d2da9fdd5e41e8182c73d610375a641c3276234463cb28133a67400a90adf3",
}


def _poisson(rng: random.Random, lam: float) -> int:
    n, acc, term, u = 0, math.exp(-lam), math.exp(-lam), rng.random()
    while u > acc:
        n += 1
        term *= lam / n
        acc += term
    return n


def _public_texts(include_episodes: bool) -> dict[str, str]:
    """The new public files, plus this change's doc addendum and changelog bullet."""
    texts = {p.name: p.read_text(encoding="utf-8") for p in (MODULE, ARTIFACT, THIS_FILE)}
    if include_episodes:
        texts[nc.EPISODES_FILE.name] = nc.EPISODES_FILE.read_text(encoding="utf-8")
    doc = (REPO / "docs" / "international-forecast-validation.md").read_text(encoding="utf-8")
    start = doc.index("## Addendum: Nigeria case-conditioned forecast")
    end = doc.find("\n## ", start + 1)
    texts["addendum"] = doc[start:] if end < 0 else doc[start:end]
    changelog = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    start = changelog.index("- **Nigeria case-conditioned forecast")
    stop = [i for i in (changelog.find("\n- **", start + 1), changelog.find("\n## ", start + 1)) if i > 0]
    texts["changelog"] = changelog[start:min(stop)]
    return texts


class IdentityTest(unittest.TestCase):
    def test_onward_term_matches_a_branching_simulation(self) -> None:
        # One draw, one timing class: first-generation cases are gamma-mixed Poisson with mean m; each
        # is confirmed in S with probability a and otherwise has NegBin(r, k) offspring, each confirmed
        # in S with probability g. P(nothing in S) must equal nb_none(m, k, H) with
        # H = a + (1 - a) * (1 - (1 + r g / k) ** -k).
        m, k, r, a, g = 1.2, 0.4, 0.8, 0.3, 14 / nc.G_NODES
        rng = random.Random(17)
        n = none = 0
        while n < 200_000:
            n += 1
            hit = False
            for _ in range(_poisson(rng, rng.gammavariate(k, m / k))):
                if rng.random() < a:
                    hit = True
                    break
                if any(rng.random() < g for _ in range(_poisson(rng, rng.gammavariate(k, r / k)))):
                    hit = True
                    break
            none += not hit
        h = a + (1 - a) * nc._onward_hit(r, k)[14]
        self.assertAlmostEqual(nc._onward_hit(r, k)[14], 1 - (1 + r * g / k) ** -k, places=12)
        self.assertAlmostEqual(none / n, kc.nb_none(m, k, h), delta=0.004)

    def test_onward_hit_nodes(self) -> None:
        hits = nc._onward_hit(0.7, 0.3)
        self.assertEqual(len(hits), nc.G_NODES + 1)
        self.assertEqual(hits[0], 0.0)
        self.assertAlmostEqual(hits[-1], 1 - (1 + 0.7 / 0.3) ** -0.3, places=12)
        self.assertTrue(all(a <= b for a, b in zip(hits, hits[1:])))
        self.assertAlmostEqual(nc._onward_hit(0.7, 1e6)[-1], 1 - math.exp(-0.7), places=5)

    def test_poisson_sampler_mean_and_large_means(self) -> None:
        rng = random.Random(4)
        for lam in (0.3, 4.0, 75.0):
            xs = [nc._poisson(rng, lam) for _ in range(20_000)]
            self.assertAlmostEqual(sum(xs) / len(xs), lam, delta=0.03 * lam + 0.01)


class WindowTest(unittest.TestCase):
    def test_clock_and_window_arithmetic(self) -> None:
        self.assertEqual(nc.day_end(dt.date(2026, 10, 7)), 1.0)
        self.assertEqual(nc.day_end(nc.CONDITIONING_DATE), 14.0)
        self.assertEqual(nc.T_WINDOW_OPEN, 14.0)
        self.assertEqual(nc.T_CONFIRM_END, 42.0)
        self.assertEqual(nc.T_REPORT_END, 45.0)
        self.assertLessEqual(nc.T_CONFIRM_END + nc.REPORT_DELAY, nc.T_REPORT_END)  # report-by never binds
        self.assertEqual(nc.CURVE_DATES[0], dt.date(2026, 10, 21))
        self.assertEqual(nc.CURVE_DATES[-1], dt.date(2026, 11, 17))
        self.assertEqual(len(nc.CURVE_DATES), 28)
        self.assertEqual(nc.report_cutoff(nc.CONDITIONING_DATE), 13.5)
        self.assertLess(nc.T_CARE, nc.T_LIST)
        self.assertLessEqual(nc.T_PRECAUTION[0], nc.T_LIST)

    def test_sets(self) -> None:
        sets = nc.SETS
        self.assertEqual(sets["window"], ((14.0, 42.0),))
        self.assertEqual(sets["cond_and_window"], ((-nc.INF, 13.5), (14.0, 42.0)))
        self.assertEqual(sets["by_2026-10-21"], ((-nc.INF, 14.5),))
        # A confirmation on 20 October, reported on 21 October, neither conditions the headline nor qualifies.
        self.assertFalse(nc._in(13.8, sets["cond_and_window"]))
        self.assertTrue(nc._in(13.4, sets["cond_and_window"]))
        self.assertTrue(nc._in(41.9, sets["window"]))
        self.assertFalse(nc._in(42.1, sets["window"]))
        # From 21 October on, the conditioning set covers everything before the window, so the joint set is
        # everything to the window's end.
        for date in nc.CURVE_DATES:
            self.assertGreater(nc.report_cutoff(date), nc.T_WINDOW_OPEN)
            self.assertLess(nc.report_cutoff(date), nc.T_CONFIRM_END)
            self.assertEqual(nc._date_sets(date.isoformat()), (f"by_{date.isoformat()}", "through_window_end"))

    def test_probability_in_sets(self) -> None:
        samples = sorted([1.0, 13.6, 14.0, 20.0, 50.0])
        self.assertAlmostEqual(nc._prob_in(samples, nc.SETS["cond_and_window"]), 2 / 5)
        self.assertAlmostEqual(nc._prob_in(samples, nc.SETS["window"]), 1 / 5)
        self.assertEqual(nc._prob_in(samples, nc.SETS["none_before"]), 0.0)

    def test_detection_rule(self) -> None:
        self.assertEqual(nc._confirm(9.0, 1.0, 13.0), 14.0)
        self.assertEqual(nc._confirm(15.0, 1.0, 13.0), 16.0)


class InputTest(unittest.TestCase):
    def test_reference_episodes(self) -> None:
        eps = nc.load_episodes()
        self.assertEqual(eps[:13], kc.load_features())
        kenya = eps[13]
        self.assertEqual(len(eps), 14)
        self.assertEqual(kenya["episode_id"], "ken-2026-10-nairobi")
        self.assertTrue(kenya["outcome"])
        self.assertEqual((kenya["e_comm"], kenya["f_pre"], kenya["post_level"], kenya["p_death"], kenya["funeral_level"]),
                         ([0.01, 0.10], [0.0, 0.80], "VHF", 1.0, "SDB"))
        self.assertEqual((kenya["e_hosp"], kenya["pre_level"], kenya["e_fun"]), ([0.4, 1.0], "STANDARD", [1.0, 1.0]))
        self.assertTrue(kenya["rationale"].strip())
        for ep in eps:
            self.assertIn(ep["pre_level"], kc.LEVELS)
            self.assertIn(ep["post_level"], kc.LEVELS)
            self.assertIn(ep["funeral_level"], kc.FUNERAL)
        for eid in nc.DISPUTED_YES:
            self.assertIn(eid, {e["episode_id"] for e in eps})

    def test_benchmarks(self) -> None:
        marks = nc.benchmarks()
        self.assertAlmostEqual(marks["B1_pooled_reference_rate"]["probability"], 6.5 / 15, places=12)
        b2 = marks["B2_closest_analogue"]
        eps = nc.load_episodes()
        for reading in (lambda r: r[0], lambda r: (r[0] + r[1]) / 2, lambda r: r[1]):
            ids = [e["episode_id"] for e in eps if e["pre_level"] == "NONE" and reading(e["f_pre"]) >= 0.8]
            self.assertEqual(ids, b2["episode_ids"])
        self.assertAlmostEqual(b2["probability"], (b2["yes"] + 0.5) / (b2["episodes"] + 1), places=12)

    def test_registered_inputs(self) -> None:
        self.assertEqual(nc.MODEL_VERSION, "nigeria_conditioned-v1.0.0")
        self.assertEqual((nc.SEED, nc.N_DRAWS, nc.N_DRAWS_SENSITIVITY, nc.N_TIMING), (20261020, 40_000, 20_000, 60_000))
        self.assertAlmostEqual(sum(s["weight"] for s in nc.SCENARIOS.values()), 1.0)
        self.assertEqual(nc.incubation_prior(), (3.0, 0.406))
        self.assertEqual(len(nc.SENSITIVITY), 15)
        self.assertEqual(nc.REGISTRATION["plan_sha256"],
                         "02aba2506065add1189d8a6a695209f0ce4dd47e2273ef8c4effbadb82912b2b")

    def test_every_input_is_labelled(self) -> None:
        inputs = nc.build_artifact(nc.run(n=60), [])["inputs"]
        for key in inputs:
            self.assertIn(key, nc.PROVENANCE, key)
            self.assertTrue(nc.PROVENANCE[key].startswith(
                ("source:", "judgement", "case evidence", "the published", "this map", "registered", "the registered")), key)

    def test_case_evidence_cites_the_captured_sources(self) -> None:
        text = json.dumps(nc.CITATIONS)
        for digest in ("d453bcb13f26dd67f306e4302c6bed78bb95b993d2d145db3ee1e67f2a9192c4",
                       "b0e8eafedb27bbb63bcec0c34af47a07864b5e56b0ceeb5f5cc4d3377e64a29a",
                       "451eb0eb2e18a743aa668b3f7c3f47ea638bfca4c91df8fd0863dfea39715e9c",
                       "e4dc146db5b422266a420a3e10d467f32161c09f123a2e314abbea592aad483f",
                       "d88c332f9c74ab95836301b3e866f150d9aeb52112efc6d4707443200532eff7"):
            self.assertIn(digest, text)

    def test_frozen_files_untouched(self) -> None:
        for path, digest in FROZEN.items():
            self.assertEqual(hashlib.sha256((REPO / path).read_bytes()).hexdigest(), digest, path)


class RunTest(unittest.TestCase):
    N = 1500

    @classmethod
    def setUpClass(cls) -> None:
        cls.res = nc.run(n=cls.N)

    def test_deterministic(self) -> None:
        again = nc.run(n=self.N)
        self.assertEqual(json.dumps(kc._rounded(self.res), sort_keys=True),
                         json.dumps(kc._rounded(again), sort_keys=True))

    def test_curve_falls_monotonically(self) -> None:
        ps = [row["p"] for row in self.res["curve"]]
        self.assertTrue(all(0.0 <= p <= 1.0 for p in ps))
        self.assertTrue(all(a >= b - 1e-12 for a, b in zip(ps, ps[1:])))
        self.assertGreaterEqual(self.res["headline"]["p"], ps[0] - 1e-12)
        self.assertLess(ps[-1], 0.05)
        for row in self.res["curve"]:
            self.assertLessEqual(row["p10"], row["p90"])
            self.assertLessEqual(max(row["index_linked"], row["background_later_importation"]), row["p"] + 1e-12)

    def test_decomposition_sums_to_the_total(self) -> None:
        sim = self.res["simulation"]
        parts = sim["decomposition"]
        self.assertEqual(set(parts), set(nc.SIM_PATHWAYS))
        self.assertAlmostEqual(sum(v["expected_qualifying_cases"] for v in parts.values()),
                               sim["expected_qualifying_cases"], places=9)
        self.assertAlmostEqual(sum(sim["shares_of_yes_by_first_qualifying_case"].values()), 1.0, places=9)
        self.assertEqual(parts["P4"]["expected_qualifying_cases"], 0.0)
        self.assertLessEqual(sim["p_at_least_10"], sim["p_at_least_3"])
        self.assertLessEqual(sim["p_at_least_3"], sim["p_yes_simulated"])
        # The simulated probability of YES agrees with the closed form within Monte Carlo error.
        p = self.res["headline"]["p"]
        se = math.sqrt(p * (1 - p) / (self.N * nc.SIM_REPLICATES * 0.5))
        self.assertLess(abs(sim["p_yes_simulated"] - p), 4 * se)

    def test_scenarios_order_by_precaution(self) -> None:
        rows = {r["scenario"]: r for r in self.res["scenarios"]}
        self.assertGreater(rows["N1"]["median_m_calibrated"], rows["N2"]["median_m_calibrated"])
        self.assertAlmostEqual(sum(r["share_given_no_report"] for r in self.res["scenarios"]), 1.0, places=9)

    def test_validation_covers_fourteen_episodes(self) -> None:
        rows = self.res["validation"]["episodes"]
        self.assertEqual(len(rows), 14)
        yes = sum(r["outcome"] for r in rows)
        for r in rows:
            self.assertAlmostEqual(r["base_rate_loo"], (yes - r["outcome"] + 0.5) / 14)

    def test_incubation_row_moves_the_forecast(self) -> None:
        base = nc.run({"full": False}, n=self.N)["headline"]["p"]
        longer = nc.run({"full": False, "incubation": (3.0, 0.3)}, n=self.N)["headline"]["p"]
        self.assertNotEqual(base, longer)


class ArtifactTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.artifact = json.loads(ARTIFACT.read_text(encoding="utf-8"))

    def test_payload_digest_verifies(self) -> None:
        self.assertEqual(self.artifact["payload_sha256"], nc.payload_digest(self.artifact))

    def test_required_sections(self) -> None:
        for key in ("question", "rules", "registration", "headline", "curve", "count_tiers", "decomposition",
                    "scenarios", "validation", "sensitivity", "scoring_plan", "inputs", "case_evidence",
                    "disclosures", "method"):
            self.assertIn(key, self.artifact)
        head = self.artifact["headline"]
        for key in ("B1_pooled_reference_rate", "B2_closest_analogue"):
            self.assertIn(key, head["benchmarks"])
        self.assertEqual(len(head["interval_80"]), 2)
        self.assertEqual([r["date"] for r in self.artifact["curve"]], [d.isoformat() for d in nc.CURVE_DATES])
        self.assertEqual([r["variant"] for r in self.artifact["sensitivity"]], [name for name, _, _ in nc.SENSITIVITY])
        self.assertEqual(self.artifact["registration"]["wiki_page_id"],
                         "reference:bdbv-nigeria-forecast-precommitment-2026-10-20")
        self.assertIn(f"{round(100 * head['probability'])}%", head["label"])

    def test_committed_artifact_regenerates(self) -> None:
        # Sensitivity rows are carried over to keep this test quick; the module recomputes them.
        fresh = nc.build_artifact(nc.run(), self.artifact["sensitivity"])
        self.assertEqual(fresh, self.artifact)

    def test_no_personal_or_facility_detail(self) -> None:
        # The needles are assembled from fragments so that this file does not itself carry them.
        forbidden = [a + b for a, b in (("Niza", "miye"), ("NIZA", "MIYE"), ("Tur", "kish"), ("Ugan", "dan "),
                                        ("Niger", "ian national"), ("Congo", "lese"), ("air", "line"),
                                        ("fli", "ght"), ("Abu", "ja"), ("-year", "-old"), ("ye", "ars old"),
                                        ("busi", "ness trip"), ("hus", "band"), ("wi", "fe"))]
        for name, text in _public_texts(include_episodes=True).items():
            for needle in forbidden:
                self.assertNotIn(needle, text, f"{name}: {needle}")
        # Sex in artifact text: whole words, any case. The frozen episodes file is copied verbatim and excluded.
        words = re.compile(r"\b(" + "|".join(("wo" + "man", "fe" + "male", "s" + "he", "h" + "er")) + r")\b",
                           re.IGNORECASE)
        for name, text in _public_texts(include_episodes=False).items():
            self.assertIsNone(words.search(text), f"{name}: {words.search(text)}")

    def test_never_hit_or_miss(self) -> None:
        text = ARTIFACT.read_text(encoding="utf-8")
        self.assertIn("never called a hit or a miss", text)
        for needle in ("as a miss", "forecast missed", "forecast hit"):
            self.assertNotIn(needle, text)

    def test_new_files_pass_public_hygiene_and_style(self) -> None:
        for name, text in _public_texts(include_episodes=True).items():
            self.assertFalse(public_repo_hygiene.contains_marker(text), name)
            self.assertNotIn(chr(0x2014), text, name)


if __name__ == "__main__":
    unittest.main()
