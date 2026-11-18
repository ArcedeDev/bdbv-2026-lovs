# SPDX-License-Identifier: Apache-2.0
"""Weekly loop, test 3: the R rule, build refusals and the cutoff, on a fixed clock."""
from __future__ import annotations

import datetime as dt
import unittest

from lovs.forecast.weekly import targets

UTC = dt.timezone.utc
R = dt.date(2026, 11, 25)


def at(day: dt.date, hour: int, minute: int = 0) -> dt.datetime:
    return dt.datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC)


class RegistrationDayTest(unittest.TestCase):
    def test_last_wednesday_on_or_before_today(self) -> None:
        self.assertEqual(R.weekday(), 2)
        self.assertEqual(targets.registration_day(R), R)
        self.assertEqual(targets.registration_day(R + dt.timedelta(days=1)), R)
        self.assertEqual(targets.registration_day(R + dt.timedelta(days=6)), R)
        self.assertEqual(targets.registration_day(R - dt.timedelta(days=1)), R - dt.timedelta(days=7))

    def test_build_on_r_before_2000z(self) -> None:
        self.assertEqual(targets.check_build_clock(at(R, 12, 30)), R)
        self.assertEqual(targets.check_build_clock(at(R, 19, 59)), R)

    def test_refuses_at_or_after_2000z(self) -> None:
        with self.assertRaises(targets.TimingError) as caught:
            targets.check_build_clock(at(R, 20, 0))
        self.assertEqual(caught.exception.code, "missed")

    def test_refuses_thursday_catch_up(self) -> None:
        with self.assertRaises(targets.TimingError) as caught:
            targets.check_build_clock(at(R + dt.timedelta(days=1), 8, 0))
        self.assertEqual(caught.exception.code, "missed")

    def test_naive_clock_refused(self) -> None:
        with self.assertRaises(ValueError):
            targets.check_build_clock(dt.datetime(2026, 11, 25, 12, 30))

    def test_deadline_and_window(self) -> None:
        self.assertEqual(targets.deadline(R), dt.datetime(2026, 11, 25, 23, 59, 59, tzinfo=UTC))
        self.assertEqual(targets.window_opens(R), dt.date(2026, 11, 26))


class HorizonTest(unittest.TestCase):
    def test_horizons_count_from_r(self) -> None:
        for k in (1, 2, 3, 4):
            start, end = targets.week_bounds(R, k)
            self.assertEqual(start, R + dt.timedelta(days=7 * (k - 1)))
            self.assertEqual(end, R + dt.timedelta(days=7 * k))
            self.assertEqual(end.weekday(), 2)
            days = targets.data_days(R, k)
            self.assertEqual(days[0], start + dt.timedelta(days=1))
            self.assertEqual(days[-1], end)
            self.assertEqual(len(days), 7)

    def test_lead_runs_from_the_cutoff(self) -> None:
        cutoff = dt.date(2026, 11, 22)
        self.assertEqual(targets.lead_days(R, cutoff, 1), 10)
        self.assertEqual(targets.lead_days(R, cutoff, 4), 31)

    def test_no_target_day_overlaps_conditioning(self) -> None:
        cutoff = R - dt.timedelta(days=3)
        for k in (1, 2, 3, 4):
            self.assertGreater(targets.data_days(R, k)[0], cutoff)


class CutoffTest(unittest.TestCase):
    def _published(self, last: dt.date) -> dict[dt.date, dt.date]:
        out, day = {}, dt.date(2026, 11, 1)
        while day <= last:
            out[day] = day + dt.timedelta(days=2)
            day += dt.timedelta(days=1)
        return out

    def test_newest_day_on_both_mains_at_most_r_minus_3(self) -> None:
        published = self._published(dt.date(2026, 11, 23))
        packets = set(published)
        self.assertEqual(targets.choose_cutoff(R, published, packets), dt.date(2026, 11, 22))

    def test_day_missing_from_the_mcp_main_is_not_used(self) -> None:
        published = self._published(dt.date(2026, 11, 22))
        packets = set(published) - {dt.date(2026, 11, 22)}
        self.assertEqual(targets.choose_cutoff(R, published, packets), dt.date(2026, 11, 21))

    def test_day_first_published_on_r_is_not_used(self) -> None:
        published = self._published(dt.date(2026, 11, 22))
        published[dt.date(2026, 11, 22)] = R
        self.assertEqual(targets.choose_cutoff(R, published, set(published)), dt.date(2026, 11, 21))

    def test_no_usable_day_refuses(self) -> None:
        with self.assertRaises(targets.TimingError) as caught:
            targets.choose_cutoff(R, {}, set())
        self.assertEqual(caught.exception.code, "no_cutoff")


if __name__ == "__main__":
    unittest.main()
