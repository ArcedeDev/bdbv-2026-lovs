# SPDX-License-Identifier: Apache-2.0
"""Weekly loop, test 4: truth fixtures and arrival-timed first-published values."""
from __future__ import annotations

import copy
import datetime as dt
import unittest

from lovs.forecast.weekly import promotion_rows, resolve, spec, targets, truth
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
        R, n1 = _day("2026-06-17"), spec.SERIES_BY_ID["N1"]
        self.assertNotIn(_day("2026-06-24"), self.by_day)
        early = resolve.resolve_target(n1, self.by_day, R, 1, _now(_day("2026-07-06")))
        self.assertEqual(early.status, "pending_boundary")
        late = resolve.resolve_target(n1, self.by_day, R, 1, _now(_day("2026-06-24") + D(days=28)))
        self.assertEqual(late.status, "unscoreable_span")
        self.assertTrue(late.reason)
        # The week starting there loses its start boundary too.
        nxt = resolve.resolve_target(n1, self.by_day, _day("2026-06-24"), 1, _now(_day("2026-08-26")))
        self.assertEqual(nxt.status, "unscoreable_span")
        # N4 is a level on the end Wednesday, so only the week ending there is lost.
        n4 = resolve.resolve_target(spec.SERIES_BY_ID["N4"], self.by_day, _day("2026-06-24"), 1,
                                    _now(_day("2026-08-26")))
        self.assertEqual(n4.status, "observed")

    def test_pending_before_the_target_ends(self) -> None:
        R = _day("2026-09-23")
        got = resolve.resolve_target(spec.SERIES_BY_ID["N1"], self.by_day, R, 2, _now(_day("2026-10-01")))
        self.assertEqual(got.status, "pending")

    def test_sr172_gap_inside_a_week_keeps_the_target(self) -> None:
        # 2026-09-28 (a Monday) is missing, as SR172 is in the real record.
        R = _day("2026-09-23")
        self.assertNotIn(_day("2026-09-28"), self.by_day)
        flow = resolve.resolve_target(spec.SERIES_BY_ID["N1"], self.by_day, R, 1, _now(_day("2026-10-26")))
        self.assertEqual(flow.status, "observed")
        expected = self.by_day[_day("2026-09-30")]["confirmed_total"] - self.by_day[R]["confirmed_total"]
        self.assertEqual(flow.value, expected)
        mean = resolve.resolve_target(spec.SERIES_BY_ID["N5"], self.by_day, R, 1, _now(_day("2026-10-26")))
        self.assertEqual(mean.status, "observed")
        self.assertEqual(mean.n_days, 6)

    def test_partial_alerts_day_is_dropped_from_n6(self) -> None:
        R = _day("2026-09-09")
        by_day = dict(self.by_day)
        flagged = _day("2026-09-12")
        by_day[flagged] = promotion_rows.row_from_promotion(promotion_from_row(
            next(r for r in self.rows if r["data_as_of"] == flagged.isoformat()), alerts_absent=("Haut-Uele",)))
        self.assertIn("alerts_province_absent", by_day[flagged]["flags"])
        got = resolve.resolve_target(spec.SERIES_BY_ID["N6"], by_day, R, 1, _now(_day("2026-10-26")))
        self.assertEqual(got.status, "observed")
        self.assertEqual(got.n_days, 6)
        days = [d for d in by_day if R < d <= R + D(days=7) and d != flagged]
        self.assertAlmostEqual(got.value, sum(by_day[d]["alerts_reported"] for d in days) / len(days))
        # Two more days missing leave 4 of 7: unscoreable once past the span limit.
        for d in (_day("2026-09-13"), _day("2026-09-14")):
            del by_day[d]
        short = resolve.resolve_target(spec.SERIES_BY_ID["N6"], by_day, R, 1, _now(_day("2026-10-26")))
        self.assertEqual(short.status, "unscoreable_span")

    def test_withheld_boundary_value_is_held(self) -> None:
        R = _day("2026-09-09")
        by_day = dict(self.by_day)
        end = R + D(days=7)
        row = next(r for r in self.rows if r["data_as_of"] == end.isoformat())
        by_day[end] = promotion_rows.row_from_promotion(
            promotion_from_row(row, withheld=("cumul_cas_confirmes_drc",)))
        self.assertIsNone(by_day[end]["confirmed_total"])
        self.assertIn("withheld:confirmed_total", by_day[end]["flags"])
        got = resolve.resolve_target(spec.SERIES_BY_ID["N1"], by_day, R, 1, _now(_day("2026-10-26")))
        self.assertEqual(got.status, "held_basis_break")


