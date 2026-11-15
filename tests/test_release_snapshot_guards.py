# SPDX-License-Identifier: Apache-2.0
"""Tests for release-time README snapshot-date currency guard."""
from __future__ import annotations

import collections
import contextlib
import io
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import release_snapshot

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


class TestReadmeCurrencyGuard(unittest.TestCase):
    def test_format_snapshot_date(self):
        self.assertEqual("24 May 2026", release_snapshot._format_snapshot_date("2026-05-24"))
        self.assertEqual("1 June 2026", release_snapshot._format_snapshot_date("2026-06-01"))

    def test_stale_phrase_is_flagged(self):
        text = "The 23 May 2026 snapshot indicates the picture."
        self.assertEqual(
            ["23 May 2026"],
            release_snapshot.find_stale_readme_snapshot_dates(text, "2026-05-24"),
        )

    def test_current_phrase_is_clean(self):
        text = "The 24 May 2026 snapshot indicates the picture."
        self.assertEqual(
            [],
            release_snapshot.find_stale_readme_snapshot_dates(text, "2026-05-24"),
        )

    def test_non_snapshot_dates_are_ignored(self):
        text = "Imperial published a 20 May 2026 update; ECDC reported on 19 May 2026."
        self.assertEqual(
            [],
            release_snapshot.find_stale_readme_snapshot_dates(text, "2026-05-24"),
        )

    def test_live_readme_matches_built_snapshot(self):
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        out = json.loads(
            (REPO_ROOT / "data" / "live-bdbv-2026-output.json").read_text(encoding="utf-8")
        )
        as_of = str(out["as_of"])[:10]
        self.assertEqual([], release_snapshot.find_stale_readme_snapshot_dates(readme, as_of))


class TestOverdueReviewLine(unittest.TestCase):
    def test_the_review_prints_one_line_when_commitments_are_overdue(self):
        status = {"overdue_open_commitments": [
            {"ledger_id": "bdbv-2026-cal-225", "resolution_date": "2026-11-04", "evidence_grace_days": 3},
            {"ledger_id": "bdbv-2026-cal-226", "resolution_date": "2026-11-04", "evidence_grace_days": 3},
        ]}
        line = release_snapshot.overdue_review_line(status)
        self.assertNotIn("\n", line)
        self.assertIn("2 open commitments overdue for resolution", line)
        self.assertIn("resolution date 2026-11-04", line)

    def test_the_review_prints_nothing_when_none_is_overdue(self):
        self.assertIsNone(release_snapshot.overdue_review_line({"overdue_open_commitments": []}))



class TestFailingTestListing(unittest.TestCase):
    """A failed suite names every failing and erroring test, not the first few."""

    def _suite_output(self, failures: int, errors: int) -> str:
        lines = ["import unittest", "", "class Injected(unittest.TestCase):"]
        lines += [f"    def test_fail_{i:02d}(self):\n        self.assertEqual({i}, -1)" for i in range(failures)]
        lines += [f"    def test_error_{i:02d}(self):\n        raise RuntimeError('injected {i}')" for i in range(errors)]
        lines += ["    def test_passes(self):\n        self.assertTrue(True)"]
        with tempfile.TemporaryDirectory() as tmp:
            (pathlib.Path(tmp) / "test_injected.py").write_text("\n".join(lines) + "\n", encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "-m", "unittest", "discover", "-s", tmp],
                cwd=tmp, text=True, capture_output=True,
            )
        self.assertNotEqual(0, result.returncode)
        return result.stdout + result.stderr

    def test_every_injected_failure_and_error_is_listed(self):
        summary = release_snapshot.summarize_unittest_failures(self._suite_output(23, 4))
        self.assertEqual(23, len(summary["failures"]))
        self.assertEqual(4, len(summary["errors"]))
        self.assertEqual({"failures": 23, "errors": 4}, summary["reported"])
        self.assertIn("test_injected.Injected.test_fail_22", summary["failures"])
        self.assertIn("test_injected.Injected.test_error_03", summary["errors"])
        self.assertTrue(summary["complete"])

    def test_the_printed_report_carries_every_id_and_the_counts(self):
        output = self._suite_output(31, 2)
        lines = release_snapshot.failing_test_report(output)
        text = "\n".join(lines)
        self.assertIn("31 failing, 2 erroring", lines[0])
        for i in range(31):
            self.assertIn(f"FAIL  test_injected.Injected.test_fail_{i:02d}", text)
        for i in range(2):
            self.assertIn(f"ERROR test_injected.Injected.test_error_{i:02d}", text)
        self.assertEqual(1 + 31 + 2, len(lines))

    def test_a_listing_that_disagrees_with_the_suite_count_says_so(self):
        output = (
            "======\nFAIL: test_a (mod.Case.test_a)\n------\n"
            "Ran 5 tests in 0.1s\n\nFAILED (failures=2, errors=1)\n"
        )
        summary = release_snapshot.summarize_unittest_failures(output)
        self.assertFalse(summary["complete"])
        self.assertIn("suite reported 2 failures and 1 errors", "\n".join(release_snapshot.failing_test_report(output)))

    def test_each_failing_subtest_is_listed_with_its_parameters(self):
        output = (
            "FAIL: test_a (mod.Case.test_a) (zone='bunia')\n"
            "FAIL: test_a (mod.Case.test_a) (zone='beni')\n"
            "Ran 1 test in 0.1s\n\nFAILED (failures=2)\n"
        )
        summary = release_snapshot.summarize_unittest_failures(output)
        self.assertEqual(["mod.Case.test_a (zone='bunia')", "mod.Case.test_a (zone='beni')"], summary["failures"])
        self.assertTrue(summary["complete"])

    def test_a_clean_run_reports_nothing(self):
        self.assertEqual([], release_snapshot.failing_test_report("Ran 3 tests in 0.1s\n\nOK\n"))


