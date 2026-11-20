# SPDX-License-Identifier: Apache-2.0
"""Weekly loop, test 12: every week is complete and every target past settle has a status."""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from lovs.forecast.weekly import promotion_rows, register, resolve, spec, targets
from tests.weekly_fixtures import R, fixture_week, promotion_from_row, synthetic_rows

UTC = dt.timezone.utc


class CompletenessTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.build, cls.public, cls.private, _ = fixture_week(Path(cls.tmp.name))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def test_manifest_pins_commitment(self) -> None:
        for name in ("manifest.json", "pins.csv", "commitment.json"):
            self.assertTrue((self.public / name).is_file(), name)
        manifest = json.loads((self.public / "manifest.json").read_text(encoding="utf-8"))
        for name, digest in manifest["files"].items():
            self.assertEqual(hashlib.sha256((self.public / name).read_bytes()).hexdigest(), digest, name)
        self.assertEqual(manifest["registration_day"], R.isoformat())
        self.assertEqual(manifest["deadline"], f"{R.isoformat()}T23:59:59Z")
        self.assertEqual(manifest["spec_sha256"], spec.spec_sha256())
        self.assertEqual(manifest["core_sha256"], spec.core_sha256())     # the v2 core digest
        self.assertEqual(manifest["counts"]["pins"], len(self.build.pins))
        self.assertEqual(manifest["counts"]["forecasts"], len(self.build.forecasts["forecasts"]))
        self.assertIn("lovs", manifest["fetched"])
        self.assertIn("mcp", manifest["fetched"])

    def test_forecast_count(self) -> None:
        active = self.build.forecasts["active_series"]
        self.assertEqual(len(self.build.forecasts["forecasts"]), len(active) * len(spec.HORIZONS) * 5)
        self.assertEqual(len(active), 15)

    def test_every_pin_traces_to_its_forecasts(self) -> None:
        cells = {(f["series"], f["horizon_days"], f["method"]) for f in self.build.forecasts["forecasts"]}
        rows = list(csv.DictReader(io.StringIO((self.public / "pins.csv").read_text(encoding="utf-8"))))
        self.assertEqual(len(rows), len(self.build.pins))
        self.assertLessEqual(len(rows), 300)
        for row in rows:
            s = spec.SERIES_BY_ID[row["series"]]
            for m in spec.methods_for(s):
                self.assertIn((row["series"], int(row["horizon_days"]), m), cells)
                self.assertNotEqual(row[spec.price_column(m)], "", row["pin_id"])
            for m in set(spec.METHOD_IDS) - set(spec.methods_for(s)):
                self.assertEqual(row[spec.price_column(m)], "")
        ids = [r["pin_id"] for r in rows]
        self.assertEqual(len(ids), len(set(ids)))

    def test_every_target_past_settle_has_a_status(self) -> None:
        rows = synthetic_rows(start=dt.date(2026, 6, 1), end=dt.date(2027, 1, 20), missing=frozenset())
        by_day = {dt.date.fromisoformat(r["data_as_of"]): promotion_rows.row_from_promotion(promotion_from_row(r))
                  for r in rows}
        now = dt.datetime(2027, 1, 21, 13, tzinfo=UTC)
        statuses = resolve.resolve_week_targets(R, self.build.forecasts["active_series"], by_day, now)
        self.assertEqual(len(statuses), 15 * 4)
        for st in statuses:
            _, end = targets.week_bounds(R, st.k)
            self.assertIn(st.status, spec.TARGET_STATUSES)
            self.assertTrue(st.reason)
            if now.date() > end + dt.timedelta(days=spec.SETTLE_DAYS):
                self.assertNotIn(st.status, ("pending", "pending_boundary"))


if __name__ == "__main__":
    unittest.main()
