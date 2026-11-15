# SPDX-License-Identifier: Apache-2.0
"""Tests for release-time README snapshot-date currency guard."""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

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


if __name__ == "__main__":
    unittest.main()
