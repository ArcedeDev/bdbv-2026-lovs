# SPDX-License-Identifier: Apache-2.0
"""Weekly loop, test 1: a week regenerates byte for byte from its pinned inputs.

The fixture week is built twice and regenerated from its own public archives; every file
and every trajectory digest must agree. A registered week in data/weekly-forecasts/ is
regenerated the same way when its private half is available locally
(LOVS_WEEKLY_PRIVATE_ROOT), and its regenerated forecasts must hash to its commitment.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from lovs.forecast.weekly import register
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
