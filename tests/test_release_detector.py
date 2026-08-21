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
from lovs import source_dates


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

    def test_republication_of_an_ingested_cut_does_not_create_a_snapshot_day(self):
        """A partner republishing a cut we already hold is not a new knowledge state.

        Regression for 2026-08-19. The INRB/INSP/UMIE repository released
        build-2026-08-19-13ed921 ("Add sitrep 93") carrying data_as_of 2026-08-15, the
        same cut already published in the 16 August snapshot. Keyed on publication date
        alone this read as a new snapshot day, and a 19 August snapshot was cut in which
        every count was carried forward and the corridor and burden surfaces moved on
        nothing but the clock.
        """
        manifest = {
            "entries": [
                {
                    "source_id": "inrb-sitrep-093-2026-08-15",
                    "published_at": "2026-08-16T00:00:00Z",
                    "source_tier": "national_moh",
                    "normalized_content": {
                        "publication_date": "2026-08-16",
                        "data_as_of": "2026-08-15",
                        "model_use": "reviewed_sitrep_promotion_json",
                    },
                },
                {
                    "source_id": "inrb-umie-ebola-drc-2026-build-2026-08-19-13ed921",
                    "published_at": "2026-08-19T08:08:00Z",
                    "source_tier": "national_moh",
                    "normalized_content": {
                        "publication_date": "2026-08-19",
                        "data_as_of": "2026-08-15",
                        "snapshot_trigger": True,
                    },
                },
            ]
        }

        verdict = rs.detect_snapshot_readiness(
            manifest, "2026-08-16", datetime(2026, 8, 19, 18, 0, tzinfo=timezone.utc)
        )

        self.assertFalse(verdict["ready"])
        self.assertEqual(verdict["latest_source_date"], "2026-08-16")
        # Named, not silently dropped: the operator can see what looked new and why it
        # was not treated as new.
        self.assertEqual(len(verdict["uninformative_sources"]), 1)
        self.assertIn("build-2026-08-19-13ed921", verdict["uninformative_sources"][0])
        self.assertIn("carry no data beyond the covered cut (2026-08-15)", verdict["reason"])

    def test_a_genuinely_newer_cut_still_creates_a_snapshot_day(self):
        """The guard must not suppress a real edition, including the tightest case.

        A SitRep published the day after the last snapshot, whose data date equals that
        snapshot's publication clock, is one day newer than the cut actually covered and
        must still route. Comparing against the snapshot's publication clock instead of
        its data coverage would drop this edition.
        """
        manifest = {
            "entries": [
                {
                    "source_id": "inrb-sitrep-093-2026-08-15",
                    "published_at": "2026-08-16T00:00:00Z",
                    "source_tier": "national_moh",
                    "normalized_content": {
                        "publication_date": "2026-08-16",
                        "data_as_of": "2026-08-15",
                        "model_use": "reviewed_sitrep_promotion_json",
                    },
                },
                {
                    "source_id": "inrb-sitrep-094-2026-08-16",
                    "published_at": "2026-08-17T00:00:00Z",
                    "source_tier": "national_moh",
                    "normalized_content": {
                        "publication_date": "2026-08-17",
                        "data_as_of": "2026-08-16",
                        "model_use": "reviewed_sitrep_promotion_json",
                    },
                },
            ]
        }

        verdict = rs.detect_snapshot_readiness(
            manifest, "2026-08-16", datetime(2026, 8, 17, 18, 0, tzinfo=timezone.utc)
        )

        self.assertTrue(verdict["ready"])
        self.assertEqual(verdict["latest_source_date"], "2026-08-17")

    def test_detection_capture_announces_an_edition_without_defining_coverage(self):
        """A detection capture stamps its own post date as its data date.

        The WordPress capture of a SitRep post exists to say "a new edition is up". If it
        were allowed to define analytic coverage, coverage would read as the post date
        rather than the edition's report date, which is a day earlier, and the very
        edition the detection points at would then look like it adds nothing.
        """
        detection = {
            "source_id": "insp-wordpress-sitrep-n093-api-wp25339-2026-08-16",
            "published_at": "2026-08-16T00:00:00Z",
            "source_tier": "national_moh",
            "normalized_content": {
                "publication_date": "2026-08-16",
                "data_as_of": "2026-08-16",
                "model_use": (
                    "detection_and_private_staging_only_until_reviewed_sitrep_promotion_json"
                ),
            },
        }
        promotion = {
            "source_id": "inrb-sitrep-093-2026-08-15",
            "published_at": "2026-08-16T00:00:00Z",
            "source_tier": "national_moh",
            "normalized_content": {
                "publication_date": "2026-08-16",
                "data_as_of": "2026-08-15",
                "model_use": "reviewed_sitrep_promotion_json",
            },
        }
        entries = [detection, promotion]
        # Coverage comes from the promotion (2026-08-15), never the detection (2026-08-16).
        self.assertEqual(
            source_dates.data_coverage_through(entries, "2026-08-16"), "2026-08-15"
        )
        # And the detection is still free to advance the route on its own publication date.
        self.assertTrue(source_dates.source_triggers_snapshot(detection))

    def test_batch_release_does_not_read_its_own_sources_as_new(self):
        """The released snapshot declares its coverage; the detector must not infer it.

        On 2026-08-20 INSP released SitReps 94, 95 and 96 together after four silent
        days, and the resulting cut sits at data day 2026-08-18 while every source that
        built it was published on 08-20. Inferring coverage from the snapshot's as_of
        cannot resolve that: filtering the manifest by publication date reports coverage
        as 2026-08-15 and the three editions then read as new data against the very
        snapshot they built, which is how a knowledge-state artifact gets cut twice on
        one knowledge state.
        """
        entries = [
            {
                "source_id": f"inrb-sitrep-{n:03d}-2026-08-{day}",
                "published_at": "2026-08-20T00:00:00Z",
                "source_tier": "national_moh",
                "normalized_content": {
                    "publication_date": "2026-08-20",
                    "data_as_of": f"2026-08-{day}",
                    "model_use": "reviewed_sitrep_promotion_json",
                },
            }
            for n, day in ((94, "16"), (95, "17"), (96, "18"))
        ]
        manifest = {"entries": entries}
        summary = {
            "as_of": "2026-08-18T23:59:59Z",
            "date_semantics": {
                "source_clocks": {"headline_count_endpoint": "inrb-sitrep-096-2026-08-18"}
            },
        }
        covered = rs.released_coverage_through(summary, manifest)
        self.assertEqual("2026-08-18", covered)
        # Inference from as_of alone cannot see this cut at all: every source that
        # built it was published after its own as_of, so the publication-date filter
        # selects nothing and coverage reads as unknown, which lets all three route.
        self.assertEqual("", source_dates.data_coverage_through(entries, "2026-08-18"))
        for entry in entries:
            self.assertTrue(source_dates.source_adds_data_beyond(entry, ""))
        verdict = rs.detect_snapshot_readiness(
            manifest,
            "2026-08-18",
            datetime(2026, 8, 20, 20, 0, tzinfo=timezone.utc),
            covered_through=covered,
        )
        self.assertFalse(verdict["ready"])
        self.assertEqual(3, len(verdict["uninformative_sources"]))

    def test_declared_coverage_still_routes_a_genuinely_newer_edition(self):
        """Declaring coverage must not blind the detector to the next real cut."""
        entries = [
            {
                "source_id": "inrb-sitrep-096-2026-08-18",
                "published_at": "2026-08-20T00:00:00Z",
                "source_tier": "national_moh",
                "normalized_content": {
                    "publication_date": "2026-08-20",
                    "data_as_of": "2026-08-18",
                    "model_use": "reviewed_sitrep_promotion_json",
                },
            },
            {
                "source_id": "inrb-sitrep-097-2026-08-19",
                "published_at": "2026-08-21T00:00:00Z",
                "source_tier": "national_moh",
                "normalized_content": {
                    "publication_date": "2026-08-21",
                    "data_as_of": "2026-08-19",
                    "model_use": "reviewed_sitrep_promotion_json",
                },
            },
        ]
        verdict = rs.detect_snapshot_readiness(
            {"entries": entries},
            "2026-08-18",
            datetime(2026, 8, 21, 20, 0, tzinfo=timezone.utc),
            covered_through="2026-08-18",
        )
        self.assertTrue(verdict["ready"])
        self.assertEqual("2026-08-21", verdict["latest_source_date"])

    def test_missing_declaration_falls_back_to_inferred_coverage(self):
        """A snapshot that names no endpoint keeps the historical behaviour."""
        self.assertEqual("", rs.released_coverage_through({}, {"entries": []}))

    def test_detection_capture_cannot_advance_coverage_it_may_not_define(self):
        """A detection capture announces an edition; it never carries one.

        It stamps its own post date as its data date, so on a batch-release day it
        claims to reach past every edition it points at. Excluded from DEFINING
        coverage but allowed to claim it ADVANCES coverage, six such captures voted a
        snapshot due on 2026-08-20 whose reviewed promotions carried nothing past the
        cut already released.
        """
        detection = {
            "source_id": "insp-wordpress-sitrep-n096-pdf-media25354-2026-08-20",
            "published_at": "2026-08-20T00:00:00Z",
            "source_tier": "national_moh",
            "normalized_content": {
                "publication_date": "2026-08-20",
                "data_as_of": "2026-08-20",
                "model_use": (
                    "detection_and_private_staging_only_until_reviewed_sitrep_promotion_json"
                ),
            },
        }
        self.assertFalse(source_dates.source_defines_data_coverage(detection))
        self.assertFalse(source_dates.source_adds_data_beyond(detection, "2026-08-18"))
        # Still archived, still routable, just not evidence of new data.
        self.assertTrue(source_dates.source_triggers_snapshot(detection))

    def test_a_source_without_a_data_date_keeps_its_historical_behaviour(self):
        """Publication-clock-only sources stay informative.

        Some sources publish a count tagged only with a publication date (the DRC MoH
        dashboard aggregate is the canonical one; see lovs.publication_clock_contract).
        There is no data date to compare, so the guard must not silently swallow them.
        """
        entry = {"normalized_content": {"publication_date": "2026-08-19"}}
        self.assertTrue(source_dates.source_adds_data_beyond(entry, "2026-08-15"))


    def test_promotion_gate_uses_primary_source_report_date_for_publication_day_snapshot(self):
        summary = {
            "as_of": "2026-08-14T23:59:59Z",
            "data_as_of": "2026-08-14",
            "date_semantics": {
                "source_clocks": {
                    "headline_count_endpoint": "inrb-sitrep-091-2026-08-13"
                }
            },
        }
        manifest = {
            "entries": [
                {
                    "source_id": "inrb-sitrep-091-2026-08-13",
                    "published_at": "2026-08-14T00:00:00Z",
                    "normalized_content": {"data_as_of": "2026-08-13"},
                }
            ]
        }

        self.assertEqual(
            "2026-08-13",
            rs._promotion_gate_required_through(summary, manifest),
        )


if __name__ == "__main__":
    unittest.main()
