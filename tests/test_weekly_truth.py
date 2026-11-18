# SPDX-License-Identifier: Apache-2.0
"""Weekly loop, test 4: truth fixtures and arrival-timed first-published values."""
from __future__ import annotations

import copy
import datetime as dt
import unittest

from lovs.forecast.weekly import promotion_rows, resolve, spec, truth
from tests.weekly_fixtures import promotion_from_row, synthetic_rows

UTC = dt.timezone.utc
D = dt.timedelta


def _day(text: str) -> dt.date:
    return dt.date.fromisoformat(text)


def _now(day: dt.date) -> dt.datetime:
    return dt.datetime(day.year, day.month, day.day, 13, 0, tzinfo=UTC)


def _truth_rows(rows: list[dict], **promo_kwargs) -> dict[dt.date, dict]:
    return {_day(r["data_as_of"]): promotion_rows.row_from_promotion(promotion_from_row(r, **promo_kwargs))
            for r in rows}


class CalendarFixtureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rows = synthetic_rows()
        cls.by_day = _truth_rows(cls.rows)

    def test_missing_wednesday_0729_is_pending_then_unscoreable_span(self) -> None:
        R, n1 = _day("2026-07-22"), spec.SERIES_BY_ID["N1"]
        self.assertNotIn(_day("2026-07-29"), self.by_day)
        early = resolve.resolve_target(n1, self.by_day, R, 1, _now(_day("2026-08-10")))
        self.assertEqual(early.status, "pending_boundary")
        late = resolve.resolve_target(n1, self.by_day, R, 1, _now(_day("2026-07-29") + D(days=28)))
        self.assertEqual(late.status, "unscoreable_span")
        self.assertTrue(late.reason)
        # The week starting there loses its start boundary too.
        nxt = resolve.resolve_target(n1, self.by_day, _day("2026-07-29"), 1, _now(_day("2026-09-30")))
        self.assertEqual(nxt.status, "unscoreable_span")
        # N4 is a level on the end Wednesday, so only the week ending there is lost.
        n4 = resolve.resolve_target(spec.SERIES_BY_ID["N4"], self.by_day, _day("2026-07-29"), 1,
                                    _now(_day("2026-09-30")))
        self.assertEqual(n4.status, "observed")

    def test_pending_before_the_target_ends(self) -> None:
        R = _day("2026-10-28")
        got = resolve.resolve_target(spec.SERIES_BY_ID["N1"], self.by_day, R, 2, _now(_day("2026-11-05")))
        self.assertEqual(got.status, "pending")

    def test_sr172_gap_inside_a_week_keeps_the_target(self) -> None:
        # 2026-11-02 (a Monday) is missing, as SR172 is in the real record.
        R = _day("2026-10-28")
        self.assertNotIn(_day("2026-11-02"), self.by_day)
        flow = resolve.resolve_target(spec.SERIES_BY_ID["N1"], self.by_day, R, 1, _now(_day("2026-11-30")))
        self.assertEqual(flow.status, "observed")
        expected = self.by_day[_day("2026-11-04")]["confirmed_total"] - self.by_day[R]["confirmed_total"]
        self.assertEqual(flow.value, expected)
        mean = resolve.resolve_target(spec.SERIES_BY_ID["N5"], self.by_day, R, 1, _now(_day("2026-11-30")))
        self.assertEqual(mean.status, "observed")
        self.assertEqual(mean.n_days, 6)

    def test_partial_alerts_day_is_dropped_from_n6(self) -> None:
        R = _day("2026-10-14")
        by_day = dict(self.by_day)
        flagged = _day("2026-10-17")
        by_day[flagged] = promotion_rows.row_from_promotion(promotion_from_row(
            next(r for r in self.rows if r["data_as_of"] == flagged.isoformat()), alerts_absent=("Haut-Uele",)))
        self.assertIn("alerts_province_absent", by_day[flagged]["flags"])
        got = resolve.resolve_target(spec.SERIES_BY_ID["N6"], by_day, R, 1, _now(_day("2026-11-30")))
        self.assertEqual(got.status, "observed")
        self.assertEqual(got.n_days, 6)
        days = [d for d in by_day if R < d <= R + D(days=7) and d != flagged]
        self.assertAlmostEqual(got.value, sum(by_day[d]["alerts_reported"] for d in days) / len(days))
        # Two more days missing leave 4 of 7: unscoreable once past the span limit.
        for d in (_day("2026-10-18"), _day("2026-10-19")):
            del by_day[d]
        short = resolve.resolve_target(spec.SERIES_BY_ID["N6"], by_day, R, 1, _now(_day("2026-11-30")))
        self.assertEqual(short.status, "unscoreable_span")

    def test_withheld_boundary_value_is_held(self) -> None:
        R = _day("2026-10-14")
        by_day = dict(self.by_day)
        end = R + D(days=7)
        row = next(r for r in self.rows if r["data_as_of"] == end.isoformat())
        by_day[end] = promotion_rows.row_from_promotion(
            promotion_from_row(row, withheld=("cumul_cas_confirmes_drc",)))
        self.assertIsNone(by_day[end]["confirmed_total"])
        self.assertIn("withheld:confirmed_total", by_day[end]["flags"])
        got = resolve.resolve_target(spec.SERIES_BY_ID["N1"], by_day, R, 1, _now(_day("2026-11-30")))
        self.assertEqual(got.status, "held_basis_break")


