# SPDX-License-Identifier: Apache-2.0
"""Weekly loop, test 8: weekly rows never reach the ledgers, the record or recalibration.

A copy of the repository is probed twice through every consumer the design names, once
as it stands and once with a fixture weekly store under data/weekly-forecasts/; every
output must be byte-identical. No weekly id can match the public ledger's id pattern.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from lovs.forecast import registered_side
from lovs.forecast.weekly import spec
from tests.weekly_fixtures import fixture_week

REPO = Path(__file__).resolve().parents[1]

PROBE = r'''
import datetime as dt, hashlib, json, subprocess, sys
from lovs.forecast import opsforecast as of, opsresolver, record, recalibration
import refresh_pipeline

def h(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()

rec = record.build()
rows = record.of_record([record.ScoredForecast(**r) for r in rec["rows"]])
series = sorted(p for p in (of.REPO / "data").glob("operational-series-*.json"))[-1]
report = opsresolver.build_report(opsresolver.load_ledger(), of.load_rows(series), dt.date(2026, 12, 31))
commitments = refresh_pipeline.carry_forward_commitments("2026-11-25")
exports = subprocess.run([sys.executable, "-m", "lovs.public_exports", "--check"], capture_output=True, text=True)
print(json.dumps({
    "record": h(rec), "record_rows": len(rec["rows"]),
    "power_check": h(recalibration.power_check(rows)),
    "build_report": h(report),
    "carry_forward_commitments": h(commitments),
    "public_exports_check": [exports.returncode, exports.stdout.strip()],
}))
'''

CONSUMERS = (
    "lovs/forecast/record.py", "lovs/forecast/recalibration.py", "lovs/forecast/opsresolver.py",
    "lovs/forecast/public_register.py", "lovs/forecast/registered_side.py", "lovs/public_exports.py",
    "refresh_pipeline.py",
)


def _copy_repo(dest: Path) -> Path:
    root = dest / "repo"
    shutil.copytree(REPO, root, ignore=shutil.ignore_patterns(".git", "__pycache__", ".process", "tmp"))
    return root


def _probe(root: Path) -> dict:
    env = dict(os.environ, PYTHONPATH=str(root), PYTHONDONTWRITEBYTECODE="1")
    env.pop("LOVS_IDB_TRACK_B_RECORD", None)
    out = subprocess.run([sys.executable, "-c", PROBE], cwd=root, env=env, capture_output=True, text=True,
                         timeout=600)
    if out.returncode != 0:
        raise AssertionError(f"probe failed: {out.stderr[-2000:]}")
    return json.loads(out.stdout.strip().splitlines()[-1])


class IsolationTest(unittest.TestCase):
    def test_consumers_unchanged_with_a_weekly_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _copy_repo(Path(tmp))
            before = _probe(root)
            build, public, private, _ = fixture_week(root)
            shutil.rmtree(root / "private")  # the private half never sits in the repository
            self.assertTrue((root / "data" / "weekly-forecasts").is_dir())
            after = _probe(root)
        self.assertEqual(before, after)
        self.assertEqual(before["public_exports_check"][0], 0, before["public_exports_check"])

    def test_no_weekly_id_matches_the_ledger_pattern(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            build, *_ = fixture_week(Path(tmp))
        ids = [p["pin_id"] for p in build.pins] + [f["forecast_id"] for f in build.forecasts["forecasts"]]
        self.assertTrue(ids)
        for wid in ids:
            self.assertTrue(wid.startswith(spec.ID_PREFIX))
            self.assertIsNone(registered_side._LEDGER_ID.fullmatch(wid))
            self.assertIsNone(registered_side._LEDGER_ID.search(wid))

    def test_consumers_never_name_the_weekly_store(self) -> None:
        for rel in CONSUMERS:
            text = (REPO / rel).read_text(encoding="utf-8")
            self.assertNotIn("weekly-forecasts", text, rel)
            self.assertNotIn(spec.ID_PREFIX, text, rel)
            self.assertNotIn("forecast.weekly", text, rel)


# Every Wednesday from the 2026-11-18 shadow week on can be a loop week whose files sit in
# data/weekly-forecasts/ of the tree under test (review/WEEKLY-CODE-REVIEW.md C1).
FIRST_POSSIBLE_LOOP_WEEK = dt.date(2026, 11, 18)


class FixtureDayTest(unittest.TestCase):
    def test_fixture_day_can_never_be_a_loop_week(self) -> None:
        from tests import weekly_fixtures as fx
        self.assertEqual(fx.R.weekday(), 2)
        self.assertLess(fx.R, FIRST_POSSIBLE_LOOP_WEEK)
        self.assertLess(fx.BUILD_TIME[:10], FIRST_POSSIBLE_LOOP_WEEK.isoformat())
        for v in fx.FETCHED.values():
            self.assertLess(v["fetched_at"][:10], FIRST_POSSIBLE_LOOP_WEEK.isoformat())

    def test_fixture_week_beside_committed_loop_weeks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for k in range(6):
                week = root / "data" / "weekly-forecasts" / (FIRST_POSSIBLE_LOOP_WEEK + dt.timedelta(days=7 * k)).isoformat()
                week.mkdir(parents=True)
                for name in ("manifest.json", "pins.csv", "commitment.json", "vintage-delta.jsonl",
                             "zones-delta.jsonl", "province-covariates-delta.jsonl"):
                    (week / name).write_text("{}\n", encoding="utf-8")
            fixture_week(root)          # raised FileExistsError when the fixture day was 2026-11-25


if __name__ == "__main__":
    unittest.main()
