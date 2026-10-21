# SPDX-License-Identifier: Apache-2.0
"""Nigeria v4: the registered constants, the infectiousness shapes, exposure and infection, conditioning, curve,
digests, the frozen earlier records and public text."""
from __future__ import annotations

import bisect
import hashlib
import json
import math
import random
import re
import unittest

from lovs import public_repo_hygiene
from lovs.forecast import kenya_conditioned as kc
from lovs.forecast import nigeria_nowcast as nn
from lovs.forecast import nigeria_v4 as v

REPO = v.REPO
ARTIFACT = v.DEFAULT_OUT
MODULE = REPO / "lovs" / "forecast" / "nigeria_v4.py"
THIS_FILE = REPO / "tests" / "test_nigeria_v4.py"
DOC_HEADING = "### Nigeria v4"
CHANGELOG_BULLET = "- **Nigeria v4"
V4_RESULTS_SHA256: str | None = "cf2106f327c444c79df72ed59c598965dbd45758e291ee3ca1c1a55ecae22d47"
V4_FIRST_RUN_PAYLOAD_SHA256: str | None = "0feee1a57924a8a44abc1322480a7bf25b5ccc401a3ff216c50a121f6e5e39a3"

FROZEN = {   # sha256 of the v1 to v3 modules and artifacts as merged; v4 must never change them
    "lovs/forecast/nigeria_conditioned.py": "4e9ad34eccc5913d5740b67df92db90bbed418770858344ce915518b89d71ae8",
    "lovs/forecast/nigeria_nowcast.py": "042cb28466458096514056dacf6bc37af15491bf64667f4deb24e1c39ff58b1a",
    "lovs/forecast/nigeria_v2.py": "c1dae3c5e230bf466f8256e17c45405af1b492ca46c6b2e0de2fd08e8397609c",
    "data/case-conditioned/nigeria-2026-10-20-forecast.json": "14da3c435e07d1a0da08b28be6c3b947adb8c1988a5290859a0ac98d58dbf9db",
    "data/case-conditioned/nigeria-2026-10-20-nowcast.json": "cca6f13a6040ebd9b02114c4e8fec59f9086a5543faa2e70c41577c0bd443890",
    "data/case-conditioned/nigeria-2026-10-21-v2.json": "2b0195fc9980f65e37b16b88f7ffd244a7f394acd088d439fbba32fa899dfb33",
    "data/case-conditioned/nigeria-2026-10-21-v3.json": "29f1cd7378de7caf4e11df3a052838d69801117b2ebf19c2fc36cccabd694268",
}


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


def _params(seed: int = 7, **overrides) -> dict:
    opts = v.options(overrides)
    return v.draw_parameters(random.Random(seed), opts, v.profiles(opts))


