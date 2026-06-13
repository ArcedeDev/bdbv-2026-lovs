"""Unit tests for the cross-surface byte-parity gate.

Three cases:
  (a) positive: matching pair -> checked=1, mismatches=missing=[]
  (b) negative: bytes differ on one pair -> mismatches=[entry], missing=[]
  (c) missing: website side absent -> missing=[entry], mismatches=[]
"""

from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path

from lovs.cross_surface_parity import check_cross_surface_parity


def _make_pair(lovs_root: Path, website_public_root: Path, lovs_bytes: bytes, web_bytes: bytes | None) -> None:
    """Materialize one static pair on disk (brief.pdf) with the given bytes."""
    (lovs_root / "deliverables").mkdir(parents=True, exist_ok=True)
    (lovs_root / "deliverables" / "brief.pdf").write_bytes(lovs_bytes)
    if web_bytes is not None:
        website_public_root.mkdir(parents=True, exist_ok=True)
        (website_public_root / "brief.pdf").write_bytes(web_bytes)


class TestCrossSurfaceParity(unittest.TestCase):
    def test_positive_case_matching_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lovs = root / "lovs"
            web = root / "web"
            payload = b"%PDF-1.7 fake content"
            _make_pair(lovs, web, payload, payload)
            result = check_cross_surface_parity(lovs, web)
            self.assertEqual(result["mismatches"], [])
            # missing entries for the other 3 static pairs + glob (no source files)
            # are expected; this test focuses on the matched brief.pdf pair
            self.assertGreaterEqual(result["checked"], 1)
            # ensure brief.pdf is NOT in the mismatches or missing lines
            self.assertFalse(any("brief.pdf" in m for m in result["mismatches"]))
            self.assertFalse(any("brief.pdf" in m for m in result["missing"]))

    def test_negative_case_bytes_differ(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lovs = root / "lovs"
            web = root / "web"
            _make_pair(lovs, web, b"LOVS-content", b"WEBSITE-content-drifted")
            result = check_cross_surface_parity(lovs, web)
            self.assertEqual(len(result["mismatches"]), 1)
            self.assertIn("brief.pdf", result["mismatches"][0])
            self.assertIn("LOVS sha256=", result["mismatches"][0])
            self.assertIn("website sha256=", result["mismatches"][0])

    def test_missing_website_side(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lovs = root / "lovs"
            web = root / "web"
            # LOVS has brief.pdf; website public root does not
            _make_pair(lovs, web, b"x", None)
            web.mkdir(parents=True, exist_ok=True)  # create empty website root
            result = check_cross_surface_parity(lovs, web)
            self.assertEqual(result["mismatches"], [])
            self.assertTrue(any("brief.pdf" in m and "website side missing" in m for m in result["missing"]))

    def test_public_dataset_manifest_compares_sanitized_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lovs = root / "lovs"
            web = root / "web"
            lovs_manifest = {
                "schema_version": 2,
                "inputs": [{"path": "data/live-bdbv-2026-output.json", "sha256": "abc"}],
                "outputs": [{"path": "lovs-public-health-dataset.xlsx", "sha256": "def"}],
            }
            web_manifest = {
                "schema_version": 2,
                "outputs": [{"path": "lovs-public-health-dataset.xlsx", "sha256": "def"}],
            }
            lovs_path = lovs / "deliverables" / "public-health-dataset"
            lovs_path.mkdir(parents=True)
            web.mkdir(parents=True)
            (lovs_path / "lovs-public-health-dataset.manifest.json").write_text(
                json.dumps(lovs_manifest, indent=2) + "\n",
                encoding="utf-8",
            )
            (web / "lovs-public-health-dataset.manifest.json").write_text(
                json.dumps(web_manifest, indent=2) + "\n",
                encoding="utf-8",
            )

            result = check_cross_surface_parity(lovs, web)

        self.assertEqual(result["mismatches"], [])
        self.assertFalse(
            any("lovs-public-health-dataset.manifest.json" in m for m in result["missing"])
        )


if __name__ == "__main__":
    unittest.main()
