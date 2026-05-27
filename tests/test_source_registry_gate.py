# SPDX-License-Identifier: Apache-2.0
"""Tests for source-registry release gates."""
from __future__ import annotations

import copy
import json
import pathlib
import tempfile
import unittest

from lovs import source_registry_gate


class TestSourceRegistryGate(unittest.TestCase):

    def test_default_registry_and_open_covariate_metadata_validate(self):
        summary = source_registry_gate.validate_all()
        self.assertGreaterEqual(summary["registry_sources"], 17)
        self.assertEqual(summary["covariate_packages"], 2)
        self.assertEqual(summary["covariate_resources"], 8)

    def test_drc_moh_dashboard_api_request_is_registered(self):
        payload = source_registry_gate.load_json(source_registry_gate.DEFAULT_REGISTRY_PATH)
        source = next(
            source for source in payload["sources"]
            if source["registry_id"] == "drc-moh-epidemie-dashboard"
        )
        self.assertEqual(source["api_request"]["response_kind"], "drc_moh_epidemie_dashboard")
        self.assertTrue(source["api_request"]["url"].startswith("https://"))
        self.assertEqual("Sitrep/009 dashboard payload", source["latest_known"]["edition"])
        self.assertEqual("not_recorded", source["latest_known"]["data_as_of"])
        self.assertEqual("2026-05-24", source["latest_known"]["publication_date"])
        self.assertIn("all-published-bulletins", source["notes"])
        self.assertIn("Keep SitRep/009 latest zone rows source-review", source["notes"])

    def test_inrb_umie_github_release_feed_is_registered_as_drc_only(self):
        payload = source_registry_gate.load_json(source_registry_gate.DEFAULT_REGISTRY_PATH)
        source = next(
            source for source in payload["sources"]
            if source["registry_id"] == "inrb-umie-ebola-drc-2026-github"
        )
        self.assertEqual("INRB-UMIE/Ebola_DRC_2026", source["github_release"]["repo"])
        self.assertEqual("outbreak_manifest", source["archive_target"])
        self.assertIn("counts", source["feeds"])
        self.assertIn("DRC-only", source["notes"])
        self.assertIn("composition step", source["notes"])

    def test_exact_bdbv_connector_seed_urls_are_registered(self):
        payload = source_registry_gate.load_json(source_registry_gate.DEFAULT_REGISTRY_PATH)
        by_id = {source["registry_id"]: source for source in payload["sources"]}
        self.assertEqual(
            "https://www.afro.who.int/health-topics/ebola-disease/outbreak-drc-26",
            by_id["who-afro-outbreak-hub"]["landing_url"],
        )
        self.assertEqual(
            "https://africacdc.org/news-item/africa-cdc-declares-the-ongoing-bundibugyo-ebola-outbreak-a-public-health-emergency-of-continental-security/",
            by_id["africa-cdc-bdbv-phecs"]["landing_url"],
        )
        self.assertIn("counts", by_id["who-afro-outbreak-hub"]["feeds"])
        self.assertIn("counts", by_id["africa-cdc-bdbv-phecs"]["feeds"])

    def test_covariate_source_cannot_feed_counts(self):
        payload = source_registry_gate.load_json(source_registry_gate.DEFAULT_REGISTRY_PATH)
        flowminder = next(
            source for source in payload["sources"]
            if source["registry_id"] == "flowminder-drc-health-zone-popmob"
        )
        flowminder["feeds"] = ["counts"]
        with tempfile.TemporaryDirectory() as tmpdir:
            path = pathlib.Path(tmpdir) / "source_registry.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(source_registry_gate.SourceRegistryGateError):
                source_registry_gate.validate_source_registry(path)

    def test_hdx_covariate_source_requires_package_id(self):
        payload = source_registry_gate.load_json(source_registry_gate.DEFAULT_REGISTRY_PATH)
        flowminder = next(
            source for source in payload["sources"]
            if source["registry_id"] == "flowminder-drc-health-zone-popmob"
        )
        flowminder.pop("hdx_package_id")
        with tempfile.TemporaryDirectory() as tmpdir:
            path = pathlib.Path(tmpdir) / "source_registry.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(source_registry_gate.SourceRegistryGateError):
                source_registry_gate.validate_source_registry(path)

    def test_open_covariate_metadata_registry_ids_must_resolve(self):
        payload = source_registry_gate.load_json(source_registry_gate.DEFAULT_OPEN_COVARIATE_PATH)
        broken = copy.deepcopy(payload)
        broken["packages"][0]["registry_id"] = "missing-registry-row"
        with tempfile.TemporaryDirectory() as tmpdir:
            path = pathlib.Path(tmpdir) / "open-covariates.json"
            path.write_text(json.dumps(broken), encoding="utf-8")
            with self.assertRaises(source_registry_gate.SourceRegistryGateError):
                source_registry_gate.validate_open_covariate_sources(path)

    def test_unknown_extractor_backend_is_rejected(self):
        payload = source_registry_gate.load_json(source_registry_gate.DEFAULT_REGISTRY_PATH)
        source = next(
            source for source in payload["sources"]
            if source["registry_id"] == "who-dg-official-social"
        )
        source["extractor_backend"] = "ad_hoc_scraper"

        with tempfile.TemporaryDirectory() as tmpdir:
            path = pathlib.Path(tmpdir) / "source_registry.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(source_registry_gate.SourceRegistryGateError):
                source_registry_gate.validate_source_registry(path)


if __name__ == "__main__":
    unittest.main()
