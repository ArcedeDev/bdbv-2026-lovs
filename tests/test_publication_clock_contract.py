# SPDX-License-Identifier: Apache-2.0
"""Tests for the count-clock cross-surface release contract."""
from __future__ import annotations

import json
import pathlib
import tempfile
import unittest

from lovs import publication_clock_contract


class TestPublicationClockContract(unittest.TestCase):
    def test_current_snapshot_declares_publication_clock_primary(self):
        result = publication_clock_contract.validate()
        self.assertGreaterEqual(result["primaries_checked"], 3)
        self.assertGreaterEqual(result["publication_clock_only"], 1)

    def test_publication_clock_primary_requires_audit_declaration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            contract = root / "contract.json"
            live = root / "live.json"
            manifest = root / "manifest.json"

            contract.write_text(
                json.dumps({
                    "reported_counts": {
                        "deaths": {
                            "primary_source_id": "publication-only-source",
                        },
                    },
                }),
                encoding="utf-8",
            )
            live.write_text(
                json.dumps({
                    "analysis_dependency_audit": [
                        {
                            "surface": "death_back_projection_and_grid",
                            "inputs": {"deaths": 179},
                            "clock_basis": "dated source input",
                        },
                    ],
                }),
                encoding="utf-8",
            )
            manifest.write_text(
                json.dumps({
                    "entries": [
                        {
                            "source_id": "publication-only-source",
                            "normalized_content": {"date_rapportage": None},
                        },
                    ],
                }),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                publication_clock_contract.PublicationClockContractError,
                "declares the publication clock",
            ):
                publication_clock_contract.validate(contract, live, manifest)


if __name__ == "__main__":
    unittest.main()
