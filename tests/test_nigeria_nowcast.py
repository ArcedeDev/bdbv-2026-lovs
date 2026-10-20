# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import hashlib
import json
import random
import re
import unittest

from lovs import public_repo_hygiene
from lovs.forecast import kenya_conditioned as kc
from lovs.forecast import nigeria_nowcast as nn

REPO = nn.REPO
ARTIFACT = nn.DEFAULT_OUT
MODULE = REPO / "lovs" / "forecast" / "nigeria_nowcast.py"
THIS_FILE = REPO / "tests" / "test_nigeria_nowcast.py"
FROZEN = {"lovs/forecast/kenya_conditioned.py": "ff4d8577912acf04961b587809627d55748db5d00e6757b0966f3c7d27502e43"}
DOC_HEADING = "### Nigeria nowcast"
CHANGELOG_BULLET = "- **Nigeria nowcast"


def _public_texts() -> dict[str, str]:
    """The nowcast's public files, plus its doc subsection and changelog bullet once merged."""
    texts = {p.name: p.read_text(encoding="utf-8") for p in (MODULE, THIS_FILE)}
    if ARTIFACT.exists():
        texts[ARTIFACT.name] = ARTIFACT.read_text(encoding="utf-8")
    doc = (REPO / "docs" / "international-forecast-validation.md").read_text(encoding="utf-8")
    if DOC_HEADING in doc:
        start = doc.index(DOC_HEADING)
        ends = [i for i in (doc.find("\n### ", start + 1), doc.find("\n## ", start + 1)) if i > 0]
        texts["doc_subsection"] = doc[start:min(ends)] if ends else doc[start:]
    changelog = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    if CHANGELOG_BULLET in changelog:
        start = changelog.index(CHANGELOG_BULLET)
        ends = [i for i in (changelog.find("\n- **", start + 1), changelog.find("\n## ", start + 1)) if i > 0]
        texts["changelog"] = changelog[start:min(ends)] if ends else changelog[start:]
    return texts


class ProfileTest(unittest.TestCase):
    def test_window_shares_sum_to_one(self) -> None:
        rng = random.Random(1)
        for reading in nn.READINGS:
            surv, fatal = nn.knots(nn.F_SURVIVOR, reading), nn.knots(nn.F_FATAL, reading)
            for _ in range(200):
                kn = nn.mix(surv, fatal, rng.random())
                shares = nn.window_shares(kn, rng.uniform(*nn.T_ISO))
                self.assertAlmostEqual(sum(shares.values()), 1.0, places=12)
                self.assertTrue(all(v >= 0.0 for v in shares.values()))

    def test_time_point_reading_reproduces_the_evidence_shares(self) -> None:
        surv = nn.window_shares(nn.knots(nn.F_SURVIVOR), 13.0)
        for key, value in (("community", 0.24), ("facility", 0.73), ("isolation", 0.03)):
            self.assertAlmostEqual(surv[key], value, places=9)
        fatal = nn.window_shares(nn.knots(nn.F_FATAL), 13.0)
        self.assertAlmostEqual(fatal["community"], 0.18, places=9)
        self.assertAlmostEqual(fatal["facility"], 0.79, places=9)
        other = nn.window_shares(nn.knots(nn.F_SURVIVOR, "day_of_illness"), 13.0)
        self.assertAlmostEqual(other["community"], 0.14, places=9)
        self.assertAlmostEqual(other["isolation"], 0.07, places=9)
        self.assertEqual(nn.knots(nn.F_SURVIVOR)[-1], (14.0, 1.0))
        self.assertEqual(nn.T_AS_OF, 14.0)

    def test_restricted_infection_times_stay_in_window_and_follow_f(self) -> None:
        kn = nn.knots(nn.F_SURVIVOR)
        rng = random.Random(2)
        times = [nn.infection_time(rng, kn, 4.0, 13.0) for _ in range(40_000)]
        self.assertTrue(all(4.0 <= t <= 13.0 for t in times))
        # Share of the facility window falling in (4, 6]: (0.12 + 0.12) / 0.73.
        self.assertAlmostEqual(sum(1 for t in times if t <= 6.0) / len(times), 0.24 / 0.73, delta=0.01)
        early = [nn.infection_time(rng, kn, None, 4.0) for _ in range(40_000)]
        self.assertAlmostEqual(sum(1 for t in early if t == 0.0) / len(early), 0.01 / 0.24, delta=0.005)
        for u in (0.005, 0.2, 0.5, 0.97):
            self.assertAlmostEqual(nn.cdf(kn, nn.invert(kn, u)), max(u, 0.01), places=12)


