# SPDX-License-Identifier: Apache-2.0
"""Weekly loop, test 7: forecasts and origin features read the week's vintage only."""
from __future__ import annotations

import copy
import datetime as dt
import unittest

from lovs.forecast.weekly import export, register, vintage
from tests.weekly_fixtures import BUILD_TIME, FETCHED, GENERATOR, R, fixture_inputs, synthetic_rows


class LeakageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.inputs = fixture_inputs()
        cls.cutoff = dt.date(2026, 11, 15)
        cls.rows = vintage.build_vintage(cls.inputs["extract"], cls.inputs["promotions"], R, cls.cutoff,
                                         extract_last_sitrep=cls.inputs["extract_last_sitrep"])
        # Rows the build must never read: data days after the cutoff, and a row whose
        # publication is on R itself.
        future = synthetic_rows(start=dt.date(2026, 11, 16), end=dt.date(2026, 12, 20), missing=frozenset())
        for row in future:
            row["source"] = "promotion"
            row["flags"] = []
        late = copy.deepcopy(cls.rows[-1])
        late["published_at"] = R.isoformat()
        cls.leaky = cls.rows + future

    def _build(self, rows):
        return register.build_week(R, rows, self.cutoff, n_paths=200, taken_seeds=frozenset(),
                                   generator=GENERATOR, fetched=FETCHED, build_time=BUILD_TIME)

    def test_features_from_the_vintage_only(self) -> None:
        clean = export.origin_features(self.rows, R, self.cutoff)
        leaky = export.origin_features(self.leaky, R, self.cutoff)
        self.assertEqual(clean, leaky)
        for feature in clean:
            self.assertLessEqual(feature["as_of"], self.cutoff.isoformat())

    def test_forecasts_from_the_vintage_only(self) -> None:
        a = register.forecasts_bytes(self._build(self.rows).forecasts)
        b = register.forecasts_bytes(self._build(self.leaky).forecasts)
        self.assertEqual(a, b)

    def test_rows_published_on_r_are_excluded(self) -> None:
        promos = copy.deepcopy(self.inputs["promotions"])
        promos[-1]["published_at"] = f"{R.isoformat()}T01:00:00Z"
        rows = vintage.build_vintage(self.inputs["extract"], promos, R, self.cutoff,
                                     extract_last_sitrep=self.inputs["extract_last_sitrep"])
        self.assertNotIn(promos[-1]["data_as_of"], {r["data_as_of"] for r in rows})


if __name__ == "__main__":
    unittest.main()
