# SPDX-License-Identifier: Apache-2.0
"""Tests for lovs.retrospective_attribution_audit_gate (spec section 9.2)."""
from __future__ import annotations

import copy
import json
import pathlib
import tempfile
import unittest

from lovs import retrospective_attribution_audit_gate as gate


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _good_ledger() -> dict:
    return {
        "blocks": [
            {
                "block_id": "calibration-block:test:2026-05-20",
                "pinned_at": "2026-05-20",
                "points": [
                    {"source": "bunia", "target": "bundibugyo-uga", "risk_adj_50": [0.04, 0.12]},
                ],
            },
            {
                "block_id": "calibration-block:test:2026-05-26",
                "pinned_at": "2026-05-26",
                "points": [
                    {"source": "bunia", "target": "goma-cod", "risk_adj_50": [0.10, 0.27]},
                ],
            },
        ],
    }


def _write_pair(tmp_dir: pathlib.Path, ledger: dict) -> tuple[pathlib.Path, pathlib.Path]:
    ledger_path = tmp_dir / "ledger.json"
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
    pinned_hashes = {
        b["block_id"]: gate.compute_block_hash(b) for b in ledger["blocks"]
    }
    hashes_path = tmp_dir / "pinned-block-hashes.json"
    hashes_path.write_text(
        json.dumps({"block_hashes": pinned_hashes}), encoding="utf-8"
    )
    return ledger_path, hashes_path


class TestRetrospectiveAuditGate(unittest.TestCase):
    def test_clean_ledger_passes(self):
        with tempfile.TemporaryDirectory() as td:
            ledger_path, hashes_path = _write_pair(pathlib.Path(td), _good_ledger())
            self.assertEqual(
                [],
                gate.check_pinned_blocks_unchanged(ledger_path, hashes_path),
            )

    def test_synthetic_mutation_of_pinned_block_refused(self):
        with tempfile.TemporaryDirectory() as td:
            tmp_dir = pathlib.Path(td)
            ledger_path, hashes_path = _write_pair(tmp_dir, _good_ledger())
            # Mutate the pinned block's risk_adj_50.
            mutated = _good_ledger()
            mutated["blocks"][0]["points"][0]["risk_adj_50"] = [0.99, 0.99]
            ledger_path.write_text(json.dumps(mutated), encoding="utf-8")
            problems = gate.check_pinned_blocks_unchanged(ledger_path, hashes_path)
            self.assertTrue(any("hash changed" in p for p in problems))

    def test_removed_pinned_block_refused(self):
        with tempfile.TemporaryDirectory() as td:
            tmp_dir = pathlib.Path(td)
            ledger_path, hashes_path = _write_pair(tmp_dir, _good_ledger())
            mutated = _good_ledger()
            mutated["blocks"].pop(0)  # remove first block
            ledger_path.write_text(json.dumps(mutated), encoding="utf-8")
            problems = gate.check_pinned_blocks_unchanged(ledger_path, hashes_path)
            self.assertTrue(any("missing from the current ledger" in p for p in problems))

    def test_new_block_added_is_allowed(self):
        with tempfile.TemporaryDirectory() as td:
            tmp_dir = pathlib.Path(td)
            ledger_path, hashes_path = _write_pair(tmp_dir, _good_ledger())
            extended = _good_ledger()
            extended["blocks"].append(
                {
                    "block_id": "calibration-block:test:2026-06-19",
                    "pinned_at": "2026-06-19",
                    "points": [],
                }
            )
            ledger_path.write_text(json.dumps(extended), encoding="utf-8")
            # New block has no reference hash; gate stays silent.
            problems = gate.check_pinned_blocks_unchanged(ledger_path, hashes_path)
            self.assertEqual([], problems)

    def test_missing_hashes_file_is_tolerated(self):
        with tempfile.TemporaryDirectory() as td:
            tmp_dir = pathlib.Path(td)
            ledger_path = tmp_dir / "ledger.json"
            ledger_path.write_text(json.dumps(_good_ledger()))
            hashes_path = tmp_dir / "nonexistent.json"
            problems = gate.check_pinned_blocks_unchanged(ledger_path, hashes_path)
            self.assertEqual([], problems)


class TestAgainstLiveRepo(unittest.TestCase):
    def test_committed_ledger_matches_pinned_hashes(self):
        # The pinned-hashes file landed in S7 must match the current ledger.
        self.assertEqual([], gate.check_pinned_blocks_unchanged())


if __name__ == "__main__":
    unittest.main()
