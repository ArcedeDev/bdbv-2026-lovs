# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import copy
import json
import pathlib
import tempfile
import unittest
from unittest import mock

from lovs import health_zone_centroids as hzc
from lovs import lovs_evidence as E
from lovs import model_tournament as T
from lovs import tournament_geography as G
from lovs import tournament_resolution as R
from tests.test_model_tournament import TournamentFixture


POLICY = {
    "schema_version": "bdbv-model-tournament-resolution-policy/v1",
    "event_clock": "first_public_authority_publication",
    "negative_evidence": "reviewed_target_specific_full_window_coverage",
    "accepted_coverage_assessments": ["explicit_negative", "complete_target_coverage_no_detection"],
    "pre_window_status": "unscoreable_conflicting_evidence",
    "missing_coverage_status": "unscoreable_surveillance_dark",
    "review_required": True,
}


class ResolutionDraftTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.promotions = self.root / "promotions"
        self.promotions.mkdir()
        self.registry_path = self.root / "evidence.json"
        self.manifest_path = self.root / "manifest.json"
        self.source_registry = self.root / "source_registry.json"
        self.source_registry.write_text('{"sources": []}')
        self.registry = {"schema_version": 1, "chains": []}
        self.manifest = {"entries": []}
        self.index = hzc.CentroidIndex.load_default()
        self.a, self.b, self.other = self.index.zone_ids()[:3]
        self.round = {
            "round_id": "bdbv-test-round-001", "window_start": "2027-01-02",
            "window_end": "2027-01-31", "resolution_policy": copy.deepcopy(POLICY),
            "freeze_receipt": {"forecast_sha256": "a" * 64},
            "build_receipt": {"inputs": [{"role": "health_zone_centroids", "sha256": hzc.file_sha256(hzc.CENTROIDS_PATH)}]},
            "target_events": [{"target_id": t, "geography_id": f"cod-health-zone:{t}"} for t in (self.a, self.b)],
        }
        self.real_validate_round = T.validate_round
        self.default_cutoff = "2027-02-02T12:00:00Z"
        # Round binding has its own adversarial suite. These fixtures isolate the
        # resolver while retaining real source, table, evidence and alias validators.
        for patch in (
            mock.patch.object(T, "validate_round"),
            mock.patch.object(T, "RESOLUTION_POLICY", POLICY, create=True),
            mock.patch.object(E, "default_manifest_path", return_value=self.manifest_path),
            mock.patch.object(E, "default_source_registry_path", return_value=self.source_registry),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def promotion(self, number=1, *, data_day="2027-01-31", published="2027-02-01T00:00:00Z",
                  targets=()):
        sid = f"test-source-{number}"
        cid = f"ec:test:promotion:{number}:{data_day}"
        source = {"source_id": f"src:{sid}", "tier": "T1_PRIMARY", "citation": "Synthetic authority",
                  "url": f"https://example.org/{sid}", "finding": "Synthetic reviewed table",
                  "manifest_source_id": sid}
        chain = {
            "chain_id": cid, "claim": {"claim_id": f"claim:test:promotion:{number}",
            "artifact": "synthetic", "locator": "table", "statement": "Synthetic reviewed source", "value": "table"},
            "verdict": "supported", "reviewed_at": "2027-02-01T12:00:00Z", "reviewer": "synthetic-source-reviewer",
            "sources": [source], "steps": [{"step_id": f"step:test:{number}", "kind": "cross_check", "finding": "Synthetic check"}],
            "next_action": "Synthetic test only",
        }
        self.registry["chains"].append(chain)
        self.manifest["entries"].append({"source_id": sid, "url": source["url"], "published_at": published})
        rows = [{"province": self.index.zone(t)["province"], "zone": self.index.zone(t)["name"],
                 "confirmed": 1} for t in (self.other, *targets)]
        doc = {"schema_version": "sitrep-promotion/v1", "status": "reviewed", "sitrep_number": number,
               "source_id": sid, "data_as_of": data_day, "published_at": published,
               "review": {"ready_for_model_use": True, "source_review_status": "reviewed",
                          "reviewed_by": "synthetic-source-reviewer", "reviewed_at": "2027-02-01T12:00:00Z",
                          "evidence_chain_id": cid},
               "figures": {"cumul_cas_confirmes_drc": len(rows), "health_zone_table": {
                   "date": data_day, "zone_attribution_status": "published", "rows": rows,
                   "reconciliation": {"named_zone_confirmed_sum": len(rows), "national_confirmed_total": len(rows)}}}}
        path = self.promotions / f"sitrep-{number:03}-{data_day}.json"
        path.write_text(json.dumps(doc))
        return path, doc

    def draft(self, **kwargs):
        self.registry_path.write_text(json.dumps(self.registry))
        self.manifest_path.write_text(json.dumps(self.manifest))
        return R.build_resolution_draft(
            self.round, evidence_cutoff_utc=kwargs.pop("evidence_cutoff_utc", self.default_cutoff),
            promotions_dir=self.promotions, evidence_registry_path=self.registry_path, **kwargs)

    def outcome(self, draft, target=None):
        return next(r for r in draft["resolution_candidate"]["target_outcomes"] if r["target_id"] == (target or self.a))

    def coverage(self, target=None):
        target = target or self.a
        binding = {"round_id": self.round["round_id"], "forecast_sha256": "a" * 64,
                   "resolution_policy_sha256": T.content_hash(self.round["resolution_policy"])}
        row = {"target_id": target, "assessment": "explicit_negative",
               "window_start": self.round["window_start"], "window_end": self.round["window_end"]}
        chain = copy.deepcopy(self.registry["chains"][0])
        chain["chain_id"] = f"ec:test:coverage:{target}:2027-02-01"
        chain["claim"]["claim_id"] = f"claim:test:coverage:{target}"
        chain["claim"]["value"] = "explicit_negative"
        chain["reviewed_at"] = "2027-02-01T12:00:00Z"
        chain["coverage"] = dict(binding, **row)
        self.registry["chains"].append(chain)
        return dict(binding, schema_version=R.COVERAGE_SCHEMA_VERSION,
                    assessments=[dict(row, evidence_chain_ids=[chain["chain_id"]])])

    def test_deterministic_pending_draft_never_changes_registry(self):
        self.promotion()
        first, second = self.draft(), self.draft()
        self.assertEqual(first, second)
        self.assertEqual(json.loads(self.registry_path.read_text()), self.registry)
        self.assertEqual(len(first["resolution_candidate"]["target_outcomes"]), 2)
        for chain in first["draft_chains"]:
            self.assertEqual(chain["verdict"], "pending")
            self.assertIsNone(chain["reviewer"])
            self.assertIsNone(chain["reviewed_at"])

    def test_absence_and_operational_fields_never_establish_no(self):
        path, doc = self.promotion()
        doc["figures"]["province_operational"] = {"complete": True, "contact_followup_rate_pct": 100}
        path.write_text(json.dumps(doc))
        self.assertEqual(self.outcome(self.draft())["resolution_status"], "unscoreable_surveillance_dark")

    def test_empty_feed_is_unscoreable_and_cannot_finalize(self):
        draft = self.draft()
        self.assertEqual(self.outcome(draft)["resolution_status"], "unscoreable_no_feed")
        self.assertTrue(draft["diagnostics"])
        self.assertEqual(draft["draft_chains"][0]["sources"], [])

    def test_publication_not_data_day_controls_detection(self):
        self.promotion(data_day="2027-01-01", published="2027-01-02T00:00:00Z", targets=(self.a,))
        draft = self.draft()
        self.assertEqual(self.outcome(draft)["resolution_status"], "resolved_yes")
        clocks = next(c for c in draft["draft_chains"] if c["claim"]["value"] == "resolved_yes")["source_clocks"][0]
        self.assertEqual(clocks["data_as_of"], "2027-01-01")
        self.assertEqual(clocks["published_at"], "2027-01-02T00:00:00Z")

    def test_publication_after_window_does_not_backdate_yes(self):
        self.promotion(targets=(self.a,))
        self.assertEqual(self.outcome(self.draft())["resolution_status"], "unscoreable_surveillance_dark")

    def test_pre_window_publication_is_explicit_conflict(self):
        self.promotion(data_day="2027-01-01", published="2027-01-01T23:59:59Z", targets=(self.a,))
        self.assertEqual(self.outcome(self.draft())["reason"], "pre_window_detection")

    def test_ambiguous_publication_never_becomes_exact_detection(self):
        self.promotion(data_day="2027-01-01", published="2027-01-01", targets=(self.a,))
        self.assertEqual(self.outcome(self.draft())["resolution_status"], "unscoreable_conflicting_evidence")

    def test_source_receipt_clock_is_preserved_over_printed_date(self):
        path, doc = self.promotion(data_day="2027-01-01", published="2027-01-01", targets=(self.a,))
        doc["source_receipt"] = {"source_id": doc["source_id"], "published_at": "2027-01-02T00:00:00Z"}
        path.write_text(json.dumps(doc))
        draft = self.draft()
        self.assertEqual(self.outcome(draft)["resolution_status"], "resolved_yes")
        clocks = draft["draft_chains"][0]["source_clocks"][0]
        self.assertEqual(clocks["promotion_published_at"], "2027-01-01")

    def test_source_receipt_mismatch_or_missing_primary_review_rejected(self):
        path, doc = self.promotion()
        doc["source_receipt"] = {"source_id": "wrong-source", "published_at": doc["published_at"]}
        path.write_text(json.dumps(doc))
        with self.assertRaisesRegex(ValueError, "receipt identity"):
            self.draft()
        del doc["source_receipt"]
        path.write_text(json.dumps(doc))
        self.registry["chains"][0]["sources"][0]["tier"] = "T2_DERIVED"
        draft = self.draft()
        self.assertEqual(self.outcome(draft)["resolution_status"], "unscoreable_no_feed")
        self.assertTrue(any("T1_PRIMARY" in line for line in draft["diagnostics"]))

    def test_same_data_day_editions_preserve_first_public_detection(self):
        self.promotion(1, data_day="2027-01-01", published="2027-01-01T23:00:00Z", targets=(self.a,))
        self.promotion(2, data_day="2027-01-01", published="2027-01-02T01:00:00Z", targets=(self.a,))
        self.assertEqual(self.outcome(self.draft())["reason"], "pre_window_detection")

    def test_missing_reconciliation_does_not_erase_positive(self):
        path, doc = self.promotion(data_day="2027-01-03", published="2027-01-04T00:00:00Z", targets=(self.a,))
        del doc["figures"]["health_zone_table"]["reconciliation"]
        path.write_text(json.dumps(doc))
        draft = self.draft()
        self.assertTrue(draft["diagnostics"])
        self.assertEqual(self.outcome(draft)["resolution_status"], "unscoreable_conflicting_evidence")

    def test_reviewed_bound_coverage_permits_no(self):
        self.promotion()
        self.assertEqual(self.outcome(self.draft(coverage_assessments=self.coverage()))["outcome"], 0)

    def test_positive_and_negative_coverage_conflict(self):
        self.promotion(data_day="2027-01-03", published="2027-01-04T00:00:00Z", targets=(self.a,))
        self.assertEqual(self.outcome(self.draft(coverage_assessments=self.coverage()))["resolution_status"],
                         "unscoreable_conflicting_evidence")

    def test_rejects_unbound_stale_or_unreviewed_coverage(self):
        self.promotion()
        coverage = self.coverage()
        original = copy.deepcopy(self.registry)
        for field, value in (("coverage", {}), ("verdict", "pending"),
                             ("reviewed_at", "2027-01-20T12:00:00Z"),
                             ("reviewed_at", "2027-02-03T12:00:00Z")):
            with self.subTest(field=field, value=value):
                self.registry = copy.deepcopy(original)
                self.registry["chains"][-1][field] = value
                with self.assertRaises(ValueError):
                    self.draft(coverage_assessments=coverage)

    def test_rejects_substituted_duplicate_unknown_or_short_coverage(self):
        self.promotion()
        coverage = self.coverage()
        for variant in ("hash", "duplicate", "unknown", "window"):
            doc = copy.deepcopy(coverage)
            if variant == "hash":
                doc["forecast_sha256"] = "b" * 64
            elif variant == "duplicate":
                doc["assessments"] *= 2
            elif variant == "unknown":
                doc["assessments"][0]["target_id"] = "unknown"
            else:
                doc["assessments"][0]["window_end"] = "2027-01-30"
            with self.subTest(variant=variant), self.assertRaises(ValueError):
                self.draft(coverage_assessments=doc)

    def test_bad_table_values_duplicates_pending_and_names_fail_closed(self):
        path, baseline = self.promotion(targets=(self.a,))
        for variant in ("bool", "negative", "duplicate", "name", "reconciliation"):
            doc = copy.deepcopy(baseline)
            table = doc["figures"]["health_zone_table"]
            if variant in ("bool", "negative"):
                table["rows"][0]["confirmed"] = True if variant == "bool" else -1
            elif variant == "duplicate":
                table["rows"].append(copy.deepcopy(table["rows"][0]))
            elif variant == "name":
                table["rows"][0]["zone"] = "not a known zone"
            else:
                table["reconciliation"]["national_confirmed_total"] += 1
            path.write_text(json.dumps(doc))
            with self.subTest(variant=variant), self.assertRaises(ValueError):
                self.draft()

    def test_pending_positive_is_preserved_as_conflicting_evidence(self):
        path, doc = self.promotion(targets=(self.a,))
        doc["figures"]["health_zone_table"]["rows"][-1]["officially_integrated"] = False
        path.write_text(json.dumps(doc))
        self.assertEqual(self.outcome(self.draft())["resolution_status"], "unscoreable_conflicting_evidence")

    def test_missing_chain_cannot_certify_no_unless_unchanged_unrelated_baseline(self):
        good, _ = self.promotion(1)
        coverage = self.coverage()
        old, doc = self.promotion(2, data_day="2026-08-03", published="2026-08-04T00:00:00Z")
        self.registry["chains"].pop()
        draft = self.draft(coverage_assessments=coverage)
        self.assertEqual(self.outcome(draft)["reason"], "unsupported_new_source_history")
        self.round["build_receipt"]["inputs"].append(R.C._input_row(old, "reviewed_sitrep_promotion"))
        self.assertEqual(self.outcome(self.draft(coverage_assessments=coverage))["outcome"], 0)
        doc["figures"]["health_zone_table"]["rows"].append({
            "zone": self.index.zone(self.a)["name"], "province": self.index.zone(self.a)["province"], "confirmed": 1})
        old.write_text(json.dumps(doc))
        with self.assertRaisesRegex(ValueError, "frozen baseline promotion changed"):
            self.draft(coverage_assessments=coverage)
        old.unlink()
        with self.assertRaisesRegex(ValueError, "baseline promotion is missing"):
            self.draft(coverage_assessments=coverage)

    def test_receipt_without_source_id_and_explicit_unventilated_residual(self):
        path, doc = self.promotion(targets=(self.a,), data_day="2027-01-03", published="2027-01-03")
        source = self.registry["chains"][0]["sources"][0]
        doc["source_receipt"] = {"source_url": source["url"], "sha256": "a" * 64,
                                 "wordpress_published_at": "2027-01-04T00:00:00Z"}
        table = doc["figures"]["health_zone_table"]
        table["rows"].append({"zone": "Autres ZS (donnees non ventilees)", "confirmed": 94})
        table["reconciliation"]["national_confirmed_total"] += 94
        doc["figures"]["cumul_cas_confirmes_drc"] += 94
        path.write_text(json.dumps(doc))
        draft = self.draft()
        self.assertEqual(self.outcome(draft)["outcome"], 1)
        self.assertEqual(draft["draft_chains"][0]["source_clocks"][0]["published_at"], "2027-01-04T00:00:00Z")

    def test_blank_unallocated_cell_counts_as_zero_only_in_a_reconciled_table(self):
        path, doc = self.promotion(targets=(self.a,), data_day="2027-01-03", published="2027-01-04T00:00:00Z")
        table = doc["figures"]["health_zone_table"]
        table["rows"].append({"province": self.index.zone(self.a)["province"], "zone": "A ventiler",
                              "confirmed": None, "confirmed_deaths": 3})
        path.write_text(json.dumps(doc))
        self.assertEqual(self.outcome(self.draft())["outcome"], 1)
        table["reconciliation"]["national_confirmed_total"] += 2
        doc["figures"]["cumul_cas_confirmes_drc"] += 2
        path.write_text(json.dumps(doc))
        with self.assertRaisesRegex(ValueError, "blank unallocated confirmed count"):
            self.draft()

    def attribution(self, target, name="Nouvelle Zone Test", reviewed_at="2027-02-01T12:00:00Z"):
        path = self.root / "zone-attributions.json"
        path.write_text(json.dumps({"schema_version": G.SCHEMA_VERSION, "attributions": [{
            "province": self.index.zone(target)["province"], "sitrep_zone": name, "zone_id": target,
            "first_sitrep": 2, "basis": "Synthetic location inside the frozen polygon.",
            "limitation": "Synthetic boundary.",
            "sources": [{"url": "https://example.org/layer", "finding": "Synthetic placement"}],
            "reviewed_by": "synthetic-geography-reviewer", "reviewed_at": reviewed_at}]}))
        return path

    def test_unknown_zone_stops_draft_unless_a_reviewed_attribution_names_its_frozen_zone(self):
        path, doc = self.promotion(data_day="2027-01-03", published="2027-01-04T00:00:00Z")
        table = doc["figures"]["health_zone_table"]
        table["rows"].append({"province": self.index.zone(self.a)["province"], "zone": "Nouvelle Zone Test",
                              "confirmed": 1})
        for field in ("named_zone_confirmed_sum", "national_confirmed_total"):
            table["reconciliation"][field] += 1
        doc["figures"]["cumul_cas_confirmes_drc"] += 1
        path.write_text(json.dumps(doc))
        absent = self.root / "absent.json"
        with self.assertRaisesRegex(ValueError, "matches no GRID3 zone"):
            self.draft(zone_attributions_path=absent)
        attributions = self.attribution(self.a)
        draft = self.draft(zone_attributions_path=attributions)
        self.assertEqual(self.outcome(draft)["outcome"], 1)
        self.assertNotIn("outcome", self.outcome(draft, self.b))
        self.assertIn("zone_attributions", [row["role"] for row in draft["inputs"]])
        chain = next(c for c in draft["draft_chains"] if c["claim"]["value"] == "resolved_yes")
        self.assertTrue(any(":attribution:" in s["step_id"] and "Limitation: Synthetic boundary." in s["finding"]
                            for s in chain["steps"]))
        self.assertTrue(any("counted in frozen zone" in line for line in draft["diagnostics"]))
        late = self.attribution(self.a, reviewed_at="2027-02-02T12:00:01Z")
        with self.assertRaisesRegex(ValueError, "matches no GRID3 zone"):
            self.draft(zone_attributions_path=late)

    def test_unused_attribution_leaves_inputs_unchanged(self):
        self.promotion()
        plain = self.draft(zone_attributions_path=self.root / "absent.json")
        unused = self.draft(zone_attributions_path=self.attribution(self.a))
        self.assertEqual(plain["inputs"], unused["inputs"])
        self.assertEqual(plain["resolution_candidate"], unused["resolution_candidate"])

    def use_table_rule(self, quiet=True, later=True):
        self.round["resolution_policy"] = copy.deepcopy(T.SUPPORTED_RESOLUTION_POLICIES[-1])
        self.assertIn("negative_table_rule", self.round["resolution_policy"])
        # After the accounting grace, so an unaccounted province is left unscoreable.
        self.default_cutoff = "2027-02-15T12:00:00Z"
        # The last edition before the window and, unless a test needs a gap, a certain edition on the
        # first window day that lists nothing.
        self.promotion(1, data_day="2026-12-31", published="2027-01-01T00:00:00Z")
        if quiet:
            self.account(*self.promotion(10, data_day="2027-01-02", published="2027-01-03T00:00:00Z"))
        # Unless a test needs none, a certain edition dated after the window end that lists nothing
        # and accounts for no province, so a never-listed target can resolve no.
        if later:
            self.promotion(11, data_day="2027-02-01", published="2027-02-01T18:00:00Z")

    def account(self, path, doc, *, skew=None):
        """Print each province's affected-zone count as the authority does."""
        named = {}
        for row in doc["figures"]["health_zone_table"]["rows"]:
            if row.get("confirmed") and "ventil" not in row["zone"].lower():
                named[row["province"]] = named.get(row["province"], 0) + 1
        provinces = {self.index.zone(z)["province"] for z in (self.a, self.b, self.other)}
        doc["figures"]["province_table"] = [
            {"province": p, "health_zones_touched": f"{named.get(p, 0) + (skew or {}).get(p, 0)}/30 (x%)"}
            for p in sorted(provinces)]
        path.write_text(json.dumps(doc))

    def test_table_rule_absent_target_in_accounted_end_table_resolves_no(self):
        self.use_table_rule()
        self.account(*self.promotion(2, data_day="2027-01-10", published="2027-01-11T00:00:00Z"))
        self.account(*self.promotion(3))
        draft = self.draft()
        row = self.outcome(draft)
        self.assertEqual((row["resolution_status"], row["reason"], row["outcome"]),
                         ("resolved_no", "reconciled_zone_table_absence", 0))
        self.assertEqual(row["evidence_as_of"], "2027-02-01")
        chain = next(c for c in draft["draft_chains"] if c["claim"]["claim_id"].endswith(f":{self.a}"))
        self.assertEqual(chain["coverage"]["assessment"], "reconciled_zone_table_absence")
        self.assertEqual(chain["coverage"]["resolution_policy_sha256"],
                         T.content_hash(self.round["resolution_policy"]))
        self.assertTrue(any(s["step_id"].endswith(":accounted-table") for s in chain["steps"]))
        # The accounting table and the latest certain edition that omits the target.
        self.assertEqual([c["source_id"] for c in chain["source_clocks"]], ["test-source-3", "test-source-11"])

    def test_table_rule_a_never_listed_target_needs_a_certain_table_after_the_window_end(self):
        self.use_table_rule(later=False)
        self.account(*self.promotion(2))
        # The only omitting table is dated on the window end: another edition with that table date
        # could list the target and be published inside the window.
        row = self.outcome(self.draft())
        self.assertEqual((row["resolution_status"], row["reason"]),
                         ("unscoreable_conflicting_evidence", "no_certain_table_after_the_window_end"))
        # An edition without an exact publication time is not certain, so it cannot close the gap.
        self.promotion(3, data_day="2027-02-01", published="2027-02-01")
        self.assertEqual(self.outcome(self.draft())["reason"], "no_certain_table_after_the_window_end")
        self.promotion(4, data_day="2027-02-01", published="2027-02-01T18:00:00Z")
        self.assertEqual(self.outcome(self.draft())["reason"], "reconciled_zone_table_absence")

    def test_table_rule_a_later_omission_never_turns_a_listing_into_a_no(self):
        self.use_table_rule()
        # Listed in a certain edition inside the window, then dropped from every later table.
        self.account(*self.promotion(2, data_day="2027-01-20", published="2027-01-21T00:00:00Z", targets=(self.a,)))
        self.account(*self.promotion(3))
        row = self.outcome(self.draft())
        self.assertEqual((row["resolution_status"], row["reason"]), ("resolved_yes", "first_public_detection_in_window"))

    def test_table_rule_never_certifies_an_unaccounted_province(self):
        self.use_table_rule()
        path, doc = self.promotion(2)
        self.account(path, doc, skew={self.index.zone(self.a)["province"]: 1})
        self.assertEqual(self.outcome(self.draft())["reason"], "end_of_window_table_does_not_account_for_province")
        self.assertEqual(self.outcome(self.draft(), self.b)["reason"], "reconciled_zone_table_absence")
        table = doc["figures"]["health_zone_table"]
        table["rows"].append({"province": self.index.zone(self.b)["province"], "zone": "A ventiler", "confirmed": 2})
        table["reconciliation"]["national_confirmed_total"] += 2
        doc["figures"]["cumul_cas_confirmes_drc"] += 2
        self.account(path, doc)
        for target in (self.a, self.b):
            self.assertEqual(self.outcome(self.draft(), target)["resolution_status"], "unscoreable_surveillance_dark")
        del doc["figures"]["province_table"]
        table["rows"].pop()
        table["reconciliation"]["national_confirmed_total"] -= 2
        doc["figures"]["cumul_cas_confirmes_drc"] -= 2
        path.write_text(json.dumps(doc))
        self.assertEqual(self.outcome(self.draft())["resolution_status"], "unscoreable_surveillance_dark")

    def test_table_rule_scores_a_yes_only_in_an_accounted_province(self):
        self.use_table_rule()
        path, doc = self.promotion(2, data_day="2027-01-20", published="2027-01-21T00:00:00Z", targets=(self.a,))
        self.account(path, doc)
        end_path, end_doc = self.promotion(3, targets=(self.a,))
        self.account(end_path, end_doc, skew={self.index.zone(self.a)["province"]: 1})
        row = self.outcome(self.draft())
        self.assertEqual((row["resolution_status"], row["reason"]),
                         ("unscoreable_surveillance_dark", "end_of_window_table_does_not_account_for_province"))
        self.assertNotIn("outcome", row)
        self.account(end_path, end_doc)
        draft = self.draft()
        self.assertEqual(self.outcome(draft)["resolution_status"], "resolved_yes")
        chain = next(c for c in draft["draft_chains"] if c["claim"]["claim_id"].endswith(f":{self.a}"))
        self.assertTrue(any(s["step_id"].endswith(":accounted-province") for s in chain["steps"]))

    def test_table_rule_does_not_read_reviewed_coverage(self):
        self.use_table_rule()
        self.account(*self.promotion(2, data_day="2027-01-20", published="2027-01-21T00:00:00Z", targets=(self.a,)))
        self.account(*self.promotion(3, targets=(self.a,)))
        plain = self.draft()
        covered = self.draft(coverage_assessments=self.coverage(self.a))
        self.assertEqual(plain["resolution_candidate"], covered["resolution_candidate"])
        self.assertEqual(self.outcome(covered)["resolution_status"], "resolved_yes")
        self.assertTrue(any("not read under the table rule" in line for line in covered["diagnostics"]))

    def test_table_rule_dates_by_data_day_when_editions_are_posted_out_of_order(self):
        self.use_table_rule()
        self.account(*self.promotion(2, data_day="2027-01-16", published="2027-01-17T00:00:00Z", targets=(self.a,)))
        self.registry["chains"].pop()
        self.account(*self.promotion(3, data_day="2027-01-17", published="2027-02-01T06:00:00Z", targets=(self.a,)))
        self.account(*self.promotion(4, targets=(self.a,)))
        # Edition 3 is posted after edition 4, and edition 2 is doubtful: the first listing lies
        # between 1 January and 1 February, so it is not a no.
        self.assertEqual(self.outcome(self.draft())["reason"], "first_listing_date_crosses_a_window_boundary")

    def test_table_rule_dates_by_data_day_not_by_edition_number(self):
        self.use_table_rule()
        # Two promotions share a number and the edition on 30 January omits the target: its first
        # listing is on or after 30 January and published 1 February, across the window end.
        self.account(*self.promotion(2, data_day="2027-01-30", published="2027-01-31T00:00:00Z"))
        self.account(*self.promotion(2, targets=(self.a,)))
        self.assertEqual(self.outcome(self.draft())["reason"], "first_listing_date_crosses_a_window_boundary")

    def test_table_rule_a_gap_at_the_window_start_crosses_the_boundary(self):
        self.use_table_rule(quiet=False)
        self.account(*self.promotion(3, data_day="2027-01-03", published="2027-01-04T00:00:00Z", targets=(self.a,)))
        self.account(*self.promotion(4, targets=(self.a,)))
        self.assertEqual(self.outcome(self.draft())["reason"], "first_listing_date_crosses_a_window_boundary")
        self.account(*self.promotion(2, data_day="2027-01-02", published="2027-01-03T00:00:00Z"))
        self.assertEqual(self.outcome(self.draft())["resolution_status"], "resolved_yes")

    def test_table_rule_a_gap_after_the_window_end_is_a_no(self):
        self.use_table_rule(later=False)
        self.account(*self.promotion(2))
        self.account(*self.promotion(4, data_day="2027-02-03", published="2027-02-04T00:00:00Z", targets=(self.a,)))
        # The only omitting edition has the window end as data day: a same-day edition could list a.
        self.assertEqual(self.outcome(self.draft())["reason"], "first_listing_date_crosses_a_window_boundary")
        self.account(*self.promotion(3, data_day="2027-02-01", published="2027-02-02T00:00:00Z"))
        draft = self.draft()
        row = self.outcome(draft)
        self.assertEqual((row["resolution_status"], row["reason"]), ("resolved_no", "first_listing_after_window"))
        chain = next(c for c in draft["draft_chains"] if c["claim"]["claim_id"].endswith(f":{self.a}"))
        self.assertEqual(chain["coverage"]["assessment"], "reconciled_zone_table_absence")
        self.assertTrue(any(s["step_id"].endswith(":first-listing-date") for s in chain["steps"]))

    def test_table_rule_a_floor_on_the_day_before_the_window_end_crosses(self):
        self.use_table_rule(later=False)
        self.account(*self.promotion(2, data_day="2027-01-30", published="2027-01-31T00:00:00Z"))
        self.account(*self.promotion(4, data_day="2027-02-01", published="2027-02-02T00:00:00Z", targets=(self.a,)))
        self.assertEqual(self.outcome(self.draft())["reason"], "first_listing_date_crosses_a_window_boundary")

    def test_table_rule_keeps_a_pre_window_detection_unscored(self):
        self.use_table_rule()
        self.account(*self.promotion(2, data_day="2027-01-01", published="2027-01-01T12:00:00Z", targets=(self.a,)))
        self.account(*self.promotion(3, targets=(self.a,)))
        draft = self.draft()
        self.assertEqual(self.outcome(draft)["reason"], "pre_window_detection")
        self.assertEqual(self.outcome(draft, self.b)["resolution_status"], "resolved_no")

    def test_table_rule_a_missing_later_edition_cannot_hide_an_earlier_listing(self):
        self.use_table_rule()
        # A certain edition on 29 January omits a; a's first certain listing is posted 5 February.
        # An edition posted earlier could exist unseen, so a is not a no.
        self.account(*self.promotion(2, data_day="2027-01-29", published="2027-01-29T12:00:00Z"))
        self.account(*self.promotion(3, data_day="2027-01-30", published="2027-02-05T00:00:00Z", targets=(self.a,)))
        self.account(*self.promotion(5, targets=(self.a,)))
        self.assertEqual(self.outcome(self.draft())["reason"], "first_listing_date_crosses_a_window_boundary")

    def test_table_rule_a_late_posted_pre_window_listing_is_not_a_yes(self):
        self.use_table_rule(quiet=False)
        # Data day 30 December, posted 5 January: the first listing may be before the window.
        self.account(*self.promotion(2, data_day="2026-12-30", published="2027-01-05T00:00:00Z", targets=(self.a,)))
        self.account(*self.promotion(4, targets=(self.a,)))
        self.assertNotEqual(self.outcome(self.draft())["resolution_status"], "resolved_yes")

    def test_table_rule_a_doubtful_listing_a_later_certain_table_omits_moves_no_bound(self):
        self.use_table_rule()
        self.account(*self.promotion(3, data_day="2027-01-10", published="2027-01-11T00:00:00Z"))
        path, doc = self.promotion(4, data_day="2026-12-31", published="2027-01-01T00:00:00Z", targets=(self.a,))
        doc["figures"]["health_zone_table"]["rows"][-1]["officially_integrated"] = False
        self.account(path, doc)
        self.account(*self.promotion(5, data_day="2027-01-12", published="2027-01-13T00:00:00Z", targets=(self.a,)))
        self.account(*self.promotion(6, targets=(self.a,)))
        # The reconciled table of 10 January omits the row printed on 31 December, so the
        # authority's count first holds the target in the edition of 12 January.
        self.assertEqual(self.outcome(self.draft())["resolution_status"], "resolved_yes")
        # An untimed listing is not certain either, and moves no bound.
        self.promotion(7, data_day="2027-01-11", published="2027-01-11", targets=(self.a,))
        self.assertEqual(self.outcome(self.draft())["resolution_status"], "resolved_yes")

    def test_table_rule_refuses_a_draft_inside_the_accounting_grace(self):
        self.use_table_rule()
        self.account(*self.promotion(2, data_day="2027-01-20", published="2027-01-21T00:00:00Z", targets=(self.a,)))
        path, doc = self.promotion(3, targets=(self.a,))
        self.account(path, doc, skew={self.index.zone(self.a)["province"]: 1})
        with self.assertRaisesRegex(R.ResolutionDraftError, "waits until after 2027-02-14"):
            self.draft(evidence_cutoff_utc="2027-02-14T23:00:00Z")
        self.assertEqual(self.outcome(self.draft(evidence_cutoff_utc="2027-02-15T00:00:00Z"))["reason"],
                         "end_of_window_table_does_not_account_for_province")
        self.account(path, doc)
        self.assertEqual(self.outcome(self.draft(evidence_cutoff_utc="2027-02-02T12:00:00Z"))["resolution_status"],
                         "resolved_yes")

    def test_table_rule_keeps_in_window_detection_and_first_listing_after_window(self):
        self.use_table_rule()
        self.account(*self.promotion(2, data_day="2027-01-10", published="2027-01-11T00:00:00Z", targets=(self.b,)))
        self.account(*self.promotion(3, targets=(self.a, self.b)))
        draft = self.draft()
        self.assertEqual(self.outcome(draft, self.b)["resolution_status"], "resolved_yes")
        # No certain edition between 10 and 31 January omits a: its first listing may be in the window.
        self.assertEqual(self.outcome(draft)["reason"], "first_listing_date_crosses_a_window_boundary")
        self.assertEqual(self.outcome(draft, self.b)["reason"], "first_public_detection_in_window")

    def renumber(self, old, new, data_day="2027-01-31"):
        source = self.promotions / f"sitrep-{old:03}-{data_day}.json"
        doc = json.loads(source.read_text())
        doc["sitrep_number"] = new
        source.unlink()
        (self.promotions / f"sitrep-{new:03}-{data_day}.json").write_text(json.dumps(doc))

    def test_table_rule_a_gap_leaves_only_a_listing_that_crosses_a_boundary_unscored(self):
        self.use_table_rule()
        self.account(*self.promotion(2, targets=(self.a,)))
        self.renumber(2, 9)
        draft = self.draft()
        # Editions 2 to 8 are missing: the first listing lies between 1 January and 1 February,
        # across the window end, so only that target is unscoreable; the never-listed one is a no.
        self.assertEqual(self.outcome(draft)["reason"], "first_listing_date_crosses_a_window_boundary")
        self.assertEqual(self.outcome(draft, self.b)["reason"], "reconciled_zone_table_absence")

    def test_table_rule_a_gap_inside_the_window_keeps_the_yes(self):
        self.use_table_rule()
        self.account(*self.promotion(2, data_day="2027-01-05", published="2027-01-06T00:00:00Z"))
        self.account(*self.promotion(3, data_day="2027-01-20", published="2027-01-21T00:00:00Z", targets=(self.a,)))
        self.renumber(3, 6, data_day="2027-01-20")
        self.account(*self.promotion(7, targets=(self.a,)))
        draft = self.draft()
        self.assertEqual(self.outcome(draft)["resolution_status"], "resolved_yes")
        self.assertEqual(self.outcome(draft, self.b)["resolution_status"], "resolved_no")

    def test_table_rule_without_an_edition_before_the_first_listing_cannot_date_it(self):
        self.use_table_rule()
        (self.promotions / "sitrep-001-2026-12-31.json").unlink()
        self.account(*self.promotion(9, targets=(self.a,)))
        draft = self.draft()
        self.assertEqual(self.outcome(draft)["reason"], "first_listing_date_crosses_a_window_boundary")
        self.assertEqual(self.outcome(draft, self.b)["reason"], "reconciled_zone_table_absence")

    def test_table_rule_a_doubtful_edition_after_the_first_listing_keeps_the_yes(self):
        self.use_table_rule()
        self.account(*self.promotion(2, data_day="2027-01-16", published="2027-01-17T00:00:00Z", targets=(self.a,)))
        self.account(*self.promotion(3, targets=(self.a,)))
        path, doc = self.promotion(4, data_day="2027-02-05", published="2027-02-06T00:00:00Z", targets=(self.a,))
        self.account(path, doc)
        self.registry["chains"].pop()
        draft = self.draft()
        self.assertEqual(self.outcome(draft)["resolution_status"], "resolved_yes")
        self.assertEqual(self.outcome(draft, self.b)["resolution_status"], "resolved_no")
        self.registry["chains"].append(copy.deepcopy(self.registry["chains"][-1]))
        self.registry["chains"][-1]["chain_id"] = doc["review"]["evidence_chain_id"]
        doc["figures"]["health_zone_table"]["rows"][-1]["officially_integrated"] = False
        self.account(path, doc)
        self.assertEqual(self.outcome(self.draft())["resolution_status"], "resolved_yes")

    def test_table_rule_a_never_integrated_listing_the_table_omits_is_a_no(self):
        self.use_table_rule()
        path, doc = self.promotion(2, data_day="2027-01-16", published="2027-01-17T00:00:00Z", targets=(self.a,))
        doc["figures"]["health_zone_table"]["rows"][-1]["officially_integrated"] = False
        self.account(path, doc)
        self.account(*self.promotion(3))
        draft = self.draft()
        # The row was never officially integrated and the reconciled cumulative table omits it.
        self.assertEqual(self.outcome(draft)["reason"], "reconciled_zone_table_absence")
        self.assertEqual(self.outcome(draft, self.b)["resolution_status"], "resolved_no")

    def test_table_rule_a_doubtful_listing_before_the_accounting_table_widens_its_date(self):
        self.use_table_rule()
        path, doc = self.promotion(2, data_day="2027-01-16", published="2027-01-17T00:00:00Z", targets=(self.a,))
        doc["figures"]["health_zone_table"]["rows"][-1]["officially_integrated"] = False
        self.account(path, doc)
        self.account(*self.promotion(3, targets=(self.a,)))
        # The first certain listing is the accounting table; the edition before it is doubtful, so
        # the first listing lies between 1 January and 1 February, across the window end.
        self.assertEqual(self.outcome(self.draft())["reason"], "first_listing_date_crosses_a_window_boundary")

    def test_table_rule_an_unanchored_edition_widens_only_the_next_listing(self):
        self.use_table_rule()
        self.account(*self.promotion(2, data_day="2027-01-16", published="2027-01-17T00:00:00Z"))
        self.registry["chains"].pop()
        self.account(*self.promotion(3, targets=(self.a,)))
        draft = self.draft()
        self.assertEqual(self.outcome(draft)["reason"], "first_listing_date_crosses_a_window_boundary")
        self.assertEqual(self.outcome(draft, self.b)["reason"], "reconciled_zone_table_absence")

    def test_table_rule_each_province_takes_its_own_earliest_table(self):
        self.use_table_rule()
        self.assertNotEqual(self.index.zone(self.a)["province"], self.index.zone(self.b)["province"])
        self.account(*self.promotion(2), skew={self.index.zone(self.a)["province"]: 1})
        self.account(*self.promotion(3, data_day="2027-02-03", published="2027-02-04T00:00:00Z"))
        draft = self.draft()
        for target in (self.a, self.b):
            self.assertEqual(self.outcome(draft, target)["resolution_status"], "resolved_no")
        accounted = {line.split(": ")[1] for line in draft["diagnostics"] if " accounted by " in line}
        self.assertIn(f"{hzc.province_key(self.index.zone(self.a)['province'])} accounted by test-source-3 "
                      "(table date 2027-02-03)", accounted)
        self.assertIn(f"{hzc.province_key(self.index.zone(self.b)['province'])} accounted by test-source-2 "
                      "(table date 2027-01-31)", accounted)

    def test_table_rule_ignores_a_table_dated_before_the_window_end(self):
        self.use_table_rule()
        path, doc = self.promotion(2)
        doc["figures"]["health_zone_table"]["date"] = "2027-01-30"
        self.account(path, doc)
        self.assertNotIn("outcome", self.outcome(self.draft()))

    def test_table_rule_pending_or_negative_residual_tables_never_certify(self):
        self.use_table_rule()
        path, doc = self.promotion(2, targets=(self.b,))
        doc["figures"]["health_zone_table"]["rows"][-1]["officially_integrated"] = False
        self.account(path, doc)
        self.assertNotEqual(self.outcome(self.draft())["resolution_status"], "resolved_no")
        doc["figures"]["health_zone_table"]["rows"][-1].pop("officially_integrated")
        table = doc["figures"]["health_zone_table"]
        table["rows"].append({"province": self.index.zone(self.other)["province"], "zone": self.index.zone("bunia")["name"]
                              if "bunia" in self.index.zone_ids() else "Bunia", "confirmed": 1})
        table["reconciliation"]["named_zone_confirmed_sum"] += 1
        table["reconciliation"]["declared_negative_residual"] = {"confirmed": -1}
        self.account(path, doc)
        self.assertNotEqual(self.outcome(self.draft())["resolution_status"], "resolved_no")

    def test_new_round_cannot_freeze_v1(self):
        candidate = {"resolution_policy": copy.deepcopy(T.RESOLUTION_POLICY_V1),
                     "expected_round_id": "bdbv-2026-tournament-round-002",
                     "build_receipt": {"schema_version": T.BUILD_RECEIPT_SCHEMA_VERSION}}
        with mock.patch.object(T, "RESOLUTION_POLICY", T.SUPPORTED_RESOLUTION_POLICIES[-1]):
            with self.assertRaisesRegex(T.TournamentConfigError, "may carry resolution policy v1"):
                T._validate_build_binding(candidate, "2026-11-12T00:00:00Z", {})

    def test_a_frozen_round_keeps_validating_under_an_earlier_supported_policy(self):
        v2 = T.SUPPORTED_RESOLUTION_POLICIES[-1]
        later = dict(v2, schema_version="bdbv-model-tournament-resolution-policy/v-later")
        candidate = {"resolution_policy": copy.deepcopy(v2),
                     "expected_round_id": "bdbv-2026-tournament-round-002",
                     "build_receipt": {"schema_version": T.BUILD_RECEIPT_SCHEMA_VERSION}}
        with (mock.patch.object(T, "RESOLUTION_POLICY", later),
              mock.patch.object(T, "SUPPORTED_RESOLUTION_POLICIES", (*T.SUPPORTED_RESOLUTION_POLICIES, later))):
            with self.assertRaisesRegex(T.TournamentConfigError, "payload hash"):
                T._validate_build_binding(candidate, "2026-11-12T00:00:00Z", {})

    def test_table_rule_negative_passes_existing_finalizer(self):
        self.use_table_rule()
        self.account(*self.promotion(2))
        draft = self.draft()
        reviewed = {"schema_version": 1, "chains": copy.deepcopy(draft["draft_chains"])}
        for chain in reviewed["chains"]:
            chain.update(verdict="derived_supported", reviewer="synthetic-outcome-reviewer",
                         reviewed_at="2027-02-02T11:00:00Z")
        receipt = next(c for c in reviewed["chains"] if c.get("coverage"))
        T._validate_negative_coverage(receipt["coverage"], self.round, self.a, receipt["reviewed_at"])
        early = dict(receipt, reviewed_at="2027-01-31T23:00:00Z")
        with self.assertRaises(T.TournamentConfigError):
            T._validate_negative_coverage(early["coverage"], self.round, self.a, early["reviewed_at"])
        legacy = dict(self.round, resolution_policy=copy.deepcopy(POLICY))
        with self.assertRaises(T.TournamentConfigError):
            T._validate_negative_coverage(receipt["coverage"], legacy, self.a, receipt["reviewed_at"])

    def test_committed_reviewed_history_supports_draft_without_legacy_edits(self):
        registry = E.load_registry(T.EVIDENCE_REGISTRY_PATH)
        history = self.root / "reviewed-history"
        history.mkdir()
        for path in R.P.PROMOTIONS_DIR.glob("sitrep-*.json"):
            match = R.C._PROMOTION_NAME_RE.fullmatch(path.name)
            if match and int(match.group(1)) <= 141:
                (history / path.name).write_bytes(path.read_bytes())
        records, inputs, diagnostics = R._read_promotions(
            history, registry, self.index, T._utc_datetime("2026-11-05T00:00:00Z", "cutoff"))
        self.assertEqual(len(records), 122)
        sr141 = next(r for r in records if r["source_id"] == "inrb-sitrep-141-2026-10-02")
        self.assertEqual(sr141["published_at"], "2026-10-04T09:21:35Z")
        sr27 = next(r for r in records if r["source_id"] == "inrb-sitrep-027-2026-06-10")
        self.assertTrue(sr27["reconciled"])
        self.assertTrue(any("sitrep-016" in d and "no outcome authority" in d for d in diagnostics))
        observed = {t for r in records for t in r["counts"]}
        targets = sorted(set(self.index.zone_ids()) - observed)[:2]
        self.round.update(window_start="2026-10-06", window_end="2026-11-04",
                          target_events=[{"target_id": t, "geography_id": f"cod-health-zone:{t}"} for t in targets])
        self.round["build_receipt"]["inputs"].extend(inputs)
        with (mock.patch.object(E, "default_manifest_path", return_value=T.REPO_ROOT / "data/bundibugyo-2026/manifest.json"),
              mock.patch.object(E, "default_source_registry_path", return_value=T.REPO_ROOT / "data/external_sources/source_registry.json")):
            draft = R.build_resolution_draft(self.round, evidence_cutoff_utc="2026-11-05T00:00:00Z",
                                            promotions_dir=history)
        self.assertEqual({r["resolution_status"] for r in draft["resolution_candidate"]["target_outcomes"]},
                         {"unscoreable_no_feed"})
        self.assertTrue(all(row["sha256"] == hzc.file_sha256(R.P.PROMOTIONS_DIR / pathlib.Path(row["path"]).name)
                            for row in inputs))

    def test_future_input_rejected_and_later_filename_not_read(self):
        path, doc = self.promotion()
        doc["published_at"] = "2027-02-03T00:00:00Z"
        path.write_text(json.dumps(doc))
        with self.assertRaises(ValueError):
            self.draft()
        path.unlink()
        (self.promotions / "sitrep-002-2027-02-03.json").write_text("invalid unread input")
        self.assertEqual(self.outcome(self.draft())["resolution_status"], "unscoreable_no_feed")

    def test_wrong_policy_and_unclosed_window_fail(self):
        with self.assertRaisesRegex(ValueError, "completed window"):
            self.draft(evidence_cutoff_utc="2027-01-31T23:59:59Z")
        self.round["resolution_policy"]["event_clock"] = "observation_date"
        with self.assertRaisesRegex(ValueError, "frozen resolution policy"):
            self.draft()

    def test_changed_centroids_or_geography_contract_rejected(self):
        self.round["build_receipt"]["inputs"][0]["sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "centroid input"):
            self.draft()
        self.round["build_receipt"]["inputs"][0]["sha256"] = hzc.file_sha256(hzc.CENTROIDS_PATH)
        self.round["target_events"][0]["geography_id"] = "different-geography"
        with self.assertRaisesRegex(ValueError, "COD health-zone"):
            self.draft()

    def test_existing_finalizer_requires_genuine_chain_review(self):
        self.promotion()
        draft = self.draft(coverage_assessments=self.coverage())
        proposed_registry = {"schema_version": 1, "chains": draft["draft_chains"]}
        with self.assertRaises(ValueError):
            T.build_resolution(draft["resolution_candidate"], self.round, proposed_registry)
        reviewed = copy.deepcopy(proposed_registry)
        for chain in reviewed["chains"]:
            chain.update(verdict="derived_supported", reviewer="synthetic-outcome-reviewer",
                         reviewed_at="2027-02-02T11:00:00Z")
        result = T.build_resolution(draft["resolution_candidate"], self.round, reviewed)
        self.assertEqual(result["target_outcomes"], draft["resolution_candidate"]["target_outcomes"])

    def real_round(self, policy, current=None):
        fixture = TournamentFixture()
        candidate = fixture.candidate()
        candidate = {k: v for k, v in candidate.items() if k not in {"build_receipt", "resolution_policy"}}
        mapping = {"a": self.a, "b": self.b}
        candidate["target_events"] = copy.deepcopy(self.round["target_events"])
        for target in candidate["target_events"]:
            target["event_definition"] = "first public authority detection"
        candidate["predictions"] = [row for row in candidate["predictions"] if row["target_id"] in mapping]
        for row in candidate["predictions"]:
            row["target_id"] = mapping[row["target_id"]]
        snapshot, registry, schedule = fixture.source_snapshot(), fixture.registry(), fixture.schedule()
        roles = ("model_registry", "tournament_schedule", "source_snapshot", "health_zone_centroids",
                 "reviewed_sitrep_promotion", "implementation")
        inputs = [{"path": f"synthetic/{role}.json", "role": role,
                   "sha256": hzc.file_sha256(hzc.CENTROIDS_PATH) if role == "health_zone_centroids" else "b" * 64,
                   "data_day": "2026-08-05", "published_at": "2026-08-09T18:32:11Z",
                   "reviewed_at": "2026-08-09T21:21:04Z"} for role in roles]
        baseline_path, baseline_doc = self.promotion(3, data_day="2026-08-03", published="2026-08-04T00:00:00Z")
        baseline_doc["review"]["reviewed_at"] = "2026-08-04T21:21:04Z"
        self.registry["chains"][-1]["reviewed_at"] = "2026-08-04T21:21:04Z"
        baseline_path.write_text(json.dumps(baseline_doc))
        for row in inputs:
            if row["role"] == "reviewed_sitrep_promotion":
                row.update(R.C._input_row(baseline_path, "reviewed_sitrep_promotion"))
        receipt = {"schema_version": T.BUILD_RECEIPT_SCHEMA_VERSION,
                   "candidate_payload_sha256": T.content_hash(candidate),
                   "round_id": candidate["expected_round_id"], "source_release_id": candidate["source_release_id"],
                   "source_snapshot_content_sha256": T.content_hash(snapshot),
                   "registry_content_sha256": T.content_hash(registry),
                   "schedule_content_sha256": T.content_hash(schedule),
                   "source_availability_cutoff_utc": "2026-08-11T00:00:00Z", "inputs": inputs}
        candidate.update(build_receipt=receipt, resolution_policy=copy.deepcopy(policy))
        # The fixture's release stays verified for the rest of the test, as the finalizer
        # revalidates the round.
        release = mock.patch.object(T, "_verified_source_release", side_effect=fixture._fixture_verified_source_release)
        release.start()
        self.addCleanup(release.stop)
        if policy == T.RESOLUTION_POLICY_V1:
            # A v1 fixture round stands in for round 001, the only round that may carry v1.
            v1 = mock.patch.object(T, "V1_POLICY_ROUNDS", T.V1_POLICY_ROUNDS | {candidate["expected_round_id"]})
            v1.start()
            self.addCleanup(v1.stop)
        with (mock.patch.object(T, "validate_round", self.real_validate_round),
              mock.patch.object(T, "RESOLUTION_POLICY", current or policy)):
            self.round = T.build_forecast_manifest(
                candidate, snapshot, registry=registry, schedule=schedule, control=fixture.control(),
                existing_rounds=[], frozen_at="2027-01-01T00:00:00Z",
                approval_receipt=fixture.approval_receipt(candidate))

    def test_real_v3_round_to_draft_review_resolution_and_score(self):
        self.real_round(POLICY)
        with mock.patch.object(T, "validate_round", self.real_validate_round):
            self.promotion(1, data_day="2027-01-03", published="2027-01-04T00:00:00Z", targets=(self.a,))
            self.promotion(2)
            coverage = self.coverage(self.b)
            coverage["forecast_sha256"] = self.round["freeze_receipt"]["forecast_sha256"]
            self.registry["chains"][-1]["coverage"]["forecast_sha256"] = coverage["forecast_sha256"]
            draft = self.draft(coverage_assessments=coverage)
            reviewed = {"schema_version": 1, "chains": copy.deepcopy(draft["draft_chains"])}
            with self.assertRaises(ValueError):
                T.build_resolution(draft["resolution_candidate"], self.round, reviewed)
            for chain in reviewed["chains"]:
                chain.update(verdict="derived_supported", reviewer="synthetic-outcome-reviewer",
                             reviewed_at="2027-02-02T11:00:00Z")
            resolved = T.build_resolution(draft["resolution_candidate"], self.round, reviewed)
            score = T.score_round(self.round, resolved)
            self.assertEqual({r["outcome"] for r in resolved["target_outcomes"]}, {0, 1})
            self.assertEqual(score["round_id"], self.round["round_id"])

    def test_a_new_freeze_must_carry_the_current_policy(self):
        v2 = T.SUPPORTED_RESOLUTION_POLICIES[-1]
        later = dict(v2, schema_version="bdbv-model-tournament-resolution-policy/v-later")
        with self.assertRaisesRegex(T.TournamentConfigError, "current resolution policy"):
            self.real_round(v2, current=later)

    def test_real_v3_round_resolves_a_table_rule_negative_through_the_finalizer(self):
        policy = T.SUPPORTED_RESOLUTION_POLICIES[-1]
        self.real_round(policy)
        self.assertIn("negative_table_rule", self.round["resolution_policy"])
        with (mock.patch.object(T, "validate_round", self.real_validate_round),
              mock.patch.object(T, "RESOLUTION_POLICY", policy)):
            self.account(*self.promotion(6, data_day="2027-01-02", published="2027-01-03T00:00:00Z"))
            self.account(*self.promotion(4, data_day="2027-01-03", published="2027-01-04T00:00:00Z", targets=(self.a,)))
            self.account(*self.promotion(5, targets=(self.a,)))
            self.promotion(7, data_day="2027-02-01", published="2027-02-01T18:00:00Z", targets=(self.a,))
            draft = self.draft()
            self.assertEqual(self.outcome(draft, self.b)["reason"], "reconciled_zone_table_absence")
            reviewed = {"schema_version": 1, "chains": copy.deepcopy(draft["draft_chains"])}
            for chain in reviewed["chains"]:
                chain.update(verdict="derived_supported", reviewer="synthetic-outcome-reviewer",
                             reviewed_at="2027-01-31T23:00:00Z")
            with self.assertRaises(ValueError):
                T.build_resolution(draft["resolution_candidate"], self.round, reviewed)
            for chain in reviewed["chains"]:
                chain["reviewed_at"] = "2027-02-02T11:00:00Z"
            resolved = T.build_resolution(draft["resolution_candidate"], self.round, reviewed)
            self.assertEqual({r["target_id"]: r.get("outcome") for r in resolved["target_outcomes"]},
                             {self.a: 1, self.b: 0})
            score = T.score_round(self.round, resolved)
            self.assertEqual(score["scored_target_count"], 2)

    def test_cli_create_only_and_canonical_write_refusal(self):
        round_path = self.root / "round.json"
        round_path.write_text(json.dumps(self.round))
        output = self.root / "draft.json"
        argv = ["--round", str(round_path), "--evidence-cutoff-utc", "2027-02-02T12:00:00Z", "--output", str(output)]
        with mock.patch.object(R, "build_resolution_draft", return_value={"draft": 1}) as build:
            self.assertEqual(R.main(argv), 0)
            self.assertEqual(R.main(argv), 0)
            build.return_value = {"draft": 2}
            self.assertEqual(R.main(argv), 2)
            self.assertEqual(json.loads(output.read_text()), {"draft": 1})
            argv[-1] = str(T.RESOLUTIONS_DIR / "should-not-exist.json")
            build.reset_mock()
            self.assertEqual(R.main(argv), 2)
            build.assert_not_called()

    def test_malformed_reconciliation_has_named_validation_error(self):
        path, original = self.promotion()
        for malformed in (["malformed"], {"declared_negative_residual": ["malformed"]}):
            doc = copy.deepcopy(original)
            doc["figures"]["health_zone_table"]["reconciliation"] = malformed
            path.write_text(json.dumps(doc))
            with self.assertRaisesRegex(R.ResolutionDraftError, "must be an object"):
                self.draft()


if __name__ == "__main__":
    unittest.main()
