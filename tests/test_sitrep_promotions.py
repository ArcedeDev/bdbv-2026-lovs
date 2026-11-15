# SPDX-License-Identifier: Apache-2.0
"""Tests for reviewed SitRep promotion payloads."""
from __future__ import annotations

import copy
import hashlib
import json
import pathlib
import tempfile
import unittest
from unittest import mock

from lovs import sitrep_promotion_gate
from lovs import sitrep_promotions
from lovs import release_contract


SR55_RAW_PATH = (
    pathlib.Path(__file__).parents[1]
    / "data"
    / "bundibugyo-2026"
    / "raw"
    / "44706aea5157df826e05f4be706abca3acf93033d0e586a31c8e03711a94d91b"
)


class TestSitRepPromotions(unittest.TestCase):
    def test_sr169_zero_residual_does_not_invent_a_printed_row(self):
        import refresh_pipeline

        promotions = sitrep_promotions.reviewed_promotions_by_number()
        current = refresh_pipeline._promotion_note(169, promotions[169])
        self.assertIn("specifies no unventilated row", current)
        self.assertNotIn("explicit unventilated row", current)
        previous = refresh_pipeline._promotion_note(168, promotions[168])
        self.assertIn("493 deaths", previous)

    def test_sr169_daily_deaths_exclude_retrospective_harmonisation(self):
        promotions = sitrep_promotions.reviewed_promotions_by_number()
        previous, current = (promotions[n]["figures"] for n in (168, 169))
        self.assertEqual(32, current["new_confirmed_deaths_24h"])
        self.assertEqual(
            314,
            current["cumul_deces_parmi_confirmes_drc"]
            - previous["cumul_deces_parmi_confirmes_drc"]
            - current["new_confirmed_deaths_24h"],
        )
        self.assertEqual(5372, sum(
            row["confirmed_deaths"] for row in current["health_zone_table"]["rows"]
        ))

    def test_sr56_structures_reviewed_provincial_bed_occupancy(self):
        promotion = sitrep_promotions.reviewed_promotions_by_number()[56]
        rows = promotion["figures"]["province_operational"]["byProvince"]
        self.assertEqual(89.9, rows["Ituri"]["bedOccupancyPct"])
        self.assertEqual(160.0, rows["Nord-Kivu"]["bedOccupancyPct"])
        self.assertEqual(42.2, rows["Sud-Kivu"]["bedOccupancyPct"])

    def test_sr130_nord_kivu_occupancy_is_a_backed_conflict_not_absent(self):
        promotion = sitrep_promotions.reviewed_promotions_by_number()[130]
        rows = {
            row["province"]: row
            for row in promotion["figures"]["operational_tables"]["care_capacity_by_province"]
        }
        nord_kivu = rows["Nord-Kivu"]
        self.assertIsNone(nord_kivu["occupancy_percent"])
        self.assertEqual("conflict", nord_kivu["occupancy_status"])
        self.assertEqual(66.2, nord_kivu["occupancy_percent_printed"])
        self.assertEqual((338, 308), (nord_kivu["patients"], nord_kivu["beds"]))
        self.assertNotIn("occupancy_status", rows["Ituri"])

        def mutated(**changes):
            payload = copy.deepcopy(promotion)
            row = next(
                r for r in payload["figures"]["operational_tables"]["care_capacity_by_province"]
                if r["province"] == "Nord-Kivu"
            )
            row.update(changes)
            return payload

        for changes in (
            {"occupancy_percent": 66.2},
            {"occupancy_percent_printed": None},
            {"occupancy_status": "absent"},
            {"occupancy_percent_printed": 71.0},
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(sitrep_promotions.SitRepPromotionError):
                    sitrep_promotions.validate_promotion(mutated(**changes), require_reviewed=True)

    def test_reviewed_sitrep18_is_model_ready(self):
        rows = sitrep_promotions.reviewed_promotions_by_number()
        sitrep18 = rows[18]

        self.assertEqual("2026-06-01", sitrep18["data_as_of"])
        self.assertEqual(355, sitrep18["figures"]["country_scope_confirmed_total"])
        self.assertEqual(61, sitrep18["figures"]["country_scope_confirmed_deaths"])
        self.assertEqual(23, sitrep18["figures"]["lab_indicators_24h"]["samples_positive"])

    def test_candidate_payload_cannot_be_model_ready(self):
        payload = sitrep_promotions.candidate_payload_from_sidecar({
            "source_id": "insp-wordpress-sitrep-n019-pdf",
            "registry_id": "insp-wordpress-sitrep-feed",
            "url": "https://insp.cd/sitrep-19.pdf",
            "published_at": "2026-06-03T12:00:00Z",
            "normalized_content": {
                "sitrep_number": 19,
                "publication_date_candidates": ["2026-06-02"],
                "pdf_asset": {"sitrep_number": 19},
            },
        })
        reviewed_candidate = copy.deepcopy(payload)
        reviewed_candidate["review"]["ready_for_model_use"] = True

        with self.assertRaises(sitrep_promotions.SitRepPromotionError):
            sitrep_promotions.validate_promotion(reviewed_candidate)

    @unittest.skipUnless(SR55_RAW_PATH.is_file(), "reviewed SitRep 55 raw bytes unavailable")
    def test_sitrep55_builds_content_addressed_release_envelope(self):
        promotion = sitrep_promotions.reviewed_promotions_by_number()[55]
        release = release_contract.build_release_envelope(promotion)

        self.assertEqual("bdbv-release/v1", release["schema_version"])
        self.assertEqual(
            "bdbv-sr055-2026-07-08-44706aea5157df82",
            release["release_id"],
        )
        self.assertEqual(55, release["edition"])
        self.assertEqual("2026-07-08", release["snapshot_date"])
        self.assertEqual("published", release["publication_state"])
        self.assertEqual(
            "44706aea5157df826e05f4be706abca3acf93033d0e586a31c8e03711a94d91b",
            release["source_receipt"]["sha256"],
        )

    def test_release_envelope_fails_closed_without_structured_receipt(self):
        promotion = copy.deepcopy(sitrep_promotions.reviewed_promotions_by_number()[55])
        promotion.pop("source_receipt")
        with self.assertRaises(release_contract.ReleaseContractError):
            release_contract.build_release_envelope(promotion)

    def test_release_envelope_rejects_boolean_sizes_and_bad_calendar_dates(self):
        promotion = sitrep_promotions.reviewed_promotions_by_number()[55]
        boolean_size = copy.deepcopy(promotion)
        boolean_size["source_receipt"]["byte_length"] = True
        with self.assertRaises(release_contract.ReleaseContractError):
            release_contract.build_release_envelope(boolean_size)

        bad_date = copy.deepcopy(promotion)
        bad_date["data_as_of"] = "2026-02-31"
        with self.assertRaises(release_contract.ReleaseContractError):
            release_contract.build_release_envelope(bad_date)

    def test_release_envelope_rejects_receipt_not_bound_to_local_raw_archive(self):
        promotion = copy.deepcopy(sitrep_promotions.reviewed_promotions_by_number()[55])
        promotion["source_receipt"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(
            release_contract.ReleaseContractError, "local raw archive"
        ):
            release_contract.build_release_envelope(promotion)

    def test_release_envelope_accepts_restricted_private_raw_archive(self):
        raw = b"restricted official sitrep bytes"
        sha256 = hashlib.sha256(raw).hexdigest()
        promotion = copy.deepcopy(sitrep_promotions.reviewed_promotions_by_number()[55])
        promotion["source_receipt"]["sha256"] = sha256
        promotion["source_receipt"]["byte_length"] = len(raw)
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            public_dir = root / "public"
            private_dir = root / "private"
            private_dir.mkdir()
            (private_dir / sha256).write_bytes(raw)
            with (
                mock.patch.object(release_contract, "_RAW_ARCHIVE_DIR", public_dir),
                mock.patch.object(release_contract, "_PRIVATE_RAW_ARCHIVE_DIR", private_dir),
            ):
                release = release_contract.build_release_envelope(promotion)

        self.assertEqual(sha256, release["source_receipt"]["sha256"])

    def test_historical_promotion_without_receipt_remains_unenriched(self):
        promotion = sitrep_promotions.reviewed_promotions_by_number()[54]
        snapshot = {"as_of": "2026-07-07T23:59:59Z", "convergence": {}}

        self.assertEqual(
            snapshot, release_contract.maybe_enrich_snapshot(snapshot, promotion)
        )

    def test_contract_only_retains_historical_materialization_without_receipt(self):
        import refresh_pipeline

        promotion = sitrep_promotions.reviewed_promotions_by_number()[54]
        snapshot = {"as_of": "2026-07-07T23:59:59Z", "convergence": {}}
        with tempfile.TemporaryDirectory(dir=refresh_pipeline.REPO_ROOT) as tmp:
            output_path = pathlib.Path(tmp) / "historical.json"
            output_path.write_text(json.dumps(snapshot), encoding="utf-8")
            with (
                mock.patch.object(refresh_pipeline, "OUT_PATH", output_path),
                mock.patch.object(
                    refresh_pipeline,
                    "_latest_reviewed_promotion_at_or_before",
                    return_value=(pathlib.Path("sr54.json"), promotion),
                ),
            ):
                self.assertEqual(
                    0,
                    refresh_pipeline.main(
                        ["--as-of", "2026-07-07", "--contract-only"]
                    ),
                )

            self.assertEqual(snapshot, json.loads(output_path.read_text(encoding="utf-8")))

    @unittest.skipUnless(SR55_RAW_PATH.is_file(), "reviewed SitRep 55 raw bytes unavailable")
    def test_contract_enrichment_preserves_materialized_model_values(self):
        snapshot_path = pathlib.Path(__file__).parents[1] / "data" / "live-bdbv-2026-output.json"
        original = json.loads(snapshot_path.read_text(encoding="utf-8"))
        original.pop("release", None)
        nowcast = original["convergence"]["true_burden_nowcast"]
        nowcast.pop("estimate_registry", None)
        original["convergence"]["methodology"] = [
            row
            for row in original["convergence"]["methodology"]
            if row.get("quantity")
            != "Primary sensitivity scenario (care versus ascertainment)"
        ]
        original["convergence"]["true_burden_nowcast"]["care_adjusted"][
            "method"
        ] = "legacy contradictory method"
        untouched = copy.deepcopy(original)

        promotion = sitrep_promotions.reviewed_promotions_by_number()[55]
        enriched = release_contract.enrich_snapshot(original, promotion)

        self.assertEqual(untouched, original)
        self.assertEqual(
            "bdbv-sr055-2026-07-08-44706aea5157df82",
            enriched["release"]["release_id"],
        )
        registry = enriched["convergence"]["true_burden_nowcast"][
            "estimate_registry"
        ]
        self.assertEqual("primary_sensitivity", registry[0]["display_role"])
        self.assertIn("NOT a measured care correction", registry[0]["method"])

        projected = copy.deepcopy(enriched)
        projected.pop("release")
        projected_nowcast = projected["convergence"]["true_burden_nowcast"]
        projected_nowcast.pop("estimate_registry")
        projected["convergence"]["methodology"] = [
            row
            for row in projected["convergence"]["methodology"]
            if row.get("quantity")
            != "Primary sensitivity scenario (care versus ascertainment)"
        ]
        projected_nowcast["care_adjusted"]["method"] = "legacy contradictory method"
        self.assertEqual(untouched, projected)

    def test_gate_requires_reviewed_coverage_through_release_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = pathlib.Path(tmp)
            source = sitrep_promotions.PROMOTIONS_DIR / "sitrep-018-2026-06-01.json"
            (directory / source.name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

            ok = sitrep_promotion_gate.validate(directory, require_through="2026-06-01")
            self.assertEqual("2026-06-01", ok["latest_data_as_of"])

            with self.assertRaises(sitrep_promotions.SitRepPromotionError):
                sitrep_promotion_gate.validate(directory, require_through="2026-06-02")

    def test_sr119_denominator_scope_break_is_explicit_and_reconciled(self):
        """A changed province roster must never masquerade as footprint improvement."""
        promotion = sitrep_promotions.reviewed_promotions_by_number()[119]
        footprint = promotion["figures"]["affected_health_zone_footprint"]
        self.assertEqual(62, footprint["affected"])
        self.assertEqual(167, footprint["total_in_affected_provinces"])
        self.assertEqual(105, footprint["not_affected_in_affected_provinces"])
        self.assertFalse(footprint["series_comparable_to_prior"])
        self.assertEqual(118, footprint["scope_change_from_sitrep"])
        self.assertEqual(
            footprint["affected"] + footprint["not_affected_in_affected_provinces"],
            footprint["total_in_affected_provinces"],
        )
        self.assertIn("must not be plotted as improvement", footprint["scope_change_note"].lower())



class TestMovementDetailLint(unittest.TestCase):
    """One person's or one body's route, named places and dates are withheld."""

    # Synthetic sentences. The places are not in the outbreak area on purpose.
    FLAGGED = (
        "One body conveyed from Lyon to Turin through Modane on 3 March after a death on 1 March as printed.",
        "A body passed at the Modane control point without checks.",
        "1 body was held at the Modane PoE.",
        "A confirmed case travelled from Lyon to Turin by road.",
        "One suspect was intercepted at the Modane PoC.",
        "A contact left via Modane before follow-up ended.",
        "A patient was moved on 3 March after a death on 1 March in the same household.",
        "The body was stopped at the control point of Modane.",
        "A confirmed-positive body was intercepted at the Modane PoC in public transport.",
        "Un corps a été transporté de Lyon à Turin en passant par Modane.",
        "Une dépouille a été interceptée au point de contrôle de Modane.",
        "Un cas confirmé a voyagé de Lyon à Turin.",
        "Un contact est parti via Modane.",
        "Une patiente a été transférée le 3 mars après un décès le 1er mars.",
        "Le corps a été retenu au PoC de Modane.",
    )
    PASSING = (
        "4 bodies intercepted at Tshopo points of control.",
        "The Avakubi control points are out of service because of insecurity.",
        "One body conveyed into the DRC from a neighbouring country, with route and dates withheld here.",
        "A body passed a control point without control amid threats from the crowd against the staff.",
        "Contact follow-up covers five reporting provinces.",
        "3 bodies were carried from Lyon to Turin without control measures.",
        "One confirmed case was identified among the suspects in Nord-Ubangi.",
        "The Tshopo threats (SR173, SR180) and the closure from SR173 to SR177 were printed before.",
        "11 bodies were intercepted at the Modane PoC.",
        "4 corps interceptés aux points de contrôle de la Tshopo.",
        "Des contacts ont voyagé de Lyon à Turin.",
        "Un corps a été intercepté, le lieu et les dates étant retenus ici.",
    )

    def _edition(self, number: int) -> dict:
        payload = copy.deepcopy(sitrep_promotions.reviewed_promotions_by_number()[182])
        payload["sitrep_number"] = number
        return payload

    def test_single_person_or_body_movement_sentences_are_flagged(self):
        for sentence in self.FLAGGED:
            with self.subTest(sentence=sentence):
                self.assertEqual([1], sitrep_promotions.movement_detail_sentences(sentence))

    def test_aggregate_counts_and_withheld_wording_pass(self):
        for sentence in self.PASSING:
            with self.subTest(sentence=sentence):
                self.assertEqual([], sitrep_promotions.movement_detail_sentences(sentence))

    def test_a_semicolon_ends_the_sentence_and_the_index_counts_from_one(self):
        text = (
            "Teams moved from Lyon to Turin; a suspect case was isolated in Haut-Uele. "
            "No intervention effect is established. " + self.FLAGGED[0]
        )
        self.assertEqual([4], sitrep_promotions.movement_detail_sentences(text))

    def test_the_floor_is_the_edition_after_the_newest_promotion_when_the_rule_landed(self):
        self.assertEqual(183, sitrep_promotions.MOVEMENT_DETAIL_LINT_FROM_EDITION)

    def test_an_edition_at_the_floor_fails_hard_without_printing_the_sentence(self):
        floor = sitrep_promotions.MOVEMENT_DETAIL_LINT_FROM_EDITION
        cases = (
            ("narrative", lambda f: f["narrative"]["challenges"].append("Reported calmly. " + self.FLAGGED[0]),
             "figures.narrative.challenges[2] sentence 2"),
            ("finding", lambda f: f["operational_tables"]["challenges"][0].update(finding=self.FLAGGED[1]),
             "figures.operational_tables.challenges[0].finding sentence 1"),
            ("poe note", lambda f: f["operational_tables"]["poe_poc_narrative_by_province"][0].update(
                scope_note=self.FLAGGED[8]),
             "figures.operational_tables.poe_poc_narrative_by_province[0].scope_note sentence 1"),
        )
        for label, inject, location in cases:
            with self.subTest(label=label):
                payload = self._edition(floor)
                inject(payload["figures"])
                with self.assertRaises(sitrep_promotions.SitRepPromotionError) as raised:
                    sitrep_promotions.validate_promotion(payload, require_reviewed=True)
                message = str(raised.exception)
                self.assertIn(location, message)
                for word in ("Lyon", "Turin", "Modane", "March"):
                    self.assertNotIn(word, message)

    def test_a_clean_edition_at_the_floor_validates(self):
        payload = self._edition(sitrep_promotions.MOVEMENT_DETAIL_LINT_FROM_EDITION)
        sitrep_promotions.validate_promotion(payload, require_reviewed=True)

    def test_editions_below_the_floor_are_exempt(self):
        payload = self._edition(sitrep_promotions.MOVEMENT_DETAIL_LINT_FROM_EDITION - 1)
        payload["figures"]["narrative"]["challenges"].append(self.FLAGGED[0])
        sitrep_promotions.validate_promotion(payload, require_reviewed=True)

    def test_every_promotion_on_main_still_validates(self):
        rows = sitrep_promotions.load_reviewed_promotions()
        self.assertGreaterEqual(len(rows), 162)
        self.assertEqual(len(rows), len(list(sitrep_promotions.PROMOTIONS_DIR.glob("*.json"))))


class TestDerivedZoneAudit(unittest.TestCase):
    def test_published_daily_audit_matches_its_literal_rows(self):
        for promotion in sitrep_promotions.load_reviewed_promotions():
            table = promotion['figures'].get('health_zone_table', {})
            rows = table.get('day_rows', [])
            for audit in table.get('reconciliation', {}).get('province_arithmetic', []):
                for key, metric in [('daily_cases_sum', 'new_confirmed_24h'), ('daily_community_sum', 'community_deaths_24h'), ('daily_cte_sum', 'cte_deaths_24h'), ('daily_total_sum', 'total_confirmed_deaths_24h')]:
                    if key not in audit:
                        continue
                    actual = sum(row.get(metric) or 0 for row in rows if row.get('province') == audit['province'] and 'ventil' not in row.get('zone', '').lower())
                    self.assertEqual(actual, audit[key], f"{promotion['source_id']} {audit['province']} {key}: derived audit must match literal source rows")

if __name__ == "__main__":
    unittest.main()
