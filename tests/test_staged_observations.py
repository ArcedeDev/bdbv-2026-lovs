# SPDX-License-Identifier: Apache-2.0
"""Tests for the external-source staged-observation contract."""
from __future__ import annotations

import json
import pathlib
import unittest

from lovs import lovs_staged_observations


ROOT = pathlib.Path(__file__).resolve().parent.parent


class TestStagedObservations(unittest.TestCase):

    def test_current_observed_file_validates_against_manifest(self):
        observed = json.loads(
            (ROOT / "data" / "external_sources" / "bdbv-2026.observed.json").read_text(
                encoding="utf-8"
            )
        )
        manifest = json.loads(
            (ROOT / "data" / "bundibugyo-2026" / "manifest.json").read_text(
                encoding="utf-8"
            )
        )
        source_ids = {entry["source_id"] for entry in manifest["entries"]}
        gaps = lovs_staged_observations.validate_staged_observations(
            observed,
            manifest_source_ids=source_ids,
        )
        self.assertEqual(gaps, [])

    def test_watch_file_is_non_model_input(self):
        watch = json.loads(
            (ROOT / "data" / "external_sources" / "bdbv-2026.watch.json").read_text(
                encoding="utf-8"
            )
        )
        gaps = lovs_staged_observations.validate_watch_signals(watch)
        self.assertEqual(gaps, [])

    def test_approx_text_cannot_be_model_eligible(self):
        payload = {
            "staged_observations": [
                {
                    field: "x"
                    for field in lovs_staged_observations.REQUIRED_OBSERVATION_FIELDS
                }
            ]
        }
        obs = payload["staged_observations"][0]
        obs.update({
            "observation_id": "obs:test",
            "source_id": "source",
            "source_tier": "official_who",
            "metric": "cases",
            "data_as_of": "2026-05-21",
            "value_kind": "approx_text",
            "location_scope": {"scope_type": "aggregate"},
            "exclusions": [],
            "claim_status": "official",
            "admissibility": "model_eligible",
            "model_use": "eligible_after_release",
            "conflicts_with": [],
        })
        gaps = lovs_staged_observations.validate_staged_observations(payload)
        self.assertTrue(any("approx_text cannot be model_eligible" in gap for gap in gaps))


if __name__ == "__main__":
    unittest.main()
