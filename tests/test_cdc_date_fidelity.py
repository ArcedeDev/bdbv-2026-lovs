"""Unit tests for the CDC data-as-of fidelity gate.

The gate is a forward-looking tripwire that re-parses each CDC entry's raw
HTML and asserts the manifest's stored ``data_as_of`` still matches the
parser's output. These tests exercise the observable shapes the gate can
emit:

  (a) parser output matches the stored value (clean)
  (b) parser output differs from stored (mismatch, fail)
  (c) raw HTML missing on disk, no retention contract (unverifiable, info)
  (d) raw HTML missing on disk, retention contract in force (mismatch, fail)
  (e) stored ``data_as_of`` is null but parser produced a date (mismatch)
  (f) raw bytes are not parseable as HTML (unverifiable, info)
  (g) two entries: one matching, one missing (mixed)
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from lovs.cdc_date_fidelity import check_cdc_data_as_of_matches_raw

SYNTHETIC_HTML_AS_OF_MAY_19 = (
    "<html><body>"
    "<p>CDC Current Situation: May 20, 2026</p>"
    "<p>As of May 19, the DRC and Uganda Ministries of Health report the following:</p>"
    "<p>DRC: A total of 700 suspected cases, 60 confirmed cases, 150 suspected deaths, and 5 confirmed deaths.</p>"
    "<p>As of May 19, 2026, the Ebola Bundibugyo outbreak in DRC has been confirmed in three provinces.</p>"
    "</body></html>"
).encode("utf-8")


def _write_manifest(path: Path, entries: list[dict]) -> None:
    path.write_text(
        json.dumps({"entries": entries}, indent=2) + "\n",
        encoding="utf-8",
    )


class TestCdcDateFidelity(unittest.TestCase):
    def test_positive_case_parser_matches_stored(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sources = root / "sources"
            sources.mkdir()
            (sources / "cdc-current-situation-2026-05-20.html").write_bytes(
                SYNTHETIC_HTML_AS_OF_MAY_19
            )
            manifest_path = root / "manifest.json"
            _write_manifest(
                manifest_path,
                [
                    {
                        "source_id": "cdc-current-situation-2026-05-20",
                        "normalized_content": {"data_as_of": "2026-05-19"},
                    }
                ],
            )
            result = check_cdc_data_as_of_matches_raw(manifest_path, sources)
            self.assertEqual(result["checked"], 1)
            self.assertEqual(result["mismatches"], [])
            self.assertEqual(result["unverifiable"], [])

    def test_negative_case_stored_drifted_by_one_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sources = root / "sources"
            sources.mkdir()
            (sources / "cdc-current-situation-2026-05-20.html").write_bytes(
                SYNTHETIC_HTML_AS_OF_MAY_19
            )
            manifest_path = root / "manifest.json"
            _write_manifest(
                manifest_path,
                [
                    {
                        "source_id": "cdc-current-situation-2026-05-20",
                        "normalized_content": {"data_as_of": "2026-05-20"},
                    }
                ],
            )
            result = check_cdc_data_as_of_matches_raw(manifest_path, sources)
            self.assertEqual(result["checked"], 1)
            self.assertEqual(result["unverifiable"], [])
            self.assertEqual(len(result["mismatches"]), 1)
            self.assertIn("cdc-current-situation-2026-05-20", result["mismatches"][0])
            self.assertIn("stored=2026-05-20", result["mismatches"][0])
            self.assertIn("parsed=2026-05-19", result["mismatches"][0])

    def test_unverifiable_case_missing_raw_html(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sources = root / "sources"
            sources.mkdir()
            manifest_path = root / "manifest.json"
            _write_manifest(
                manifest_path,
                [
                    {
                        "source_id": "cdc-current-situation-2026-05-20",
                        "normalized_content": {"data_as_of": "2026-05-19"},
                    }
                ],
            )
            result = check_cdc_data_as_of_matches_raw(manifest_path, sources)
            self.assertEqual(result["checked"], 0)
            self.assertEqual(result["mismatches"], [])
            self.assertEqual(len(result["unverifiable"]), 1)
            self.assertIn("no retained raw HTML", result["unverifiable"][0])
            self.assertIn("cdc-current-situation-2026-05-20", result["unverifiable"][0])

    def test_stored_null_but_raw_present_is_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sources = root / "sources"
            sources.mkdir()
            (sources / "cdc-current-situation-2026-05-20.html").write_bytes(
                SYNTHETIC_HTML_AS_OF_MAY_19
            )
            manifest_path = root / "manifest.json"
            _write_manifest(
                manifest_path,
                [
                    {
                        "source_id": "cdc-current-situation-2026-05-20",
                        "normalized_content": {"data_as_of": None},
                    }
                ],
            )
            result = check_cdc_data_as_of_matches_raw(manifest_path, sources)
            self.assertEqual(result["checked"], 1)
            self.assertEqual(result["unverifiable"], [])
            self.assertEqual(len(result["mismatches"]), 1)
            self.assertIn("stored data_as_of is null", result["mismatches"][0])
            self.assertIn("parser produced 2026-05-19", result["mismatches"][0])

    def test_binary_file_falls_through_to_unverifiable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sources = root / "sources"
            sources.mkdir()
            (sources / "cdc-current-situation-2026-05-20.html").write_bytes(
                b"\xff\xfe\x00\x01\x02\x03\x80\x81PK\x03\x04"
            )
            manifest_path = root / "manifest.json"
            _write_manifest(
                manifest_path,
                [
                    {
                        "source_id": "cdc-current-situation-2026-05-20",
                        "normalized_content": {"data_as_of": "2026-05-19"},
                    }
                ],
            )
            result = check_cdc_data_as_of_matches_raw(manifest_path, sources)
            self.assertEqual(result["mismatches"], [])
            self.assertEqual(len(result["unverifiable"]), 1)
            self.assertIn("cdc-current-situation-2026-05-20", result["unverifiable"][0])

    def test_missing_raw_with_retention_required_is_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sources = root / "sources"
            sources.mkdir()
            manifest_path = root / "manifest.json"
            _write_manifest(
                manifest_path,
                [
                    {
                        "source_id": "cdc-current-situation-2026-05-25",
                        "normalized_content": {
                            "data_as_of": "2026-05-25",
                            "raw_retention_required": True,
                        },
                    }
                ],
            )
            result = check_cdc_data_as_of_matches_raw(manifest_path, sources)
            self.assertEqual(result["checked"], 0)
            self.assertEqual(result["unverifiable"], [])
            self.assertEqual(len(result["mismatches"]), 1)
            self.assertIn("cdc-current-situation-2026-05-25", result["mismatches"][0])
            self.assertIn("raw_retention_required=true", result["mismatches"][0])
            self.assertIn("raw HTML missing", result["mismatches"][0])

    def test_mixed_case_one_matching_one_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sources = root / "sources"
            sources.mkdir()
            (sources / "cdc-current-situation-2026-05-20.html").write_bytes(
                SYNTHETIC_HTML_AS_OF_MAY_19
            )
            manifest_path = root / "manifest.json"
            _write_manifest(
                manifest_path,
                [
                    {
                        "source_id": "cdc-current-situation-2026-05-20",
                        "normalized_content": {"data_as_of": "2026-05-19"},
                    },
                    {
                        "source_id": "cdc-current-situation-2026-05-21",
                        "normalized_content": {"data_as_of": "2026-05-21"},
                    },
                    {
                        "source_id": "ecdc-bdbv-drc-uga-2026-05-21-live",
                        "normalized_content": {"data_as_of": "2026-05-21"},
                    },
                ],
            )
            result = check_cdc_data_as_of_matches_raw(manifest_path, sources)
            self.assertEqual(result["checked"], 1)
            self.assertEqual(result["mismatches"], [])
            self.assertEqual(len(result["unverifiable"]), 1)
            self.assertIn("cdc-current-situation-2026-05-21", result["unverifiable"][0])


if __name__ == "__main__":
    unittest.main()