class SamplerTest(unittest.TestCase):
    def test_discrete_samplers(self) -> None:
        rng = random.Random(3)
        ints = [nn._randint(rng, (1, 6)) for _ in range(60_000)]
        self.assertEqual(set(ints), set(range(1, 7)))
        self.assertAlmostEqual(sum(ints) / len(ints), 3.5, delta=0.03)
        self.assertAlmostEqual(sum(nn._binomial(rng, 20, 0.3) for _ in range(20_000)) / 20_000, 6.0, delta=0.06)
        for lam in (0.0, 0.4, 3.0):
            xs = [nn._poisson(rng, lam) for _ in range(20_000)]
            self.assertAlmostEqual(sum(xs) / len(xs), lam, delta=0.03 * lam + 0.01)
        lags = [nn._erlang_mean(rng, 5.0) for _ in range(40_000)]
        self.assertAlmostEqual(sum(lags) / len(lags), 5.0, delta=0.08)

    def test_incubation_is_read_from_the_programme_prior(self) -> None:
        self.assertEqual(nn.incubation_prior(), (3, 0.406))

    def test_options_reject_unknown_keys(self) -> None:
        with self.assertRaises(ValueError):
            nn.options({"bogus": 1})
        with self.assertRaises(ValueError):
            nn.options({"scenario": "N3"})


class RunTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.draws = nn.run(n=3_000, seed=11)
        cls.summary = nn.summarise(cls.draws)

    def test_determinism(self) -> None:
        self.assertEqual(nn.run(n=200, seed=5), nn.run(n=200, seed=5))
        self.assertNotEqual(nn.run(n=200, seed=5), nn.run(n=200, seed=6))

    def test_group_decomposition_sums_to_total(self) -> None:
        for d in self.draws:
            parts, groups = d["parts"], d["groups"]
            self.assertEqual(d["total"], sum(groups.values()))
            self.assertEqual(sum(parts.values()), d["total"])
            self.assertEqual(len(d["onsets"]), d["total"])
            self.assertLessEqual(len(d["reports"]), d["total"])
            self.assertLessEqual(groups["C"], nn.GROUPS["C"]["n"][1])
            self.assertLessEqual(groups["W"], nn.GROUPS["W"]["n"][1])
        n1 = self.summary["N1_infected_by_group"]
        self.assertAlmostEqual(n1["total"]["mean"], sum(n1[g]["mean"] for g in "HWCO"), places=9)
        self.assertAlmostEqual(sum(n1["total"]["distribution"].values()), 1.0, places=9)

    def test_n6_non_decreasing_and_n7_matches(self) -> None:
        rows = self.summary["N6_cumulative_reports"]["by_date"]
        self.assertEqual(rows[0]["date"], "2026-10-21")
        self.assertEqual(rows[-1]["date"], "2026-11-17")
        for a, b in zip(rows, rows[1:]):
            self.assertLessEqual(a["expected_cumulative"], b["expected_cumulative"])
            self.assertLessEqual(a["p_at_least_one"], b["p_at_least_one"])
            self.assertLessEqual(a["interval_80"][0], b["interval_80"][0])
            self.assertLessEqual(a["interval_80"][1], b["interval_80"][1])
        n7 = self.summary["N7_cross_check"]
        self.assertEqual(n7["p_at_least_one_report_by_2026-11-17"], rows[-1]["p_at_least_one"])
        self.assertLessEqual(rows[-1]["p_at_least_one"], self.summary["N2_p_at_least_one_infection"]["any"])

    def test_n5_onsets_account_for_every_infection(self) -> None:
        n5 = self.summary["N5_first_generation_onsets"]
        self.assertEqual(len(n5["by_date"]), 41)
        total = sum(r["expected_onsets"] for r in n5["by_date"]) + n5["before_2026-10-08"] + n5["after_2026-11-17"]
        self.assertAlmostEqual(total, self.summary["N1_infected_by_group"]["total"]["mean"], places=9)

    def test_household_direct_mean_matches_its_parts(self) -> None:
        # E[n] E[AR] E[s_H] E[v] with independent draws; s_H mixes 0.24 and 0.18 by w_fatal (mean 0.15).
        draws = nn.run(n=30_000, seed=12)
        mean = sum(d["parts"]["H_direct"] for d in draws) / len(draws)
        expected = 3.5 * 0.365 * (0.85 * 0.24 + 0.15 * 0.18) * 0.75
        self.assertAlmostEqual(mean, expected, delta=0.04 * expected)

    def test_rows_move_the_way_their_input_does(self) -> None:
        base = nn.row_summary(nn.run(n=3_000, seed=13))
        more = nn.row_summary(nn.run({"n_w": (30, 60)}, n=3_000, seed=13))
        self.assertGreater(more["p_any_health_worker"], base["p_any_health_worker"])
        fewer_unlisted = nn.row_summary(nn.run({"unlisted": (0.05, 0.15)}, n=3_000, seed=13))
        self.assertAlmostEqual(fewer_unlisted["expected_first_generation"], base["expected_first_generation"], places=9)

    def test_sensitivity_list_is_the_registered_twelve(self) -> None:
        self.assertEqual(len(nn.SENSITIVITY), 12)
        self.assertEqual([r[0].split("_")[0] for r in nn.SENSITIVITY], [f"S{i}" for i in range(1, 13)])
        for _, _, over in nn.SENSITIVITY + nn.UNREGISTERED_SENSITIVITY:
            nn.options(over)
        self.assertEqual(len(nn.UNREGISTERED_SENSITIVITY), 1)


