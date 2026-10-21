# SPDX-License-Identifier: Apache-2.0
"""Nigeria v2: determinism, exposure windows, group sums, curve and N6 shape, frozen v1 pins and public text."""
from __future__ import annotations

import hashlib
import json
import math
import random
import re
import unittest

from lovs import public_repo_hygiene
from lovs.forecast import kenya_conditioned as kc
from lovs.forecast import nigeria_v2 as v2

REPO = v2.REPO
ARTIFACT = v2.DEFAULT_OUT
MODULE = REPO / "lovs" / "forecast" / "nigeria_v2.py"
THIS_FILE = REPO / "tests" / "test_nigeria_v2.py"
DOC_HEADING = "### Nigeria v2"
CHANGELOG_BULLET = "- **Nigeria v2"
# The frozen v1 records and the modules v2 imports: byte-identical after this change.
FROZEN = {
    "lovs/forecast/nigeria_conditioned.py": "4e9ad34eccc5913d5740b67df92db90bbed418770858344ce915518b89d71ae8",
    "lovs/forecast/nigeria_nowcast.py": "042cb28466458096514056dacf6bc37af15491bf64667f4deb24e1c39ff58b1a",
    "data/case-conditioned/nigeria-2026-10-20-forecast.json": "14da3c435e07d1a0da08b28be6c3b947adb8c1988a5290859a0ac98d58dbf9db",
    "data/case-conditioned/nigeria-2026-10-20-nowcast.json": "cca6f13a6040ebd9b02114c4e8fec59f9086a5543faa2e70c41577c0bd443890",
    "data/case-conditioned/nigeria-reference-episodes.json": "11e157a7bf836f66c480a548911364254169e0543f021de23fde5d4ae4a4d4f1",
    "lovs/forecast/kenya_conditioned.py": "ff4d8577912acf04961b587809627d55748db5d00e6757b0966f3c7d27502e43",
    "lovs/forecast/kenya_escape.py": "7cf651618a92a1856304a1a26c8b74f7e7ab0d374ac767743abb56f19a21b1d5",
}


def _section(text: str, start_marker: str) -> str | None:
    if start_marker not in text:
        return None
    start = text.index(start_marker)
    ends = [i for i in (text.find("\n### ", start + 1), text.find("\n## ", start + 1), text.find("\n- **", start + 1))
            if i > 0] if start_marker.startswith("- **") else [
        i for i in (text.find("\n### ", start + 1), text.find("\n## ", start + 1)) if i > 0]
    return text[start:min(ends)] if ends else text[start:]


def _public_texts() -> dict[str, str]:
    """The v2 public files, plus its doc subsection and changelog bullet once written."""
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