class InputTest(unittest.TestCase):
    def test_registered_inputs(self) -> None:
        self.assertEqual(v.MODEL_VERSION, "nigeria_v4-v1.0.0")
        self.assertEqual((v.SEED, v.N_DRAWS, v.N_DRAWS_SENSITIVITY, v.RUNS_PER_DRAW), (2026102104, 40_000, 20_000, 4))
        self.assertEqual(v.REGISTRATION["rules_v4_sha256"],
                         "82ffd6424f9f2d2f6a05b5eead54ce8aa950ff529924b991f38c90213e3e8a62")
        self.assertEqual((v.T_WINDOW_OPEN, v.T_CONFIRM_END, v.T_REPORT_END, v.T_LIST), (14.0, 42.0, 45.0, 14.0))
        self.assertEqual((v.P_ADMIT_DAY4, v.ADMIT_DAY4, v.ADMIT_LATE, v.T_END), (0.75, 4.0, (5.0, 9.0), (12.5, 14.0)))
        self.assertEqual((v.P_DEATH, v.DEATH, v.TERMINAL_DAYS, v.N_TERMINAL), ((0.03, 0.20), (14.0, 24.0), 2.43, (5, 20)))
        self.assertEqual((v.K_IND, v.R_SURVIVOR, v.R_FATAL, v.READING), ((0.4, 1.0), (0.07, 0.47), (0.3, 1.0), "time_point"))
        self.assertEqual((v.LISTED_TOTAL, v.E_TRAVEL, v.B_CAREGIVERS, v.U_HCW_SHARE), (44, (5, 20), (1, 4), (0.4, 0.9)))
        self.assertEqual((v.V_UNLISTED, v.W_UNLISTED_STAFF), ((0.5, 2.0), (0.3, 1.0)))
        self.assertEqual(v.PRESENCE, {"A": (0.6, 1.0), "B_home": (0.5, 1.0), "B_facility": (0.5, 1.0), "C": (0.5, 1.0),
                                      "NH": (0.1, 0.4), "NO": (0.5, 1.0)})
        self.assertEqual((v.P_BRANCH_K, v.LAMBDA_A_K, v.LAMBDA_B), (0.5, (3.5, 51.03), (0.05, 0.25)))
        self.assertEqual(v.LAMBDA_A_U, {"c": (2, 3, 4), "days": 3.0, "h_u": (60.0, 100.0)})
        self.assertEqual(v.LAMBDA_C, {"k": (0, 1), "days": 2.43, "h_c": (40.0, 79.0)})
        self.assertEqual((v.LAG_LISTED_MEAN, v.LAG_UNLISTED_MEAN, v.P_DETECT_UNLISTED), (1.0, (3.0, 7.0), (0.85, 1.0)))
        self.assertEqual((v.R_SECOND, v.UNDETECTED_SPAN, v.Q_REPORTED_BY_CHECK), ((0.1, 0.8), 10.0, (0.1, 0.4)))
        self.assertEqual(v.BACKGROUND, {"imports_per_week": (0.01, 0.06), "p_local": (0.05, 0.30), "delay_shape": 10,
                                        "delay_mean": 16.0, "imports_from": 13.0})

    def test_sensitivity_list_is_the_registered_twenty_seven(self) -> None:
        self.assertEqual(len(v.SENSITIVITY), 27)
        self.assertEqual(v.SENSITIVITY[-1], ("baseline", "Baseline at the sensitivity sample size", {}))
        for _, _, over in v.SENSITIVITY:
            v.options(over)
        with self.assertRaises(KeyError):
            v.options({"bogus": 1})

    def test_hierarchical_mean(self) -> None:
        k = v._digamma(3.5) - math.log(51.03)
        u = v._digamma(3.5) - math.log(240.0)
        self.assertAlmostEqual(v.hierarchical_mean_log_lambda_a(), 0.5 * (k + u), places=12)
        self.assertAlmostEqual(v._digamma(1.0), -0.5772156649015329, places=10)
        self.assertAlmostEqual(v._digamma(3.5), 1.1031566406452432, places=10)


