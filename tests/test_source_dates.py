# SPDX-License-Identifier: Apache-2.0
"""Tests for source date semantics."""
from __future__ import annotations

import unittest

from lovs import source_dates


class TestSourceDates(unittest.TestCase):

    def test_report_date_precedes_publication_for_plotting(self):
        entry = {
            "published_at": "2026-05-23T00:00:00Z",
            "retrieved_at": "2026-05-23T18:00:00Z",
            "normalized_content": {
                "date_rapportage": "2026-05-22T00:00:00+00:00",
                "date_publication": "2026-05-23T00:00:00+00:00",
            },
        }

        self.assertEqual(source_dates.source_report_date(entry), "2026-05-22")
        self.assertEqual(source_dates.source_data_date(entry), "2026-05-22")
        self.assertEqual(source_dates.source_publication_date(entry), "2026-05-23")
        self.assertEqual(source_dates.source_retrieval_date(entry), "2026-05-23")

    def test_data_date_falls_back_to_publication_when_no_report_clock_exists(self):
        entry = {
            "published_at": "2026-05-21T00:00:00Z",
            "normalized_content": {},
        }

        self.assertIsNone(source_dates.source_report_date(entry))
        self.assertEqual(source_dates.source_data_date(entry), "2026-05-21")

    def test_explicitly_missing_report_date_does_not_fall_back_to_publication(self):
        entry = {
            "published_at": "2026-05-24T00:00:00Z",
            "normalized_content": {
                "data_as_of": None,
                "date_rapportage": None,
                "publication_date": "2026-05-24",
            },
        }

        self.assertIsNone(source_dates.source_report_date(entry))
        self.assertIsNone(source_dates.source_data_date(entry))
        self.assertEqual(source_dates.source_publication_date(entry), "2026-05-24")

    def test_entries_for_snapshot_filters_by_publication_availability(self):
        entries = [
            {
                "source_id": "old",
                "published_at": "2026-05-22T12:00:00Z",
                "retrieved_at": "2026-05-22T13:00:00Z",
            },
            {
                "source_id": "future",
                "published_at": "2026-05-23T00:00:00Z",
                "retrieved_at": "2026-05-23T01:00:00Z",
            },
            {
                "source_id": "undated",
                "normalized_content": {},
            },
        ]

        visible = source_dates.entries_for_snapshot(entries, "2026-05-22")

        self.assertEqual(["old"], [entry["source_id"] for entry in visible])

    def test_context_and_explicit_non_trigger_sources_do_not_advance_snapshot(self):
        self.assertFalse(source_dates.source_triggers_snapshot({
            "published_at": "2026-05-25T00:00:00Z",
            "normalized_content": {"model_use": "context_only"},
        }))
        self.assertFalse(source_dates.source_triggers_snapshot({
            "published_at": "2026-05-25T00:00:00Z",
            "normalized_content": {"snapshot_trigger": False},
        }))
        self.assertFalse(source_dates.source_triggers_snapshot({
            "published_at": "2026-05-25T00:00:00Z",
            "source_tier": "aggregator",
            "normalized_content": {},
        }))
        self.assertTrue(source_dates.source_triggers_snapshot({
            "published_at": "2026-05-25T00:00:00Z",
            "source_tier": "national_moh",
            "normalized_content": {},
        }))


if __name__ == "__main__":
    unittest.main()