class ProfileTest(unittest.TestCase):
    def test_profile_peaks_at_one(self) -> None:
        for w in (0.0, 0.15, 0.3, 1.0):
            vals = v2.profile(w)
            self.assertAlmostEqual(max(vals), 1.0, places=12)
            self.assertEqual(len(vals), 15)

    def test_window_shares(self) -> None:
        rng = random.Random(1)
        for _ in range(300):
            vals = v2.profile(rng.uniform(0.0, 0.3))
            t_iso = rng.uniform(*v2.T_ISO)
            g = v2.window_exposure(vals, t_iso)
            self.assertAlmostEqual(sum(g.values()), sum(vals), places=10)
            self.assertTrue(all(x > 0.0 for x in g.values()))
        surv = v2.window_exposure(v2.profile(0.0), 13.0)
        self.assertAlmostEqual(surv["home"], 0.14 / 0.12, places=10)
        self.assertAlmostEqual(surv["facility"], 0.79 / 0.12, places=10)
        self.assertAlmostEqual(surv["isolation"], 0.07 / 0.12, places=10)
        self.assertAlmostEqual(v2.integral(v2.profile(0.0), 12.5, 13.5), (0.5 * 0.04 + 0.5 * 0.04) / 0.12, places=10)

    def test_infection_times_follow_g_inside_the_window(self) -> None:
        vals = v2.profile(0.0)
        rng = random.Random(2)
        times = [v2.sample_time(rng, vals, 4.0, 13.0) for _ in range(40_000)]
        self.assertTrue(all(4.0 <= t <= 13.0 for t in times))
        # Share in [4, 6): (0.10 + 0.12) / 0.79.
        self.assertAlmostEqual(sum(1 for t in times if t < 6.0) / len(times), 0.22 / 0.79, delta=0.01)

    def test_onward_share(self) -> None:
        full = v2.onward_segments(2.0, math.inf, listed=False)
        self.assertAlmostEqual(sum((b - a) * f for a, b, f in full), 1.0, places=12)
        self.assertEqual(full[0][0], 2.0)
        self.assertEqual(full[-1][1], 17.0)
        # A listed case with onset after day 13 carries the monitoring factor throughout.
        late = v2.onward_segments(14.0, math.inf, listed=True)
        self.assertAlmostEqual(sum((b - a) * f for a, b, f in late), 0.2, places=12)
        # A listed case with onset at day 10: days 0 to 3 of illness in full, the rest at 0.2.
        mid = v2.onward_segments(10.0, math.inf, listed=True)
        head = sum(v2.SURVIVOR_UNIT[:3])
        self.assertAlmostEqual(sum((b - a) * f for a, b, f in mid), head + 0.2 * (1.0 - head), places=12)
        # Confirmation ends infectiousness.
        cut = v2.onward_segments(10.0, 12.0, listed=False)
        self.assertAlmostEqual(sum((b - a) * f for a, b, f in cut), sum(v2.SURVIVOR_UNIT[:2]), places=12)
        self.assertEqual(v2.onward_segments(10.0, 9.0, listed=False), [])

    def test_detection_rule(self) -> None:
        rng = random.Random(3)
        self.assertTrue(all(v2.confirm_time(rng, 9.0, True) > 13.0 for _ in range(200)))
        self.assertTrue(all(v2.confirm_time(rng, 15.0, True) > 15.0 for _ in range(200)))
        lags = [v2.confirm_time(rng, 20.0, False) - 20.0 for _ in range(40_000)]
        self.assertAlmostEqual(sum(lags) / len(lags), 5.0, delta=0.1)


class InputTest(unittest.TestCase):
    def test_registered_inputs(self) -> None:
        self.assertEqual(v2.MODEL_VERSION, "nigeria_v2-v1.0.0")
        self.assertEqual((v2.SEED, v2.N_DRAWS, v2.N_DRAWS_SENSITIVITY), (2026102102, 40_000, 20_000))
        self.assertEqual(v2.incubation_prior(), (3.0, 0.406))
        self.assertEqual(v2.REGISTRATION["rules_sha256"],
                         "acf07202acb0dcf07ab2ac0ebd41a94ef6dc3737817d471540e59e21a44a0a3f")
        self.assertEqual((v2.T_WINDOW_OPEN, v2.T_CONFIRM_END, v2.T_REPORT_END, v2.COND_CUTOFF), (14.0, 42.0, 45.0, 13.5))
        self.assertEqual(len(v2.CURVE_DATES), 28)

    def test_sensitivity_list_is_the_registered_sixteen(self) -> None:
        self.assertEqual(len(v2.SENSITIVITY), 16)
        self.assertEqual([r[0].split("_")[0] for r in v2.SENSITIVITY], [f"R{i}" for i in range(1, 17)])
        for _, _, over in v2.SENSITIVITY:
            v2.options(over)
        with self.assertRaises(ValueError):
            v2.options({"bogus": 1})

    def test_benchmarks(self) -> None:
        marks = v2.benchmarks()
        self.assertAlmostEqual(marks["B1_pooled_destination_import_rate"]["probability"], 2.5 / 18, places=12)
        self.assertAlmostEqual(marks["B2_unsuspected_care_destination_subgroup"]["probability"], 2.5 / 3, places=12)

    def test_leave_one_out(self) -> None:
        loo = v2.leave_one_out(grid=40)
        for key in ("uganda", "kenya"):
            self.assertGreater(loo[key]["predictive_mean"], 0.0)
            self.assertTrue(0.0 <= loo[key]["p_at_least_observed"] <= 1.0)
        self.assertEqual(loo["france"]["predictive_mean"], 0.0)
        mean, tail = v2._negbin_mean_tail(4.0, 2.0, 1.0, 0)
        self.assertEqual((mean, tail), (2.0, 1.0))

    def test_v1_values_beside(self) -> None:
        beside = v2.v1_beside()
        self.assertEqual(beside["v1_forecast"]["headline"], 0.3262)
        self.assertEqual(beside["v1_nowcast"]["N7_p_at_least_one_report_by_2026-11-17"], 0.7856)

    def test_frozen_files_untouched(self) -> None:
        for path, digest in FROZEN.items():
            self.assertEqual(hashlib.sha256((REPO / path).read_bytes()).hexdigest(), digest, path)


