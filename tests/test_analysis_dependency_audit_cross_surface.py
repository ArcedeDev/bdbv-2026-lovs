# SPDX-License-Identifier: Apache-2.0
"""Cross-surface regression for analysis_dependency_audit.

The audit lives in two surfaces:
  - LOVS output: data/live-bdbv-2026-output.json -> analysis_dependency_audit
  - Public workbook CSV: deliverables/public-health-dataset/analysis_dependency_audit.csv

The exporter is allowed to enrich the CSV with derived columns (clock_basis
fallback from blocked_by, model_use, held_out_reason). It is not allowed to
drift on the core invariants: every audit surface must appear in both
surfaces, with the same status and the same model inputs. This test locks
those invariants so a future cycle cannot silently drop or rename an audit
entry on one side only.

Skips cleanly when canonical artifacts are absent (developer worktree
without a fresh pipeline run); the release_snapshot.run_release_gates path
runs the full suite against regenerated artifacts.
"""
from __future__ import annotations

import csv
import json
import pathlib
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
LIVE_OUTPUT = REPO_ROOT / "data" / "live-bdbv-2026-output.json"
AUDIT_CSV = REPO_ROOT / "deliverables" / "public-health-dataset" / "analysis_dependency_audit.csv"


class AnalysisDependencyAuditCrossSurfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for path in (LIVE_OUTPUT, AUDIT_CSV):
            if not path.exists():
                raise unittest.SkipTest(f"missing canonical artifact: {path}")
        cls.lovs = json.loads(LIVE_OUTPUT.read_text(encoding="utf-8")).get(
            "analysis_dependency_audit", []
        )
        with AUDIT_CSV.open(newline="", encoding="utf-8") as handle:
            cls.csv_rows = list(csv.DictReader(handle))

    def test_lovs_audit_is_non_empty(self):
        self.assertGreater(len(self.lovs), 0, "LOVS analysis_dependency_audit is empty")

    def test_surfaces_match_between_lovs_and_csv(self):
        lovs_surfaces = sorted(e["surface"] for e in self.lovs)
        csv_surfaces = sorted(r["surface"] for r in self.csv_rows)
        self.assertEqual(
            lovs_surfaces,
            csv_surfaces,
            "analysis_dependency_audit surface set drift between LOVS and CSV",
        )

    def test_every_surface_has_non_empty_status(self):
        for entry in self.lovs:
            self.assertTrue(
                isinstance(entry.get("status"), str) and entry["status"].strip(),
                f"LOVS audit surface {entry.get('surface')!r} missing status",
            )
        for row in self.csv_rows:
            self.assertTrue(
                row.get("status", "").strip(),
                f"CSV audit row {row.get('surface')!r} missing status",
            )

    def test_status_matches_between_lovs_and_csv_per_surface(self):
        csv_by_surface = {r["surface"]: r for r in self.csv_rows}
        for entry in self.lovs:
            surface = entry["surface"]
            self.assertIn(surface, csv_by_surface, f"{surface} missing in CSV")
            self.assertEqual(
                entry["status"],
                csv_by_surface[surface]["status"],
                f"status drift on surface {surface!r}",
            )

    def test_inputs_match_between_lovs_and_csv_per_surface(self):
        csv_by_surface = {r["surface"]: r for r in self.csv_rows}
        for entry in self.lovs:
            surface = entry["surface"]
            lovs_inputs = entry.get("inputs", {})
            csv_inputs_raw = csv_by_surface[surface].get("input_values") or "{}"
            try:
                csv_inputs = json.loads(csv_inputs_raw)
            except json.JSONDecodeError as exc:
                self.fail(
                    f"CSV input_values for surface {surface!r} is not valid JSON: {exc}"
                )
            self.assertEqual(
                lovs_inputs,
                csv_inputs,
                f"input values drift on surface {surface!r}",
            )


if __name__ == "__main__":
    unittest.main()
