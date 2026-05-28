# SPDX-License-Identifier: Apache-2.0
"""Zone-alias-bridge coverage release gate (spec §7.2).

Every entry in `corridor_watchlist.source_zones` (and every key in
`insp_per_zone_block.by_lovs_zone` when present) must have a corresponding
INRB canonical Nom in the alias bridge. A bridge miss silently drops per-zone
data on import. This gate makes that silence impossible.

The bridge is loaded via `lovs.zone_alias_bridge.ZoneAliasBridge.load_default`
so the gate uses the same vendored upstream aliases as the loader.

Stdlib-only.
"""
from __future__ import annotations

import json
import pathlib

from lovs.zone_alias_bridge import ZoneAliasBridge, ZoneAliasBridgeError


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_SNAPSHOT_PATH = REPO_ROOT / "data" / "live-bdbv-2026-output.json"
DEFAULT_CONTRACT_PATH = REPO_ROOT / "data" / "snapshot_contract.json"


def check_zone_alias_bridge_coverage(
    snapshot_path: pathlib.Path = DEFAULT_SNAPSHOT_PATH,
    contract_path: pathlib.Path = DEFAULT_CONTRACT_PATH,
) -> list[str]:
    problems: list[str] = []
    try:
        bridge = ZoneAliasBridge.load_default()
    except ZoneAliasBridgeError as exc:
        return [f"zone_alias_bridge load failed: {exc}"]
    # Self-consistency: the bridge must round-trip.
    if not bridge.round_trip_ok():
        problems.append("zone_alias_bridge round-trip failed (duplicate INRB target?)")

    expected: set[str] = set()
    if snapshot_path.is_file():
        try:
            snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            return [f"{snapshot_path}: invalid JSON: {exc}"]
        insp_block = snapshot.get("insp_per_zone_block") or {}
        expected.update((insp_block.get("by_lovs_zone") or {}).keys())
    if contract_path.is_file():
        try:
            contract = json.loads(contract_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            return problems + [f"{contract_path}: invalid JSON: {exc}"]
        expected.update(contract.get("corridor_watchlist", {}).get("source_zones") or [])
    for zone_id in sorted(expected):
        if bridge.inrb_for(zone_id) is None:
            problems.append(
                f"zone_alias_bridge has no INRB Nom for LOVS source zone {zone_id!r}; "
                "add it to data/lovs_zone_alias_bridge.json or remove from the "
                "corridor_watchlist.source_zones / insp_per_zone_block"
            )
    return problems


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--snapshot",
        type=pathlib.Path,
        default=DEFAULT_SNAPSHOT_PATH,
    )
    parser.add_argument(
        "--contract",
        type=pathlib.Path,
        default=DEFAULT_CONTRACT_PATH,
    )
    args = parser.parse_args(argv)
    problems = check_zone_alias_bridge_coverage(args.snapshot, args.contract)
    for line in problems:
        sys.stderr.write(f"[FAIL] zone_alias_bridge: {line}\n")
    if problems:
        return 1
    print("zone_alias_bridge_gate ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