class ShapeTest(unittest.TestCase):
    def test_time_point_density_is_the_derivative_of_knots(self) -> None:
        for table in (nn.F_SURVIVOR, nn.F_FATAL):
            for reading in ("time_point", "day_of_illness"):
                prof = v.shape_pieces(table, reading)
                kn = nn.knots(table, reading)
                scale = max(table) / sum(table)
                for t in [x / 4 for x in range(0, 64)]:
                    self.assertAlmostEqual(v.integral(prof, 0.0, t) * scale, nn.cdf(kn, t), places=12,
                                           msg=f"{reading} t={t}")

    def test_point_mass_counts_only_from_zero(self) -> None:
        prof = v.shape_pieces(nn.F_SURVIVOR)
        top = max(nn.F_SURVIVOR)
        self.assertAlmostEqual(v.integral(prof, 0.0, 0.0 + 1e-12), nn.F_SURVIVOR[0] / top, places=9)
        self.assertEqual(v.integral(prof, 1e-9, 1e-9), 0.0)
        self.assertAlmostEqual(v.integral(prof, 4.0, 12.5),
                               (sum(nn.F_SURVIVOR[5:13]) + 0.5 * nn.F_SURVIVOR[13]) / top, places=12)

    def test_fatal_shape_held_from_day_8(self) -> None:
        held = v.held_after_peak(v.shape_pieces(nn.F_FATAL))
        top = max(nn.F_FATAL)
        for t0, t1, d in held[1]:
            want = 1.0 if t0 >= 8.0 else nn.F_FATAL[int(t1)] / top
            self.assertAlmostEqual(d, want, places=12, msg=(t0, t1))
        self.assertAlmostEqual(v.integral(held, 8.0, 14.0), 6.0, places=12)

    def test_flat_profile(self) -> None:
        flat = v.flat_pieces()
        self.assertAlmostEqual(v.integral(flat, 0.0, 14.0), 14.0 * (1 / 15) / max(nn.F_SURVIVOR), places=12)
        self.assertEqual(flat[0], 0.0)

    def test_sample_time_follows_the_shape(self) -> None:
        prof = v.shape_pieces(nn.F_SURVIVOR)
        rng = random.Random(3)
        xs = [v.sample_time(rng, prof, 4.0, 13.0) for _ in range(20_000)]
        self.assertTrue(all(4.0 <= x <= 13.0 for x in xs))
        share = sum(x <= 7.0 for x in xs) / len(xs)
        self.assertAlmostEqual(share, v.integral(prof, 4.0, 7.0) / v.integral(prof, 4.0, 13.0), delta=0.015)


class ContactTest(unittest.TestCase):
    def test_exposure_integral_hand_case(self) -> None:
        # A caregiver at home on [0, 4) at 0.8 and at the facility on [4, 13] at 0.6.
        prof = v.shape_pieces(nn.F_SURVIVOR)
        top = max(nn.F_SURVIVOR)
        home = (nn.F_SURVIVOR[0] + sum(nn.F_SURVIVOR[1:5])) / top
        fac = sum(nn.F_SURVIVOR[5:14]) / top
        self.assertAlmostEqual(0.8 * v.integral(prof, 0.0, 4.0) + 0.6 * v.integral(prof, 4.0, 13.0),
                               0.8 * home + 0.6 * fac, places=12)

    def test_group_sizes_and_zero_travel_hazard(self) -> None:
        for seed in range(40):
            p = _params(seed)
            z = p["sizes"]
            self.assertEqual(z["E"] + z["A"] + z["B"] + z["C"], 44)
            self.assertTrue(5 <= z["E"] <= 20 and 1 <= z["B"] <= 4 and z["C"] >= 0 and 5 <= z["I"] <= 20)
            self.assertLessEqual(z["NH"], round(1.0 * z["A"]) + 1)
            counts = {g: sum(1 for c in p["contacts"] if c[0] == g) for g in ("A", "B", "C", "NH", "NO")}
            self.assertEqual(counts, {g: z[g] for g in counts})
            self.assertEqual(len(p["contacts"]), 44 - z["E"] + z["NH"] + z["NO"])   # travel contacts carry nothing
            self.assertEqual({c[1] for c in p["contacts"] if c[0] in ("A", "B", "C")}, {True})
            self.assertEqual({c[1] for c in p["contacts"] if c[0] in ("NH", "NO")} - {False}, set())

    def test_infection_probability_is_one_minus_exp(self) -> None:
        p = _params(11)
        surv, fatal = p["shapes"]
        for group, _, segs, p_s, p_f in p["contacts"]:
            xs = sum(w * v.integral(surv, a, b) for a, b, w in segs)
            xf = sum(w * v.integral(fatal, a, b) for a, b, w in segs)
            self.assertAlmostEqual(p_s, 1 - math.exp(-p["lam"][group] * p["psi"] * p["r"] * xs), places=12)
            self.assertAlmostEqual(p_f, 1 - math.exp(-p["lam"][group] * p["psi"] * p["r_fatal"] * xf), places=12)

    def test_segments_by_group(self) -> None:
        p = _params(5)
        s, t = p["s"], p["t"]
        for group, _, segs, *_ in p["contacts"]:
            spans = [(a, b) for a, b, _ in segs]
            want = {"A": [(s, t)], "B": [(0.0, s), (s, t)], "C": [(s, t)], "NH": [(s, t)], "NO": [(0.0, t)]}[group]
            self.assertEqual(spans, want)
            lo, hi = {"A": (0.6, 1.0), "B": (0.5, 1.0), "C": (0.5, 1.0), "NH": (0.1, 0.4), "NO": (0.5, 1.0)}[group]
            self.assertTrue(all(lo <= w <= hi for _, _, w in segs))

    def test_hazard_groups(self) -> None:
        lam = _params(9)["lam"]
        self.assertEqual((lam["NH"], lam["I"], lam["NO"]), (lam["A"], lam["A"], lam["C"]))


