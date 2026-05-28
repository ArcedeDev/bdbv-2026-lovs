# SPDX-License-Identifier: Apache-2.0
"""Tests for lovs.zone_alias_bridge_gate (spec §7.2)."""
from __future__ import annotations

import json
import pathlib
import tempfile
import time
import unittest

from lovs import zone_alias_bridge_gate as gate


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _write(name: str, d: dict, tmp_dir: pathlib.Path) -> pathlib.Path:
    p = tmp_dir / name
    p.write_text(json.dumps(d), encoding="utf-8")
    return p


class TestZoneAliasBridgeGate(unittest.TestCase):
    def test_committed_state_passes(self):
        # Default-loaded bridge covers every source zone in the committed
        # snapshot_contract.json by Phase 2 verification.
        self.assertEqual([], gate.check_zone_alias_bridge_coverage())

    def test_extra_source_zone_without_bridge_caught(self):
        with tempfile.TemporaryDirectory() as td:
            tmp_dir = pathlib.Path(td)
            # Snapshot stays empty.
            snap_path = _write("snap.json", {}, tmp_dir)
            # Contract claims a corridor_watchlist source the bridge does not
            # cover.
            contract = json.loads(
                (REPO_ROOT / "data" / "snapshot_contract.json").read_text(
                    encoding="utf-8"
                )
            )
            contract["corridor_watchlist"]["source_zones"] = sorted(
                set(contract["corridor_watchlist"]["source_zones"]) | {"never-bridged-zone"}
            )
            contract_path = _write("contract.json", contract, tmp_dir)
            problems = gate.check_zone_alias_bridge_coverage(snap_path, contract_path)
            self.assertTrue(any("never-bridged-zone" in p for p in problems))

    def test_insp_block_zone_without_bridge_caught(self):
        with tempfile.TemporaryDirectory() as td:
            tmp_dir = pathlib.Path(td)
            snap = {
                "insp_per_zone_block": {
                    "by_lovs_zone": {
                        "never-bridged-zone": {
                            "confirmed": 1,
                            "suspected": 1,
                            "confirmed_deaths": 0,
                            "suspected_deaths": 0,
                        },
                    },
                },
            }
            snap_path = _write("snap.json", snap, tmp_dir)
            # Use an empty contract so we isolate the snapshot path.
            contract_path = _write("contract.json", {}, tmp_dir)
            problems = gate.check_zone_alias_bridge_coverage(snap_path, contract_path)
            self.assertTrue(any("never-bridged-zone" in p for p in problems))

    def test_runtime_under_250ms(self):
        start = time.monotonic()
        gate.check_zone_alias_bridge_coverage()
        elapsed = time.monotonic() - start
        self.assertLess(elapsed, 0.25)


if __name__ == "__main__":
    unittest.main()
