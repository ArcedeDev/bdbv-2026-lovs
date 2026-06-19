# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the read-only BDBV cycle-status composer."""
from __future__ import annotations

import hashlib
import json
import pathlib
import tempfile
import unittest

import cycle_status


def _fake_health(ready: bool = False) -> dict:
    return {
        "traffic_light": "yellow",
        "ready_for_public_release": ready,
        "issues": [{"code": "review_queue_nonempty", "severity": "review"}],
        "prep": {
            "release_check_returncode": 0,
            "website_sync_status": "skipped",
            "website_gates_status": "skipped",
        },
        "live_public_parity": {"status": "skipped"},
        "freshness": {
            "classified_review_queue": [
                {
                    "classification": "source_review_blocked",
                    "registry_id": "drc-moh-epidemie-dashboard",
                    "publisher": "DRC MoH",
                    "latest_detected_date": "2026-05-23",
                    "review_reasons": ["drc_moh_table_semantics_source_review"],
                    "extracted_counts": {"dashboard_zone_rows_confirmed_total": 19},
                },
                {
                    "classification": "watch_only",
                    "registry_id": "wikipedia-aggregator",
                    "publisher": "Wikipedia",
                    "latest_detected_date": "2026-05-24",
                    "review_reasons": ["count_tuple_differs_from_latest_archive"],
                    "extracted_counts": {"deaths": 216},
                },
            ]
        },
    }


def _fake_resolution() -> dict:
    return {
        "as_of": "2026-05-24",
        "summary": {
            "by_status": {"resolved_yes": 2, "resolved_no": 0, "pending": 10, "unscoreable_no_feed": 0},
            "mean_brier_resolved": 0.395349,
        },
        "points": [
            {"corridor": "bunia -> kampala-uga", "status": "resolved_yes", "brier": 0.389376},
            {"corridor": "rwampara -> kampala-uga", "status": "resolved_yes", "brier": 0.401322},
            {"corridor": "mongbwalu -> beni-cod", "status": "pending", "brier": None},
        ],
        "proposed_ledger_outcomes": {"advisory_not_written": True},
    }


class RoutingTests(unittest.TestCase):
    def test_known_classifications_route_to_nonempty_owner_action(self):
        for classification in (
            "source_review_required",
            "source_review_blocked",
            "fetch_blocked",
            "watch_only",
            "context_update_review",
        ):
            routed = cycle_status.route_review_item({"classification": classification, "registry_id": "x"})
            self.assertTrue(routed["owner_role"], classification)
            self.assertTrue(routed["action"], classification)
            self.assertEqual(routed["classification"], classification)

    def test_unknown_classification_falls_back_to_manual(self):
        routed = cycle_status.route_review_item({"classification": "totally_new", "registry_id": "x"})
        self.assertEqual(routed["owner_role"], "manual")

    def test_watch_only_action_forbids_promotion(self):
        routed = cycle_status.route_review_item({"classification": "watch_only"})
        self.assertIn("never be promoted", routed["action"])

    def test_context_update_action_forbids_count_routing(self):
        routed = cycle_status.route_review_item({"classification": "context_update_review"})
        self.assertIn("do not route as a count", routed["action"])


class CalibrationSummaryTests(unittest.TestCase):
    def test_summarize_ok_lists_resolved_and_marks_founder_gated(self):
        summary = cycle_status.summarize_calibration(_fake_resolution())
        self.assertEqual(summary["status"], "ok")
        self.assertEqual(summary["by_status"]["resolved_yes"], 2)
        self.assertEqual(len(summary["resolved"]), 2)
        self.assertEqual(summary["ledger_append"], "founder-gated")

    def test_missing_report_degrades(self):
        self.assertEqual(cycle_status.summarize_calibration(None), {"status": "no_report"})


