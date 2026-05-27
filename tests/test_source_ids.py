# SPDX-License-Identifier: Apache-2.0
"""Tests for the shared canonical-source-id helper.

This helper is the single source of truth for the ``-live`` live-capture
suffix rule that both ``publication_clock_contract._find_manifest_entry``
and ``snapshot_contract.validate_dataset_exports`` rely on. Before it
landed each contract carried its own copy of the rule and a defect shipped
on the May-27 release cycle when the two copies fell out of sync.
"""
from __future__ import annotations

import unittest

from lovs import source_ids


class TestCanonicalSourceId(unittest.TestCase):
    def test_strips_live_suffix(self):
        self.assertEqual(
            source_ids.canonical_source_id("ecdc-bdbv-drc-uga-2026-05-27-live"),
            "ecdc-bdbv-drc-uga-2026-05-27",
        )

    def test_passes_through_canonical_form(self):
        self.assertEqual(
            source_ids.canonical_source_id("ecdc-bdbv-drc-uga-2026-05-27"),
            "ecdc-bdbv-drc-uga-2026-05-27",
        )

    def test_empty_and_none_normalise_to_empty(self):
        self.assertEqual(source_ids.canonical_source_id(None), "")
        self.assertEqual(source_ids.canonical_source_id(""), "")


class TestSourceIdsMatch(unittest.TestCase):
    def test_canonical_matches_live(self):
        self.assertTrue(
            source_ids.source_ids_match("x-2026-05-27", "x-2026-05-27-live")
        )
        self.assertTrue(
            source_ids.source_ids_match("x-2026-05-27-live", "x-2026-05-27")
        )

    def test_different_dates_do_not_match(self):
        self.assertFalse(
            source_ids.source_ids_match("x-2026-05-26", "x-2026-05-27-live")
        )

    def test_empty_inputs_never_match(self):
        self.assertFalse(source_ids.source_ids_match(None, None))
        self.assertFalse(source_ids.source_ids_match("", "x"))
        self.assertFalse(source_ids.source_ids_match("x", ""))


class TestFindManifestEntryBySourceId(unittest.TestCase):
    def setUp(self):
        self.entries = [
            {"source_id": "who-don-2026-05-22"},
            {"source_id": "ecdc-bdbv-drc-uga-2026-05-27-live"},
            {"source_id": "drc-moh-sitrep-009"},
        ]

    def test_exact_match_wins(self):
        found = source_ids.find_manifest_entry_by_source_id(
            self.entries, "who-don-2026-05-22"
        )
        self.assertEqual(found, {"source_id": "who-don-2026-05-22"})

    def test_canonical_query_matches_live_entry(self):
        # snapshot primaries always carry the canonical form, so a canonical
        # query against a "-live"-suffixed manifest entry must match.
        found = source_ids.find_manifest_entry_by_source_id(
            self.entries, "ecdc-bdbv-drc-uga-2026-05-27"
        )
        self.assertEqual(
            found, {"source_id": "ecdc-bdbv-drc-uga-2026-05-27-live"}
        )

    def test_live_query_matches_canonical_entry(self):
        entries = [{"source_id": "ecdc-bdbv-drc-uga-2026-05-27"}]
        found = source_ids.find_manifest_entry_by_source_id(
            entries, "ecdc-bdbv-drc-uga-2026-05-27-live"
        )
        self.assertEqual(found, {"source_id": "ecdc-bdbv-drc-uga-2026-05-27"})

    def test_no_match_returns_none(self):
        self.assertIsNone(
            source_ids.find_manifest_entry_by_source_id(self.entries, "nope")
        )
        self.assertIsNone(
            source_ids.find_manifest_entry_by_source_id(self.entries, "")
        )

    def test_exact_match_preferred_over_canonical_match(self):
        # If both forms are in the manifest (an unusual transition state),
        # the exact-match form wins so deduplication stays predictable.
        entries = [
            {"source_id": "x-2026-05-27-live", "note": "live"},
            {"source_id": "x-2026-05-27", "note": "canonical"},
        ]
        found = source_ids.find_manifest_entry_by_source_id(entries, "x-2026-05-27")
        self.assertEqual(found["note"], "canonical")


if __name__ == "__main__":
    unittest.main()
