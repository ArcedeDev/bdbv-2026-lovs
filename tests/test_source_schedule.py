# SPDX-License-Identifier: Apache-2.0
"""Tests for scheduled source-prep cadence."""
from __future__ import annotations

import json
import os
import pathlib
import tempfile
import unittest
from unittest import mock

import release_snapshot
import source_ingest
from lovs import source_registry_gate
from lovs import source_schedule


class TestSourceSchedule(unittest.TestCase):

    def setUp(self) -> None:
        self.registry = source_registry_gate.load_json(
            source_registry_gate.DEFAULT_REGISTRY_PATH
        )
        self.schedule = source_schedule.build_schedule(self.registry)

    def _slots_for(self, registry_id: str) -> set[str]:
        row = next(
            source for source in self.schedule["sources"]
            if source["registry_id"] == registry_id
        )
        return set(row["slots"])

    def test_default_registry_schedule_validates_and_assigns_every_source(self):
        summary = source_schedule.validate_schedule_policy(self.registry)

        self.assertEqual(5, summary["slots"])
        self.assertEqual(len(self.registry["sources"]), summary["assigned_sources"])
        self.assertTrue(all(source["slots"] for source in self.schedule["sources"]))
        self.assertTrue(all(
            "bdbv_daily_prep_cron.sh" in slot["command"]
            and "$(date -u +\\%F)" in slot["command"]
            and "--build-review-snapshot" in slot["command"]
            and "--website-gates" in slot["command"]
            for slot in self.schedule["slots"]
        ))

    def test_cron_wrapper_is_executable_and_release_staged(self):
        wrapper = pathlib.Path("tools/bdbv_daily_prep_cron.sh")

        self.assertTrue(wrapper.exists())
        self.assertTrue(os.access(wrapper, os.X_OK))
        self.assertNotIn("/Users/", wrapper.read_text(encoding="utf-8"))
        self.assertIn(str(wrapper), release_snapshot.PUBLIC_RELEASE_PATHS)
        self.assertIn("daily_snapshot_prep.py", release_snapshot.PUBLIC_RELEASE_PATHS)

    def test_daily_prep_has_no_default_private_earth_agent(self):
        text = pathlib.Path("daily_snapshot_prep.py").read_text(encoding="utf-8")

        self.assertIn("LOVS_EARTH_AGENT_ID", text)
        self.assertNotIn("bdbv-" + "snapshot-prep-manager", text)

    def test_daily_slots_prioritize_primary_official_sources(self):
        slots = self._slots_for("drc-moh-epidemie-dashboard")

        self.assertIn("africa_morning_primary", slots)
        self.assertIn("africa_midday_official", slots)
        self.assertIn("africa_evening_readiness", slots)
        self.assertNotIn("weekly_covariate_context", slots)

    def test_cdc_is_us_timed_crosscheck_not_morning_primary(self):
        slots = self._slots_for("cdc-situation-summary")

        self.assertIn("africa_midday_official", slots)
        self.assertIn("africa_evening_readiness", slots)
        self.assertIn("americas_evening_crosscheck", slots)
        self.assertNotIn("africa_morning_primary", slots)

    def test_monthly_covariates_are_weekly_context_only(self):
        self.assertEqual(
            {"weekly_covariate_context"},
            self._slots_for("flowminder-drc-health-zone-popmob"),
        )

    def test_air_official_social_sources_are_high_frequency_candidates(self):
        slots = self._slots_for("who-dg-official-social")

        self.assertIn("africa_morning_primary", slots)
        self.assertIn("africa_midday_official", slots)
        self.assertIn("africa_evening_readiness", slots)
        self.assertIn("americas_evening_crosscheck", slots)

    def test_radio_okapi_stays_watch_review_not_count_input(self):
        source = next(
            source for source in self.schedule["sources"]
            if source["registry_id"] == "radio-okapi-bdbv-watch"
        )

        self.assertTrue(source["review_only"])
        self.assertEqual("watch_review", source["schedule_group"])
        self.assertNotIn("counts", source["feeds"])

    def test_live_check_slot_fetches_only_slot_sources(self):
        expected = set(source_schedule.source_ids_for_slot(
            self.registry,
            "weekly_covariate_context",
        ))
        seen: list[str] = []

        def fake_live_source_check(source, manifest, as_of):
            seen.append(source["registry_id"])
            return {
                "registry_id": source["registry_id"],
                "title": source.get("title"),
                "publisher": source.get("publisher"),
                "source_tier": source.get("source_tier"),
                "url": source.get("landing_url"),
                "archive_target": source.get("archive_target"),
                "latest_known": source.get("latest_known"),
                "newest_archived": None,
                "latest_archived_source_id": None,
                "latest_archived_counts": {},
                "retrieved_at": "2026-05-24T00:00:00Z",
                "status": "fetched",
                "http_status": 200,
                "content_type": "application/json",
                "content_length": 2,
                "content_hash": "0" * 64,
                "detected_dates": [],
                "latest_detected_date": None,
                "extracted_counts": {},
                "needs_review": False,
                "review_reasons": [],
            }

        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = pathlib.Path(tmpdir) / "freshness.json"
            with mock.patch.object(
                source_ingest,
                "live_source_check",
                side_effect=fake_live_source_check,
            ):
                exit_code = source_ingest.live_check(
                    "2026-05-24",
                    out_path,
                    slot_id="weekly_covariate_context",
                )

            self.assertEqual(0, exit_code)
            self.assertEqual(expected, set(seen))
            payload = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertEqual("weekly_covariate_context", payload["slot_id"])
            self.assertEqual(len(expected), payload["summary"]["checked"])

    def test_live_check_default_output_is_slot_specific(self):
        expected = source_ingest.FRESHNESS_DIR / "bdbv-2026-2026-05-24-africa_morning_primary.json"

        with tempfile.TemporaryDirectory() as tmpdir:
            freshness_dir = pathlib.Path(tmpdir)
            with mock.patch.object(source_ingest, "FRESHNESS_DIR", freshness_dir):
                with mock.patch.object(
                    source_ingest,
                    "live_source_check",
                    return_value={
                        "registry_id": "stub",
                        "title": "Stub",
                        "publisher": "Stub",
                        "source_tier": "national_moh",
                        "url": "https://example.test",
                        "archive_target": "outbreak_manifest",
                        "latest_known": {},
                        "newest_archived": None,
                        "latest_archived_source_id": None,
                        "latest_archived_counts": {},
                        "retrieved_at": "2026-05-24T00:00:00Z",
                        "status": "fetched",
                        "http_status": 200,
                        "content_type": "text/html",
                        "content_length": 2,
                        "content_hash": "0" * 64,
                        "detected_dates": [],
                        "latest_detected_date": None,
                        "extracted_counts": {},
                        "needs_review": False,
                        "review_reasons": [],
                    },
                ):
                    exit_code = source_ingest.live_check(
                        "2026-05-24",
                        slot_id="africa_morning_primary",
                    )

            self.assertEqual(0, exit_code)
            self.assertTrue((freshness_dir / expected.name).exists())

    def test_air_preferred_live_report_marks_capture_backend(self):
        source = next(
            source for source in self.registry["sources"]
            if source["registry_id"] == "who-dg-official-social"
        )

        def fake_fetch(url: str):
            self.assertEqual(source["landing_url"], url)
            return (
                b"<html><body>23 May 2026 Uganda reported three new confirmed Ebola cases.</body></html>",
                200,
                "text/html",
            )

        row = source_ingest.live_source_check(
            source,
            {"entries": []},
            "2026-05-24",
            fetch_fn=fake_fetch,
        )

        self.assertEqual("air_preferred", row["extractor_backend"])
        self.assertEqual("air_preferred", row["capture_backend"])
        self.assertIn("same manifest review path", row["capture_note"])


if __name__ == "__main__":
    unittest.main()