class OpenDecisionTests(unittest.TestCase):
    def test_decisions_derived_from_inputs(self):
        decisions = cycle_status.open_human_decisions(_fake_health(ready=False), cycle_status.summarize_calibration(_fake_resolution()))
        kinds = {d["kind"] for d in decisions}
        self.assertIn("source_review", kinds)       # the blocked DRC dashboard
        self.assertIn("publication", kinds)          # ready_for_public_release False
        self.assertIn("calibration_ledger_append", kinds)  # resolved + advisory

    def test_clean_cycle_has_no_invented_decisions(self):
        clean = _fake_health(ready=True)
        clean["freshness"]["classified_review_queue"] = []
        decisions = cycle_status.open_human_decisions(clean, {"status": "no_report"})
        self.assertEqual(decisions, [])


class BuildTests(unittest.TestCase):
    def setUp(self):
        self._health_dir = cycle_status.HEALTH_DIR
        self._res_path = cycle_status.RESOLUTION_REPORT_PATH
        self._tmp = tempfile.TemporaryDirectory()
        cycle_status.HEALTH_DIR = pathlib.Path(self._tmp.name) / "health"
        cycle_status.HEALTH_DIR.mkdir(parents=True)
        cycle_status.RESOLUTION_REPORT_PATH = pathlib.Path(self._tmp.name) / "res.json"

    def tearDown(self):
        cycle_status.HEALTH_DIR = self._health_dir
        cycle_status.RESOLUTION_REPORT_PATH = self._res_path
        self._tmp.cleanup()

    def test_build_with_fixtures(self):
        (cycle_status.HEALTH_DIR / "bdbv-2026-2026-05-24-full-health.json").write_text(json.dumps(_fake_health()))
        cycle_status.RESOLUTION_REPORT_PATH.write_text(json.dumps(_fake_resolution()))
        status = cycle_status.build_cycle_status("2026-05-24")
        # Route + analytic date come from the current repo snapshot state
        # (read-only). The current snapshot's analytic as_of is 2026-06-17
        # after the reviewed SitRep #034 endpoint; its latest completed source
        # publication date is 2026-06-18, so publication freshness is ahead of
        # the analytic endpoint.
        self.assertEqual(status["publication_route"]["basis"], "latest_completed_source_publication_date")
        self.assertTrue(status["readiness"]["snapshot_due"])
        self.assertEqual(status["analytic_data_date"], "2026-06-17")
        self.assertTrue(status["health"]["report_present"])
        self.assertEqual(len(status["health"]["review_queue"]), 2)
        self.assertEqual(status["calibration"]["by_status"]["resolved_yes"], 2)

    def test_build_missing_health_degrades(self):
        cycle_status.RESOLUTION_REPORT_PATH.write_text(json.dumps(_fake_resolution()))
        status = cycle_status.build_cycle_status("2099-01-01")
        self.assertFalse(status["health"]["report_present"])
        self.assertEqual(status["health"]["review_queue"], [])
        # Still surfaces the founder-gated calibration append.
        self.assertTrue(any(d["kind"] == "calibration_ledger_append" for d in status["open_human_decisions"]))


class WriteSafetyTests(unittest.TestCase):
    def test_write_artifacts_leaves_ledger_unchanged(self):
        ledger = cycle_status.PROTECTED_PATHS[0]
        before = hashlib.sha256(ledger.read_bytes()).hexdigest()
        status = cycle_status.build_cycle_status("2026-05-24")
        with tempfile.TemporaryDirectory() as d:
            written = cycle_status.write_artifacts(status, pathlib.Path(d))
            self.assertTrue(pathlib.Path(written["json"]).exists())
            self.assertTrue(pathlib.Path(written["routing_plan"]).exists())
        after = hashlib.sha256(ledger.read_bytes()).hexdigest()
        self.assertEqual(before, after)

    def test_atomic_write_refuses_every_protected_path(self):
        for protected in cycle_status.PROTECTED_PATHS:
            with self.assertRaises(RuntimeError):
                cycle_status._atomic_write_text(protected, "should not write")

    def test_print_mode_writes_no_file(self):
        with tempfile.TemporaryDirectory() as d:
            rc = cycle_status.main(["--as-of", "2099-01-01", "--out-dir", d, "--print"])
            self.assertEqual(rc, 0)
            self.assertEqual(list(pathlib.Path(d).glob("*.json")), [])


if __name__ == "__main__":
    unittest.main()
