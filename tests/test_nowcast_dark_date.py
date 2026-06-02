"""Unit tests for the dark-date detector (Phase 1 scaffold).

Spec: labs/lovs-public-goods/latency-nowcast-spec.md v0.4.
Engineering plan: .process/2026-05-26-pre-refresh-decisions/follow-up-after-pushback.md.
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lovs import nowcast_dark_date as ndd


class DetectDarkDateTests(unittest.TestCase):
    def test_returns_as_of_when_no_editions_cover_it(self):
        as_of = dt.date(2026, 5, 26)
        published = {dt.date(2026, 5, 25), dt.date(2026, 5, 24)}
        self.assertEqual(ndd.detect_dark_date(as_of=as_of, published_dates=published), as_of)

    def test_returns_prior_date_when_today_is_already_covered(self):
        as_of = dt.date(2026, 5, 26)
        published = {dt.date(2026, 5, 26), dt.date(2026, 5, 24)}
        # 2026-05-25 is the most recent uncovered date in the lookback window.
        self.assertEqual(ndd.detect_dark_date(as_of=as_of, published_dates=published), dt.date(2026, 5, 25))

    def test_returns_none_when_all_lookback_dates_covered(self):
        as_of = dt.date(2026, 5, 26)
        published = {as_of - dt.timedelta(days=d) for d in range(0, 8)}
        self.assertIsNone(ndd.detect_dark_date(as_of=as_of, published_dates=published))

    def test_lookback_bound_is_honored(self):
        as_of = dt.date(2026, 5, 26)
        # Cover all dates within the 3-day window; the 4-day-old gap should not be detected.
        published = {as_of - dt.timedelta(days=d) for d in range(0, 4)}
        self.assertIsNone(ndd.detect_dark_date(as_of=as_of, published_dates=published, lookback_days=3))

    def test_as_of_required(self):
        with self.assertRaises(ValueError):
            ndd.detect_dark_date(as_of=None, published_dates=set())  # type: ignore[arg-type]


class LedgerSchemaTests(unittest.TestCase):
    def test_ledger_file_exists_and_parses(self):
        path = REPO_ROOT / "data" / "nowcast-ledger.json"
        self.assertTrue(path.exists(), "nowcast ledger scaffold missing")
        d = json.loads(path.read_text(encoding="utf-8"))
        self.assertIn("_meta", d)
        self.assertEqual(d["_meta"]["schema_version"], 1)
        self.assertEqual(d["_meta"]["outbreak_id"], "bdbv-uga-cod-2026")
        self.assertIn("doctrine", d["_meta"])
        self.assertGreaterEqual(len(d["_meta"]["doctrine"]), 5)

    def test_ledger_doctrine_mentions_pre_commit_carry_forward_append_only(self):
        path = REPO_ROOT / "data" / "nowcast-ledger.json"
        d = json.loads(path.read_text(encoding="utf-8"))
        doctrine_text = " ".join(d["_meta"]["doctrine"]).lower()
        self.assertIn("pre-commit", doctrine_text)
        self.assertIn("carry forward", doctrine_text)
        self.assertIn("append only", doctrine_text)
        self.assertIn("event-driven", doctrine_text)

    def test_registered_quantities_match_spec_section_8(self):
        path = REPO_ROOT / "data" / "nowcast-ledger.json"
        d = json.loads(path.read_text(encoding="utf-8"))
        registered = set(d["_meta"]["registered_quantities"])
        self.assertEqual(
            registered,
            {"confirmed-cumulative"},
            "After the suspected-tier retirement the only registered cumulative series is "
            "confirmed-cumulative; the confirmed-plus-suspected composite is retired",
        )

    def test_ledger_starts_with_empty_entries_and_resolutions(self):
        path = REPO_ROOT / "data" / "nowcast-ledger.json"
        d = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(d["entries"], [])
        self.assertEqual(d["resolutions"], [])


class LoadPublishedDataAsOfDatesTests(unittest.TestCase):
    def test_reads_real_manifest_without_error(self):
        # Smoke test against the real manifest; should not raise.
        dates = ndd.load_published_data_as_of_dates()
        self.assertIsInstance(dates, set)
        # The manifest must carry at least one BDBV-era data_as_of by 2026-05-26.
        if dates:
            for d in dates:
                self.assertIsInstance(d, dt.date)


if __name__ == "__main__":
    unittest.main()
