# SPDX-License-Identifier: Apache-2.0
"""Tests for the generated snapshot contract gate."""
from __future__ import annotations

import copy
import csv
import json
import pathlib
import shutil
import tempfile
import unittest
from unittest import mock

import export_public_health_dataset
from lovs import snapshot_contract


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


class TestSnapshotContract(unittest.TestCase):
    def _snapshot(self) -> dict:
        return json.loads(
            (REPO_ROOT / "data" / "live-bdbv-2026-output.json").read_text(encoding="utf-8")
        )

    def test_multi_country_cut_without_reviewed_composition_fails_closed(self):
        snapshot = self._snapshot()
        # A non-SitRep headline source carries no reviewed DRC/Uganda split.
        snapshot["reported_counts"]["confirmed"]["primary_source_id"] = "who-don-2026-09-21"
        with self.assertRaisesRegex(snapshot_contract.SnapshotContractError, "multi-country"):
            snapshot_contract.build_contract(snapshot)

    def test_drc_denominator_falls_back_only_for_a_single_country_cut(self):
        composition = {"confirmed": {"total": 7793, "drc": 7773, "uganda": 20}}
        self.assertEqual(
            7773,
            snapshot_contract._drc_confirmed_denominator({"data_as_of": "2026-09-21"}, 7793, composition),
        )
        # Before any reviewed SitRep shows a second country, the headline is DRC-only.
        self.assertEqual(
            250,
            snapshot_contract._drc_confirmed_denominator({"data_as_of": "2026-05-28"}, 250, {}),
        )
        # From the first reviewed multi-country SitRep (SR15, 2026-05-29) onward it is not.
        with self.assertRaisesRegex(snapshot_contract.SnapshotContractError, "inrb-sitrep-015"):
            snapshot_contract._drc_confirmed_denominator({"data_as_of": "2026-05-29"}, 270, {})
        with self.assertRaisesRegex(snapshot_contract.SnapshotContractError, "data_as_of"):
            snapshot_contract._drc_confirmed_denominator({}, 7793, {})

    def test_fragment_match_respects_number_boundaries(self):
        present = snapshot_contract.narrative_fragment_present
        self.assertFalse(present("the remaining 20 confirmed cases", "0 confirmed cases"))
        self.assertTrue(present("the DRC residual is **0 confirmed cases**", "0 confirmed cases"))
        self.assertFalse(present("77730 cases", "7773"))
        self.assertTrue(present("a total of 7773.", "7773"))

    def test_stale_combined_residual_prose_fails_the_narrative_gate(self):
        contract = snapshot_contract.build_contract(self._snapshot())
        fragments = contract["narrative_required_fragments"]["headline_zone_unallocated"]
        self.assertIn("DRC national count is 7773 confirmed cases", fragments)
        current = (
            "There is no DRC confirmed-case source-attribution lag in this cut. The "
            "headline is 7793 confirmed cases; the DRC national count is 7773 confirmed "
            "cases. Corridor risk uses 7773 confirmed cases that are officially "
            "zone-attributed. The DRC residual is 0 confirmed cases, so none is unallocated."
        )
        tail = " ".join(f for f in fragments if "confirmed cases" not in f)
        snapshot_contract.validate_narrative(f"{current} {tail}", contract, "current")
        # The pre-fix brief took the residual against the DRC+Uganda headline.
        stale = current.replace(
            "The DRC residual is 0 confirmed cases, so none is unallocated.",
            "The remaining 20 confirmed cases are unallocated headline context.",
        )
        with self.assertRaisesRegex(snapshot_contract.SnapshotContractError, "0 confirmed cases"):
            snapshot_contract.validate_narrative(f"{stale} {tail}", contract, "stale")

    def test_contract_captures_current_partition(self):
        contract = snapshot_contract.build_contract(self._snapshot())

        self.assertEqual(7793, contract["confirmed_case_partition"]["headline_confirmed_total"])
        self.assertEqual(7773, contract["confirmed_case_partition"]["drc_confirmed_total"])
        self.assertEqual(7773, contract["confirmed_case_partition"]["zone_attributed_confirmed_total"])
        self.assertEqual(0, contract["confirmed_case_partition"]["unallocated_confirmed_total"])
        self.assertEqual(63, contract["corridor_watchlist"]["source_zone_count"])
        # Biena and Manguredjipa, registered by SitRep 104, widen the reviewed
        # source vector to 60 source zones. Crossing 60 sources with nine
        # targets and excluding the Goma/Beni self-edges yields 538.
        self.assertEqual(565, contract["corridor_watchlist"]["corridor_count"])
        # Zero-confirmed INSP-monitored zones are excluded from corridor
        # generation, so the descriptive watchlist no longer carries degenerate
        # [0,0] rows: the adjusted-50 lower-bound floor is now strictly positive.
        self.assertGreater(contract["corridor_watchlist"]["adjusted_50_lower_range_pct"][0], 0.0)
        self.assertGreater(contract["corridor_watchlist"]["adjusted_50_lower_range_pct"][1], 15.0)
        self.assertGreater(contract["corridor_watchlist"]["adjusted_50_upper_range_pct"][1], 40.0)
        self.assertEqual(
            "descriptive_watchlist_not_forecast",
            contract["method_status"]["corridor_interpretation"],
        )
        self.assertIn("do not scale", contract["method_status"]["source_load_policy"])
        self.assertIn("source-attribution lag", contract["method_status"]["source_load_policy"])
        self.assertEqual(0, contract["visibility_method"]["history_snapshot_count"])
        self.assertIn("single_snapshot", contract["visibility_method"]["method_basis"])
        self.assertIn("proxy", contract["visibility_method"]["method_basis"])
        self.assertEqual(
            "Rosello 2015 BDBV Isiro onset-to-notification",
            contract["visibility_method"]["delay_prior"]["label"],
        )
        self.assertEqual(
            [1.1345, 0.1285],
            contract["visibility_method"]["delay_prior"]["gamma_shape_rate"],
        )
        self.assertEqual(
            "ec:lovs:grepi:reporting-delay-update:2026-05-23",
            contract["visibility_method"]["delay_prior"]["evidence_chain_id"],
        )
        self.assertEqual(
            ["Camacho 2015 EBOV-Zaire onset-to-notification sensitivity"],
            [
                prior["label"]
                for prior in contract["visibility_method"]["sensitivity_delay_priors"]
            ],
        )
        self.assertEqual(
            {"total": 7793, "drc": 7773, "uganda": 20},
            {
                key: contract["country_scope_composition"]["confirmed"][key]
                for key in ("total", "drc", "uganda")
            },
        )
        self.assertEqual(
            {"total": 3761, "drc": 3759, "uganda": 2},
            {
                key: contract["country_scope_composition"]["confirmed_deaths"][key]
                for key in ("total", "drc", "uganda")
            },
        )
        self.assertEqual(
            {"total": 1946, "drc": 1935, "uganda": 11},
            {
                key: contract["country_scope_composition"]["recovered"][key]
                for key in ("total", "drc", "uganda")
            },
        )
        # The semantic-delta block exists only while an edition publishes a NATIONAL
        # confirmed/suspected split of the isolation census. SitRep 92 published one
        # (777 = 311 + 461 + a five-patient unclassified remainder); SitRep 93 splits
        # the census per province only, and those provinces sum to 466 suspected
        # against its own printed national census of 730, so no national split is
        # derived and the block is empty. Whenever it IS populated the partition must
        # close exactly and must not become a C2 denominator.
        delta = contract.get("inrb_semantic_delta") or {}
        if delta:
            census = delta["national_isolation_census"]
            self.assertEqual(
                census,
                delta["confirmed_in_isolation"]
                + delta["suspected_in_isolation"]
                + delta["unclassified_in_isolation"],
                "the published isolation partition must close on the census",
            )
            self.assertEqual(
                delta["suspected_in_isolation"], delta["reported_suspected_in_isolation"]
            )
            self.assertNotEqual(
                "suspected_in_isolation",
                delta.get("active_queue_basis"),
                "the isolation subtotal must never become the active-queue denominator",
            )

    def test_snapshot_contract_rejects_aggregate_smearing(self):
        snapshot = self._snapshot()
        smeared = copy.deepcopy(snapshot)
        for corridor in smeared["corridors"]:
            corridor["drivers"] = ["headline confirmed count 88 applied to this source zone"]

        with self.assertRaises(snapshot_contract.SnapshotContractError):
            snapshot_contract.validate_snapshot(smeared)

    def test_snapshot_contract_rejects_country_scope_mismatch(self):
        snapshot = copy.deepcopy(self._snapshot())
        # The partition guard ("zone-attributed exceeds headline") runs BEFORE the
        # country-scope composition check, so the synthetic primary must be >= the
        # fixture's zone-attributed total to reach the country-scope branch, yet
        # != the promoted country-scope total so the mismatch still fires. The
        # zone-attributed total is the smallest such value, and it moves every
        # cycle (2456 at SitRep67, 2519 at SitRep68), so derive it from the
        # fixture rather than pinning a literal that silently stops exercising
        # this branch when the numbers drift.
        zone_total = sum(
            int(zone["confirmed"]) for zone in snapshot["zone_attributed_counts"].values()
        )
        country_scope_total = int(snapshot["reported_counts"]["confirmed"]["primary"])
        self.assertNotEqual(
            zone_total,
            country_scope_total,
            "fixture no longer separates zone-attributed from country-scope; this test would "
            "pass vacuously",
        )
        snapshot["reported_counts"]["confirmed"]["primary"] = zone_total

        with self.assertRaisesRegex(
            snapshot_contract.SnapshotContractError,
            "country-scope total",
        ):
            snapshot_contract.build_contract(snapshot)

    def test_snapshot_contract_rejects_isolation_census_as_suspected(self):
        snapshot = copy.deepcopy(self._snapshot())
        snapshot["reported_counts"]["suspected_in_isolation"] = {
            "min": 262,
            "max": 262,
            "primary": 262,
            "primary_source_id": "inrb-sitrep-083-2026-08-05",
            "conflicting_source_ids": [],
        }

        with self.assertRaisesRegex(
            snapshot_contract.SnapshotContractError,
            "suspected-only split",
        ):
            snapshot_contract.build_contract(snapshot)

    def test_semantic_delta_allows_source_declared_unclassified_remainder(self):
        delta = {
            "source_id": "inrb-sitrep-test",
            "national_isolation_census": 674,
            "confirmed_in_isolation": 311,
            "suspected_in_isolation": 343,
            "unclassified_in_isolation": 20,
            "reported_suspected_in_isolation": 343,
        }

        snapshot_contract._validate_inrb_semantic_delta(delta)

        delta["unclassified_in_isolation"] = 30
        with self.assertRaisesRegex(snapshot_contract.SnapshotContractError, "isolation census"):
            snapshot_contract._validate_inrb_semantic_delta(delta)

    def test_snapshot_contract_does_not_reclassify_isolation_census_for_c2(self):
        snapshot = self._snapshot()
        c2 = next(
            row for row in snapshot["analysis_dependency_audit"]
            if row.get("surface") == "active_queue_projection_c2"
        )
        # The load-bearing invariant: the current suspected-in-isolation care
        # census is narrower than the full active suspected queue. It must never
        # revive C2 as if it were the full queue. C2 remains tied to SitRep 18,
        # the last edition publishing suspected_under_investigation + isolation.
        self.assertNotEqual(461, c2["inputs"]["active_suspected_total"])
        self.assertEqual(289, c2["inputs"]["active_suspected_total"])
        self.assertTrue(c2["inputs_provenance"]["carried_forward"])
        # The basis has to be named, and the confirmed anchor has to come from
        # the same edition as the queue, so the yield is never a current
        # confirmed count crossed with an older queue.
        self.assertEqual(
            "suspected_active_total",
            c2["inputs_provenance"]["active_queue_basis"],
        )
        self.assertEqual(
            c2["inputs_provenance"]["source_data_as_of"],
            c2["inputs_provenance"]["carriedForwardFrom"],
        )

    def test_snapshot_contract_allows_target_source_overlap_without_self_edge(self):
        snapshot_contract.validate_snapshot(self._snapshot())

    def test_snapshot_contract_rejects_stale_narrative(self):
        contract = snapshot_contract.build_contract(self._snapshot())
        stale = (
            "The current 42-corridor watchlist spans 64.7% to 69.5% upper bounds "
            "and applies the 84 confirmed cases to every source zone."
        )

        with self.assertRaises(snapshot_contract.SnapshotContractError):
            snapshot_contract.validate_narrative(stale, contract, "fixture")

    def test_snapshot_contract_rejects_corridor_overclaim(self):
        contract = snapshot_contract.build_contract(self._snapshot())
        overclaim = (
            "The current 66-corridor watchlist spans 0.6-20.8% lower and "
            "1.8-47.6% upper bounds using 84 confirmed cases, 79 confirmed "
            "cases, 5 confirmed cases, officially zone-attributed, "
            "source-attribution lag, "
            "unallocated, and 11 DRC MoH source zones. This is a corridor "
            "deployment ranking."
        )

        with self.assertRaises(snapshot_contract.SnapshotContractError):
            snapshot_contract.validate_narrative(overclaim, contract, "fixture")

    def test_snapshot_contract_rejects_undisclosed_single_snapshot_visibility(self):
        snapshot = copy.deepcopy(self._snapshot())
        snapshot["visibility"]["history_snapshot_count"] = 0
        snapshot["visibility"]["method_basis"] = "empirical_history"
        snapshot["visibility"]["method_caveat"] = "field-observed daily cadence"

        with self.assertRaises(snapshot_contract.SnapshotContractError):
            snapshot_contract.validate_snapshot(snapshot)

    def test_snapshot_contract_rejects_stale_camacho_default_for_bdbv_specific_run(self):
        snapshot = copy.deepcopy(self._snapshot())
        snapshot["visibility"]["delay_prior"] = {
            "label": "Camacho 2015 EBOV-Zaire onset-to-notification sensitivity",
            "gamma_shape_rate": [0.81, 0.18],
            "evidence_chain_id": "ec:lovs:module-c:reporting-delay-priors:2026-05-20",
        }
        snapshot["visibility"]["sensitivity_delay_priors"] = []

        with self.assertRaises(snapshot_contract.SnapshotContractError):
            snapshot_contract.validate_snapshot(snapshot)

    def test_narrative_rejects_stale_reporting_delay_attribution(self):
        contract = snapshot_contract.build_contract(self._snapshot())

        rosello_default = (
            "Reporting completeness 50% range [39.7%, 45.8%]. The inherent reporting "
            "delay (Rosello 2015 eLife BDBV Isiro 2012 onset-to-notification default, "
            "with Camacho 2015 retained as a faster-reporting sensitivity comparator)."
        )
        snapshot_contract.validate_visibility_prior_attribution(rosello_default, contract, "ok")

        camacho_as_default = (
            "Reporting completeness 50% range [39.7%, 45.8%]. The inherent reporting "
            "delay (Camacho 2015 PLOS Currents, an Ebola-Zaire onset-to-notification "
            "delay applied as a Bundibugyo proxy)."
        )
        with self.assertRaises(snapshot_contract.SnapshotContractError):
            snapshot_contract.validate_visibility_prior_attribution(
                camacho_as_default, contract, "stale"
            )

        stale_2014_delay = (
            "The reporting-completeness nowcast assumes a delay distribution drawn "
            "from 2014 West Africa surveillance."
        )
        with self.assertRaises(snapshot_contract.SnapshotContractError):
            snapshot_contract.validate_visibility_prior_attribution(
                stale_2014_delay, contract, "stale"
            )

    def test_dataset_gate_rejects_country_scope_rows_read_as_drc(self):
        contract = snapshot_contract.build_contract(self._snapshot())
        composition = contract["country_scope_composition"]["confirmed"]
        total_key = snapshot_contract.COUNTRY_SCOPE_COMPOSITION_METRICS["confirmed"]["total_key"]
        cases = (
            ("reported_counts.csv", f"source:{composition['source_id']}:{total_key}"),
            ("timeline.csv", f"timeline:{composition['source_id']}:{total_key}"),
            # Every SitRep's country-scope rows are gated, not only the latest.
            ("timeline.csv", "timeline:inrb-sitrep-112-2026-09-03:country_scope_confirmed_uganda_anchor"),
        )
        self._assert_gate_rejects_relabelled_rows(
            contract,
            [(filename, row_id, "location", "COD") for filename, row_id in cases],
        )

    def _assert_gate_rejects_relabelled_rows(self, contract, cases):
        """Export a clean dataset, then relabel one cell per case and expect the gate to name the row."""
        with tempfile.TemporaryDirectory() as tmp:
            clean_dir = pathlib.Path(tmp) / "clean"
            export_public_health_dataset.export_package(clean_dir)
            snapshot_contract.validate_dataset_exports(contract, clean_dir)
            for index, (filename, row_id, column, value) in enumerate(cases):
                with self.subTest(filename=filename, row_id=row_id, column=column):
                    dataset_dir = pathlib.Path(tmp) / f"relabelled-{index}"
                    shutil.copytree(clean_dir, dataset_dir)
                    path = dataset_dir / filename
                    with path.open(newline="", encoding="utf-8") as f:
                        reader = csv.DictReader(f)
                        fieldnames = reader.fieldnames
                        rows = list(reader)
                    relabelled = [row for row in rows if row["row_id"] == row_id]
                    self.assertEqual(1, len(relabelled), row_id)
                    relabelled[0][column] = value
                    with path.open("w", newline="", encoding="utf-8") as f:
                        writer = csv.DictWriter(f, fieldnames=fieldnames)
                        writer.writeheader()
                        writer.writerows(rows)
                    with self.assertRaisesRegex(snapshot_contract.SnapshotContractError, row_id):
                        snapshot_contract.validate_dataset_exports(contract, dataset_dir)

    def test_dataset_gate_rejects_rows_read_as_a_cumulative_series(self):
        contract = snapshot_contract.build_contract(self._snapshot())
        self._assert_gate_rejects_relabelled_rows(contract, [
            # A 24-hour increment filed under the cumulative count.
            ("reported_counts.csv", "source:inrb-sitrep-112-2026-09-03:new_confirmed_24h",
             "metric", "confirmed_cases"),
            ("timeline.csv", "timeline:inrb-sitrep-112-2026-09-03:new_confirmed_24h",
             "metric", "confirmed_cases"),
            # A nested isolation census filed under the cumulative count.
            ("reported_counts.csv",
             "source:inrb-sitrep-083-2026-08-05:operational_tables.patient_movement_total.confirmed_in_isolation",
             "metric", "confirmed_cases"),
            # A per-zone count filed under the DRC-wide deaths series.
            ("reported_counts.csv",
             "source:afro-sitrep-01-pdf-2026-05-18-live:affected_health_zones.bunia.deaths",
             "metric", "deaths"),
            # Probable and suspected deaths filed under the confirmed-death series.
            ("reported_counts.csv", "source:cdc-current-situation-2026-06-02:uganda_probable_deaths",
             "metric", "deaths"),
            ("timeline.csv", "timeline:cdc-current-situation-2026-05-20:deaths_suspected",
             "metric", "deaths"),
            # A death count filed under a case metric.
            ("reported_counts.csv",
             "source:inrb-sitrep-130-2026-09-21:cumul_deces_parmi_confirmes_drc",
             "metric", "country_scope_confirmed_cases"),
            # Two values for one source in one cumulative series (SitRep 20 prints
            # the DRC count as cases_confirmed_drc and cumul_cas_confirmes_drc).
            ("reported_counts.csv", "source:inrb-sitrep-020-2026-06-03:cases_confirmed_drc",
             "value", "382"),
            # A percentage labelled as a count.
            ("reported_counts.csv", "source:inrb-sitrep-130-2026-09-21:contact_followup_rate_pct",
             "unit", "count"),
            ("timeline.csv",
             "timeline:inrb-sitrep-092-2026-08-14:operational_tables.care_capacity_by_province.Ituri.confirmedOccupancyPct",
             "unit", "count"),
            # The timeline must say what reported_counts says about the same row.
            ("timeline.csv", "timeline:inrb-sitrep-112-2026-09-03:new_confirmed_24h",
             "metric", "new_confirmed_cases_24h_revised"),
        ])

    def test_dataset_gate_knows_every_cumulative_metric_the_exporter_emits(self):
        exported = set(export_public_health_dataset.CUMULATIVE_METRIC_BY_FIELD.values())
        self.assertEqual(snapshot_contract.CUMULATIVE_SOURCE_METRICS, frozenset(exported))

    def test_dataset_gate_knows_every_daily_metric_the_exporter_emits(self):
        exported = set(export_public_health_dataset.DAILY_METRIC_BY_FIELD.values())
        self.assertEqual(snapshot_contract.DAILY_SOURCE_METRICS, frozenset(exported))

    def test_dataset_gate_rejects_drc_figures_labelled_as_both_countries(self):
        contract = snapshot_contract.build_contract(self._snapshot())
        self._assert_gate_rejects_relabelled_rows(contract, [
            # WHO AFRO SitRep 01's unqualified 33 is DRC: at "COD; UGA" it would be a
            # second value (33) for the country-scope series the source gives as 35.
            ("reported_counts.csv", "source:afro-sitrep-01-pdf-2026-05-18-live:cases_confirmed",
             "location", "COD; UGA"),
            # CDC's bare suspected count copies its DRC term while the source reports
            # Uganda figures, so it is the DRC figure.
            ("reported_counts.csv", "source:cdc-current-situation-2026-05-23:cases_suspected",
             "location", "COD; UGA"),
            # WHO DON603's bare deaths are DRC suspected deaths; as confirmed deaths they
            # would contradict the source's DRC confirmed deaths (9).
            ("reported_counts.csv", "source:who-don603-2026-05-21-live:deaths",
             "metric", "deaths"),
            # Two values for one source in one 24-hour series.
            ("reported_counts.csv", "source:inrb-sitrep-106-2026-08-28:total_confirmed_deaths_24h",
             "value", "39"),
        ])

    def test_dataset_gate_holds_reviewed_labels_and_two_country_metrics(self):
        contract = snapshot_contract.build_contract(self._snapshot())
        self._assert_gate_rejects_relabelled_rows(contract, [
            # Labels the other rules cannot see: each must still be applied.
            ("reported_counts.csv", "source:africa-cdc-phecs-2026-05-18-live:deaths_approx",
             "location", "COD; UGA"),
            ("reported_counts.csv", "source:ecdc-bdbv-drc-uga-2026-05-25-live:cases_suspected",
             "location", "COD; UGA"),
            ("reported_counts.csv", "source:cdc-current-situation-2026-05-20:cases_confirmed",
             "location", "COD"),
            # A named two-country metric never sits at one country.
            ("reported_counts.csv", "source:afro-sitrep-01-pdf-2026-05-18-live:grand_total_confirmed",
             "location", "COD"),
        ])

    def test_dataset_gate_requires_a_reviewed_geography_for_two_country_figures(self):
        contract = snapshot_contract.build_contract(self._snapshot())
        key = ("cdc-current-situation-2026-05-20", "cases_confirmed")
        with tempfile.TemporaryDirectory() as tmp:
            dataset_dir = pathlib.Path(tmp) / "dataset"
            export_public_health_dataset.export_package(dataset_dir)
            labels = dict(snapshot_contract.REVIEWED_SOURCE_FIELD_LABELS)
            del labels[key]
            with mock.patch.object(snapshot_contract, "REVIEWED_SOURCE_FIELD_LABELS", labels):
                with self.assertRaisesRegex(
                    snapshot_contract.SnapshotContractError,
                    "cdc-current-situation-2026-05-20:cases_confirmed is an unqualified confirmed_cases from a source covering more than one country",
                ):
                    snapshot_contract.validate_dataset_exports(contract, dataset_dir)

    def test_an_unqualified_two_country_figure_needs_a_reviewed_label(self):
        # A source covering both countries that reports suspected cases without naming a
        # country: the gate cannot tell DRC from the outbreak total, so a person decides.
        rows = [
            {"row_id": "source:x:cases_suspected", "row_type": "source_extracted_metric", "metric": "suspected_cases", "location": "COD; UGA", "unit": "count", "value": "900"},
            {"row_id": "source:x:cases_confirmed_uganda", "row_type": "source_extracted_metric", "metric": "confirmed_cases", "location": "UGA", "unit": "count", "value": "7"},
        ]
        with self.assertRaisesRegex(
            snapshot_contract.SnapshotContractError,
            "source:x:cases_suspected is an unqualified suspected_cases from a source covering more than one country",
        ):
            snapshot_contract._validate_source_metric_rows("reported_counts.csv", "source:", rows)
        own_name = [{"row_id": "source:x:health_worker_deaths", "row_type": "source_extracted_metric", "metric": "health_worker_deaths", "location": "COD; UGA", "unit": "count", "value": "4"}]
        with self.assertRaisesRegex(snapshot_contract.SnapshotContractError, "source:x:health_worker_deaths is an unqualified health_worker_deaths"):
            snapshot_contract._validate_source_metric_rows("reported_counts.csv", "source:", own_name)
        # A rate is not a count, and a timeline-only model row is not a source row.
        snapshot_contract._validate_source_metric_rows("reported_counts.csv", "source:", [
            dict(own_name[0], row_id="source:x:cfr_suspected_pct", metric="cfr_suspected_pct", unit="percent"),
        ])
        snapshot_contract._validate_source_metric_rows("timeline.csv", "timeline:", [
            {"row_id": "timeline:active_queue_lab_yield:2026-05-30:confirmable_active_queue_50_lower", "metric": "confirmable_active_queue_50_lower", "location": "COD; UGA", "unit": "count", "value": "3"},
        ])
        for location in ("COD; UGA", "COD"):
            with self.subTest(location=location):
                decided = [dict(rows[0], location=location), rows[1]]
                labels = {**snapshot_contract.REVIEWED_SOURCE_FIELD_LABELS, ("x", "cases_suspected"): {"location": location}}
                with mock.patch.object(snapshot_contract, "REVIEWED_SOURCE_FIELD_LABELS", labels):
                    snapshot_contract._validate_source_metric_rows("reported_counts.csv", "source:", decided)

    def test_reviewed_labels_must_name_a_known_location(self):
        snapshot_contract._check_reviewed_labels({("x", "f"): {"location": "COD", "metric": "m", "note": "n"}})
        for label in ({"metric": "total_confirmed_deaths_24h"}, {"location": "DRC"}, {"location": "COD", "value": "3"}):
            with self.subTest(label=label), self.assertRaises(ValueError):
                snapshot_contract._check_reviewed_labels({("x", "f"): label})

    def test_dataset_gate_checks_an_aliased_package_input(self):
        contract = snapshot_contract.build_contract(self._snapshot())
        with tempfile.TemporaryDirectory() as tmp:
            dataset_dir = pathlib.Path(tmp) / "dataset"
            export_public_health_dataset.export_package(dataset_dir)
            manifest_path = dataset_dir / "lovs-public-health-dataset.manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            aliased = next(i for i in manifest["inputs"] if i["path"] == "restricted/public-claim-audit-source")
            aliased["sha256"] = "0" * 64
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(snapshot_contract.SnapshotContractError, "data/evidence-chains.json"):
                snapshot_contract.validate_dataset_exports(contract, dataset_dir)

    def test_dataset_gate_rejects_a_package_input_that_matches_no_file(self):
        contract = snapshot_contract.build_contract(self._snapshot())
        with tempfile.TemporaryDirectory() as tmp:
            dataset_dir = pathlib.Path(tmp) / "dataset"
            export_public_health_dataset.export_package(dataset_dir)
            snapshot_contract.validate_dataset_exports(contract, dataset_dir)
            manifest_path = dataset_dir / "lovs-public-health-dataset.manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            live = next(i for i in manifest["inputs"] if i["path"] == "data/live-bdbv-2026-output.json")
            live["sha256"] = "0" * 64
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(snapshot_contract.SnapshotContractError, "data/live-bdbv-2026-output.json"):
                snapshot_contract.validate_dataset_exports(contract, dataset_dir)

    def test_country_scope_key_that_names_a_country_takes_that_country(self):
        def check(key, location):
            snapshot_contract._validate_country_scope_dataset_rows(
                {}, "reported_counts.csv", "source:",
                [{"row_id": f"source:inrb-sitrep-131-2026-09-22:{key}", "location": location, "value": "1"}],
            )
        check("country_scope_confirmed_uganda_anchor", "UGA")
        check("country_scope_confirmed_drc", "COD")
        check("country_scope_confirmed_total", "COD; UGA")
        for key, location in (("country_scope_confirmed_drc", "COD; UGA"), ("country_scope_confirmed_total", "COD")):
            with self.subTest(key=key), self.assertRaises(snapshot_contract.SnapshotContractError):
                check(key, location)


if __name__ == "__main__":
    unittest.main()
