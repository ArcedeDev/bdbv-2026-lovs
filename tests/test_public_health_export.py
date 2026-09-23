# SPDX-License-Identifier: Apache-2.0
"""Tests for the public-health workbook exporter."""
from __future__ import annotations

import csv
import json
import pathlib
import tempfile
import unittest
import zipfile

import export_public_health_dataset


class TestPublicHealthDatasetExport(unittest.TestCase):

    def test_export_package_writes_valid_workbook_and_sidecars(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = pathlib.Path(tmp)
            paths = export_public_health_dataset.export_package(output_dir)

            workbook = paths["workbook"]
            self.assertTrue(workbook.exists())
            self.assertTrue(zipfile.is_zipfile(workbook))
            with zipfile.ZipFile(workbook) as zf:
                names = set(zf.namelist())
            self.assertIn("xl/workbook.xml", names)
            self.assertIn("xl/worksheets/sheet1.xml", names)
            self.assertIn("xl/worksheets/sheet11.xml", names)

            self.assertTrue((output_dir / "snapshot_clocks.csv").exists())
            self.assertTrue((output_dir / "reported_counts.csv").exists())
            self.assertTrue((output_dir / "analysis_dependency_audit.csv").exists())
            self.assertTrue((output_dir / "public_claim_audit.csv").exists())
            self.assertTrue((output_dir / "sitrep_narrative.csv").exists())
            self.assertFalse((output_dir / "evidence_chains.csv").exists())
            self.assertTrue(paths["schema"].exists())
            self.assertTrue(paths["manifest"].exists())

    def test_per_zone_workbook_pointer_uses_current_inrb_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = pathlib.Path(tmp)
            paths = export_public_health_dataset.export_package(output_dir)
            with zipfile.ZipFile(paths["workbook"]) as zf:
                workbook_xml = "\n".join(
                    zf.read(name).decode("utf-8", "replace")
                    for name in zf.namelist()
                    if name.endswith(".xml")
                )

        self.assertIn("INRB-UMIE/BDBV2026-Data", workbook_xml)
        # The per-zone pointer cites the retained INRB-UMIE source-review build
        # (upstream_reference: build-2026-06-12-1dfdf1e, data as of 2026-06-11),
        # while the current primary per-zone source-load is the reviewed INSP
        # SitRep #042 endpoint.
        self.assertIn("build-2026-06-12-1dfdf1e", workbook_xml)
        self.assertIn("inrb-sitrep-042-2026-06-25", workbook_xml)
        self.assertIn("data as of 2026-06-11", workbook_xml)

    def test_sitrep_narrative_export_carries_reviewed_sitrep_47_sections(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = pathlib.Path(tmp)
            paths = export_public_health_dataset.export_package(output_dir)
            with (output_dir / "sitrep_narrative.csv").open() as f:
                rows = list(csv.DictReader(f))
            with zipfile.ZipFile(paths["workbook"]) as zf:
                workbook_xml = "\n".join(
                    zf.read(name).decode("utf-8", "replace")
                    for name in zf.namelist()
                    if name.endswith(".xml")
                )

        self.assertGreaterEqual(len(rows), 10)
        self.assertIn("SitRep Narrative", workbook_xml)
        sections = {row["section"] for row in rows}
        self.assertIn("highlights", sections)
        self.assertIn("care_continuity", sections)
        self.assertIn("challenges", sections)
        self.assertIn("priorities", sections)
        # `published_highlights` has RETIRED, and its absence is the carry bound
        # working rather than a dropped section. The publisher last printed a
        # highlights page in the SitRep 87 image packet (2026-08-10); SitReps 88
        # to 100 are thirteen consecutive editions without one, past the
        # SECTION_CARRY_EDITIONS bound of 7. Republishing a 10 August statement
        # on a 22 August sheet is the failure the bound exists to prevent.
        self.assertNotIn("published_highlights", sections)
        self.assertGreater(
            100 - 87, export_public_health_dataset.SECTION_CARRY_EDITIONS,
            "published_highlights must be outside the carry bound for this assertion to mean anything",
        )
        # Each section travels with the edition that published it, not with one
        # edition chosen for the whole sheet. SitRep 87's image packet prints a
        # highlights page and no challenges; keying the sheet to it would have
        # emptied the challenges section, and keying the sheet to SitRep 85 would
        # have hidden the highlights page entirely.
        by_section = {}
        for row in rows:
            by_section.setdefault(row["section"], set()).add(row["source_id"])
        self.assertEqual(by_section["challenges"], {"inrb-sitrep-130-2026-09-21"})
        self.assertEqual(by_section["highlights"], {"inrb-sitrep-130-2026-09-21"})
        self.assertEqual(by_section["care_continuity"], {"inrb-sitrep-130-2026-09-21"})
        # A section that did not come from the newest edition says so on its rows.
        carried = [row for row in rows if row["section"] == "highlights"]
        self.assertFalse(any("Carried from" in row["public_note"] for row in carried))
        text = "\n".join(row["text"] for row in rows)
        self.assertIn(
            "National isolation/CTE stock: 839",
            text,
        )
        self.assertIn(
            "DRC: 7773 confirmed and 3759 confirmed deaths",
            text,
        )
        # SitRep 119 opens Bulu and Sud-Ubangi, while expanding the publisher's
        # affected-province denominator. The ratio is deliberately non-comparable.
        self.assertIn(
            "Footprint: 63/167 health zones across seven provinces",
            text,
        )
        self.assertIn("24h: 40 confirmations and 27 deaths", text)
        self.assertIn("Contact follow-up 26,707/31,612 (84.5%)", text)
        # The cycle's lead epidemiological signals must survive onto the public
        # narrative surface under current sections rather than via the old
        # compact-layout carry-forward path.
        self.assertIn("PoE/PoC completeness in Tshopo", text)
        self.assertEqual(by_section["priorities"], {"inrb-sitrep-130-2026-09-21"})
        notes = "\n".join(row["public_note"] for row in rows)
        self.assertIn("final-page contact details are intentionally excluded", notes)
        self.assertNotIn("frans@", text)

    def test_surveillance_zone_export_carries_jiba_as_display_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = pathlib.Path(tmp)
            export_public_health_dataset.export_package(output_dir)
            with (output_dir / "surveillance_zones.csv").open() as f:
                rows = list(csv.DictReader(f))

        self.assertEqual(1, len(rows))
        row = rows[0]
        self.assertEqual("jiba", row["lovs_zone_id"])
        self.assertEqual("Jiba", row["zone_name"])
        self.assertEqual("2026-05-30", row["as_of_data_date"])
        self.assertEqual("2", row["suspected"])
        self.assertEqual("0", row["confirmed"])
        self.assertEqual("display_only_surveillance", row["model_use"])
        self.assertIn("retired", row["basis"].lower())
        self.assertIn("national", row["basis"].lower())

    def test_reported_counts_are_attributed(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = pathlib.Path(tmp)
            export_public_health_dataset.export_package(output_dir)
            with (output_dir / "reported_counts.csv").open() as f:
                rows = list(csv.DictReader(f))

        self.assertGreater(len(rows), 10)
        required = (
            "source_id",
            "source_url",
            "archive_sha256",
            "license",
            "evidence_ref",
            "evidence_status",
        )
        for row in rows:
            missing = [field for field in required if not row[field].strip()]
            self.assertFalse(missing, msg=f"{row['row_id']} missing {missing}")

    def test_corrections_and_restricted_sources_are_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = pathlib.Path(tmp)
            export_public_health_dataset.export_package(output_dir)
            text = (output_dir / "corrections_gaps.csv").read_text()
            evidence = (output_dir / "public_claim_audit.csv").read_text()

        self.assertIn("Kinshasa", text)
        self.assertIn("Imperial table 3", text)
        self.assertIn("Corridor gravity exponents", text)
        self.assertIn("restricted-local-review-not-redistributed", text)
        self.assertNotIn("/Users/", text)
        self.assertNotIn("/Users/", evidence)

        sensitive_needles = (
            "ec:lovs:",
            "claim:lovs:",
            "src:local-",
            "raw_bytes_relpath",
            "gamma(4.0",
            "under_ascertainment_uniform",
            "clamp [0.1",
        )
        for needle in sensitive_needles:
            self.assertNotIn(needle, evidence)

    def test_snapshot_reconciled_counts_have_values(self):
        """Reconciled headline counts must not ship blank (schema-key drift guard)."""
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = pathlib.Path(tmp)
            export_public_health_dataset.export_package(output_dir)
            with (output_dir / "reported_counts.csv").open() as f:
                rows = [
                    r for r in csv.DictReader(f)
                    if r["row_type"] == "snapshot_reconciled_metric"
                ]
        self.assertTrue(rows, "expected snapshot-reconciled rows")
        for row in rows:
            has_value = bool(row["value"].strip())
            has_range = bool(row["value_min"].strip() and row["value_max"].strip())
            self.assertTrue(
                has_value or has_range,
                msg=f"{row['row_id']} carries neither a value nor a min/max range",
            )

    def test_snapshot_clocks_preserve_publication_report_and_retrieval_dates(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = pathlib.Path(tmp)
            export_public_health_dataset.export_package(output_dir)
            with (output_dir / "snapshot_clocks.csv").open() as f:
                rows = list(csv.DictReader(f))

        by_id = {row["row_id"]: row for row in rows}
        # publication_cutoff advances to the most recent publication date across
        # the manifest, which is a DIFFERENT clock from the analytic data date.
        # SitRep #117 reports on 2026-09-08 and reached insp.cd on 2026-09-09, so
        # the cutoff is 2026-09-09 while the analytic clock stays on the reviewed
        # 2026-09-08 data date. The gap is one day, and
        # #116 adds a third date: the PDF prints "Date de publication : 08
        # septembre" but the bytes did not appear until the 9th, so the cutoff
        # tracks actual availability rather than the date the document claims.
        # The clocks must not be collapsed: the post title carries the publication
        # date, the document's "Date de rapportage" carries the data date, and they
        # have disagreed since SitRep #114.
        self.assertEqual(
            "2026-09-22",
            by_id["snapshot:publication_cutoff"]["date_value"],
        )
        self.assertEqual(
            "not_recorded",
            by_id["snapshot:generated_at"]["status"],
        )

        sitrep008 = "drc-moh-epidemie-dashboard-sitrep-008-graphql-2026-05-23"
        self.assertEqual(
            "2026-05-22",
            by_id[f"source_data_report_date:{sitrep008}"]["date_value"],
        )
        self.assertEqual(
            "2026-05-23",
            by_id[f"source_publication_date:{sitrep008}"]["date_value"],
        )
        self.assertEqual(
            "2026-05-23T18:36:26Z",
            by_id[f"source_retrieval_date:{sitrep008}"]["timestamp_value"],
        )
        sitrep009 = "drc-moh-epidemie-dashboard-sitrep-009-graphql-2026-05-24"
        self.assertEqual(
            "",
            by_id[f"source_data_report_date:{sitrep009}"]["date_value"],
        )
        self.assertEqual(
            "not_recorded",
            by_id[f"source_data_report_date:{sitrep009}"]["status"],
        )
        self.assertEqual(
            "2026-05-24",
            by_id[f"source_publication_date:{sitrep009}"]["date_value"],
        )

    def test_source_review_rows_keep_clocks_but_not_reported_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = pathlib.Path(tmp)
            export_public_health_dataset.export_package(output_dir)
            reported = (output_dir / "reported_counts.csv").read_text()
            clocks = (output_dir / "snapshot_clocks.csv").read_text()
            sources = (output_dir / "sources.csv").read_text()

        source_id = "drc-moh-epidemie-dashboard-sitrep-008-graphql-2026-05-23"
        self.assertNotIn(f"source:{source_id}:", reported)
        self.assertIn(source_id, clocks)
        self.assertIn(source_id, sources)

    def test_timeline_omits_sources_without_data_report_dates(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = pathlib.Path(tmp)
            export_public_health_dataset.export_package(output_dir)
            with (output_dir / "reported_counts.csv").open() as f:
                reported_rows = list(csv.DictReader(f))
            with (output_dir / "timeline.csv").open() as f:
                timeline_rows = list(csv.DictReader(f))
            with (output_dir / "sources.csv").open() as f:
                source_rows = list(csv.DictReader(f))

        source_id = "drc-moh-epidemie-dashboard-sitrep-009-graphql-2026-05-24"
        # After the May-25 deaths reconciliation the MoH dashboard aggregate is a
        # conflict anchor, not a reconciled-count primary, so it is no longer a
        # reconciled-metric source_id; it is retained as provenance in sources.csv
        # (and in the conflict trail of the reconciled rows), not dropped.
        self.assertNotIn(
            source_id,
            {
                row["source_id"] for row in reported_rows
                if row["row_type"] == "snapshot_reconciled_metric"
            },
            "MoH aggregate is a conflict anchor, not a reconciled-count primary",
        )
        self.assertIn(
            source_id,
            {row["source_id"] for row in source_rows},
            "expected MoH aggregate to remain as conflict-anchor provenance in sources.csv",
        )
        self.assertTrue(
            all(row["date"] for row in timeline_rows),
            "every timeline point must carry a data/report date",
        )
        self.assertNotIn(source_id, {row["source_id"] for row in timeline_rows})

    def test_timeline_exports_c2_per_date_band(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = pathlib.Path(tmp)
            export_public_health_dataset.export_package(output_dir)
            with (output_dir / "timeline.csv").open() as f:
                rows = list(csv.DictReader(f))

        by_date_metric = {(row["date"], row["metric"]): row for row in rows}
        # Carry-back of the most-recent reviewed lab positivity onto each
        # date's reported active-suspected queue. The series advances to the
        # current cycle on the suspected-in-isolation basis once INSP stops
        # publishing the full active-suspected total.
        expected = {
            ("2026-05-30", "confirmable_active_queue_50_lower"): "391",
            ("2026-05-30", "confirmable_active_queue_50_upper"): "402",
            ("2026-05-31", "confirmable_active_queue_50_lower"): "398",
            ("2026-05-31", "confirmable_active_queue_50_upper"): "405",
            ("2026-06-01", "confirmable_active_queue_50_lower"): "447",
            ("2026-06-01", "confirmable_active_queue_50_upper"): "456",
            ("2026-06-02", "confirmable_active_queue_50_lower"): "428",
            ("2026-06-02", "confirmable_active_queue_50_upper"): "434",
            ("2026-06-03", "confirmable_active_queue_50_lower"): "450",
            ("2026-06-03", "confirmable_active_queue_50_upper"): "456",
            ("2026-06-04", "confirmable_active_queue_50_lower"): "530",
            ("2026-06-04", "confirmable_active_queue_50_upper"): "536",
            ("2026-06-05", "confirmable_active_queue_50_lower"): "564",
            ("2026-06-05", "confirmable_active_queue_50_upper"): "569",
            ("2026-06-06", "confirmable_active_queue_50_lower"): "591",
            ("2026-06-06", "confirmable_active_queue_50_upper"): "596",
            ("2026-06-07", "confirmable_active_queue_50_lower"): "630",
            ("2026-06-07", "confirmable_active_queue_50_upper"): "637",
            ("2026-06-08", "confirmable_active_queue_50_lower"): "675",
            ("2026-06-08", "confirmable_active_queue_50_upper"): "681",
            ("2026-06-09", "confirmable_active_queue_50_lower"): "699",
            ("2026-06-09", "confirmable_active_queue_50_upper"): "716",
            ("2026-06-10", "confirmable_active_queue_50_lower"): "736",
            ("2026-06-10", "confirmable_active_queue_50_upper"): "741",
            ("2026-06-11", "confirmable_active_queue_50_lower"): "764",
            ("2026-06-11", "confirmable_active_queue_50_upper"): "770",
            ("2026-06-13", "confirmable_active_queue_50_lower"): "868",
            ("2026-06-13", "confirmable_active_queue_50_upper"): "875",
            ("2026-06-14", "confirmable_active_queue_50_lower"): "893",
            ("2026-06-14", "confirmable_active_queue_50_upper"): "900",
            ("2026-06-15", "confirmable_active_queue_50_lower"): "922",
            ("2026-06-15", "confirmable_active_queue_50_upper"): "929",
            ("2026-06-16", "confirmable_active_queue_50_lower"): "966",
            ("2026-06-16", "confirmable_active_queue_50_upper"): "974",
            ("2026-06-17", "confirmable_active_queue_50_lower"): "986",
            ("2026-06-17", "confirmable_active_queue_50_upper"): "993",
            ("2026-06-18", "confirmable_active_queue_50_lower"): "1033",
            ("2026-06-18", "confirmable_active_queue_50_upper"): "1041",
            ("2026-06-19", "confirmable_active_queue_50_lower"): "1043",
            ("2026-06-19", "confirmable_active_queue_50_upper"): "1050",
            ("2026-06-20", "confirmable_active_queue_50_lower"): "1087",
            ("2026-06-20", "confirmable_active_queue_50_upper"): "1094",
            ("2026-06-21", "confirmable_active_queue_50_lower"): "1124",
            ("2026-06-21", "confirmable_active_queue_50_upper"): "1129",
            ("2026-06-22", "confirmable_active_queue_50_lower"): "1177",
            ("2026-06-22", "confirmable_active_queue_50_upper"): "1183",
            ("2026-06-23", "confirmable_active_queue_50_lower"): "1207",
            ("2026-06-23", "confirmable_active_queue_50_upper"): "1214",
            ("2026-06-24", "confirmable_active_queue_50_lower"): "1232",
            ("2026-06-24", "confirmable_active_queue_50_upper"): "1238",
            ("2026-06-25", "confirmable_active_queue_50_lower"): "1286",
            ("2026-06-25", "confirmable_active_queue_50_upper"): "1293",
            ("2026-06-27", "confirmable_active_queue_50_lower"): "1387",
            ("2026-06-27", "confirmable_active_queue_50_upper"): "1397",
            ("2026-06-29", "confirmable_active_queue_50_lower"): "1478",
            ("2026-06-29", "confirmable_active_queue_50_upper"): "1491",
            ("2026-06-30", "confirmable_active_queue_50_lower"): "1551",
            ("2026-06-30", "confirmable_active_queue_50_upper"): "1564",
            ("2026-07-01", "confirmable_active_queue_50_lower"): "1615",
            ("2026-07-01", "confirmable_active_queue_50_upper"): "1629",
            ("2026-07-02", "confirmable_active_queue_50_lower"): "1653",
            ("2026-07-02", "confirmable_active_queue_50_upper"): "1667",
            ("2026-07-03", "confirmable_active_queue_50_lower"): "1679",
            ("2026-07-03", "confirmable_active_queue_50_upper"): "1693",
            ("2026-07-04", "confirmable_active_queue_50_lower"): "1712",
            ("2026-07-04", "confirmable_active_queue_50_upper"): "1726",
            ("2026-07-05", "confirmable_active_queue_50_lower"): "1784",
            ("2026-07-05", "confirmable_active_queue_50_upper"): "1799",
            ("2026-07-06", "confirmable_active_queue_50_lower"): "1875",
            ("2026-07-06", "confirmable_active_queue_50_upper"): "1946",
            ("2026-07-07", "confirmable_active_queue_50_lower"): "1944",
            ("2026-07-07", "confirmable_active_queue_50_upper"): "1961",
            ("2026-07-08", "confirmable_active_queue_50_lower"): "1980",
            ("2026-07-08", "confirmable_active_queue_50_upper"): "1997",
            ("2026-07-09", "confirmable_active_queue_50_lower"): "2022",
            ("2026-07-09", "confirmable_active_queue_50_upper"): "2040",
            ("2026-07-10", "confirmable_active_queue_50_lower"): "2053",
            ("2026-07-10", "confirmable_active_queue_50_upper"): "2070",
            ("2026-07-11", "confirmable_active_queue_50_lower"): "2106",
            ("2026-07-11", "confirmable_active_queue_50_upper"): "2123",
            ("2026-07-12", "confirmable_active_queue_50_lower"): "2139",
            ("2026-07-12", "confirmable_active_queue_50_upper"): "2155",
            ("2026-07-13", "confirmable_active_queue_50_lower"): "2192",
            ("2026-07-13", "confirmable_active_queue_50_upper"): "2209",
            ("2026-07-14", "confirmable_active_queue_50_lower"): "2241",
            ("2026-07-14", "confirmable_active_queue_50_upper"): "2256",
            ("2026-07-15", "confirmable_active_queue_50_lower"): "2285",
            ("2026-07-15", "confirmable_active_queue_50_upper"): "2300",
            ("2026-07-16", "confirmable_active_queue_50_lower"): "2346",
            ("2026-07-16", "confirmable_active_queue_50_upper"): "2361",
            ("2026-07-17", "confirmable_active_queue_50_lower"): "2436",
            ("2026-07-17", "confirmable_active_queue_50_upper"): "2452",
            ("2026-07-18", "confirmable_active_queue_50_lower"): "2508",
            ("2026-07-18", "confirmable_active_queue_50_upper"): "2522",
            ("2026-07-19", "confirmable_active_queue_50_lower"): "2582",
            ("2026-07-19", "confirmable_active_queue_50_upper"): "2597",
            ("2026-07-20", "confirmable_active_queue_50_lower"): "2636",
            ("2026-07-20", "confirmable_active_queue_50_upper"): "2651",
            ("2026-07-21", "confirmable_active_queue_50_lower"): "2703",
            ("2026-07-21", "confirmable_active_queue_50_upper"): "2719",
            ("2026-07-22", "confirmable_active_queue_50_lower"): "3064",
            ("2026-07-22", "confirmable_active_queue_50_upper"): "3078",
            ("2026-07-23", "confirmable_active_queue_50_lower"): "3148",
            ("2026-07-23", "confirmable_active_queue_50_upper"): "3164",
            ("2026-07-24", "confirmable_active_queue_50_lower"): "3242",
            ("2026-07-24", "confirmable_active_queue_50_upper"): "3258",
            ("2026-07-25", "confirmable_active_queue_50_lower"): "3368",
            ("2026-07-25", "confirmable_active_queue_50_upper"): "3383",
        }
        # The exporter must preserve every model-produced date window exactly.
        # Do not retain historical expectations after the queue basis was
        # corrected to require the complete active-suspected queue: SitRep 18
        # is the last eligible edition, and later isolation-only subsets are not
        # interchangeable with that denominator.
        expected = {}
        # Reading the durable snapshot avoids re-pinning the full historical
        # projection whenever a reviewed daily lab-yield endpoint is added.
        live = json.loads(
            (pathlib.Path(__file__).resolve().parents[1] / "data/live-bdbv-2026-output.json").read_text()
        )
        for window in live["visibility"]["active_queue_projection"]["per_date_windows"]:
            lower, upper = window["confirmable_active_queue_50"]
            expected[(window["date"], "confirmable_active_queue_50_lower")] = str(lower)
            expected[(window["date"], "confirmable_active_queue_50_upper")] = str(upper)
        for key, value in expected.items():
            self.assertEqual(value, by_date_metric[key]["value"])
            self.assertEqual("count", by_date_metric[key]["unit"])
            self.assertIn("active-queue lab-yield", by_date_metric[key]["note"])

        timeline_text = "\n".join(",".join(row.values()) for row in rows)
        self.assertNotIn("ec:lovs:", timeline_text)

    def test_analysis_dependency_audit_exports_model_use_and_holdouts(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = pathlib.Path(tmp)
            export_public_health_dataset.export_package(output_dir)
            with (output_dir / "analysis_dependency_audit.csv").open() as f:
                rows = list(csv.DictReader(f))

        by_surface = {row["surface"]: row for row in rows}
        self.assertEqual(
            "updated",
            by_surface["visibility_module_c"]["status"],
        )
        self.assertIn("7793", by_surface["visibility_module_c"]["input_values"])
        # The retired cumulative-suspected figure (349) must no longer appear on
        # the visibility input surface; confirmed is now the only cumulative input.
        self.assertNotIn("349", by_surface["visibility_module_c"]["input_values"])
        # C2 is not issued this cycle: its carried queue's expected yield is
        # already inside the confirmed count, so publishing a projection built on
        # that queue's own (older) confirmed base would plot a stale total below
        # the current confirmed line and double-count what has already resolved.
        # The row must still appear, naming why nothing was published.
        self.assertEqual(
            "not_issued",
            by_surface["active_queue_projection_c2"]["status"],
        )
        self.assertIn(
            "already inside the confirmed count",
            by_surface["active_queue_projection_c2"]["clock_basis"],
        )
        self.assertEqual(
            "not_issued_this_cycle",
            by_surface["active_queue_projection_c2"]["model_use"],
        )
        # C2 tracks the most recent edition that published a usable full active
        # suspected queue, and the confirmed anchor comes from that same edition.
        # Later suspected-in-isolation care censuses are narrower stocks and must
        # not revive the C2 graph.
        c2_values = by_surface["active_queue_projection_c2"]["input_values"]
        self.assertIn("355", c2_values)
        self.assertIn("289", c2_values)
        self.assertEqual(
            "updated_snapshot_level",
            by_surface["death_back_projection_and_grid"]["status"],
        )
        self.assertIn("3761", by_surface["death_back_projection_and_grid"]["input_values"])
        self.assertIn(
            "SitRep #130",
            by_surface["death_back_projection_and_grid"]["clock_basis"],
        )
        self.assertEqual("", by_surface["death_back_projection_and_grid"]["held_out_reason"])
        self.assertEqual(
            "source_attribution_lag",
            by_surface["corridor_watchlist"]["status"],
        )
        # The per-zone table closes on the DRC national count, so the DRC
        # residual is 0; Uganda's 20 stay in the country-scope headline and are
        # never reported as an unallocated DRC zone residual.
        corridor_inputs = json.loads(by_surface["corridor_watchlist"]["input_values"])
        self.assertEqual(7793, corridor_inputs["headline_confirmed"])
        self.assertEqual(7773, corridor_inputs["drc_confirmed"])
        self.assertEqual(7773, corridor_inputs["zone_attributed_confirmed"])
        self.assertEqual(0, corridor_inputs["unallocated_drc_confirmed"])
        self.assertNotIn("unallocated_headline_confirmed", corridor_inputs)
        self.assertIn(
            "Against the DRC national count of 7773, 0 DRC confirmed are unallocated",
            by_surface["corridor_watchlist"]["blocked_by"],
        )
        self.assertIn("inrb-sitrep-130-2026-09-21", by_surface["corridor_watchlist"]["blocked_by"])

    def test_public_deliverables_carry_no_source_review_status_token(self):
        """Regression gate: the internal source-review status signal must never
        reach a public surface. Sources/clocks may keep the source as provenance,
        but the structured status token (source_review / display_only / superseded)
        is redacted from every shipped CSV and the workbook XML."""
        forbidden = (
            export_public_health_dataset.PUBLIC_SUPPRESSED_TABLE_SEMANTICS
            | export_public_health_dataset.PUBLIC_SUPPRESSED_MODEL_USES
        )
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = pathlib.Path(tmp)
            paths = export_public_health_dataset.export_package(output_dir)
            csv_blobs = {
                p.name: p.read_text()
                for p in output_dir.glob("*.csv")
            }
            with zipfile.ZipFile(paths["workbook"]) as zf:
                workbook_xml = "\n".join(
                    zf.read(n).decode("utf-8", "replace")
                    for n in zf.namelist()
                    if n.endswith(".xml")
                )

        for token in forbidden:
            for name, blob in csv_blobs.items():
                self.assertNotIn(token, blob, f"{name} leaks source-review token {token!r}")
            self.assertNotIn(token, workbook_xml, f"workbook leaks source-review token {token!r}")

    def test_source_death_fields_export_under_death_metrics(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = pathlib.Path(tmp)
            export_public_health_dataset.export_package(output_dir)
            with (output_dir / "reported_counts.csv").open() as f:
                rows = [
                    r for r in csv.DictReader(f)
                    if r["row_type"] == "source_extracted_metric"
                    and any(
                        token in r["row_id"].split(":", 2)[2].rsplit(".", 1)[-1]
                        for token in ("death", "deces")
                    )
                ]

        self.assertTrue(rows, "expected source-level death rows")
        for row in rows:
            self.assertTrue(
                any(token in row["metric"] for token in ("death", "deces")), msg=row["row_id"]
            )

    def test_timeline_basis_column(self):
        # BINARY CHECK (Step 2): every per-point death row carries an explicit
        # basis. A death row dated on/after the 2026-06-02 cutoff is
        # confirmed_only; a death row dated before the cutoff is broad_register;
        # case rows carry an empty basis.
        #
        # The 2026-06-02 deaths_confirmed row is the snapshot-reconciled death
        # row (as_of 2026-06-02). The pre-cutoff death row is a source-extracted
        # death point carried into the timeline. Both are exercised through the
        # real emit paths with controlled inputs so the assertion is
        # deterministic and independent of the production snapshot.
        june2_deaths_row = export_public_health_dataset.build_reported_counts_rows(
            {
                "as_of": "2026-06-02T23:59:59Z",
                "country_scope": ["COD", "UGA"],
                "reported_counts": {},
                "reported_deaths": {
                    "confirmed": {
                        "primary": 63,
                        "min": 61,
                        "max": 63,
                        "primary_source_id": "inrb-sitrep-019-2026-06-02",
                        "conflicting_source_ids": [],
                    },
                },
            },
            {"entries": []},
            {},
            {},
        )
        by_metric = {row["metric"]: row for row in june2_deaths_row}
        self.assertIn("deaths_confirmed", by_metric)
        self.assertEqual("confirmed_only", by_metric["deaths_confirmed"]["basis"])

        # Pre-cutoff: a source-extracted deaths point (dated 2026-05-31) projected
        # through build_timeline_rows must carry broad_register, while a case row
        # on the same date carries an empty basis.
        count_rows = [
            {
                "row_id": "source:inrb-sitrep-017-2026-05-31:deaths",
                "row_type": "source_extracted_metric",
                "metric": "deaths",
                "location": "COD",
                "as_of_date": "2026-05-31",
                "value": 49,
                "unit": "count",
                "source_id": "inrb-sitrep-017-2026-05-31",
                "evidence_ref": "PUBLIC-CLAIM-AUDIT",
                "source_url": "",
                "archive_sha256": "",
                "license": "",
                "correction_note": "",
            },
            {
                "row_id": "source:inrb-sitrep-017-2026-05-31:cases_confirmed",
                "row_type": "source_extracted_metric",
                "metric": "confirmed_cases",
                "location": "COD",
                "as_of_date": "2026-05-31",
                "value": 328,
                "unit": "count",
                "source_id": "inrb-sitrep-017-2026-05-31",
                "evidence_ref": "PUBLIC-CLAIM-AUDIT",
                "source_url": "",
                "archive_sha256": "",
                "license": "",
                "correction_note": "",
            },
        ]
        timeline = export_public_health_dataset.build_timeline_rows(count_rows)
        by_id = {row["row_id"]: row for row in timeline}
        deaths_row = by_id["timeline:inrb-sitrep-017-2026-05-31:deaths"]
        case_row = by_id["timeline:inrb-sitrep-017-2026-05-31:cases_confirmed"]
        self.assertEqual("broad_register", deaths_row["basis"])
        self.assertEqual("", case_row["basis"])
        # Every emitted timeline row carries the basis column.
        for row in timeline:
            self.assertIn("basis", row)

    def test_timeline_csv_has_basis_column(self):
        # The basis column must reach the shipped timeline.csv surface.
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = pathlib.Path(tmp)
            export_public_health_dataset.export_package(output_dir)
            with (output_dir / "timeline.csv").open() as f:
                reader = csv.DictReader(f)
                self.assertIn("basis", reader.fieldnames)
                rows = list(reader)
        # Any death-metric timeline row dated on/after the cutoff is confirmed_only;
        # any dated before is broad_register; case rows are empty, and so are
        # suspected, probable and alert death counts, which are off the death tier.
        for row in rows:
            off_tier = any(token in row["metric"] for token in ("suspect", "probable", "alert"))
            if "death" in row["metric"] and not off_tier:
                expected = (
                    "confirmed_only" if row["date"][:10] >= "2026-06-02" else "broad_register"
                )
                self.assertEqual(expected, row["basis"], msg=row["row_id"])
            else:
                self.assertEqual("", row["basis"], msg=row["row_id"])

    @staticmethod
    def _source_rows_by_key(entries: list[dict]) -> dict[str, tuple]:
        rows = export_public_health_dataset.build_reported_counts_rows(
            {}, {"entries": entries}, {}, {}
        )
        return {
            row["row_id"].split(":", 2)[2]: (row["metric"], row["location"], row["value"])
            for row in rows
            if row["row_type"] == "source_extracted_metric"
        }

    def test_insp_country_scope_composition_rows_carry_their_own_geography(self):
        # An INSP SitRep reports the DRC terms beside the DRC + Uganda
        # country-scope total and the Uganda anchor. The entry's country_scope is
        # COD, but only the DRC terms are DRC figures: a consumer filtering
        # metric=confirmed_cases and location=COD must get 7773, never 7793 or 20.
        by_key = self._source_rows_by_key([{
            "source_id": "inrb-sitrep-130-2026-09-21",
            "country_scope": ["COD"],
            "geography_id": "COD:national",
            "normalized_content": {
                "data_as_of": "2026-09-21",
                "country_scope_confirmed_total": 7793,
                "country_scope_confirmed_uganda_anchor": 20,
                "cumul_cas_confirmes_drc": 7773,
                "country_scope_confirmed_deaths": 3761,
                "country_scope_confirmed_deaths_uganda_anchor": 2,
                "cumul_deces_parmi_confirmes_drc": 3759,
                "country_scope_recovered_total": 1946,
                "country_scope_recovered_uganda_anchor": 11,
                "gueris": 1935,
                "contact_followup_rate_pct": 81.4,
            },
        }])
        self.assertEqual(
            {
                "country_scope_confirmed_total": ("country_scope_confirmed_cases", "COD; UGA", 7793),
                "country_scope_confirmed_uganda_anchor": ("confirmed_cases", "UGA", 20),
                "cumul_cas_confirmes_drc": ("confirmed_cases", "COD", 7773),
                "country_scope_confirmed_deaths": ("country_scope_deaths", "COD; UGA", 3761),
                "country_scope_confirmed_deaths_uganda_anchor": ("deaths", "UGA", 2),
                "cumul_deces_parmi_confirmes_drc": ("deaths", "COD", 3759),
                "country_scope_recovered_total": ("country_scope_recovered_total", "COD; UGA", 1946),
                "country_scope_recovered_uganda_anchor": (
                    "country_scope_recovered_uganda_anchor", "UGA", 11,
                ),
                "gueris": ("gueris", "COD", 1935),
                "contact_followup_rate_pct": ("contact_followup_rate_pct", "COD", 81.4),
            },
            by_key,
        )

    def test_key_geography_overrides_the_entry_country_scope(self):
        by_key = self._source_rows_by_key([
            {
                # Early INSP SitReps name the country in English keys.
                "source_id": "inrb-sitrep-015-2026-05-29",
                "country_scope": ["COD"],
                "geography_id": "COD:national",
                "normalized_content": {
                    "data_as_of": "2026-05-29",
                    "cases_confirmed_total": 270,
                    "cases_confirmed_drc": 263,
                    "cases_confirmed_uganda": 7,
                    "deaths_confirmed_total": 43,
                    "deaths_confirmed_drc": 42,
                    "deaths_uganda": 1,
                },
            },
            {
                # A country-scope probable death is not a confirmed death.
                "source_id": "inrb-sitrep-018-2026-06-01",
                "country_scope": ["COD"],
                "geography_id": "COD:national",
                "normalized_content": {
                    "data_as_of": "2026-06-01",
                    "country_scope_probable_deaths": 1,
                },
            },
            {
                # SitReps 112 to 118 were recorded with a two-country entry scope;
                # their unqualified figures are still DRC national figures.
                "source_id": "inrb-sitrep-112-2026-09-03",
                "country_scope": ["COD", "UGA"],
                "geography_id": "drc",
                "normalized_content": {
                    "data_as_of": "2026-09-03",
                    "new_confirmed_24h": 31,
                },
            },
            {
                # A multi-country source: an unqualified value keeps the entry
                # scope, and a key naming two countries does not narrow it.
                "source_id": "cdc-current-situation-2026-05-23",
                "country_scope": ["COD", "UGA"],
                "geography_id": "ituri-bdbv-corridor",
                "normalized_content": {
                    "data_as_of": "2026-05-23",
                    "cases_confirmed": 88,
                    "cases_confirmed_united_states": 0,
                    "suspected_cases_italy": 2,
                    "uganda_cases_drc_travel_linked": 5,
                },
            },
        ])
        self.assertEqual(("country_scope_confirmed_cases", "COD; UGA", 270), by_key["cases_confirmed_total"])
        self.assertEqual(("confirmed_cases", "COD", 263), by_key["cases_confirmed_drc"])
        self.assertEqual(("confirmed_cases", "UGA", 7), by_key["cases_confirmed_uganda"])
        self.assertEqual(("country_scope_deaths", "COD; UGA", 43), by_key["deaths_confirmed_total"])
        self.assertEqual(("deaths", "COD", 42), by_key["deaths_confirmed_drc"])
        self.assertEqual(("deaths", "UGA", 1), by_key["deaths_uganda"])
        self.assertEqual(
            ("country_scope_probable_deaths", "COD; UGA", 1),
            by_key["country_scope_probable_deaths"],
        )
        self.assertEqual(("new_confirmed_cases_24h", "COD", 31), by_key["new_confirmed_24h"])
        self.assertEqual(("confirmed_cases", "COD; UGA", 88), by_key["cases_confirmed"])
        self.assertEqual(("confirmed_cases", "USA", 0), by_key["cases_confirmed_united_states"])
        self.assertEqual(("suspected_cases", "ITA", 2), by_key["suspected_cases_italy"])
        self.assertEqual(
            ("uganda_cases_drc_travel_linked", "COD; UGA", 5),
            by_key["uganda_cases_drc_travel_linked"],
        )

    def test_snapshot_reconciled_rows_are_country_scope(self):
        # The reconciled headline is the DRC + Uganda country-scope count.
        rows = export_public_health_dataset.build_reported_counts_rows(
            {
                "as_of": "2026-09-21T23:59:59Z",
                "reported_counts": {
                    "confirmed": {"primary": 7793, "min": 7753, "max": 7793,
                                  "primary_source_id": "inrb-sitrep-130-2026-09-21"},
                },
                "reported_deaths": {
                    "confirmed": {"primary": 3761, "min": 3734, "max": 3761,
                                  "primary_source_id": "inrb-sitrep-130-2026-09-21"},
                },
            },
            {"entries": []},
            {},
            {},
        )
        by_id = {row["row_id"]: row for row in rows}
        confirmed = by_id["snapshot:reported_counts:confirmed"]
        deaths = by_id["snapshot:reported_deaths:confirmed"]
        self.assertEqual(("confirmed_cases", "COD; UGA"), (confirmed["metric"], confirmed["location"]))
        self.assertEqual(("deaths_confirmed", "COD; UGA"), (deaths["metric"], deaths["location"]))

    def test_timeline_rows_carry_the_source_row_location(self):
        count_rows = export_public_health_dataset.build_reported_counts_rows(
            {},
            {"entries": [{
                "source_id": "inrb-sitrep-130-2026-09-21",
                "country_scope": ["COD"],
                "geography_id": "COD:national",
                "normalized_content": {
                    "data_as_of": "2026-09-21",
                    "country_scope_confirmed_total": 7793,
                    "country_scope_confirmed_uganda_anchor": 20,
                    "cumul_cas_confirmes_drc": 7773,
                },
            }]},
            {},
            {},
        )
        timeline = export_public_health_dataset.build_timeline_rows(count_rows)
        by_key = {
            row["row_id"].split(":", 2)[2]: (row["metric"], row["location"], row["value"])
            for row in timeline
        }
        self.assertEqual(
            {
                "country_scope_confirmed_total": ("country_scope_confirmed_cases", "COD; UGA", 7793),
                "country_scope_confirmed_uganda_anchor": ("confirmed_cases", "UGA", 20),
                "cumul_cas_confirmes_drc": ("confirmed_cases", "COD", 7773),
            },
            by_key,
        )

    def test_exported_country_scope_rows_never_read_as_drc(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = pathlib.Path(tmp)
            export_public_health_dataset.export_package(output_dir)
            with (output_dir / "reported_counts.csv").open() as f:
                counts = list(csv.DictReader(f))
            with (output_dir / "timeline.csv").open() as f:
                reader = csv.DictReader(f)
                self.assertIn("location", reader.fieldnames)
                timeline = list(reader)

        checked = 0
        for label, rows in (("reported_counts", counts), ("timeline", timeline)):
            for row in rows:
                parts = row["row_id"].split(":", 2)
                if len(parts) != 3 or not parts[2].startswith("country_scope_"):
                    continue
                checked += 1
                expected = "UGA" if parts[2].endswith("_uganda_anchor") else "COD; UGA"
                self.assertEqual(expected, row["location"], msg=f"{label} {row['row_id']}")
                if row["metric"] in ("confirmed_cases", "deaths"):
                    self.assertEqual("UGA", row["location"], msg=f"{label} {row['row_id']}")
        self.assertGreater(checked, 0, "expected country-scope source rows")

    @staticmethod
    def _source_metric_location_unit(entries: list[dict]) -> dict[str, tuple]:
        rows = export_public_health_dataset.build_reported_counts_rows(
            {}, {"entries": entries}, {}, {}
        )
        return {
            row["row_id"].split(":", 2)[2]: (row["metric"], row["location"], row["unit"])
            for row in rows
            if row["row_type"] == "source_extracted_metric"
        }

    def test_only_cumulative_fields_join_the_cumulative_series(self):
        # An INSP SitRep prints 24-hour increments, isolation censuses, zone-table
        # bookkeeping, percentages and its own number beside the DRC cumulative
        # counts. Only the cumulative fields may carry confirmed_cases or deaths,
        # so a filter on metric and location returns the cumulative series alone.
        by_key = self._source_metric_location_unit([{
            "source_id": "inrb-sitrep-112-2026-09-03",
            "country_scope": ["COD"],
            "geography_id": "COD:national",
            "normalized_content": {
                "data_as_of": "2026-09-03",
                "sitrep_number": 112,
                "cumul_cas_confirmes_drc": 6100,
                "cumul_deces_parmi_confirmes_drc": 2900,
                "new_confirmed_24h": 94,
                "new_confirmed_deaths_24h": 23,
                "total_confirmed_deaths_24h": 23,
                "community_deaths_24h": 17,
                "cte_deaths_24h": 6,
                "suspected_cases_day": 300,
                "suspected_deaths_day": 40,
                "contact_followup_rate_pct": 81.4,
                "health_zone_table": {
                    "reconciliation": {
                        "notified_24h_confirmed": 94,
                        "named_zone_confirmed_sum": 6050,
                        "national_confirmed_total": 6100,
                    },
                },
                "operational_tables": {
                    "patient_movement_total": {"confirmed_in_isolation": 150},
                    "alerts_total": {"alerts_validated_deaths": 40},
                    "care_capacity_by_province": {"Ituri": {"confirmedOccupancyPct": 61.5}},
                    "ppl_infections": {"confirmed": 121, "deaths": 36},
                },
                "province_operational": {
                    "byProvince": {"Ituri": {"confirmedInIsolation": 90}},
                },
            },
        }])
        self.assertEqual(
            {
                "sitrep_number": ("sitrep_number", "COD", "identifier"),
                "cumul_cas_confirmes_drc": ("confirmed_cases", "COD", "count"),
                "cumul_deces_parmi_confirmes_drc": ("deaths", "COD", "count"),
                "new_confirmed_24h": ("new_confirmed_cases_24h", "COD", "count"),
                "new_confirmed_deaths_24h": ("new_confirmed_deaths_24h", "COD", "count"),
                "total_confirmed_deaths_24h": ("new_confirmed_deaths_24h", "COD", "count"),
                "community_deaths_24h": ("community_deaths_24h", "COD", "count"),
                "cte_deaths_24h": ("cte_deaths_24h", "COD", "count"),
                "suspected_cases_day": ("new_suspected_cases_24h", "COD", "count"),
                "suspected_deaths_day": ("new_suspected_deaths_24h", "COD", "count"),
                "contact_followup_rate_pct": ("contact_followup_rate_pct", "COD", "percent"),
                "health_zone_table.reconciliation.notified_24h_confirmed": (
                    "health_zone_table_reconciliation_notified_24h_confirmed", "COD", "count",
                ),
                "health_zone_table.reconciliation.named_zone_confirmed_sum": (
                    "health_zone_table_reconciliation_named_zone_confirmed_sum", "COD", "count",
                ),
                "health_zone_table.reconciliation.national_confirmed_total": (
                    "health_zone_table_reconciliation_national_confirmed_total", "COD", "count",
                ),
                "operational_tables.patient_movement_total.confirmed_in_isolation": (
                    "operational_tables_patient_movement_total_confirmed_in_isolation", "COD", "count",
                ),
                "operational_tables.alerts_total.alerts_validated_deaths": (
                    "operational_tables_alerts_total_alerts_validated_deaths", "COD", "count",
                ),
                "operational_tables.care_capacity_by_province.Ituri.confirmedOccupancyPct": (
                    "operational_tables_care_capacity_by_province_Ituri_confirmedOccupancyPct",
                    "COD",
                    "percent",
                ),
                # Health-worker infections are a subset, not the national count.
                "operational_tables.ppl_infections.confirmed": (
                    "operational_tables_ppl_infections_confirmed", "COD", "count",
                ),
                "operational_tables.ppl_infections.deaths": (
                    "operational_tables_ppl_infections_deaths", "COD", "count",
                ),
                "province_operational.byProvince.Ituri.confirmedInIsolation": (
                    "province_operational_byProvince_Ituri_confirmedInIsolation", "COD", "count",
                ),
            },
            by_key,
        )

    def test_suspected_probable_active_and_partial_figures_keep_their_own_metrics(self):
        by_key = self._source_metric_location_unit([
            {
                "source_id": "cdc-current-situation-2026-06-02",
                "country_scope": ["COD", "UGA"],
                "geography_id": "ituri-bdbv-corridor",
                "normalized_content": {
                    "data_as_of": "2026-06-02",
                    "deaths_suspected": 134,
                    "deaths_suspected_drc": 134,
                    "cases_probable": 105,
                    "uganda_probable_cases": 1,
                    "uganda_probable_deaths": 1,
                    "new_confirmed_cases_24_to_48h": 26,
                    "new_confirmed_cases_uganda": 3,
                },
            },
            {
                "source_id": "who-don603-2026-05-21-live",
                "country_scope": ["COD", "UGA"],
                "geography_id": "ituri-bdbv-corridor",
                "normalized_content": {
                    "data_as_of": "2026-05-21",
                    "cases_suspected_approx": 600,
                    "cases_suspected_min": 500,
                    "cfr_suspected_pct": 25.4,
                    "cfr_central": 0.3,
                    "contact_followup_rate": 0.21,
                    "deaths_used": 88,
                    "health_worker_deaths": 4,
                    # Until June every Uganda case was imported, so these May
                    # fields are Uganda's whole count (total 85 = DRC 83 + 2).
                    "cases_confirmed_uganda_imported": 2,
                    "deaths_confirmed_uganda_imported": 1,
                    # A 20 May page quoting 19 May figures is not a 20 May count.
                    "earlier_figures_19_may": {"cases_suspected": 543, "deaths_at_least": 131},
                },
            },
            {
                "source_id": "inrb-sitrep-017-2026-05-31",
                "country_scope": ["COD"],
                "geography_id": "COD:national",
                "normalized_content": {
                    "data_as_of": "2026-05-31",
                    "cumul_cas_suspects": 349,
                    "cases_confirmed_active_drc": 267,
                    "cas_confirmes_actifs": 267,
                    "cases_confirmed_active_total": 274,
                    "suspected_active_total": 220,
                    "country_scope_probable_total": 1,
                },
            },
            {
                # From June Uganda splits its count; the split is a subset.
                "source_id": "uganda-moh-ebola-update-2026-06-06",
                "country_scope": ["UGA"],
                "geography_id": "UGA:national",
                "normalized_content": {
                    "data_as_of": "2026-06-06",
                    "cases_confirmed_uganda": 19,
                    "uganda_imported_confirmed": 14,
                    "uganda_local_confirmed": 5,
                },
            },
        ])
        self.assertEqual(
            {
                "deaths_suspected": ("suspected_deaths", "COD; UGA", "count"),
                "deaths_suspected_drc": ("suspected_deaths", "COD", "count"),
                "cases_probable": ("probable_cases", "COD; UGA", "count"),
                "uganda_probable_cases": ("probable_cases", "UGA", "count"),
                "uganda_probable_deaths": ("probable_deaths", "UGA", "count"),
                "new_confirmed_cases_24_to_48h": (
                    "new_confirmed_cases_24_to_48h", "COD; UGA", "count",
                ),
                "new_confirmed_cases_uganda": ("new_confirmed_cases_uganda", "UGA", "count"),
                "cases_suspected_approx": ("suspected_cases", "COD; UGA", "count"),
                "cases_suspected_min": ("cases_suspected_min", "COD; UGA", "count"),
                "cfr_suspected_pct": ("cfr_suspected_pct", "COD; UGA", "percent"),
                "cfr_central": ("cfr_central", "COD; UGA", "proportion"),
                "contact_followup_rate": ("contact_followup_rate", "COD; UGA", "proportion"),
                "deaths_used": ("deaths_used", "COD; UGA", "count"),
                "health_worker_deaths": ("health_worker_deaths", "COD; UGA", "count"),
                "cases_confirmed_uganda_imported": ("confirmed_cases", "UGA", "count"),
                "deaths_confirmed_uganda_imported": ("deaths", "UGA", "count"),
                "cases_confirmed_uganda": ("confirmed_cases", "UGA", "count"),
                "uganda_imported_confirmed": ("uganda_imported_confirmed", "UGA", "count"),
                "uganda_local_confirmed": ("uganda_local_confirmed", "UGA", "count"),
                "earlier_figures_19_may.cases_suspected": (
                    "earlier_figures_19_may_cases_suspected", "COD; UGA", "count",
                ),
                "earlier_figures_19_may.deaths_at_least": (
                    "earlier_figures_19_may_deaths_at_least", "COD; UGA", "count",
                ),
                "cumul_cas_suspects": ("suspected_cases", "COD", "count"),
                "cases_confirmed_active_drc": ("active_confirmed_cases", "COD", "count"),
                "cas_confirmes_actifs": ("active_confirmed_cases", "COD", "count"),
                "cases_confirmed_active_total": (
                    "country_scope_active_confirmed_cases", "COD; UGA", "count",
                ),
                "suspected_active_total": ("active_suspected_cases", "COD", "count"),
                "country_scope_probable_total": ("country_scope_probable_cases", "COD; UGA", "count"),
            },
            by_key,
        )

    def test_an_unclassified_case_field_stops_the_export(self):
        # The vocabulary is an allow-list, so a new top-level case field must be
        # classified before release rather than silently leave its series.
        entry = {
            "source_id": "inrb-sitrep-131-2026-09-22",
            "country_scope": ["COD"],
            "geography_id": "COD:national",
            "normalized_content": {
                "data_as_of": "2026-09-22",
                "cumul_cas_confirmes_rdc": 7790,
                "operational_tables": {"new_block": {"confirmed_transferred": 3}},
            },
        }
        with self.assertRaisesRegex(ValueError, "cumul_cas_confirmes_rdc"):
            export_public_health_dataset.build_reported_counts_rows({}, {"entries": [entry]}, {}, {})
        # A nested table field is named by its own path and needs no entry.
        del entry["normalized_content"]["cumul_cas_confirmes_rdc"]
        self.assertEqual(
            {"operational_tables.new_block.confirmed_transferred": (
                "operational_tables_new_block_confirmed_transferred", "COD", "count",
            )},
            self._source_metric_location_unit([entry]),
        )

    def test_health_zone_rows_are_per_zone_drc_counts(self):
        # A WHO AFRO SitRep covers DRC and Uganda, but its health zones are DRC
        # zones: each zone row is DRC, and never a DRC-wide cumulative count.
        by_key = self._source_metric_location_unit([{
            "source_id": "afro-sitrep-01-pdf-2026-05-18-live",
            "country_scope": ["COD", "UGA"],
            "geography_id": "ituri-bdbv-corridor",
            "normalized_content": {
                "data_as_of": "2026-05-18",
                "affected_health_zones": {
                    "bunia": {"confirmed": 6, "deaths": 18, "suspected": 61},
                    "affected": 7,
                },
            },
        }])
        self.assertEqual(
            {
                "affected_health_zones.bunia.confirmed": ("health_zone_confirmed_cases", "COD", "count"),
                "affected_health_zones.bunia.deaths": ("health_zone_deaths", "COD", "count"),
                "affected_health_zones.bunia.suspected": ("health_zone_suspected_cases", "COD", "count"),
                "affected_health_zones.affected": ("affected_health_zones_affected", "COD", "count"),
            },
            by_key,
        )

    def test_units_name_what_a_non_count_value_measures(self):
        by_key = self._source_metric_location_unit([{
            "source_id": "inrb-sitrep-090-2026-08-12",
            "country_scope": ["COD"],
            "geography_id": "COD:national",
            "normalized_content": {
                "data_as_of": "2026-08-12",
                "letalite_pct": 46.8,
                "lab_indicators_24h": {"positivity_percent": 12.5, "pending_delay_days_gt": 3},
                "province_operational": {"national": {"bedOccupancyPct": 70.1}},
                "monitoring_period_days": 21,
                "funding_gbp_up_to": 20000000,
                "http_status": 200,
                "post_id": 25553,
                "media_asset_id": 25554,
                "asset_size": 10487530,
                "pdf_page_count": 10,
                "source_receipt": {"byte_length": 723708, "media_id": 25554, "post_id": 25553},
            },
        }])
        self.assertEqual(
            {
                "letalite_pct": "percent",
                "lab_indicators_24h.positivity_percent": "percent",
                "lab_indicators_24h.pending_delay_days_gt": "days",
                "province_operational.national.bedOccupancyPct": "percent",
                "monitoring_period_days": "days",
                "funding_gbp_up_to": "GBP",
                "http_status": "identifier",
                "post_id": "identifier",
                "media_asset_id": "identifier",
                "asset_size": "bytes",
                "pdf_page_count": "count",
                "source_receipt.byte_length": "bytes",
                "source_receipt.media_id": "identifier",
                "source_receipt.post_id": "identifier",
            },
            {key: unit for key, (_, _, unit) in by_key.items()},
        )

    def test_basis_labels_only_the_confirmed_death_tier(self):
        basis = export_public_health_dataset.death_basis
        self.assertEqual("confirmed_only", basis("deaths", "2026-09-21"))
        self.assertEqual("confirmed_only", basis("new_confirmed_deaths_24h", "2026-09-21"))
        self.assertEqual("broad_register", basis("health_zone_deaths", "2026-05-21"))
        for metric in (
            "suspected_deaths",
            "new_suspected_deaths_24h",
            "probable_deaths",
            "country_scope_probable_deaths",
            "deaths_probable",
            "operational_tables_alerts_total_alerts_validated_deaths",
            "operational_tables_patient_movement_total_deaths_suspect_or_confirmed_24h",
        ):
            with self.subTest(metric=metric):
                self.assertEqual("", basis(metric, "2026-09-21"))
                self.assertEqual("", basis(metric, "2026-05-21"))

    def test_exported_cumulative_series_hold_one_value_per_source(self):
        # metric plus location must select one cumulative series: every source
        # gives each series one value, taken from a top-level source field.
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = pathlib.Path(tmp)
            export_public_health_dataset.export_package(output_dir)
            with (output_dir / "reported_counts.csv").open() as f:
                counts = list(csv.DictReader(f))
            with (output_dir / "timeline.csv").open() as f:
                timeline = list(csv.DictReader(f))

        cumulative = {
            "confirmed_cases", "deaths", "suspected_cases", "suspected_deaths",
            "probable_cases", "probable_deaths", "country_scope_confirmed_cases",
            "country_scope_deaths", "country_scope_probable_cases",
            "country_scope_probable_deaths",
        }
        for label, rows in (("reported_counts", counts), ("timeline", timeline)):
            values: dict[tuple, set] = {}
            for row in rows:
                parts = row["row_id"].split(":", 2)
                if parts[0] not in ("source", "timeline") or row["metric"] not in cumulative:
                    continue
                self.assertNotIn(".", parts[2], msg=f"{label} {row['row_id']}")
                self.assertEqual("count", row["unit"], msg=f"{label} {row['row_id']}")
                values.setdefault((parts[1], row["metric"], row["location"]), set()).add(row["value"])
            self.assertTrue(values, label)
            conflicts = {key: sorted(v) for key, v in values.items() if len(v) > 1}
            self.assertEqual({}, conflicts, label)

    def test_workbook_is_byte_deterministic(self):
        """Two exports of the same snapshot must produce identical workbook bytes."""
        with tempfile.TemporaryDirectory() as t1, tempfile.TemporaryDirectory() as t2:
            w1 = export_public_health_dataset.export_package(pathlib.Path(t1))["workbook"]
            w2 = export_public_health_dataset.export_package(pathlib.Path(t2))["workbook"]
            self.assertEqual(w1.read_bytes(), w2.read_bytes())


if __name__ == "__main__":
    unittest.main()
