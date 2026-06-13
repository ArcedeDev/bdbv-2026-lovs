# SPDX-License-Identifier: Apache-2.0
"""Tests for the headline chain-to-source wiring + enforcement (Blocker 1).

Two layers are exercised:

  * the deterministic ``primary_source_id -> backing chain`` mapping in
    ``lovs.lovs_evidence`` (built from each chain's ``claim.locator`` binding);
  * the publish gate ``lovs.semantic_freshness_gate.check_headline_evidence_chains``
    that FAILs a snapshot whose headline metric embeds no chain matching its
    ``primary_source_id`` and PASSES one that does.

The keystone regression is locked here: the CURRENT on-disk
``data/public_snapshot.json`` must already carry a matching evidence-chain
embed for its promoted primary source. No clock, no network.
"""
from __future__ import annotations

import json
import pathlib
import unittest

from lovs import lovs_evidence
from lovs import public_exports
from lovs import semantic_freshness_gate as gate


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
PUBLIC_SNAPSHOT_PATH = REPO_ROOT / "data" / "public_snapshot.json"
PUBLIC_EXPORT_SOURCE_PATH = REPO_ROOT / "data" / "public_export_source.json"

SITREP_019_SOURCE = "inrb-sitrep-019-2026-06-02"
SITREP_019_CHAIN = "ec:lovs:data:inrb-sitrep-019-visual-promotion:2026-06-02"
SITREP_028_SOURCE = "inrb-sitrep-028-2026-06-11"


class TestHeadlineChainMapping(unittest.TestCase):
    """The locator-anchored (path, source_id) -> chain_id index."""

    def setUp(self) -> None:
        self.registry = lovs_evidence.load_registry()

    def test_confirmed_source_maps_to_promotion_chain(self):
        chain = lovs_evidence.headline_chain_for(
            self.registry, lovs_evidence.HEADLINE_CONFIRMED_LOCATOR, SITREP_019_SOURCE
        )
        self.assertEqual(SITREP_019_CHAIN, chain)

    def test_confirmed_deaths_source_maps_to_promotion_chain(self):
        chain = lovs_evidence.headline_chain_for(
            self.registry,
            lovs_evidence.HEADLINE_CONFIRMED_DEATHS_LOCATOR,
            SITREP_019_SOURCE,
        )
        self.assertEqual(SITREP_019_CHAIN, chain)

    def test_mapping_is_deterministic_and_unambiguous(self):
        # Built twice -> identical; no source binds to two distinct chains.
        first = lovs_evidence.build_headline_chain_index(self.registry)
        second = lovs_evidence.build_headline_chain_index(self.registry)
        self.assertEqual(first, second)

    def test_secondary_citation_is_not_mistaken_for_backing(self):
        # An ECDC anchor cited as a secondary/conflict source in several chains
        # must NOT resolve as the confirmed-headline backing for a date that has
        # no promotion clause for it.
        chain = lovs_evidence.headline_chain_for(
            self.registry,
            lovs_evidence.HEADLINE_CONFIRMED_LOCATOR,
            "ecdc-bdbv-drc-uga-2026-05-26",
        )
        self.assertIsNone(chain)

    def test_unknown_and_empty_sources_return_none(self):
        self.assertIsNone(
            lovs_evidence.headline_chain_for(
                self.registry, lovs_evidence.HEADLINE_CONFIRMED_LOCATOR, ""
            )
        )
        self.assertIsNone(
            lovs_evidence.headline_chain_for(
                self.registry,
                lovs_evidence.HEADLINE_CONFIRMED_LOCATOR,
                "made-up-source-2026-01-01",
            )
        )

    def test_ambiguous_binding_raises(self):
        # Two chains claiming the same (path, source) binding is a fatal
        # ambiguity the publish gate could not resolve.
        bogus = {
            "schema_version": lovs_evidence.SCHEMA_VERSION,
            "chains": [
                {
                    "chain_id": "ec:lovs:data:a:2026-06-02",
                    "claim": {"locator": "reported_counts.confirmed.primary_source_id == s-2026-06-02"},
                },
                {
                    "chain_id": "ec:lovs:data:b:2026-06-02",
                    "claim": {"locator": "reported_counts.confirmed.primary_source_id == s-2026-06-02"},
                },
            ],
        }
        with self.assertRaises(lovs_evidence.EvidenceChainError):
            lovs_evidence.build_headline_chain_index(bogus)

    def test_provenance_entry_records_chain_source(self):
        entries = lovs_evidence.headline_evidence_provenance(
            self.registry,
            confirmed_primary_source_id=SITREP_019_SOURCE,
            confirmed_deaths_primary_source_id=SITREP_019_SOURCE,
        )
        by_metric = {e["metric"]: e for e in entries}
        self.assertEqual(SITREP_019_CHAIN, by_metric["confirmed"]["evidence_chain_id"])
        self.assertEqual(SITREP_019_SOURCE, by_metric["confirmed"]["chain_source"])
        self.assertTrue(by_metric["confirmed"]["backed"])
        self.assertTrue(by_metric["confirmed_deaths"]["backed"])

    def test_provenance_marks_unbacked_when_chain_does_not_anchor_source(self):
        # A chain whose locator names the source but does not actually anchor to
        # it (no matching manifest_source_id) is reported unbacked, not silently
        # accepted.
        entries = lovs_evidence.headline_evidence_provenance(
            self.registry,
            confirmed_primary_source_id="ecdc-bdbv-drc-uga-2026-05-27",
            confirmed_deaths_primary_source_id=None,
        )
        self.assertEqual(1, len(entries))
        self.assertFalse(entries[0]["backed"])
        self.assertIsNone(entries[0]["chain_source"])