class ArrivalTimingTest(unittest.TestCase):
    """First-published and settled versions come from arrival records, never git dates."""

    FP = ["a" * 40, "b" * 40, "c" * 40, "d" * 40]

    def _arrivals(self):
        # The activity record's shape: each push names the tip before and after it.
        events = [
            {"before": self.FP[0], "after": self.FP[1], "timestamp": "2026-12-01T10:00:00Z",
             "activity_type": "pr_merge"},
            {"before": self.FP[1], "after": self.FP[3], "timestamp": "2026-12-05T09:00:00Z",
             "activity_type": "pr_merge"},
        ]
        merges = {self.FP[0]: "2026-11-20T08:00:00Z"}
        return truth.arrival_times(self.FP, events, merges)

    def test_commits_take_the_first_push_that_carries_them(self) -> None:
        arr = self._arrivals()
        self.assertEqual(arr[self.FP[0]], dt.datetime(2026, 11, 20, 8, tzinfo=UTC))
        self.assertEqual(arr[self.FP[1]], dt.datetime(2026, 12, 1, 10, tzinfo=UTC))
        self.assertEqual(arr[self.FP[2]], dt.datetime(2026, 12, 5, 9, tzinfo=UTC))
        self.assertEqual(arr[self.FP[3]], dt.datetime(2026, 12, 5, 9, tzinfo=UTC))

    def test_unrecorded_commit_has_no_arrival(self) -> None:
        arr = truth.arrival_times(self.FP, [], {})
        self.assertTrue(all(v is None for v in arr.values()))

    def test_versions_ordered_by_arrival_not_git_date(self) -> None:
        arr = self._arrivals()
        path = "data/sitrep_promotions/sitrep-190-2026-11-20.json"
        # The version committed at FP[2] carries a git date (11-15) earlier than FP[1]'s, but it
        # reached main only with FP[3]'s push. The history passed in holds no git dates at all.
        history = [(self.FP[1], [(path, "blob-one")]), (self.FP[2], [(path, "blob-two")])]
        versions = truth.file_versions(history, arr)[path]
        self.assertEqual([v.blob for v in versions], ["blob-one", "blob-two"])
        self.assertEqual(truth.first_published(versions).blob, "blob-one")
        self.assertEqual(truth.as_of(versions, dt.datetime(2026, 12, 3, tzinfo=UTC)).blob, "blob-one")
        self.assertEqual(truth.as_of(versions, dt.datetime(2026, 12, 6, tzinfo=UTC)).blob, "blob-two")
        self.assertIsNone(truth.as_of(versions, dt.datetime(2026, 11, 30, tzinfo=UTC)))

    def test_duplicate_files_for_one_sitrep_by_arrival_then_name(self) -> None:
        arr = self._arrivals()
        a = "data/sitrep_promotions/sitrep-191-2026-11-21.json"
        b = "data/sitrep_promotions/sitrep-191-2026-11-21b.json"
        history = [(self.FP[1], [(b, "blob-b"), (a, "blob-a")])]
        index = truth.sitrep_index(truth.file_versions(history, arr))
        self.assertEqual([v.path for v in index[191]], [a, b])

    def test_settle_time(self) -> None:
        end = dt.date(2026, 12, 2)
        self.assertEqual(truth.settle_time(end), dt.datetime(2026, 12, 16, 23, 59, 59, tzinfo=UTC))

    def test_truth_reader_never_reads_git_dates(self) -> None:
        source = truth.__file__
        with open(source, encoding="utf-8") as handle:
            text = handle.read()
        for forbidden in ("%cI", "%ci", "%aI", "%ai", "%ct", "%at", "committer_date", "author_date"):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