class ArrivalTimingTest(unittest.TestCase):
    """First-published and settled versions come from arrival records, never git dates."""

    FP = ["a" * 40, "b" * 40, "c" * 40, "d" * 40]

    def _arrivals(self):
        # The activity record's shape: each push names the tip before and after it.
        events = [
            {"before": self.FP[0], "after": self.FP[1], "timestamp": "2026-10-27T10:00:00Z",
             "activity_type": "pr_merge"},
            {"before": self.FP[1], "after": self.FP[3], "timestamp": "2026-10-31T09:00:00Z",
             "activity_type": "pr_merge"},
        ]
        merges = {self.FP[0]: "2026-10-16T08:00:00Z"}
        return truth.arrival_times(self.FP, events, merges)

    def test_commits_take_the_first_push_that_carries_them(self) -> None:
        arr = self._arrivals()
        self.assertEqual(arr[self.FP[0]], dt.datetime(2026, 10, 16, 8, tzinfo=UTC))
        self.assertEqual(arr[self.FP[1]], dt.datetime(2026, 10, 27, 10, tzinfo=UTC))
        self.assertEqual(arr[self.FP[2]], dt.datetime(2026, 10, 31, 9, tzinfo=UTC))
        self.assertEqual(arr[self.FP[3]], dt.datetime(2026, 10, 31, 9, tzinfo=UTC))

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
        self.assertEqual(truth.as_of(versions, dt.datetime(2026, 10, 29, tzinfo=UTC)).blob, "blob-one")
        self.assertEqual(truth.as_of(versions, dt.datetime(2026, 11, 1, tzinfo=UTC)).blob, "blob-two")
        self.assertIsNone(truth.as_of(versions, dt.datetime(2026, 10, 26, tzinfo=UTC)))

    def test_duplicate_files_for_one_sitrep_by_arrival_then_name(self) -> None:
        arr = self._arrivals()
        a = "data/sitrep_promotions/sitrep-191-2026-11-21.json"
        b = "data/sitrep_promotions/sitrep-191-2026-11-21b.json"
        history = [(self.FP[1], [(b, "blob-b"), (a, "blob-a")])]
        index = truth.sitrep_index(truth.file_versions(history, arr))
        self.assertEqual([v.path for v in index[191]], [a, b])

    def test_settle_time(self) -> None:
        end = dt.date(2026, 10, 28)
        self.assertEqual(truth.settle_time(end), dt.datetime(2026, 11, 11, 23, 59, 59, tzinfo=UTC))

    def test_truth_reader_never_reads_git_dates(self) -> None:
        source = truth.__file__
        with open(source, encoding="utf-8") as handle:
            text = handle.read()
        for forbidden in ("%cI", "%ci", "%aI", "%ai", "%ct", "%at", "committer_date", "author_date"):
            self.assertNotIn(forbidden, text)


def _n7_pin(R: dt.date, threshold: float) -> dict:
    start, end = targets.week_bounds(R, 1)
    pin = {"pin_id": spec.pin_id(R, 1), "registration_day": R.isoformat(), "series": "N7", "horizon_days": 7,
           "target_start": start.isoformat(), "target_end": end.isoformat(), "threshold": threshold}
    for m in spec.methods_for(spec.SERIES_BY_ID["N7"]):
        pin[spec.price_column(m)] = 0.5
    return pin


