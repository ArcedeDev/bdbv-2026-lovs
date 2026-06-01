# SPDX-License-Identifier: Apache-2.0
"""Tests for the snapshot-readiness detector in release_snapshot.

The detector decides whether a new dated snapshot is due. It is ready only when
the manifest holds a source dated after the last snapshot AND that reporting day
is complete: either it predates the outbreak-local today, or the outbreak-local
clock (Ituri Province, CAT = UTC+2) has passed the evening hour (18:00).
"""
from __future__ import annotations

import unittest
from datetime import datetime, timezone

import release_snapshot as rs


def _manifest(*dates: str) -> dict:
    return {"entries": [{"published_at": f"{d}T00:00:00Z"} for d in dates]}


class TestSnapshotReadiness(unittest.TestCase):

    def test_no_new_data_when_latest_equals_last_snapshot(self):
        verdict = rs.detect_snapshot_readiness(
            _manifest("2026-05-19", "2026-05-20"),
            "2026-05-20",
            datetime(2026, 5, 21, 20, 0, tzinfo=timezone.utc),
        )
        self.assertFalse(verdict["ready"])
        self.assertEqual(verdict["latest_source_date"], "2026-05-20")

    def test_new_prior_day_data_is_ready(self):
        # latest = 21 May (prior to local today 23 May) -> a completed day.
        verdict = rs.detect_snapshot_readiness(
            _manifest("2026-05-20", "2026-05-21"),
            "2026-05-20",
            datetime(2026, 5, 23, 6, 0, tzinfo=timezone.utc),
        )
        self.assertTrue(verdict["ready"])

    def test_today_data_holds_before_evening(self):
        # 14:00 UTC -> 16:00 CAT, before the 18:00 evening cutoff.
        verdict = rs.detect_snapshot_readiness(
            _manifest("2026-05-20", "2026-05-21"),
            "2026-05-20",
            datetime(2026, 5, 21, 14, 0, tzinfo=timezone.utc),
        )
        self.assertFalse(verdict["ready"])
        self.assertIn("day not complete", verdict["reason"])

    def test_today_data_ready_after_evening(self):
        # 16:30 UTC -> 18:30 CAT, evening reached.
        verdict = rs.detect_snapshot_readiness(
            _manifest("2026-05-20", "2026-05-21"),
            "2026-05-20",
            datetime(2026, 5, 21, 16, 30, tzinfo=timezone.utc),
        )
        self.assertTrue(verdict["ready"])

    def test_future_dated_source_holds(self):
        verdict = rs.detect_snapshot_readiness(
            _manifest("2026-05-25"),
            "2026-05-20",
            datetime(2026, 5, 21, 20, 0, tzinfo=timezone.utc),
        )
        self.assertFalse(verdict["ready"])

    def test_empty_manifest_not_ready(self):
        verdict = rs.detect_snapshot_readiness(
            {"entries": []},
            "2026-05-20",
            datetime(2026, 5, 21, 20, 0, tzinfo=timezone.utc),
        )
        self.assertFalse(verdict["ready"])

    def test_re_retrieval_uses_publication_date_not_retrieval_time(self):
        # An older report re-fetched today carries an old published_at but a fresh
        # retrieved_at. The detector must key off the report date (published_at), so
        # re-retrieving the 20 May report after midnight on the 21st does NOT read
        # as new 21 May data and does NOT trigger a snapshot.
        manifest = {
            "entries": [
                {"published_at": "2026-05-20T00:00:00Z", "retrieved_at": "2026-05-21T00:55:53Z"},
                {"published_at": "2026-05-19T00:00:00Z", "retrieved_at": "2026-05-21T00:00:00Z"},
            ]
        }
        verdict = rs.detect_snapshot_readiness(
            manifest, "2026-05-20", datetime(2026, 5, 21, 20, 0, tzinfo=timezone.utc)
        )
        self.assertFalse(verdict["ready"])
        self.assertEqual(verdict["latest_source_date"], "2026-05-20")

    def test_published_today_report_for_prior_data_day_triggers_today_snapshot(self):
        manifest = {
            "entries": [
                {
                    "published_at": "2026-05-23T00:00:00Z",
                    "retrieved_at": "2026-05-23T18:00:00Z",
                    "normalized_content": {
                        "data_as_of": "2026-05-22",
                        "date_rapportage": "2026-05-22T00:00:00+00:00",
                    },
                }
            ]
        }
        verdict = rs.detect_snapshot_readiness(
            manifest, "2026-05-22", datetime(2026, 5, 23, 18, 0, tzinfo=timezone.utc)
        )
        self.assertTrue(verdict["ready"])
        self.assertEqual(verdict["latest_source_date"], "2026-05-23")

    def test_non_triggering_cross_check_does_not_create_new_snapshot_day(self):
        manifest = {
            "entries": [
                {
                    "published_at": "2026-05-24T00:00:00Z",
                    "source_tier": "national_moh",
                    "normalized_content": {"publication_date": "2026-05-24"},
                },
                {
                    "published_at": "2026-05-25T00:00:00Z",
                    "source_tier": "regional_body",
                    "normalized_content": {
                        "publication_date": "2026-05-25",
                        "snapshot_trigger": False,
                        "model_use": "regional_cross_check_only",
                    },
                },
            ]
        }

        verdict = rs.detect_snapshot_readiness(
            manifest, "2026-05-24", datetime(2026, 5, 25, 18, 0, tzinfo=timezone.utc)
        )

        self.assertFalse(verdict["ready"])
        self.assertEqual(verdict["latest_source_date"], "2026-05-24")


if __name__ == "__main__":
    unittest.main()
