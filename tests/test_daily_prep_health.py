# SPDX-License-Identifier: Apache-2.0
"""Tests for daily snapshot-prep health reports."""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import tempfile
import unittest
from unittest import mock

import release_snapshot
from lovs import daily_prep_health


class TestDailyPrepHealth(unittest.TestCase):
    def test_classifies_review_rows_by_operational_boundary(self):
        self.assertEqual(
            "source_review_blocked",
            daily_prep_health.classify_review_row({
                "needs_review": True,
                "review_reasons": ["drc_moh_table_semantics_source_review"],
                "extracted_counts": {"cases_confirmed": 3},
            }),
        )
        self.assertEqual(
            "model_eligible_after_review",
            daily_prep_health.classify_review_row({
                "needs_review": True,
                "review_reasons": ["detected_date_newer_than_archive"],
                "source_tier": "official_cdc",
                "archive_target": "outbreak_manifest",
                "extracted_counts": {"cases_confirmed": 83},
            }),
        )
        self.assertEqual(
            "watch_only",
            daily_prep_health.classify_review_row({
                "needs_review": True,
                "source_tier": "aggregator",
                "archive_target": "watch_list",
                "review_reasons": ["detected_date_newer_than_archive"],
            }),
        )
        self.assertEqual(
            "context_update_review",
            daily_prep_health.classify_review_row({
                "needs_review": True,
                "feeds": ["travel_monitoring", "public_guidance"],
                "review_reasons": ["context_update_date_newer_than_archive"],
            }),
        )
        self.assertEqual(
            "fetch_blocked",
            daily_prep_health.classify_review_row({
                "status": "fetch_failed",
                "needs_review": True,
            }),
        )
        self.assertEqual(
            "no_review_required",
            daily_prep_health.classify_review_row({"needs_review": False, "status": "fetched"}),
        )

    def test_health_report_green_when_artifacts_and_live_public_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            freshness = root / "freshness"
            prep = root / "prep"
            health = root / "health"
            public = root / "public"
            freshness.mkdir()
            prep.mkdir()
            public.mkdir()
            for name in daily_prep_health.PUBLIC_DATASET_ARTIFACTS:
                (public / name).write_bytes(f"artifact:{name}".encode("utf-8"))
            (freshness / "bdbv-2026-2026-05-24.json").write_text(
                json.dumps({"summary": {"checked": 1}, "sources": [
                    {"registry_id": "who", "status": "fetched", "needs_review": False}
                ]}),
                encoding="utf-8",
            )
            (prep / "bdbv-2026-2026-05-24-full-prep.json").write_text(
                json.dumps({
                    "release_check": {"returncode": 0},
                    "website_sync": {"status": "ok"},
                    "website_gates": {"status": "ok"},
                    "earth_journal": {"status": "ok"},
                    "auto_pulled": [],
                }),
                encoding="utf-8",
            )

            def fake_fetch(url: str) -> bytes:
                return (public / pathlib.Path(url).name).read_bytes()

            with mock.patch.object(daily_prep_health, "FRESHNESS_DIR", freshness), \
                    mock.patch.object(daily_prep_health, "PREP_DIR", prep), \
                    mock.patch.object(daily_prep_health, "HEALTH_DIR", health), \
                    mock.patch.object(daily_prep_health, "PUBLIC_DATASET_DIR", public), \
                    mock.patch.object(daily_prep_health, "REPO_ROOT", root):
                report = daily_prep_health.build_health_report(
                    "2026-05-24",
                    check_live_public=True,
                    fetch_fn=fake_fetch,
                    now=dt.datetime(2026, 5, 24, 12, tzinfo=dt.timezone.utc),
                )
                path = daily_prep_health.write_health_report(report)

            self.assertEqual("green", report["traffic_light"])
            self.assertTrue(report["ready_for_public_release"])
            self.assertEqual("ok", report["live_public_parity"]["status"])
            self.assertTrue(path.exists())

    def test_health_report_red_on_website_sync_or_live_public_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            freshness = root / "freshness"
            prep = root / "prep"
            public = root / "public"
            freshness.mkdir()
            prep.mkdir()
            public.mkdir()
            for name in daily_prep_health.PUBLIC_DATASET_ARTIFACTS:
                (public / name).write_bytes(b"local")
            (freshness / "bdbv-2026-2026-05-24-africa_midday_official.json").write_text(
                json.dumps({"summary": {"checked": 1}, "sources": []}),
                encoding="utf-8",
            )
            (prep / "bdbv-2026-2026-05-24-africa_midday_official-prep.json").write_text(
                json.dumps({
                    "release_check": {"returncode": 0},
                    "website_sync": {"status": "failed"},
                    "website_gates": None,
                    "auto_pulled": [],
                }),
                encoding="utf-8",
            )

            with mock.patch.object(daily_prep_health, "FRESHNESS_DIR", freshness), \
                    mock.patch.object(daily_prep_health, "PREP_DIR", prep), \
                    mock.patch.object(daily_prep_health, "PUBLIC_DATASET_DIR", public), \
                    mock.patch.object(daily_prep_health, "REPO_ROOT", root):
                report = daily_prep_health.build_health_report(
                    "2026-05-24",
                    "africa_midday_official",
                    check_live_public=True,
                    fetch_fn=lambda _url: b"remote",
                    now=dt.datetime(2026, 5, 24, 12, tzinfo=dt.timezone.utc),
                )

            self.assertEqual("red", report["traffic_light"])
            self.assertFalse(report["ready_for_public_release"])
            self.assertIn("website_sync_failed", {issue["code"] for issue in report["issues"]})
            self.assertIn("live_public_parity_failed", {issue["code"] for issue in report["issues"]})

    def test_live_public_candidate_mismatch_is_review_when_sync_intentionally_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            freshness = root / "freshness"
            prep = root / "prep"
            public = root / "public"
            freshness.mkdir()
            prep.mkdir()
            public.mkdir()
            for name in daily_prep_health.PUBLIC_DATASET_ARTIFACTS:
                (public / name).write_bytes(b"local-candidate")
            (freshness / "bdbv-2026-2026-05-25.json").write_text(
                json.dumps({"summary": {"checked": 1}, "sources": []}),
                encoding="utf-8",
            )
            (prep / "bdbv-2026-2026-05-25-full-prep.json").write_text(
                json.dumps({
                    "release_check": {"returncode": 0},
                    "website_sync": {
                        "status": "skipped",
                        "reason": (
                            "no new completed publication-state snapshot; preserving "
                            "the existing website route instead of overwriting it with "
                            "the current analytic output"
                        ),
                        "snapshot_date": "2026-05-24",
                        "basis": "analytic_as_of_no_new_completed_source_publication",
                    },
                    "website_gates": None,
                    "auto_pulled": [],
                }),
                encoding="utf-8",
            )

            with mock.patch.object(daily_prep_health, "FRESHNESS_DIR", freshness), \
                    mock.patch.object(daily_prep_health, "PREP_DIR", prep), \
                    mock.patch.object(daily_prep_health, "PUBLIC_DATASET_DIR", public), \
                    mock.patch.object(daily_prep_health, "REPO_ROOT", root):
                report = daily_prep_health.build_health_report(
                    "2026-05-25",
                    check_live_public=True,
                    fetch_fn=lambda _url: b"live-public-route",
                    now=dt.datetime(2026, 5, 25, 12, tzinfo=dt.timezone.utc),
                )

            codes = {issue["code"] for issue in report["issues"]}
            self.assertEqual("yellow", report["traffic_light"])
            self.assertFalse(report["ready_for_public_release"])
            self.assertEqual("failed", report["live_public_parity"]["status"])
            self.assertTrue(report["live_public_parity"]["expected_mismatch"])
            self.assertIn("live_public_candidate_not_synced", codes)
            self.assertNotIn("live_public_parity_failed", codes)

    def test_health_code_is_release_staged(self):
        self.assertIn(".gitignore", release_snapshot.PUBLIC_RELEASE_PATHS)
        self.assertIn("lovs", release_snapshot.PUBLIC_RELEASE_PATHS)
        self.assertIn("daily_snapshot_prep.py", release_snapshot.PUBLIC_RELEASE_PATHS)


if __name__ == "__main__":
    unittest.main()
