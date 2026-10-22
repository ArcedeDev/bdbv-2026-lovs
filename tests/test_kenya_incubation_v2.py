# SPDX-License-Identifier: Apache-2.0
import hashlib
import json
import random
import re
import unittest

from lovs import public_repo_hygiene
from lovs.forecast import kenya_incubation as v1
from lovs.forecast import kenya_incubation_v2 as v2

# Words and phrases that must not appear in this change's public text (personal detail, sites,
# release timing), held as sha256 of lower-case word n-grams so that this file does not publish them.
HASHED_TERMS = {
    "0b4bd77cec705cf5df016344c0fc673fb3c2240078e6af572b2c5c6fa76114af",
    "0d248e82c62c9386878327d491c762a002152d42ab2c391a31c44d9f62675ddf",
    "11014dc945c958e0cf82c664bea3591dff31a55b6ae110a2f4def63485f84761",
    "1366fac71146793a85e98a88ad9bb15adb9aac31da8ac6d73e67059bd5a467a9",
    "1980d3927f07fec9c8d727f187ddde727da7e45247dbb71c7555e5ae0eca66d5",
    "279e3bdae05dd4764a42ee22a6d9e132a0d82af4aa2a3eb38218131a081f9761",
    "2e3bef1e3aebf76181d547e8a54105894a5b13c7638e7bdbc2cefa85dea430df",
    "2f2d8aaa9283835c392d62e7ee2696dfa52b8d39d51f77c311b41d0b193bb182",
    "3092387e2b595774014dafde7f062fb6893772c9778ef5ab5108777077e582f8",
    "3107ea0301a27894a212be51c76b7c5b94a8700b6ace0024fc023cf1e685558d",
    "39521a62fa5724355bfcdf887982a0a417c5463c74712989312249cc1942d7e3",
    "42e20cbe942249d85888eb781322b8af910f4c3739567be3c382b3cd474fb803",
    "48b676e2b107da679512b793d5fd4cc4329f0c7c17a97cf6e0e3d1005b600b03",
    "496fed8a438bd67572909ba43487c83e52de0ad4743beab560241697c3029bd2",
    "4ed28f82ca96c3342a5c591668270f78c26dae22fd953772a6c14e52f46e1b22",
    "52058438eab4de007623a1eb7d8c6b8e97ce9f8506c76481aaa15e77e3ee73a7",
    "5d97b3e4ad844c6367a353025f92e2a4847df3601eeaec3491059dfec853bc0f",
    "626c5125a64986c001bf9e9618db5d60b3f6891528d9cf2edcf87dc9557cbd99",
    "6c97182b2dcd11f1f79a6af21a4ce48587a100bb0f1fe000eb5af80ea46aa0df",
    "78413fe80539c1cfafaf83af12229074655d59d44abf274381b5264cdfe10ce0",
    "7e8772650980842c4af1da7c2978b099c2db7b34f1e655f9ebc58a52f6773ad9",
    "859f4775eac966adedc149efb19993971934e64a9fb0402312927df04c68d38f",
    "88a1881f0025cf7117501d07aefc5d4be6696656790d765678df8bc48ca52687",
    "89b5fd22024f3dc0bed2f56996a5de989226e3b3270231f3ecf49d8bb7dd1e51",
    "8fd79738dd691b766dd5b2ee87841dbeff5ea68ebab93712f5d0cd13999bf1aa",
    "93e34374c89553fbf9c590d2f3c00271ac5dde3ac34c1a1cadb57785fef15af5",
    "98aa6675482552881925fe70d82559692d811b3bcd33f53b9a702c24d5322696",
    "9d52a3c8e4d6e27de4ad74ec621583de1630551e875f6cb6abfd8157517e7b72",
    "9f165139a8c2894a47aea23b77d330eca847264224a44d5a17b19db8b9a72c08",
    "9fee8f88b93ae8b399f50073fe60d9c137216b0cd1176defafd6580328fa5ec9",
    "a4ac536145d6649da1b86b60f21a352a9e1955e53d4532d7bb53a4e14d0bb4cd",
    "a892fec0a7ca63b4b442f3e126bec5825870993f5188062d019ef70348fb12a3",
    "b8f5ce68b44f0029754340aaa35697e49a266505d9977f9c5ae061d07efba8b8",
    "cbe6181036b5818eb3190b277bdc445b0c1fff31507aae15bf780e8176c9d942",
    "cf0622ef2a661cd2f11b0b644e40e4e00eb962918424a8c8ba53842fdf290235",
    "cf2e5bff1ef03dcd20d1a0b18ef7d89fc80a3554434165753672f6f40fde1d25",
    "d0352ccd05cc990178fc8f52e08c91ee5dea9f149c50582a0f9e2b72f89d6ae8",
    "f1004e926e8eb10e1f6c137e0c5dbeb5f75d310ec2ce165f569dc1b50dd37efd",
    "fb67af4445f110a3e260fe330e64b199b83db6192adc7ff755b709af39ab1379",
}


