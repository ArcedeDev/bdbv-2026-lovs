# SPDX-License-Identifier: Apache-2.0
"""Weekly loop, test 9: the compliant form while D5 is open.

Public per week: manifest.json, pins.csv, commitment.json and the numeric archives. No
public file holds a quantile, a mean or a trajectory; the commitment is the sha256 of the
private forecasts file; LIMITATIONS.md line 10 is unchanged.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from lovs.forecast.weekly import register, spec
from tests.weekly_fixtures import fixture_week

REPO = Path(__file__).resolve().parents[1]
LINE_10_SHA256 = "97dd49c7aa23bcfdfd03e647333eb11d6a2ab0537b1a46c56a981f43a348d9a1"
FORBIDDEN_KEYS = {"quantile", "quantiles", "mean", "means", "trajectory", "trajectories", "samples",
                  "paths", "seed", "seeds", "digest", "trajectory_digest"}


def _keys(obj, out: set[str]) -> set[str]:
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.add(str(k))
            _keys(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _keys(v, out)
    return out


def check_public_week(test: unittest.TestCase, public: Path) -> None:
    names = {p.name for p in public.iterdir()}
    test.assertEqual(names, set(register.PUBLIC_FILES), public)
    for name in names:
        text = (public / name).read_text(encoding="utf-8")
        if name.endswith(".json"):
            test.assertFalse(FORBIDDEN_KEYS & _keys(json.loads(text), set()), name)
        elif name.endswith(".jsonl"):
            for line in text.splitlines():
                test.assertFalse(FORBIDDEN_KEYS & _keys(json.loads(line), set()), name)
        elif name.endswith(".csv"):
            header = next(csv.reader(io.StringIO(text)))
            test.assertEqual(header, list(register.PIN_COLUMNS))
            test.assertFalse(FORBIDDEN_KEYS & set(header))


class CompliantFormTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.build, cls.public, cls.private, _ = fixture_week(Path(cls.tmp.name))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def test_public_files_hold_no_model_output(self) -> None:
        check_public_week(self, self.public)

    def test_commitment_matches_private_file(self) -> None:
        commitment = json.loads((self.public / "commitment.json").read_text(encoding="utf-8"))
        private = (self.private / "forecasts.json").read_bytes()
        self.assertEqual(commitment["forecasts_sha256"], hashlib.sha256(private).hexdigest())
        self.assertEqual(commitment["forecasts_bytes"], len(private))
        self.assertEqual(commitment["forecast_count"], len(self.build.forecasts["forecasts"]))
        self.assertFalse((self.public / "forecasts.json").exists())

    def test_private_file_holds_the_quantiles(self) -> None:
        doc = json.loads((self.private / "forecasts.json").read_text(encoding="utf-8"))
        self.assertTrue(all(len(f["quantiles"]) == 23 for f in doc["forecasts"]))

    def test_limitations_line_10_unchanged(self) -> None:
        line = (REPO / "LIMITATIONS.md").read_text(encoding="utf-8").splitlines(keepends=True)[9]
        self.assertEqual(hashlib.sha256(line.encode()).hexdigest(), LINE_10_SHA256)

    def test_registered_weeks_in_the_repository(self) -> None:
        store = REPO / "data" / "weekly-forecasts"
        if not store.is_dir():
            self.skipTest("no weekly week registered yet")
        for week in sorted(p for p in store.iterdir() if p.is_dir() and p.name[:2] == "20"):
            check_public_week(self, week)
            manifest = json.loads((week / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["spec_sha256"], spec.spec_sha256() if manifest["spec_version"] ==
                             spec.SPEC_VERSION else manifest["spec_sha256"])


if __name__ == "__main__":
    unittest.main()
