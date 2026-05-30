# SPDX-License-Identifier: Apache-2.0
"""Tests for sanitized public-health export artifacts."""
from __future__ import annotations

import csv
import hashlib
import json
import unittest
from pathlib import Path

from lovs import public_exports


REPO_ROOT = Path(__file__).resolve().parents[1]


class TestPublicExports(unittest.TestCase):
    def test_public_artifacts_are_current(self):
        self.assertEqual([], public_exports.check_public_artifacts())

    def test_public_snapshot_contains_partner_relevant_fields(self):
        snapshot = json.loads((REPO_ROOT / "data/public_snapshot.json").read_text())
        self.assertEqual("public_source_snapshot", snapshot["snapshot_role"])
        self.assertEqual("bdbv-uga-cod-2026", snapshot["outbreak_id"])
        self.assertEqual("2026-05-26", snapshot["data_as_of"])
        self.assertIn("reported_counts", snapshot)
        self.assertIn("affected_zones", snapshot)
        self.assertIn("zone_attributed_counts", snapshot)
        self.assertIn("source_review_geographies", snapshot)
        self.assertIn("source_ids", snapshot)
        self.assertIn("limitations", snapshot)
        self.assertIn("confirmed", snapshot["reported_counts"])
        self.assertIn("bunia", snapshot["affected_zones"])

    def test_public_snapshot_excludes_sensitive_model_fields(self):
        snapshot = json.loads((REPO_ROOT / "data/public_snapshot.json").read_text())
        self.assertEqual([], public_exports.public_snapshot_findings(snapshot))
        text = json.dumps(snapshot, sort_keys=True)
        forbidden_terms = [
            "analysis_dependency_audit",
            "calibration_blocks",
            "calibration_clock",
            "corridors",
            "gamma_shape_rate",
            "mode_b_hypotheses",
            "per_zone_under_ascertainment_bands",
            "risk_adj_lower_50",
            "risk_raw_upper_50",
        ]
        for term in forbidden_terms:
            self.assertNotIn(term, text)

    def test_reported_counts_include_public_authority_sources(self):
        with (REPO_ROOT / "data/public_reported_counts.csv").open() as handle:
            rows = list(csv.DictReader(handle))
        source_ids = {row["source_id"] for row in rows}
        metrics = {row["metric"] for row in rows}
        self.assertIn("who-don602-2026-05-15-live", source_ids)
        self.assertIn("africa-cdc-phecs-2026-05-18-live", source_ids)
        self.assertIn("confirmed_cases", metrics)
        self.assertIn("suspected_cases", metrics)
        self.assertIn("deaths", metrics)

    def test_zone_counts_publish_source_attributed_health_zone_rows(self):
        with (REPO_ROOT / "data/public_zone_counts_2026-05-26.csv").open() as handle:
            rows = list(csv.DictReader(handle))
        by_zone = {row["zone_id"]: row for row in rows}
        self.assertEqual(18, len(rows))
        self.assertEqual("36", by_zone["bunia"]["confirmed"])
        self.assertEqual("279", by_zone["bunia"]["suspected"])
        self.assertEqual("inrb-umie-ebola-drc-2026-build-2026-05-28-bb8b7d5", by_zone["bunia"]["source_id"])

    def test_release_manifest_hashes_public_outputs(self):
        manifest = json.loads((REPO_ROOT / "data/release_manifest.json").read_text())
        paths = {row["path"] for row in manifest["artifacts"]}
        source_inputs = {row["path"] for row in manifest["source_inputs"]}
        self.assertIn("data/public_export_source.json", source_inputs)
        self.assertIn("data/public_source_manifest.json", source_inputs)
        self.assertIn("data/public_calibration_commitments.json", source_inputs)
        self.assertNotIn("data/live-bdbv-2026-output.json", source_inputs)
        self.assertNotIn("data/bundibugyo-2026/manifest.json", source_inputs)
        self.assertIn("data/public_calibration_status.json", paths)
        self.assertIn("data/public_calibration_ledger.csv", paths)
        self.assertIn("data/public_precommitment_targets.csv", paths)
        self.assertIn("data/public_blindspots.json", paths)
        self.assertIn("data/public_latency_observatory.csv", paths)
        self.assertIn("data/public_nowcast_status.json", paths)
        self.assertIn("data/public_snapshot.json", paths)
        self.assertIn("data/public_reported_counts.csv", paths)
        self.assertIn("data/public_zone_counts_2026-05-26.csv", paths)
        self.assertIn("READONLY_INTERFACE_PUBLIC.md", paths)
        self.assertIn("CALIBRATION_LEDGER_PUBLIC.md", paths)
        self.assertIn("METHODOLOGY_PUBLIC.md", paths)

    def test_public_calibration_ledger_is_accountability_only(self):
        with (REPO_ROOT / "data/public_calibration_ledger.csv").open() as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(15, len(rows))
        self.assertEqual("bdbv-2026-cal-001", rows[0]["ledger_id"])
        self.assertEqual("open", {row["status"] for row in rows}.pop())
        self.assertIn("commitment_hash", rows[0])
        forbidden_columns = {
            "risk_adj_50",
            "risk_raw_lower_50",
            "risk_raw_upper_50",
            "feature_weights",
            "posterior_parameters",
            "hypothesis_id",
            "block_id",
        }
        self.assertTrue(forbidden_columns.isdisjoint(rows[0].keys()))

    def test_public_calibration_hashes_are_stable(self):
        with (REPO_ROOT / "data/public_calibration_ledger.csv").open() as handle:
            row = next(csv.DictReader(handle))
        payload = {key: row.get(key, "") for key in public_exports.PUBLIC_CALIBRATION_LEDGER_FIELDS if key != "commitment_hash"}
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        self.assertEqual(hashlib.sha256(encoded).hexdigest(), row["commitment_hash"])

    def test_public_calibration_status_summarizes_blocks(self):
        status = json.loads((REPO_ROOT / "data/public_calibration_status.json").read_text())
        self.assertEqual(15, status["ledger_rows"])
        self.assertEqual(15, status["open_commitments"])
        self.assertEqual(0, status["resolved_commitments"])
        self.assertEqual("2026-06-19", status["next_resolution_date"])
        self.assertEqual(3, len(status["blocks"]))
        self.assertEqual("awaiting_resolution", {block["status"] for block in status["blocks"]}.pop())

    def test_public_precommitment_targets_explain_roles(self):
        with (REPO_ROOT / "data/public_precommitment_targets.csv").open() as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(15, len(rows))
        roles = {row["target_set_role"] for row in rows}
        self.assertIn("watch_target", roles)
        self.assertIn("likely_positive_control", roles)
        self.assertIn("likely_negative_control", roles)
        self.assertIn("blindspot_watch", roles)

    def test_public_latency_observatory_has_measured_and_missing_rows(self):
        with (REPO_ROOT / "data/public_latency_observatory.csv").open() as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(45, len(rows))
        statuses = {row["latency_status"] for row in rows}
        self.assertEqual({"measured", "missing_data_as_of"}, statuses)
        measured = [row for row in rows if row["latency_status"] == "measured"]
        self.assertGreaterEqual(len(measured), 20)
        self.assertTrue(all(row["total_visibility_lag_days"] != "" for row in measured))

    def test_blindspots_and_nowcast_status_are_read_only(self):
        blindspots = json.loads((REPO_ROOT / "data/public_blindspots.json").read_text())
        nowcast = json.loads((REPO_ROOT / "data/public_nowcast_status.json").read_text())
        blindspot_ids = {row["blindspot_id"] for row in blindspots["blindspots"]}
        self.assertIn("restricted-publisher-bytes", blindspot_ids)
        self.assertIn("missing-data-as-of-for-latency", blindspot_ids)
        self.assertEqual("interface_defined_not_issued_for_this_snapshot", nowcast["status"])
        self.assertIn("combined_confirmed_plus_suspected_cases", nowcast["candidate_quantities"])

    def test_expanded_public_surface_excludes_sensitive_terms(self):
        paths = [
            "data/public_calibration_status.json",
            "data/public_precommitment_targets.csv",
            "data/public_blindspots.json",
            "data/public_latency_observatory.csv",
            "data/public_nowcast_status.json",
        ]
        forbidden_terms = [
            "risk_adj",
            "risk_raw",
            "mode_b_hypotheses",
            "calibration_blocks",
            "calibration_clock",
            "gamma_shape_rate",
            "source_ingest",
            "private_data_adapter",
        ]
        text = "\n".join((REPO_ROOT / path).read_text() for path in paths)
        for term in forbidden_terms:
            self.assertNotIn(term, text)


if __name__ == "__main__":
    unittest.main()