class RunTest(unittest.TestCase):
    def test_deterministic(self) -> None:
        a = v.run(n=60, seed=5, full=False)
        b = v.run(n=60, seed=5, full=False)
        self.assertEqual(a, b)

    def test_part_one_conditioning_never_binds_for_linked_cases(self) -> None:
        rng = random.Random(21)
        opts = v.options()
        shapes = v.profiles(opts)
        for _ in range(150):
            p = v.draw_parameters(rng, opts, shapes)
            for _ in range(4):
                r = v.simulate_run(rng, p)
                confs = [c for *_, c in r["first"]] + [c for *_, c in r["second"]]
                self.assertTrue(all(c > v.T_LIST for c in confs))
                # Only a background confirmation by day 13.5 or the registration-check exclusion can drop a run.
                early_bg = any(c <= v.COND_CUTOFF for c in r["local"])
                self.assertEqual(v.record(r)["kept"], not r["excluded"] and not early_bg)

    def test_e10_exclusion(self) -> None:
        def share_excluded(q: tuple[float, float]) -> tuple[float, int]:
            rng = random.Random(8)
            opts = v.options({"q": q, "branch_k_weight": 0.0})
            shapes = v.profiles(opts)
            excl = early = 0
            for _ in range(300):
                p = v.draw_parameters(rng, opts, shapes)
                r = v.simulate_run(rng, p)
                has_early = any(g in ("A", "B", "C") and onset < v.T_LIST for g, _, onset, _ in r["first"])
                early += has_early
                excl += r["excluded"]
                if not has_early:
                    self.assertFalse(r["excluded"])
                elif q == (1.0, 1.0):
                    self.assertTrue(r["excluded"])
            return excl, early
        self.assertEqual(share_excluded((0.0, 0.0))[0], 0)
        excl, early = share_excluded((1.0, 1.0))
        self.assertGreater(early, 0)
        self.assertEqual(excl, early)

    def test_terminal_staff_only_after_a_death(self) -> None:
        rng = random.Random(13)
        opts = v.options({"p_death": (0.5, 0.5)})
        shapes = v.profiles(opts)
        for _ in range(200):
            p = v.draw_parameters(rng, opts, shapes)
            r = v.simulate_run(rng, p)
            for g, t_inf, _, _ in r["first"]:
                if g == "I":
                    self.assertTrue(r["fatal"])
                    self.assertTrue(max(14.0, r["death"] - 2.43) <= t_inf <= r["death"])

    def test_curve_rule(self) -> None:
        res = v.run(n=150, seed=3)
        ps = [row["p"] for row in res["curve"]]
        self.assertEqual(len(ps), 28)
        self.assertTrue(all(b <= a + 1e-12 for a, b in zip(ps, ps[1:])))
        # The module's rule on a hand list. End of 21 October is day 15.0; a report half a day after confirmation.
        firsts = [14.2, 14.6, 30.0, 42.0, 42.1, 50.0, math.inf]
        rows = {r["date"]: r["p"] for r in v.curve(firsts)}
        self.assertAlmostEqual(rows["2026-10-21"], 1 - 3 / 6)    # 14.6 and later are alive; 42.1, 50, inf are beyond
        self.assertAlmostEqual(rows["2026-11-17"], 1 - 3 / 4)    # day 42.0 confirmations still count
        self.assertEqual(rows["2026-11-17"], 1 - 3 / (len(firsts) - bisect.bisect_right(sorted(firsts), 41.5)))

    def test_semi_analytic_pieces(self) -> None:
        self.assertAlmostEqual(v._gamma_cdf(3.0, 0.406, 26.0),
                               1 - math.exp(-0.406 * 26) * (1 + 0.406 * 26 + (0.406 * 26) ** 2 / 2), places=12)
        self.assertAlmostEqual(v._erlang_cdf(3, 0.406, 26.0), v._gamma_cdf(3.0, 0.406, 26.0), places=12)
        p = _params(2)
        self.assertTrue(0.0 < v.draw_probability(p) < 1.0)
        self.assertGreater(v.background_mean_in_window(1.0), 0.0)

    def test_implied_k_inverts(self) -> None:
        for m, k in ((1.0, 0.4), (2.5, 1.3), (0.3, 0.1)):
            p = 1 - (1 + m / k) ** (-k)
            self.assertAlmostEqual(v.implied_k(m, p), k, places=6)

    def test_rows_run(self) -> None:
        for key, _, over in v.SENSITIVITY:
            res = v.run(over, n=12, seed=4, full=False)
            self.assertTrue(0.0 <= res["headline"] <= 1.0, key)