class RunTest(unittest.TestCase):
    N = 1500

    @classmethod
    def setUpClass(cls) -> None:
        cls.res = v2.run(n=cls.N, seed=7)

    def test_deterministic(self) -> None:
        a = json.dumps(kc._rounded(v2.run(n=300, seed=5)), sort_keys=True)
        b = json.dumps(kc._rounded(v2.run(n=300, seed=5)), sort_keys=True)
        self.assertEqual(a, b)
        self.assertNotEqual(a, json.dumps(kc._rounded(v2.run(n=300, seed=6)), sort_keys=True))

    def test_poisson_counts_match_their_means(self) -> None:
        rng = random.Random(9)
        opts = v2.options()
        expected = got = 0.0
        for _ in range(4_000):
            p = v2.draw_parameters(rng, opts)
            expected += sum(p["means"].values())
            got += len(v2.simulate_run(rng, p)["first"])
        self.assertAlmostEqual(got / expected, 1.0, delta=0.05)

    def test_groups_sum_to_total(self) -> None:
        for kind in ("unconditioned", "conditioned"):
            n1 = self.res["nowcast"][kind]["N1_infected_by_group_by_end_of_20_october"]
            self.assertAlmostEqual(n1["total"]["mean"], sum(n1[g]["mean"] for g in "HWC"), places=9)
            self.assertAlmostEqual(sum(n1["total"]["distribution"].values()), 1.0, places=9)
            parts = n1["parts_mean"]
            self.assertAlmostEqual(n1["W"]["mean"], parts["W_facility"] + parts["W_isolation"], places=9)
            self.assertAlmostEqual(n1["C"]["mean"], parts["C_facility"] + parts["C_isolation"], places=9)
            self.assertAlmostEqual(n1["total"]["mean"] + n1["after_20_october_mean"],
                                   n1["first_generation_all_time_mean"], places=9)
        for kind in ("forecast_conditioned", "forecast_unconditioned"):
            s3 = self.res[kind]
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
        self.assertEqual([r["date"] for r in self.res["curve"]], [d.isoformat() for d in v2.CURVE_DATES])
        self.assertTrue(all(0.0 <= p <= 1.0 for p in ps))
        self.assertTrue(all(a >= b - 1e-12 for a, b in zip(ps, ps[1:])))

    def test_n6_non_decreasing(self) -> None:
        for kind in ("unconditioned", "conditioned"):
            n6 = self.res["nowcast"][kind]["N6_cumulative_reports"]
            rows = n6["by_date"]
            for a, b in zip(rows, rows[1:]):
                self.assertLessEqual(a["expected_cumulative"], b["expected_cumulative"])
                self.assertLessEqual(a["p_at_least_one"], b["p_at_least_one"])
                self.assertLessEqual(a["interval_80"][0], b["interval_80"][0])
                self.assertLessEqual(a["interval_80"][1], b["interval_80"][1])
        self.assertEqual(self.res["nowcast"]["conditioned"]["N6_cumulative_reports"][
            "expected_reports_by_end_of_2026-10-20"], 0.0)

    def test_onsets_account_for_every_infection(self) -> None:
        now = self.res["nowcast"]["unconditioned"]
        n5 = now["N5_onsets"]
        gen1 = sum(r["first_generation"] for r in n5["by_date"]) + n5["before_2026-10-08"]["gen1"] + n5[
            "after_2026-11-17"]["gen1"]
        self.assertAlmostEqual(gen1, now["N1_infected_by_group_by_end_of_20_october"]["first_generation_all_time_mean"],
                               places=9)

    def test_rows_move_the_way_their_input_does(self) -> None:
        base = v2.run(n=self.N, seed=7, full=False)
        self.assertEqual(base["conditioned"], self.res["conditioned"])
        more_q = v2.run({"q": (0.5, 1.0)}, n=self.N, seed=7, full=False)
        self.assertGreater(more_q["first_generation_unconditioned"]["expected_infections"],
                           base["first_generation_unconditioned"]["expected_infections"])
        none = v2.run({"conditioning": False}, n=self.N, seed=7, full=False)
        self.assertEqual(none["headline"], base["unconditioned"])


