# SPDX-License-Identifier: Apache-2.0
import hashlib
import json
import math
import random
import unittest
from pathlib import Path

from lovs import public_repo_hygiene
from lovs.forecast import kenya_escape as ke

REPO = ke.REPO
NEW_FILES = [REPO / "lovs" / "forecast" / "kenya_escape.py", REPO / "lovs" / "forecast" / "kenya_incubation.py",
             REPO / "data" / "case-conditioned" / "kenya-2026-10-17-escape-forecast.json",
             REPO / "data" / "case-conditioned" / "kenya-2026-10-17-incubation.json"]
# File sha256 of the registered 9 and 14 October forecasts and their code, as published.
FROZEN = {
    "data/case-conditioned/kenya-2026-10-09-forecast.json": "d5d2da9fdd5e41e8182c73d610375a641c3276234463cb28133a67400a90adf3",
    "data/case-conditioned/kenya-2026-10-14-forecast.json": "5ea903019eb77e8ad4f269f99f27cadb50577b4245ac49f74f2ad32b79f53c0c",
    "lovs/forecast/kenya_conditioned.py": "ff4d8577912acf04961b587809627d55748db5d00e6757b0966f3c7d27502e43",
    "lovs/forecast/kenya_second_case.py": "08ccc0b55af12e780cfe5c3ac71c733f71aa51e7aedd5b03909c95b0584fdc77",
}
# Terms that must not appear, held as sha256 so that this file does not publish them.
HASHED_TERMS = {
    "ed7608ec94f88d10a7141522ff4588ba0b34d45c17f893ecd38b673c4ba5ce76",
    "b4def8217cadae26d4da633fd2a4e58e326cbb5d570afdc3989484da07af3579",
}
HASHED_TERM_LENGTHS = (6, 8)


def _public_texts() -> dict[str, str]:
    """Every new public file, plus this change's doc addendum and changelog entry."""
    texts = {path.name: path.read_text(encoding="utf-8") for path in NEW_FILES}
    doc = (REPO / "docs" / "international-forecast-validation.md").read_text(encoding="utf-8")
    texts["addendum"] = doc[doc.index("## Addendum: Kenya escape forecast"):]
    changelog = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    start = changelog.index("- **Kenya escape forecast")
    texts["changelog"] = changelog[start:changelog.index("\n## ", start)]
    return texts


def _erlang2(rng: random.Random, mean: float) -> float:
    return -(math.log(1 - rng.random()) + math.log(1 - rng.random())) * mean / 2.0


class TimingTests(unittest.TestCase):
    def test_gamma_sampler_moments(self) -> None:
        rng = random.Random(5)
        xs = [ke._gamma(rng, 3.5, 2.0) for _ in range(40_000)]
        mean = sum(xs) / len(xs)
        var = sum((x - mean) ** 2 for x in xs) / len(xs)
        self.assertAlmostEqual(mean, 1.75, delta=0.02)
        self.assertAlmostEqual(var, 3.5 / 4.0, delta=0.03)

    def test_unlisted_and_release_timing_match_simulation(self) -> None:
        k, mu = 3.0, 9.0
        tm = ke.timing(k, mu)
        rng = random.Random(6)
        n = 60_000
        unl, rel = [], []
        for _ in range(n):
            onset = rng.uniform(*ke.UNLISTED_EXPOSURE) + rng.gammavariate(k, mu / k)
            unl.append(onset + _erlang2(rng, ke.LAG_UNLISTED) + ke.REPORT_DELAY)
            o2 = rng.uniform(*ke.EXPOSURE["hospital"]) + rng.gammavariate(k, mu / k)
            rel.append(o2 + _erlang2(rng, ke.LAG_RELEASED) + ke.REPORT_DELAY if o2 >= ke.RELEASE["hospital"] else math.inf)
        for i in (0, 7, 14, len(ke.POINTS) - 1):
            t = ke.POINTS[i]
            self.assertAlmostEqual(tm["unlisted"][i], sum(v <= t for v in unl) / n, delta=0.01)
            self.assertAlmostEqual(tm["after_release_hospital"][i], sum(v <= t for v in rel) / n, delta=0.01)

    def test_every_timing_cdf_is_a_probability(self) -> None:
        tm = ke.timing(3.0, 7.39)
        for key, values in tm.items():
            if isinstance(values, list):
                self.assertLessEqual(max(values), 1.0 + 1e-9, key)
                self.assertGreaterEqual(min(values), 0.0, key)

    def test_second_generation_timing_matches_simulation(self) -> None:
        k, mu = 4.0, 8.0
        tm = ke.timing(k, mu)
        rng = random.Random(7)
        n = 40_000
        xs = []
        for _ in range(n):
            parent = rng.uniform(*ke.UNLISTED_EXPOSURE) + rng.gammavariate(k, mu / k)
            child = parent + rng.uniform(*ke.PRIORS["second_generation_shift"]) + rng.gammavariate(k, mu / k)
            xs.append(child + _erlang2(rng, ke.LAG_UNLISTED) + ke.REPORT_DELAY)
        for i in (7, 14, len(ke.POINTS) - 1):
            self.assertAlmostEqual(tm["second_generation"][i], sum(v <= ke.POINTS[i] for v in xs) / n, delta=0.01)

    def test_conditional_matches_event_simulation(self) -> None:
        # Two draws, picked at random; Poisson report times placed by each draw's CDF. Keep the
        # runs with no report by the end of 17 October; YES needs a report in the window, which
        # opens with cases confirmed on 18 October.
        rng = random.Random(8)
        draws = ke.run(n=2, seed=3)
        for d in draws:  # scale up so that every time segment carries events
            d["paths"] = {p: [3.0 * v for v in d["paths"][p]] for p in d["paths"]}
        expected = ke.conditional(draws, 0)["p"]
        segments = []
        for d in draws:
            at = [sum(d["paths"][p][i] for p in ke.PATHWAYS) for i in (0, ke.I_OPEN, ke.I_END)]
            segments.append((at[0], at[1] - at[0], at[2] - at[1]))

        def poisson(lam: float) -> int:
            n, prod, u = 0, math.exp(-lam), rng.random()
            cum = prod
            while u > cum:
                n += 1
                prod *= lam / n
                cum += prod
            return n

        kept = yes = 0
        for _ in range(200_000):
            before, _gap, window = segments[rng.random() < 0.5]
            if poisson(before) == 0:
                kept += 1
                yes += poisson(window) > 0
        self.assertAlmostEqual(yes / kept, expected, delta=0.01)
        self.assertGreater(ke.T_OPEN, ke.POINTS[0])


class ForecastTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.artifact = json.loads(ke.DEFAULT_OUT.read_text(encoding="utf-8"))

    def test_curve_is_non_increasing(self) -> None:
        ps = [row["p"] for row in self.artifact["curve"]]
        for a, b in zip(ps, ps[1:]):
            self.assertGreaterEqual(a + 1e-9, b)

    def test_every_registered_row_and_section(self) -> None:
        self.assertEqual([r["variant"] for r in self.artifact["sensitivity"]], [n for n, _, _ in ke.SENSITIVITY])
        self.assertEqual(set(self.artifact["decomposition"]), set(ke.PATHWAYS))
        for key in ("headline", "benchmark", "curve", "secondary", "exposure_gaps", "scoring_plan", "disclosures"):
            self.assertTrue(self.artifact[key], key)
        self.assertEqual(self.artifact["curve"][0]["date"], "2026-10-17")
        self.assertEqual(self.artifact["curve"][-1]["date"], "2026-11-14")

    def test_every_input_group_is_labelled(self) -> None:
        for key in ("incubation", "listed_infections", "release_days", "unlisted_share", "untraced", "detection",
                    "second_generation", "contained_onward", "importation", "clock"):
            self.assertIn(key, self.artifact["inputs"]["provenance"])

    def test_payload_digest_verifies(self) -> None:
        self.assertEqual(self.artifact["payload_sha256"], ke.payload_digest(self.artifact))

    def test_committed_artifact_regenerates_byte_for_byte(self) -> None:
        rebuilt = ke.build_artifact(ke.summarise(ke.run()), ke.sensitivity_rows())
        self.assertEqual(ke.DEFAULT_OUT.read_text(encoding="utf-8"),
                         json.dumps(rebuilt, indent=1, sort_keys=True) + "\n")

    def test_frozen_forecasts_untouched(self) -> None:
        for path, digest in FROZEN.items():
            self.assertEqual(hashlib.sha256((REPO / path).read_bytes()).hexdigest(), digest, path)

    def test_no_identifying_or_private_detail(self) -> None:
        # The needles are assembled from fragments so that this file does not itself carry them.
        forbidden = [a + b for a, b in (("JM", "8523"), ("Nairobi ", "Hospital"), ("Bu", "ta"),
                                        ("Jambo", "jet"), ("Jambo", "Jet"), ("Kenyan ", "citizen"),
                                        ("39", "-year"), ("39 ", "year"), ("a ", "friend"),
                                        ("drove ", "him"), ("Ken", "yatta"), ("Mba", "gathi"),
                                        ("air", "port"), ("by ", "car"), ("emergency ", "unit"),
                                        ("police ", "service"), ("18 ", "hours"),
                                        ("nur", "se"), ("doc", "tor"), ("wi", "fe"), ("husb", "and"),
                                        ("daugh", "ter"), ("bro", "ther"), ("sis", "ter"), ("mo", "ther"),
                                        ("fa", "ther"))]
        for name, text in _public_texts().items():
            for needle in forbidden:
                self.assertNotIn(needle, text, f"{name}: {needle}")
            for n in HASHED_TERM_LENGTHS:
                for i in range(len(text) - n + 1):
                    digest = hashlib.sha256(text[i:i + n].encode("utf-8")).hexdigest()
                    self.assertNotIn(digest, HASHED_TERMS, f"{name}: a hashed term at offset {i}")

    def test_new_files_pass_public_hygiene_and_style(self) -> None:
        for name, text in _public_texts().items():
            self.assertFalse(public_repo_hygiene.contains_marker(text), name)
            self.assertNotIn(chr(0x2014), text, name)


if __name__ == "__main__":
    unittest.main()