@unittest.skipUnless(ARTIFACT.exists(), "artifact not built yet")
class ArtifactTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = ARTIFACT.read_text(encoding="utf-8")
        cls.artifact = json.loads(cls.text)

    def test_digests_verify(self) -> None:
        self.assertEqual(self.artifact["payload_sha256"], kc.payload_digest(self.artifact))
        self.assertEqual(self.artifact["results_sha256"], nn.results_digest(self.artifact["results"]))
        entry = self.artifact["registration"]["entry_1_first_run"]
        if entry is not None:
            self.assertEqual(entry["results_sha256"], self.artifact["results_sha256"])

    def test_registered_rows_and_method(self) -> None:
        res = self.artifact["results"]
        self.assertEqual([r["variant"] for r in res["sensitivity"]], [r[0] for r in nn.SENSITIVITY])
        self.assertEqual([r["variant"] for r in res["sensitivity_unregistered"]], ["U1_day_of_illness_reading"])
        self.assertEqual(self.artifact["method"]["seed"], 20261021)
        self.assertEqual(self.artifact["method"]["n_draws"], 40_000)
        self.assertEqual(self.artifact["model_version"], "nigeria_nowcast-v1.0.0")
        rows = res["N6_cumulative_reports"]["by_date"]
        for a, b in zip(rows, rows[1:]):
            self.assertLessEqual(a["expected_cumulative"], b["expected_cumulative"])

    def test_results_are_the_precommitted_ones(self) -> None:
        self.assertEqual(self.artifact["results_sha256"], "675ae61d9b038a298e88679d1daab6cd585c8c65bbe9eebe40546599bb1ec322")

    def test_cross_check_matches_the_forecast_artifact(self) -> None:
        head = self.artifact["cross_check"]["registered_forecast_headline"]
        path = REPO / head["artifact"]
        if not path.exists():
            self.skipTest("forecast artifact absent")
        forecast = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(forecast["payload_sha256"], head["payload_sha256"], "cross-check is stale")
        self.assertEqual(forecast["headline"]["probability"], head["probability"])
        self.assertEqual(forecast["headline"]["interval_80"], head["interval_80"])
        self.assertEqual(forecast["headline"]["unconditioned"], head["unconditioned"])

    def test_committed_artifact_regenerates_byte_for_byte(self) -> None:
        self.assertEqual(nn.render(nn.build()), self.text)

    def test_never_hit_or_miss(self) -> None:
        self.assertIn("never called a hit or a miss", self.text)
        for needle in ("as a miss", "nowcast missed", "nowcast hit", "forecast missed", "forecast hit"):
            self.assertNotIn(needle, self.text)


class PublicTextTest(unittest.TestCase):
    def test_frozen_files_untouched(self) -> None:
        for path, digest in FROZEN.items():
            self.assertEqual(hashlib.sha256((REPO / path).read_bytes()).hexdigest(), digest, path)

    def test_no_personal_or_facility_detail(self) -> None:
        # The registered forecast's deny-list, copied; needles are assembled from fragments.
        forbidden = [a + b for a, b in (("Niza", "miye"), ("NIZA", "MIYE"), ("Tur", "kish"), ("Ugan", "dan "),
                                        ("Niger", "ian national"), ("Congo", "lese"), ("air", "line"),
                                        ("fli", "ght"), ("Abu", "ja"), ("-year", "-old"), ("ye", "ars old"),
                                        ("busi", "ness trip"), ("hus", "band"), ("wi", "fe"))]
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