@unittest.skipUnless(ARTIFACT.exists(), "artifact not built yet")
class ArtifactTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = ARTIFACT.read_text(encoding="utf-8")
        cls.artifact = json.loads(cls.text)

    def test_digests_verify(self) -> None:
        self.assertEqual(self.artifact["payload_sha256"], kc.payload_digest(self.artifact))
        self.assertEqual(self.artifact["results_sha256"], v2.results_digest(self.artifact["results"]))
        entry = self.artifact["registration"]["entry_1_first_run"]
        if entry is not None:
            self.assertEqual(entry["results_sha256"], self.artifact["results_sha256"])

    def test_results_are_the_precommitted_ones(self) -> None:
        self.assertEqual(self.artifact["results_sha256"], "fbccbe504717e1415ad979aca83a440c40f643c2dedd613bfb58c54d0c85e7d9")
        self.assertEqual(self.artifact["registration"]["entry_1_first_run"]["payload_sha256"],
                         "586efa310f69ab187638a988984f99434582442fe984eb05bd028e129d9a37c4")

    def test_required_sections(self) -> None:
        res = self.artifact["results"]
        for key in ("headline", "S1_curve", "S2_count_tiers", "S3_decomposition", "nowcast", "leave_one_out",
                    "sensitivity"):
            self.assertIn(key, res)
        self.assertEqual([r["variant"] for r in res["sensitivity"]], [r[0] for r in v2.SENSITIVITY])
        for row in res["sensitivity"]:
            for key in ("headline", "unconditioned", "p_at_least_one_infection", "expected_infections",
                        "expected_health_worker_infections"):
                self.assertIn(key, row)
        for kind in ("unconditioned", "conditioned"):
            self.assertEqual(set(res["nowcast"][kind]) - {"weight_share"},
                             {"N1_infected_by_group_by_end_of_20_october", "N2_p_at_least_one_infection",
                              "N3_symptomatic_by_end_of_20_october", "N4_second_generation_infected_by_end_of_20_october",
                              "N5_onsets", "N6_cumulative_reports"})
        self.assertEqual(self.artifact["method"]["seed"], 2026102102)
        self.assertEqual(self.artifact["headline"]["probability"], res["headline"]["probability"])

    def test_v1_beside_matches_the_frozen_artifacts(self) -> None:
        self.assertEqual(self.artifact["v1_beside_v2"], kc._rounded(v2.v1_beside()))

    def test_committed_artifact_regenerates(self) -> None:
        # Sensitivity rows are carried over to keep this test quick; the module recomputes them.
        fresh = v2.build_artifact(v2.run(), self.artifact["results"]["sensitivity"])
        self.assertEqual(v2.render(fresh), self.text)

    def test_never_verdict_words(self) -> None:
        for name in (ARTIFACT.name, "doc_subsection"):
            text = _public_texts().get(name)
            if text is not None:
                self.assertIsNone(re.search(r"\b(hit|hits|miss|missed|misses)\b", text, re.IGNORECASE), name)


class PublicTextTest(unittest.TestCase):
    def test_no_personal_or_facility_detail(self) -> None:
        # The registered forecast's deny-list, copied, plus one travel term; needles are assembled from fragments.
        forbidden = [a + b for a, b in (("Niza", "miye"), ("NIZA", "MIYE"), ("Tur", "kish"), ("Ugan", "dan "),
                                        ("Niger", "ian national"), ("Congo", "lese"), ("air", "line"),
                                        ("fli", "ght"), ("Abu", "ja"), ("-year", "-old"), ("ye", "ars old"),
                                        ("busi", "ness trip"), ("hus", "band"), ("wi", "fe"), ("tran", "sit"))]
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