def _public_texts() -> dict[str, str]:
    """This change's public files, its validation-doc bullet and its changelog entry."""
    texts = {path.name: path.read_text(encoding="utf-8")
             for path in (v2.DEFAULT_OUT, v2.REPO / "lovs" / "forecast" / "kenya_incubation_v2.py",
                          v2.REPO / "tests" / "test_kenya_incubation_v2.py")}
    doc = (v2.REPO / "docs" / "international-forecast-validation.md").read_text(encoding="utf-8")
    start = doc.index("- **Incubation evidence, version 2 (run 2026-10-21).**")
    texts["doc bullet"] = doc[start:doc.index("\n", start)]
    changelog = (v2.REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    start = changelog.index("- **Kenya incubation evidence, version 2, run once.**")
    texts["changelog"] = changelog[start:changelog.index("\n## ", start)]
    return texts



def _v1_inputs_as_v2_cases() -> list[dict]:
    """Version 1's primary observations as version 2 cases with every weight 0."""
    cases = []
    for g in v1.observations():
        for window in g["onsets"]:
            cases.append({"onset_window": window, "source_a_exposure": g["exposure"], "w": 0.0,
                          "source_b": [{"exposure": (10.0, 12.0)}]})
    return cases


class ReductionTests(unittest.TestCase):
    def test_every_weight_zero_reproduces_version_1(self) -> None:
        cases = _v1_inputs_as_v2_cases()
        for combined in (False, True):
            old = v1.posterior(combined=combined)
            new = v2.posterior(cases, v1.T_OBS, combined=combined)
            self.assertEqual([(k, mu) for k, mu, _ in old], [(k, mu) for k, mu, _ in new])
            for (_, _, a), (_, _, b) in zip(old, new):
                self.assertAlmostEqual(a, b, delta=1e-12)

    def test_version_1_files_untouched(self) -> None:
        # The escape tests pin these by hash; version 2 only adds files.
        text = (v2.REPO / "data" / "case-conditioned" / "kenya-2026-10-17-incubation.json").read_text(encoding="utf-8")
        self.assertEqual(json.loads(text)["payload_sha256"],
                         "fa3cbf4aef33f5b3ae8d249faa39a5d6f2a2df3f7c45db4b957bd811bcc794d5")


class InputRuleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cases = {c["case"]: c for c in v2.build_cases("main")}

    def test_truncation_is_the_freeze(self) -> None:
        self.assertEqual(v2.FREEZE_UTC.isoformat(), "2026-10-21T15:20:16+00:00")
        self.assertAlmostEqual(v2.T_FREEZE, 18.0 + (15 + 3 + 20 / 60 + 16 / 3600) / 24, places=12)

    def test_windows_follow_version_1_convention(self) -> None:
        self.assertEqual(self.cases[2]["onset_window"], v1.ONSET_CASE2)
        self.assertEqual(self.cases[3]["onset_window"], v1.ONSET_16OCT)
        for case in (4, 5, 6, 7):
            self.assertEqual(self.cases[case]["onset_window"], v1.ONSET_17OCT)
        self.assertEqual(self.cases[8]["onset_window"], (13.0, 16.0))
        self.assertEqual(self.cases[9]["onset_window"], (14.0, 17.0))

    def test_weights_and_sources_follow_the_rules(self) -> None:
        self.assertEqual({c: r["w"] for c, r in self.cases.items()},
                         {2: 0.5, 3: 0.5, 4: 0.5, 5: 0.0, 6: 0.0, 7: 0.0, 8: 0.0, 9: 0.5})
        sources = {c: [(b["case"], b["exposure"]) for b in r["source_b"]] for c, r in self.cases.items()}
        self.assertEqual(sources[2], [])
        self.assertEqual(sources[3], [(2, (10.0, 12.0))])
        self.assertEqual(sources[4], [(2, (10.0, 12.0)), (3, (12.0, 14.0))])
        self.assertEqual(sources[9], [(2, (10.0, 12.0)), (3, (12.0, 14.0)), (4, (13.0, 15.0))])
        for c in (5, 6, 7):
            self.assertEqual(sources[c], [])
        self.assertEqual(sources[8], [(5, (13.0, 15.0)), (6, (13.0, 15.0)), (7, (13.0, 15.0))])
        for c in (2, 3, 4, 9):
            self.assertEqual(self.cases[c]["source_a_exposure"], v1.RELATIVES_EXPOSURE)
        for c in (5, 6, 7, 8):
            self.assertEqual(self.cases[c]["source_a_exposure"], v1.HOSP_EXPOSURE)

    def test_sensitivity_weights(self) -> None:
        self.assertTrue(all(c["w"] == 0.0 for c in v2.build_cases("all_w0")))
        self.assertTrue(all(c["w"] == 1.0 for c in v2.build_cases("all_w1")))
        check = {c["case"]: c for c in v2.build_cases("check_case8_exposure_to_death")}
        self.assertEqual(check[8]["source_a_exposure"], (v1.HOSP_EXPOSURE[0], v1.RELATIVES_EXPOSURE[1]))
        self.assertEqual(check[8]["w"], 0.0)

    def test_no_candidate_means_source_a_only(self) -> None:
        case = dict(self.cases[2], w=1.0)
        la = v2._conditional_window_probability(3.0, 9.0, case["source_a_exposure"], case["onset_window"], v2.T_FREEZE)
        self.assertEqual(v2.case_likelihood(3.0, 9.0, case, v2.T_FREEZE), la)

    def test_impossible_window_is_minus_infinity_not_an_error(self) -> None:
        case = {"onset_window": (0.0, 0.1), "source_a_exposure": (5.0, 6.0), "w": 1.0,
                "source_b": [{"exposure": (7.0, 8.0)}]}
        self.assertEqual(v2.log_likelihood(3.0, 9.0, [case], v2.T_FREEZE), float("-inf"))


class MixtureSimulationTests(unittest.TestCase):
    def test_case_likelihood_matches_simulation(self) -> None:
        # Draw the source (B with probability w, then a candidate uniformly), the exposure and the
        # incubation; keep onsets by T. The share in the window, divided by the share of draws whose
        # source is that one, recovers each L_X, and the mixture recombines them.
        rng = random.Random(21)
        case = {c["case"]: c for c in v2.build_cases("main")}[9]
        t_obs = v2.T_FREEZE
        for k, mu in ((3.0, 7.39), (2.0, 12.0)):
            lo, hi = case["onset_window"]
            sources = [case["source_a_exposure"]] + [b["exposure"] for b in case["source_b"]]
            estimates = []
            for exposure in sources:
                seen = [o for o in (rng.uniform(*exposure) + rng.gammavariate(k, mu / k) for _ in range(80_000))
                        if o <= t_obs]
                estimates.append(sum(lo < o <= hi for o in seen) / len(seen))
            lb = sum(estimates[1:]) / len(estimates[1:])
            expected = (1 - case["w"]) * estimates[0] + case["w"] * lb
            self.assertAlmostEqual(v2.case_likelihood(k, mu, case, t_obs), expected, delta=0.01)


class MixtureArithmeticTests(unittest.TestCase):
    def test_case_likelihood_is_the_stated_mixture(self) -> None:
        case = {c["case"]: c for c in v2.build_cases("main")}[9]
        t = v2.T_FREEZE
        for k, mu in ((2.0, 6.0), (4.0, 11.0)):
            la = v2._conditional_window_probability(k, mu, case["source_a_exposure"], case["onset_window"], t)
            lbs = [v2._conditional_window_probability(k, mu, b["exposure"], case["onset_window"], t)
                   for b in case["source_b"]]
            self.assertAlmostEqual(v2.case_likelihood(k, mu, case, t), 0.5 * la + 0.5 * sum(lbs) / 3, places=14)


class ArtifactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.artifact = v2.build_artifact()

    def test_committed_artifact_regenerates_byte_for_byte(self) -> None:
        self.assertEqual(v2.DEFAULT_OUT.read_text(encoding="utf-8"),
                         json.dumps(self.artifact, indent=1, sort_keys=True) + "\n")

    def test_payload_digest_verifies(self) -> None:
        self.assertEqual(self.artifact["payload_sha256"], v1.payload_digest(self.artifact))

    def test_rows_and_outputs(self) -> None:
        rows = {r["variant"]: r for r in self.artifact["rows"]}
        self.assertEqual(list(rows), [name for name, _, _ in v2.ROWS])
        self.assertEqual([n for n, r in rows.items() if not r["registered"]], ["check_case8_exposure_to_death"])
        for row in rows.values():
            for block in (row["kenya_only"], row["combined"]):
                for key in ("posterior_mean_days", "predictive_p_over_21_days", "mean_days_10_50_90"):
                    self.assertIn(key, block)

    def test_posteriors_are_normalised(self) -> None:
        cases = v2.build_cases("main")
        for combined in (False, True):
            self.assertAlmostEqual(sum(w for _, _, w in v2.posterior(cases, v2.T_FREEZE, combined)), 1.0, places=9)

    def test_reading_follows_the_pre_stated_rule(self) -> None:
        r = self.artifact["reading"]
        rows = {row["variant"]: row for row in self.artifact["rows"]}
        self.assertEqual(r["main_kenya_only_mean_days"], rows["main"]["kenya_only"]["posterior_mean_days"])
        self.assertEqual(r["w1_kenya_only_mean_days"], rows["all_w1"]["kenya_only"]["posterior_mean_days"])
        self.assertEqual(v2.reading(9.99, 12.0)["branch"], "below_10")
        self.assertEqual(v2.reading(10.0, 10.0)["branch"], "10_or_more_main_and_w1")
        self.assertEqual(v2.reading(11.0, 9.0)["branch"], "10_or_more_main_below_10_w1")
        self.assertEqual(r["branch"], v2.reading(r["main_kenya_only_mean_days"], r["w1_kenya_only_mean_days"])["branch"])

    def test_registration_hashes(self) -> None:
        self.assertEqual(self.artifact["registration"]["plan_sha256"],
                         "3cb955643151c3c913f3ad3c22bcf1e7cd839c6f95a30f1079cb9404666f1f91")
        self.assertEqual(self.artifact["registration"]["interpretation_sha256"],
                         "f036bf10f5aba69b6d6944dbf8cf130315b4ef2aab7f764fe32cf33ae153a064")

    def test_validation_doc_cites_the_payload(self) -> None:
        doc = (v2.REPO / "docs" / "international-forecast-validation.md").read_text(encoding="utf-8")
        self.assertIn(self.artifact["payload_sha256"], doc)

    def test_public_text_is_privacy_safe(self) -> None:
        for name, text in _public_texts().items():
            self.assertFalse(public_repo_hygiene.contains_marker(text), name)
            self.assertNotIn(chr(0x2014), text, name)
            self.assertIsNone(re.search(r"\b\d{1,3}[- ]years?\b", text), name)
            words = re.findall(r"[a-z0-9]+", text.lower())
            for n in (1, 2, 3):
                for i in range(len(words) - n + 1):
                    digest = hashlib.sha256(" ".join(words[i:i + n]).encode("utf-8")).hexdigest()
                    self.assertNotIn(digest, HASHED_TERMS, f"{name}: a hashed term at word {i}")

if __name__ == "__main__":
    unittest.main()