class LagSettleTest(unittest.TestCase):
    """review/WEEKLY-CODE-REVIEW.md I3: N7 is censored in real time and scored settled only."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.by_day = _truth_rows(synthetic_rows())
        cls.R = _day("2026-09-09")
        cls.end = targets.week_bounds(cls.R, 1)[1]
        cls.settle = truth.settle_time(cls.end)

    def test_end_day_still_absent_at_settle_is_a_lag_of_14(self) -> None:
        rows = {d: r for d, r in self.by_day.items() if d != self.end}
        views = {"first_published": (rows, {}), self.settle: (rows, {})}
        out, statuses = resolve.resolve_week([_n7_pin(self.R, 10)], self.R, views, self.settle + D(hours=1), rows)
        self.assertEqual([(r["value_kind"], r["status"], r["realised"]) for r in out],
                         [("settled", "resolved_no", 14.0)])
        self.assertEqual([(s.k, s.status, s.value) for s in statuses if s.k == 1], [(1, "observed", 14.0)])

    def test_rows_as_of_now_past_settle_give_14(self) -> None:
        # resolve_target on rows as main stands now: past end + 14, an absent end day is 14.
        rows = {d: r for d, r in self.by_day.items() if d != self.end}
        for days_after in (15, 28):
            now = dt.datetime.combine(self.end + D(days=days_after), dt.time(12), tzinfo=UTC)
            got = resolve.resolve_target(spec.SERIES_BY_ID["N7"], rows, self.R, 1, now)
            self.assertEqual((got.status, got.value), ("observed", 14.0))
        early = resolve.resolve_target(spec.SERIES_BY_ID["N7"], rows, self.R, 1, _now(self.end + D(days=10)))
        self.assertEqual(early.status, "pending_boundary")

    def test_n7_has_no_first_published_rows(self) -> None:
        views = {"first_published": (self.by_day, {}), self.settle: (self.by_day, {})}
        before = resolve.resolve_week([_n7_pin(self.R, 10)], self.R, {"first_published": (self.by_day, {})},
                                      self.settle - D(hours=1), self.by_day)[0]
        self.assertEqual(before, [])
        after = resolve.resolve_week([_n7_pin(self.R, 10)], self.R, views, self.settle + D(hours=1), self.by_day)[0]
        self.assertEqual({r["value_kind"] for r in after}, {"settled"})


class _FakeView:
    def __init__(self, day: dt.date) -> None:
        self.day = day

    def rows(self, when=None, first=False):
        return {self.day: {"marker": ("first" if first else "latest", when)}}, {}


class ResolutionViewsTest(unittest.TestCase):
    """review/WEEKLY-CODE-REVIEW.md I10 (M6): each settle view reads main as it stood then."""

    def test_settled_views_read_latest_versions_at_their_settle_time(self) -> None:
        R = _day("2026-09-09")
        day = targets.week_bounds(R, 1)[1]
        now = truth.settle_time(targets.week_bounds(R, 2)[1]) + D(hours=1)
        views, deadline_rows = resolve.views_for(_FakeView(day), R, now, {day})
        self.assertEqual(views["first_published"][0][day]["marker"], ("first", now))
        settles = [truth.settle_time(targets.week_bounds(R, k)[1]) for k in (1, 2)]
        self.assertEqual(sorted(k for k in views if k != "first_published"), settles)
        for st in settles:
            self.assertEqual(views[st][0][day]["marker"], ("latest", st))
        self.assertEqual(deadline_rows[day]["marker"], ("first", targets.deadline(R)))

    def test_a_day_without_its_packet_is_not_in_any_view(self) -> None:
        R = _day("2026-09-09")
        day = targets.week_bounds(R, 1)[1]
        now = truth.settle_time(day) + D(hours=1)
        views, _ = resolve.views_for(_FakeView(day), R, now, set())
        self.assertTrue(all(day not in rows for rows, _ in views.values()))


if __name__ == "__main__":
    unittest.main()
