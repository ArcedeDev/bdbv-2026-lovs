"""Tests for the LOVS evidence-chain registry."""
from __future__ import annotations

import copy
import pathlib
import tempfile
import unittest

from lovs import lovs_evidence
from lovs import lovs_priors_bundibugyo


class TestEvidenceChains(unittest.TestCase):

    def test_default_registry_validates(self):
        payload = lovs_evidence.load_registry()
        summary = lovs_evidence.validate_registry(payload)
        self.assertEqual(summary["unsupported_attribution"], 2)
        self.assertEqual(summary["corrected"], 3)
        self.assertEqual(summary["derived_supported"], 9)
        self.assertEqual(summary["needs_primary_source"], 3)
        self.assertEqual(summary["pending"], 1)
        # 14 baseline + SitRep #015/#016 headline-promotion and suspected-
        # revision-doctrine chains + SitRep #017 queue-drawdown / zone-ingest
        # + SitRep #018/#019/#020/#021/#022/#023/#024/#025/#026/#027/#028/#030/#031/#032
        # reviewed promotion chains + the reviewed INRB-UMIE per-zone
        # source-review chain = 34.
        self.assertEqual(summary["supported"], 34)

    def test_bdbv_r_prior_chain_is_registered(self):
        payload = lovs_evidence.load_registry()
        chain_ids = {chain["chain_id"] for chain in payload["chains"]}
        priors = lovs_priors_bundibugyo.BUNDIBUGYO_PRIORS_STAGE_TWO
        self.assertIn(
            "ec:lovs:module-d:bdbv-r-prior-gamma:2026-05-20",
            priors.evidence_chain_ids,
        )
        self.assertTrue(set(priors.evidence_chain_ids).issubset(chain_ids))

    def test_unsupported_chain_requires_blocker_step(self):
        payload = copy.deepcopy(lovs_evidence.load_registry())
        chain = payload["chains"][0]
        chain["steps"] = [step for step in chain["steps"] if step["kind"] != "blocker"]
        with self.assertRaises(lovs_evidence.EvidenceChainError):
            lovs_evidence.validate_registry(payload)

    def test_step_source_id_must_resolve_to_declared_source(self):
        payload = copy.deepcopy(lovs_evidence.load_registry())
        payload["chains"][0]["steps"][0]["source_id"] = "src:not-declared"
        with self.assertRaises(lovs_evidence.EvidenceChainError):
            lovs_evidence.validate_registry(payload)

    def test_default_path_points_to_data_registry(self):
        self.assertEqual(
            lovs_evidence.default_registry_path(),
            pathlib.Path(__file__).resolve().parent.parent / "data" / "evidence-chains.json",
        )

    def test_numbers_audit_rows_have_explicit_audit_refs(self):
        payload = lovs_evidence.load_registry()
        summary = lovs_evidence.validate_numbers_audit(registry=payload)
        self.assertGreater(summary["rows"], 0)
        self.assertGreater(summary["evidence_chain"], 0)
        self.assertGreater(summary["audit_gap"], 0)

    def test_may22_zone_attributed_corridor_chain_is_registered(self):
        payload = lovs_evidence.load_registry()
        chains = {chain["chain_id"]: chain for chain in payload["chains"]}
        chain = chains["ec:lovs:method:bdbv-zone-attributed-corridors:2026-05-22"]
        text = " ".join(
            [
                chain["claim"]["statement"],
                chain["claim"]["value"],
                chain["next_action"],
                *(step["finding"] for step in chain["steps"]),
            ]
        )
        for required in (
            # Current corridor source-load uses the reviewed INSP per-health-zone
            # series (forward-only), so the chain carries the unified cascade
            # 827 -> 714 zone-attributed + 113 unallocated across 31 monitored
            # INSP per-zone source zones.
            "827",
            "714",
            "113",
            "31 monitored INSP per-zone source zones",
            "277-corridor",
            "unallocated",
            "not the current headline confirmed aggregate",
            "not as a validated current-outbreak forecast",
            "not validate the current-outbreak corridor constants",
        ):
            self.assertIn(required, text)

    def test_numbers_audit_unknown_chain_fails(self):
        payload = lovs_evidence.load_registry()
        with tempfile.TemporaryDirectory() as tmpdir:
            audit_path = pathlib.Path(tmpdir) / "NUMBERS_AUDIT.md"
            audit_path.write_text(
                "| Quantity | Audit ref |\n"
                "|---|---|\n"
                "| bad | ec:lovs:missing:chain:2026-05-21 |\n",
                encoding="utf-8",
            )
            with self.assertRaises(lovs_evidence.EvidenceChainError):
                lovs_evidence.validate_numbers_audit(audit_path, payload)

    def test_numbers_audit_missing_row_marker_fails(self):
        payload = lovs_evidence.load_registry()
        with tempfile.TemporaryDirectory() as tmpdir:
            audit_path = pathlib.Path(tmpdir) / "NUMBERS_AUDIT.md"
            audit_path.write_text(
                "| Quantity | Value |\n"
                "|---|---|\n"
                "| missing marker | 1 |\n",
                encoding="utf-8",
            )
            with self.assertRaises(lovs_evidence.EvidenceChainError):
                lovs_evidence.validate_numbers_audit(audit_path, payload)

    def test_manifest_backed_sources_require_manifest_anchor(self):
        payload = lovs_evidence.load_registry()
        target = next(
            source
            for chain in payload["chains"]
            for source in chain["sources"]
            if source.get("manifest_source_id") == "who-dg-remarks-bdbv-2026-05-22"
        )
        target.pop("manifest_source_id")
        with self.assertRaises(lovs_evidence.EvidenceChainError):
            lovs_evidence.validate_source_anchors(payload)

    def test_source_anchor_summary_covers_default_registry(self):
        payload = lovs_evidence.load_registry()
        summary = lovs_evidence.validate_source_anchors(payload)
        self.assertGreater(summary["manifest_anchored"], 0)
        self.assertGreater(summary["registry_anchored"], 0)
        self.assertGreater(summary["artifact_anchored"], 0)


if __name__ == "__main__":
    unittest.main()
