# SPDX-License-Identifier: Apache-2.0
"""Tests for sanitized public-health export artifacts."""
from __future__ import annotations

import csv
import hashlib
import json
import subprocess
import sys
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
        self.assertIn("METHOD_CARDS_PUBLIC.md", paths)
        self.assertIn("WORKED_SNAPSHOT_REVIEW.md", paths)
        self.assertIn("PUBLIC_ADAPTATION_GUIDE.md", paths)
        self.assertIn("PUBLIC_HEALTH_USE_CASES.md", paths)
        self.assertIn("CALIBRATION_RESOLUTION_PUBLIC.md", paths)
        self.assertIn("examples/README.md", paths)
        self.assertIn("examples/local_aggregate_input.example.json", paths)
        self.assertIn("examples/source_manifest_minimal.example.json", paths)
        self.assertIn("examples/public_calibration_commitments.example.csv", paths)
        self.assertIn("examples/review_public_methodology.py", paths)
        self.assertIn("examples/summarize_public_package.py", paths)
        self.assertIn("schemas/README.md", paths)
        self.assertIn("schemas/public_snapshot.schema.json", paths)
        self.assertIn("schemas/public_source_manifest.schema.json", paths)
        self.assertIn("schemas/public_calibration_status.schema.json", paths)
        self.assertIn("schemas/public_blindspots.schema.json", paths)
        self.assertIn("schemas/public_nowcast_status.schema.json", paths)
        self.assertIn("schemas/local_aggregate_input.schema.json", paths)

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
        self.assertIn("public_group_id", status["blocks"][0])
        self.assertNotIn("public_block_id", status["blocks"][0])
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
            "hypothesis_id",
            "block_id",
            "source_ingest",
            "private_data_adapter",
        ]
        text = "\n".join((REPO_ROOT / path).read_text() for path in paths)
        for term in forbidden_terms:
            self.assertNotIn(term, text)

    def test_public_usability_docs_are_present_and_safe(self):
        paths = [
            "README.md",
            "PUBLIC_HEALTH_USE_CASES.md",
            "METHODOLOGY_PUBLIC.md",
            "METHOD_CARDS_PUBLIC.md",
            "WORKED_SNAPSHOT_REVIEW.md",
            "CALIBRATION_RESOLUTION_PUBLIC.md",
            "READONLY_INTERFACE_PUBLIC.md",
            "examples/README.md",
            "schemas/README.md",
        ]
        text = "\n".join((REPO_ROOT / path).read_text() for path in paths)
        for expected in (
            "PUBLIC_HEALTH_USE_CASES.md",
            "METHOD_CARDS_PUBLIC.md",
            "WORKED_SNAPSHOT_REVIEW.md",
            "CALIBRATION_RESOLUTION_PUBLIC.md",
            "schemas/",
            "examples/summarize_public_package.py",
            "examples/review_public_methodology.py",
            "frans@arcede.com",
        ):
            self.assertIn(expected, text)
        forbidden_terms = [
            "earth" + "_awake",
            "earth" + "_journal",
            "agent" + "_workspace",
            "compile" + "_agent" + "_brief",
            "arcede" + "://",
            "for" + "ge gate",
            "for" + "ge gates",
            "source_ingest",
            "private_data_adapter",
            "risk_adj",
            "risk_raw",
            "hypothesis_id",
            "block_id",
            "feature_weights",
            "posterior_parameters",
        ]
        for term in forbidden_terms:
            self.assertNotIn(term, text)

    def test_public_json_schemas_match_current_artifacts(self):
        schema_to_artifact = {
            "schemas/public_snapshot.schema.json": "data/public_snapshot.json",
            "schemas/public_source_manifest.schema.json": "data/public_source_manifest.json",
            "schemas/public_calibration_status.schema.json": "data/public_calibration_status.json",
            "schemas/public_blindspots.schema.json": "data/public_blindspots.json",
            "schemas/public_nowcast_status.schema.json": "data/public_nowcast_status.json",
            "schemas/local_aggregate_input.schema.json": "examples/local_aggregate_input.example.json",
        }
        for schema_path, artifact_path in schema_to_artifact.items():
            schema = json.loads((REPO_ROOT / schema_path).read_text())
            artifact = json.loads((REPO_ROOT / artifact_path).read_text())
            self.assertEqual("object", schema["type"])
            self.assertIn("$schema", schema)
            for key in schema["required"]:
                self.assertIn(key, artifact, f"{artifact_path} missing schema-required key {key}")

        manifest_schema = json.loads((REPO_ROOT / "schemas/public_source_manifest.schema.json").read_text())
        minimal_manifest = json.loads((REPO_ROOT / "examples/source_manifest_minimal.example.json").read_text())
        for key in manifest_schema["required"]:
            self.assertIn(key, minimal_manifest)

    def test_public_summary_consumer_is_read_only_and_grounded(self):
        result = subprocess.run(
            [sys.executable, "examples/summarize_public_package.py"],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual("", result.stderr)
        self.assertEqual(0, result.returncode)
        self.assertIn("BDBV Public Package Summary", result.stdout)
        self.assertIn("confirmed cases: 128", result.stdout)
        self.assertIn("health-zone rows: 18", result.stdout)
        self.assertIn("open commitments: 15", result.stdout)
        for term in ("risk_adj", "risk_raw", "feature_weights", "posterior_parameters"):
            self.assertNotIn(term, result.stdout)

    def test_public_methodology_review_consumer_is_read_only_and_grounded(self):
        result = subprocess.run(
            [sys.executable, "examples/review_public_methodology.py"],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual("", result.stderr)
        self.assertEqual(0, result.returncode)
        self.assertIn("BDBV Public Methodology Review", result.stdout)
        self.assertIn("confirmed primary: 128", result.stdout)
        self.assertIn("documented attribution gap: 19", result.stdout)
        self.assertIn("rows missing data_as_of for latency: 19", result.stdout)
        self.assertIn("open commitments: 15", result.stdout)
        self.assertIn("interface_defined_not_issued_for_this_snapshot", result.stdout)
        for term in ("risk_adj", "risk_raw", "feature_weights", "posterior_parameters"):
            self.assertNotIn(term, result.stdout)

    def test_public_adaptation_package_is_self_serve_and_safe(self):
        guide = (REPO_ROOT / "PUBLIC_ADAPTATION_GUIDE.md").read_text()
        self.assertIn("frans@arcede.com", guide)
        self.assertIn("examples/", guide)
        local_input = json.loads((REPO_ROOT / "examples/local_aggregate_input.example.json").read_text())
        source_manifest = json.loads((REPO_ROOT / "examples/source_manifest_minimal.example.json").read_text())
        with (REPO_ROOT / "examples/public_calibration_commitments.example.csv").open() as handle:
            commitments = list(csv.DictReader(handle))
        snapshot = json.loads((REPO_ROOT / "data/public_snapshot.json").read_text())
        with (REPO_ROOT / "data/public_zone_counts_2026-05-26.csv").open() as handle:
            public_zone_rows = list(csv.DictReader(handle))
        public_manifest = json.loads((REPO_ROOT / "data/public_source_manifest.json").read_text())
        with (REPO_ROOT / "data/public_calibration_ledger.csv").open() as handle:
            public_ledger_rows = list(csv.DictReader(handle))

        self.assertEqual("1.0-public-example", local_input["schema_version"])
        self.assertEqual("1.0-public-example", source_manifest["schema_version"])
        self.assertEqual(snapshot["outbreak_id"], local_input["outbreak_id"])
        self.assertEqual(snapshot["outbreak_id"], source_manifest["outbreak_id"])
        self.assertEqual(snapshot["as_of"], local_input["snapshot"]["as_of"])
        self.assertEqual(snapshot["data_as_of"], local_input["snapshot"]["data_as_of"])
        self.assertEqual(1, len(commitments))
        self.assertIn("health_zone_counts", local_input)
        self.assertIn("entries", source_manifest)
        self.assertEqual(18, len(local_input["health_zone_counts"]))
        self.assertEqual(2, len(source_manifest["entries"]))

        for example_metric, snapshot_metric in (
            ("confirmed_cases", "confirmed"),
            ("suspected_cases", "suspected"),
            ("deaths", "deaths"),
        ):
            example = local_input["reported_counts"][example_metric]
            public = snapshot["reported_counts"][snapshot_metric]
            self.assertEqual(public["primary"], example["value"])
            self.assertEqual(public["primary_source_id"], example["primary_source_id"])
            self.assertEqual(public["min"], example["conflict_range"]["min"])
            self.assertEqual(public["max"], example["conflict_range"]["max"])

        public_zone_by_id = {row["zone_id"]: row for row in public_zone_rows}
        for row in local_input["health_zone_counts"]:
            public_row = public_zone_by_id[row["zone_id"]]
            for field in ("confirmed", "suspected", "confirmed_deaths", "suspected_deaths"):
                self.assertEqual(int(public_row[field]), row[field])
            self.assertEqual(public_row["source_id"], row["source_id"])
            self.assertEqual(public_row["source_data_date"], row["source_data_date"])
            self.assertEqual(public_row["source_row_status"], row["row_status"])

        public_manifest_by_id = {row["source_id"]: row for row in public_manifest["entries"]}
        for row in source_manifest["entries"]:
            public_row = public_manifest_by_id[row["source_id"]]
            for field in (
                "publisher",
                "source_tier",
                "published_at",
                "retrieved_at",
                "data_as_of",
                "data_as_of_basis",
                "url",
                "license",
                "raw_archive_status",
                "content_hash",
            ):
                self.assertEqual(public_row[field], row[field])

        for field in public_ledger_rows[0].keys():
            self.assertEqual(public_ledger_rows[0][field], commitments[0][field])

        text = "\n".join(
            [
                guide,
                (REPO_ROOT / "examples/README.md").read_text(),
                json.dumps(local_input, sort_keys=True),
                json.dumps(source_manifest, sort_keys=True),
                "\n".join(",".join(row.values()) for row in commitments),
            ]
        )
        forbidden_terms = [
            "risk_adj",
            "risk_raw",
            "hypothesis_id",
            "block_id",
            "source_ingest",
            "private_data_adapter",
            "earth" + "_awake",
            "agent" + "_workspace",
        ]
        for term in forbidden_terms:
            self.assertNotIn(term, text)


if __name__ == "__main__":
    unittest.main()
