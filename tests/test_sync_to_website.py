# SPDX-License-Identifier: Apache-2.0
"""Tests for the website snapshot sync translator."""
from __future__ import annotations

import json
import tempfile
import unittest

import sync_to_website


class TestSyncToWebsite(unittest.TestCase):
    def _snapshot(self):
        pipeline = sync_to_website.load_pipeline_output(
            sync_to_website.REPO_ROOT / "data" / "live-bdbv-2026-output.json"
        )
        manifest = sync_to_website.load_archive_manifest(
            sync_to_website.REPO_ROOT / "data" / "bundibugyo-2026" / "manifest.json"
        )
        return sync_to_website.build_website_snapshot(pipeline, manifest), manifest

    def test_count_source_ids_have_reference_entries(self):
        snapshot, _ = self._snapshot()
        source_ids = {source["id"] for source in snapshot["sources"]}

        for metric, payload in snapshot["reportedCounts"].items():
            with self.subTest(metric=metric):
                missing = set(payload.get("sourceIds", [])) - source_ids
                self.assertFalse(missing)

    def test_cdc_current_situation_is_visible_source(self):
        snapshot, _ = self._snapshot()

        self.assertIn(
            "cdc-current-situation-2026-05-21",
            {source["id"] for source in snapshot["sources"]},
        )

    def test_website_sources_are_manifest_backed(self):
        snapshot, manifest = self._snapshot()
        actual = {source["id"] for source in snapshot["sources"]}
        expected = {
            sync_to_website.canonical_source_id(entry["source_id"])
            for entry in manifest["entries"]
        }

        self.assertEqual(expected, actual)

    def test_may21_context_sources_are_visible(self):
        snapshot, _ = self._snapshot()
        source_ids = {source["id"] for source in snapshot["sources"]}

        for source_id in (
            "cdc-traveler-management-guidance-2026-05-21-pdf",
            "cdc-returning-travelers-info-2026-05-21",
            "ecdc-threat-assessment-bdbv-2026-05-21-pdf",
            "paho-who-epialert-bdbv-2026-05-21-pdf",
            "who-afro-zambia-readiness-2026-05-21",
            "uk-gov-ebola-eastern-drc-support-2026-05-21",
        ):
            with self.subTest(source_id=source_id):
                self.assertIn(source_id, source_ids)

    def test_source_conflict_note_ids_have_reference_entries(self):
        snapshot, _ = self._snapshot()
        source_ids = {source["id"] for source in snapshot["sources"]}

        for index, note in enumerate(snapshot.get("sourceConflictNotes", [])):
            ids = [] if isinstance(note, str) else note.get("sourceIds", [])
            with self.subTest(note=index):
                self.assertFalse(set(ids) - source_ids)

    def test_all_public_source_references_resolve(self):
        snapshot, _ = self._snapshot()
        source_ids = {source["id"] for source in snapshot["sources"]}
        missing = [
            f"{path} -> {source_id}"
            for path, source_id in sync_to_website._iter_source_refs(snapshot)
            if source_id not in source_ids
        ]

        self.assertEqual([], missing)

    def test_timeline_endpoint_matches_reconciled_headline_counts(self):
        snapshot, _ = self._snapshot()
        endpoint = snapshot["timeline"][-1]

        self.assertEqual(snapshot["date"], endpoint["date"])
        for metric in ("confirmed", "suspected", "deaths"):
            with self.subTest(metric=metric):
                self.assertEqual(
                    snapshot["reportedCounts"][metric]["primary"],
                    endpoint[metric],
                )

    def test_timeline_carries_prior_snapshot_endpoint_dates(self):
        snapshot, _ = self._snapshot()
        rows = {row["date"]: row for row in snapshot["timeline"]}

        self.assertEqual(
            {
                "confirmed": 53,
                "suspected": 653,
                "deaths": 144,
            },
            {
                "confirmed": rows["2026-05-20"]["confirmed"],
                "suspected": rows["2026-05-20"]["suspected"],
                "deaths": rows["2026-05-20"]["deaths"],
            },
        )
        self.assertEqual(
            {
                "confirmed": 53,
                "suspected": 653,
                "deaths": 148,
            },
            {
                "confirmed": rows["2026-05-21"]["confirmed"],
                "suspected": rows["2026-05-21"]["suspected"],
                "deaths": rows["2026-05-21"]["deaths"],
            },
        )

    def test_snapshot_requires_data_change_explanations(self):
        snapshot, _ = self._snapshot()
        explanations = snapshot["updateExplanations"]

        for key in (
            "timelineCarryForward",
            "corridorShift",
            "blindspotValidation",
            "calibrationCarryForward",
        ):
            with self.subTest(key=key):
                self.assertIn(key, explanations)
                self.assertGreater(len(explanations[key]), 80)
        self.assertIn("source-attribution lag", explanations["corridorShift"])
        self.assertIn("not missing cases", explanations["corridorShift"])
        self.assertIn("officially zone-attributed", explanations["corridorShift"])
        self.assertIn("unallocated headline context", explanations["corridorShift"])
        self.assertIn("not a corridor-specific signal", explanations["corridorShift"])

    def test_visibility_latency_panel_is_manifest_derived(self):
        snapshot, manifest = self._snapshot()
        visibility = snapshot["visibility"]

        self.assertIn("latencySummary", visibility)
        self.assertIn("sourceLatencyTable", visibility)
        self.assertEqual(len(manifest["entries"]), visibility["latencySummary"]["nEditions"])
        self.assertEqual(
            len(manifest["entries"]),
            len(visibility["sourceLatencyTable"]),
        )
        self.assertGreater(visibility["latencySummary"]["nWithDataAsOf"], 0)
        explanations = snapshot["updateExplanations"]
        self.assertIn("84 confirmed cases", explanations["corridorShift"])
        self.assertIn("33 confirmed cases", explanations["corridorShift"])
        self.assertIn("51 confirmed cases", explanations["corridorShift"])
        self.assertIn("7 WHO AFRO source zones", explanations["corridorShift"])
        self.assertIn("42-corridor watchlist", explanations["corridorShift"])
        self.assertIn("0.4-8.9% lower bounds", explanations["corridorShift"])
        self.assertIn("1.3-23.9% upper bounds", explanations["corridorShift"])
        self.assertNotIn("high-60", explanations["corridorShift"].lower())
        self.assertNotIn("69.", explanations["corridorShift"])
        self.assertIn("historical pre-commitments", explanations["calibrationCarryForward"])

    def test_affected_zones_follow_pipeline_source_footprint(self):
        snapshot, _ = self._snapshot()

        self.assertEqual(
            {
                "bunia",
                "butembo",
                "goma-cod",
                "katwa",
                "mongbwalu",
                "nyankunde",
                "rwampara",
            },
            set(snapshot["affectedZones"]),
        )
        self.assertEqual(set(snapshot["affectedZones"]), set(snapshot["zoneAttributedCounts"]))
        self.assertEqual(42, len(snapshot["corridors"]))
        self.assertFalse(
            any(
                "aggregate confirmed count" in "; ".join(c.get("drivers", []))
                for c in snapshot["corridors"]
            )
        )

    def test_may22_corridor_range_uses_source_zone_attribution(self):
        snapshot, _ = self._snapshot()
        zone_total = sum(
            row["confirmed"] for row in snapshot["zoneAttributedCounts"].values()
        )
        upper_bounds = [c["riskAdjusted50"][1] for c in snapshot["corridors"]]
        lower_bounds = [c["riskAdjusted50"][0] for c in snapshot["corridors"]]

        self.assertEqual(33, zone_total)
        self.assertEqual(84, snapshot["reportedCounts"]["confirmed"]["primary"])
        self.assertLess(zone_total, snapshot["reportedCounts"]["confirmed"]["primary"])
        self.assertEqual(0.013, min(upper_bounds))
        self.assertEqual(0.239, max(upper_bounds))
        self.assertEqual(0.004, min(lower_bounds))
        self.assertEqual(0.089, max(lower_bounds))

    def test_zone_attributed_counts_are_provenance_backed(self):
        snapshot, _ = self._snapshot()

        for zone_id, row in snapshot["zoneAttributedCounts"].items():
            with self.subTest(zone_id=zone_id):
                self.assertGreaterEqual(row["confirmed"], 1)
                self.assertEqual("afro-sitrep-01-pdf-2026-05-18", row["source_id"])
                self.assertEqual("2026-05-18T00:00:00Z", row["source_published_at"])

    def test_public_snapshot_text_does_not_leak_tooling_names(self):
        snapshot, _ = self._snapshot()
        text = json.dumps(snapshot, ensure_ascii=False).lower()

        for needle in ("claude", "codex", "anthropic", "openai"):
            with self.subTest(needle=needle):
                self.assertNotIn(needle, text)

    def test_public_snapshot_text_does_not_leak_internal_evidence_ids(self):
        snapshot, _ = self._snapshot()
        text = json.dumps(snapshot, ensure_ascii=False)

        for needle in ("ec:lovs", "calibration-point:bdbv", "did:web", "arcede.ai"):
            with self.subTest(needle=needle):
                self.assertNotIn(needle, text)

    def test_calibration_clock_exposes_original_horizon_and_remaining_days(self):
        snapshot, _ = self._snapshot()

        self.assertEqual(snapshot["calibrationClock"]["horizonDays"], 30)
        self.assertEqual(snapshot["calibrationClock"]["remainingDays"], 28)
        self.assertEqual(snapshot["calibrationBlocks"][0]["status"], "carried_forward")
        self.assertEqual(snapshot["calibrationBlocks"][0]["pointCount"], 4)
        self.assertEqual(snapshot["calibrationBlocks"][1]["status"], "carried_forward")
        self.assertEqual(snapshot["calibrationBlocks"][1]["pointCount"], 8)
        self.assertEqual(snapshot["calibrationBlocks"][1]["remainingDays"], 29)
        self.assertTrue(
            all(point["horizonDays"] == 30 for point in snapshot["calibrationPoints"])
        )
        self.assertTrue(
            any(point.get("selectionRole") for point in snapshot["calibrationPoints"])
        )

    def test_social_image_cache_bust_matches_snapshot_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            website_root = sync_to_website.pathlib.Path(tmp)
            social_dir = website_root / "app" / "bdbv-2026" / "_lib"
            social_dir.mkdir(parents=True)
            social_path = social_dir / "social.ts"
            social_path.write_text(
                "export const url = '/og/public-health.bdbv-2026?v=clean-2026-05-21';\n",
                encoding="utf-8",
            )

            changed = sync_to_website.update_social_image_version(
                website_root, "2026-05-22"
            )

            self.assertTrue(changed)
            self.assertIn("v=clean-2026-05-22", social_path.read_text(encoding="utf-8"))

    def test_copy_assets_mirrors_generated_brief_visuals(self):
        with tempfile.TemporaryDirectory() as tmp:
            website_root = sync_to_website.pathlib.Path(tmp)

            copied = sync_to_website.copy_assets(sync_to_website.REPO_ROOT, website_root)

            self.assertIn("visuals/corridor_risk.svg", copied)
            self.assertEqual(
                (
                    sync_to_website.REPO_ROOT
                    / "brief"
                    / "visuals"
                    / "corridor_risk.svg"
                ).read_bytes(),
                (
                    website_root
                    / "public"
                    / "bdbv-2026"
                    / "visuals"
                    / "corridor_risk.svg"
                ).read_bytes(),
            )


if __name__ == "__main__":
    unittest.main()