class TestUtcDayStamp(unittest.TestCase):
    """The release check names the same-UTC-day stamp before it starts."""

    def test_stamp_and_hours_left_are_printed(self):
        now = datetime(2026, 11, 14, 9, 30, tzinfo=timezone.utc)
        lines = release_snapshot.utc_day_stamp_lines(now)
        self.assertEqual(1, len(lines))
        self.assertIn("2026-11-14", lines[0])
        self.assertIn("14.5 hours left", lines[0])

    def test_under_three_hours_warns(self):
        now = datetime(2026, 11, 14, 22, 0, tzinfo=timezone.utc)
        lines = release_snapshot.utc_day_stamp_lines(now)
        self.assertIn("2.0 hours left", lines[0])
        self.assertEqual(2, len(lines))
        self.assertIn("[WARN]", lines[1])
        self.assertIn("2026-11-15", lines[1])

    def test_exactly_three_hours_does_not_warn(self):
        now = datetime(2026, 11, 14, 21, 0, tzinfo=timezone.utc)
        self.assertEqual(1, len(release_snapshot.utc_day_stamp_lines(now)))

    def test_a_local_clock_is_read_in_utc(self):
        now = datetime(2026, 11, 15, 0, 30, tzinfo=timezone(timedelta(hours=2)))
        self.assertIn("2026-11-14", release_snapshot.utc_day_stamp_lines(now)[0])


class TestFreeSpacePreflight(unittest.TestCase):
    """The release check refuses before any stage when the volume is too full."""

    @staticmethod
    def _usage(free_bytes: int):
        usage = collections.namedtuple("usage", "total used free")
        return lambda path: usage(10 * free_bytes, 9 * free_bytes, free_bytes)

    def test_enough_space_passes_and_prints_both_numbers(self):
        required = release_snapshot.RELEASE_CHECK_REQUIRED_FREE_BYTES
        ok, line = release_snapshot.free_space_preflight(disk_usage=self._usage(required * 3))
        self.assertTrue(ok)
        self.assertIn(release_snapshot._format_gib(required * 3), line)
        self.assertIn(release_snapshot._format_gib(required), line)

    def test_below_the_requirement_refuses_with_a_clear_message(self):
        required = release_snapshot.RELEASE_CHECK_REQUIRED_FREE_BYTES
        ok, line = release_snapshot.free_space_preflight(disk_usage=self._usage(required - 1))
        self.assertFalse(ok)
        self.assertIn("free space", line)
        self.assertIn("needs", line)
        self.assertIn(str(release_snapshot.REPO_ROOT), line)

    def test_main_stops_before_the_pipeline_when_space_is_short(self):
        calls: list[str] = []
        # The pipeline and the suite are both stubbed to fail: if the preflight call is
        # ever removed, this test fails at once and cannot recurse into the full suite.
        with (
            mock.patch.object(release_snapshot, "free_space_preflight",
                              lambda **_: (False, "free space refused for the test")),
            mock.patch.object(release_snapshot, "run_pipeline", lambda *_: calls.append("pipeline")),
            mock.patch.object(release_snapshot, "run_tests", lambda: calls.append("tests")),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()) as err,
        ):
            code = release_snapshot.main([])
        self.assertEqual(1, code)
        self.assertEqual([], calls)
        self.assertIn("free space refused for the test", err.getvalue())


if __name__ == "__main__":
    unittest.main()
