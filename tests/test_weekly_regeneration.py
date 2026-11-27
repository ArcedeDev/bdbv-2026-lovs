# SPDX-License-Identifier: Apache-2.0
"""Weekly loop, test 1: a week regenerates byte for byte from its pinned inputs.

The fixture week is built twice and regenerated from its own public archives; every file
and every trajectory digest must agree. A registered week in data/weekly-forecasts/ is
regenerated the same way when its private half is available locally
(LOVS_WEEKLY_PRIVATE_ROOT), and its regenerated forecasts must hash to its commitment.
"""
from __future__ import annotations

import bisect
import hashlib
import json
import os
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from lovs.forecast.weekly import register, spec
from tests.weekly_fixtures import fixture_week

REPO = Path(__file__).resolve().parents[1]


class RegenerationTest(unittest.TestCase):
    def test_two_builds_are_byte_identical(self) -> None:
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            _, pa, qa, _ = fixture_week(Path(a))
            _, pb, qb, _ = fixture_week(Path(b))
            for name in register.PUBLIC_FILES:
                self.assertEqual((pa / name).read_bytes(), (pb / name).read_bytes(), name)
            self.assertEqual((qa / "forecasts.json").read_bytes(), (qb / "forecasts.json").read_bytes())

    def test_regenerates_from_the_public_archive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            build, public, private, _ = fixture_week(Path(tmp))
            regenerated = register.regenerate(public, private / "forecasts.json", store_root=public.parent)
            self.assertEqual(regenerated, (private / "forecasts.json").read_bytes())
            commitment = json.loads((public / "commitment.json").read_text(encoding="utf-8"))
            self.assertEqual(hashlib.sha256(regenerated).hexdigest(), commitment["forecasts_sha256"])

    def test_digests_cover_the_first_100_trajectories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            build, *_ = fixture_week(Path(tmp))
        for f in build.forecasts["forecasts"]:
            self.assertRegex(f["trajectory_sha256"], r"^[0-9a-f]{64}$")
            self.assertEqual(f["trajectories_hashed"], min(100, build.forecasts["n_paths"]))

    def test_the_pit_draws_are_the_registered_draws(self) -> None:
        # Every stored quantile is the order statistic the CDF of the rebuilt draws puts there.
        with tempfile.TemporaryDirectory() as tmp:
            build, public, private, _ = fixture_week(Path(tmp))
            cdfs, unscored = register.verified_cdfs(public, private / "forecasts.json", store_root=public.parent)
        self.assertIsNone(unscored)
        self.assertEqual(set(cdfs), {f["forecast_id"] for f in build.forecasts["forecasts"]})
        for f in build.forecasts["forecasts"]:
            cdf = cdfs[f["forecast_id"]]
            for level, value in f["quantiles"]:
                index = min(cdf.n - 1, max(0, int(round(level * (cdf.n - 1)))))
                self.assertEqual(cdf.values[bisect.bisect_right(cdf.at_or_below, index)], value, f["forecast_id"])

    def test_private_forecasts_that_differ_from_the_commitment_stop_the_build(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, public, private, _ = fixture_week(Path(tmp))
            doc = json.loads((private / "forecasts.json").read_text(encoding="utf-8"))
            doc["forecasts"][0]["quantiles"][11][1] += 1.0
            tampered = Path(tmp) / "tampered.json"
            tampered.write_bytes(register.forecasts_bytes(doc))
            with self.assertRaises(register.RegistrationError):
                register.verified_cdfs(public, tampered, store_root=public.parent)
            (public / "commitment.json").unlink()
            with self.assertRaises(register.RegistrationError):
                register.verified_cdfs(public, private / "forecasts.json", store_root=public.parent)

    def test_a_week_that_does_not_rebuild_is_scored_without_pits(self) -> None:
        # The private half matches its commitment, but the public manifest no longer rebuilds it.
        with tempfile.TemporaryDirectory() as tmp:
            _, public, private, _ = fixture_week(Path(tmp))
            manifest = json.loads((public / "manifest.json").read_text(encoding="utf-8"))
            manifest["n_paths"] -= 1
            (public / "manifest.json").write_bytes(register.json_bytes(manifest))
            got = register.verified_cdfs(public, private / "forecasts.json", store_root=public.parent)
        self.assertEqual(got, (None, register.PIT_DOES_NOT_REBUILD))

    def test_a_candidate_added_later_keeps_the_pits(self) -> None:
        # A candidate changes the full spec digest that forecasts.json records, never the draws.
        candidate = {"id": "cand.example_method", "classes": ["flow"], "priced_beside": "the frozen set"}
        with tempfile.TemporaryDirectory() as tmp:
            build, public, private, _ = fixture_week(Path(tmp))
            with mock.patch.object(spec, "CANDIDATES", (candidate,)):
                cdfs, unscored = register.verified_cdfs(public, private / "forecasts.json",
                                                        store_root=public.parent)
        self.assertIsNone(unscored)
        self.assertEqual(set(cdfs), {f["forecast_id"] for f in build.forecasts["forecasts"]})

    def test_a_week_from_another_spec_is_not_rebuilt_and_has_no_pits(self) -> None:
        # A later revision renames the method ids, so the week's seeds cannot even be looked up:
        # the week is never rebuilt, whether the version or only the method ids differ.
        renamed = tuple(m.replace(spec.SPEC_VERSION, "wk9") for m in spec.METHOD_IDS)
        with tempfile.TemporaryDirectory() as tmp:
            _, public, private, _ = fixture_week(Path(tmp))
            for patch in (mock.patch.object(spec, "SPEC_VERSION", "wk9"), mock.patch.object(spec, "METHOD_IDS", renamed)):
                with patch, mock.patch.object(register, "regenerate_build") as rebuild:
                    got = register.verified_cdfs(public, private / "forecasts.json", store_root=public.parent)
                    rebuild.assert_not_called()
                self.assertEqual(got, (None, register.PIT_EARLIER_SPEC))

    def test_registered_weeks(self) -> None:
        store = REPO / "data" / "weekly-forecasts"
        private_root = os.environ.get("LOVS_WEEKLY_PRIVATE_ROOT")
        weeks = sorted(p for p in store.iterdir() if p.is_dir() and p.name[:2] == "20") if store.is_dir() else []
        if not weeks or not private_root:
            self.skipTest("no registered week with its private half available")
        for week in weeks:
            private = Path(private_root) / week.name / "private" / "forecasts.json"
            if not private.is_file():
                continue
            regenerated = register.regenerate(week, private, store_root=store)
            commitment = json.loads((week / "commitment.json").read_text(encoding="utf-8"))
            self.assertEqual(hashlib.sha256(regenerated).hexdigest(), commitment["forecasts_sha256"], week.name)


if __name__ == "__main__":
    unittest.main()
