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


if __name__ == "__main__":
    unittest.main()
