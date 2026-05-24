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

    def test_data_date_falls_back_to_publication_when_no_report_date(self):
        entry = {
            "published_at": "2026-05-21T00:00:00Z",
            "normalized_content": {},
        }

        self.assertIsNone(source_dates.source_report_date(entry))
        self.assertEqual(source_dates.source_data_date(entry), "2026-05-21")

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


if __name__ == "__main__":
    unittest.main()