class FrozenRecordsTest(unittest.TestCase):
    def test_v1_to_v3_are_byte_identical(self) -> None:
        for rel, sha in FROZEN.items():
            self.assertEqual(hashlib.sha256((REPO / rel).read_bytes()).hexdigest(), sha, rel)


@unittest.skipUnless(ARTIFACT.exists(), "v4 artifact not yet built")
class ArtifactTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = ARTIFACT.read_text(encoding="utf-8")
        cls.artifact = json.loads(cls.text)

    def test_digests_verify(self) -> None:
        self.assertEqual(self.artifact["payload_sha256"], kc.payload_digest(self.artifact))
        self.assertEqual(self.artifact["results_sha256"], v.results_digest(self.artifact["results"]))
        self.assertEqual(self.artifact["model_version"], "nigeria_v4-v1.0.0")
        self.assertEqual(self.artifact["method"]["seed"], 2026102104)
        self.assertEqual(self.artifact["registration"]["rules_v4_sha256"], v.REGISTRATION["rules_v4_sha256"])
        entry = self.artifact["registration"].get("entry_7_first_run")
        if entry is not None:
            self.assertEqual(entry["results_sha256"], self.artifact["results_sha256"])

    @unittest.skipIf(V4_RESULTS_SHA256 is None, "first run not yet registered")
    def test_results_are_the_precommitted_ones(self) -> None:
        self.assertEqual(self.artifact["results_sha256"], V4_RESULTS_SHA256)
        self.assertEqual(self.artifact["registration"]["entry_7_first_run"]["payload_sha256"],
                         V4_FIRST_RUN_PAYLOAD_SHA256)

    def test_required_sections(self) -> None:
        res = self.artifact["results"]
        for key in ("headline", "epistemic_interval", "S1_curve", "S2_count_tiers", "S3_by_group", "early_share",
                    "per_contact", "implied_k", "diagnostics", "sensitivity", "first_generation_unconditioned"):
            self.assertIn(key, res)
        self.assertEqual([r["row"] for r in res["sensitivity"]], [r[0] for r in v.SENSITIVITY])
        self.assertEqual(self.artifact["headline"]["probability"], res["headline"]["probability"])

    def test_committed_artifact_regenerates(self) -> None:
        fresh = v.build_artifact(v.run({"diagnostics": True}), self.artifact["results"]["sensitivity"])
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
                                        ("after ", "arrival"), ("local ", "case"))]
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