class TestHeadlineChainGate(unittest.TestCase):
    """The publish gate enforcing embedded-chain-matches-source."""

    def _snapshot(self, *, embed: bool, deaths: bool = True) -> dict:
        snap: dict = {
            "as_of": "2026-06-02T23:59:59Z",
            "reported_counts": {
                "confirmed": {"primary": 370, "primary_source_id": SITREP_019_SOURCE},
            },
        }
        if deaths:
            snap["reported_deaths"] = {
                "confirmed": {"primary": 63, "primary_source_id": SITREP_019_SOURCE},
            }
        if embed:
            metrics = ["confirmed"] + (["confirmed_deaths"] if deaths else [])
            snap["headline_evidence_chain_ids"] = [
                {
                    "metric": m,
                    "primary_source_id": SITREP_019_SOURCE,
                    "chain_source": SITREP_019_SOURCE,
                    "evidence_chain_id": SITREP_019_CHAIN,
                    "backed": True,
                }
                for m in metrics
            ]
        return snap

    def test_orphan_snapshot_fails(self):
        findings = gate.check_headline_evidence_chains(self._snapshot(embed=False))
        self.assertEqual(2, len(findings))
        self.assertTrue(all(SITREP_019_SOURCE in f for f in findings))
        self.assertTrue(any("confirmed:" in f for f in findings))
        self.assertTrue(any("confirmed_deaths:" in f for f in findings))

    def test_wired_snapshot_passes(self):
        self.assertEqual([], gate.check_headline_evidence_chains(self._snapshot(embed=True)))

    def test_embedded_chain_for_wrong_source_fails(self):
        # Embedded chain backs a DIFFERENT source than the headline primary: the
        # exact bug class (numbers advance, provenance does not).
        snap = self._snapshot(embed=True, deaths=False)
        snap["headline_evidence_chain_ids"][0]["chain_source"] = "inrb-sitrep-018-2026-06-01"
        findings = gate.check_headline_evidence_chains(snap)
        self.assertEqual(1, len(findings))
        self.assertIn(SITREP_019_SOURCE, findings[0])

    def test_unbacked_entry_fails_even_if_source_matches(self):
        # backed=False must not satisfy the gate, even with a matching source.
        snap = self._snapshot(embed=True, deaths=False)
        snap["headline_evidence_chain_ids"][0]["backed"] = False
        findings = gate.check_headline_evidence_chains(snap)
        self.assertEqual(1, len(findings))

    def test_metric_absent_is_not_a_finding(self):
        # No reported_deaths block -> no deaths headline to back.
        snap = self._snapshot(embed=True, deaths=False)
        self.assertEqual([], gate.check_headline_evidence_chains(snap))

    def test_camelcase_website_keys_are_read(self):
        snap = {
            "reported_counts": {
                "confirmed": {"primarySourceId": SITREP_019_SOURCE},
            },
            "headlineEvidenceChainIds": [
                {
                    "metric": "confirmed",
                    "chainSource": SITREP_019_SOURCE,
                    "backed": True,
                }
            ],
        }
        self.assertEqual([], gate.check_headline_evidence_chains(snap))


class TestKeystoneRegression(unittest.TestCase):
    """Lock the wired-chain invariant on the real on-disk public snapshot."""

    def test_current_on_disk_public_snapshot_is_backed_and_passes(self):
        # The committed data/public_snapshot.json promotes the latest primary
        # SitRep endpoint and embeds the backing chain, so the gate passes.
        snapshot = json.loads(PUBLIC_SNAPSHOT_PATH.read_text(encoding="utf-8"))
        self.assertEqual(
            SITREP_028_SOURCE,
            snapshot["reported_counts"]["confirmed"]["primary_source_id"],
        )
        self.assertEqual([], gate.check_headline_evidence_chains(snapshot))

    def test_regenerated_public_snapshot_passes(self):
        # Re-running the public assembler over the committed source embeds the
        # backing chain (a generated consequence of the source), so the SAME
        # headline now PASSES the gate.
        source = json.loads(PUBLIC_EXPORT_SOURCE_PATH.read_text(encoding="utf-8"))
        regenerated = public_exports._public_snapshot(source)
        self.assertIn("headline_evidence_chain_ids", regenerated)
        self.assertEqual([], gate.check_headline_evidence_chains(regenerated))
        # And the public projection must not leak the raw ec:lovs: needle.
        self.assertNotIn("ec:lovs", json.dumps(regenerated["headline_evidence_chain_ids"]))


if __name__ == "__main__":
    unittest.main()
