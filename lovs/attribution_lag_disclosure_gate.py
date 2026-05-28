# SPDX-License-Identifier: Apache-2.0
"""Attribution-lag disclosure release gate (spec §7.2).

This gate refuses any snapshot whose `insp_per_zone_block` carries non-zero
per-zone `confirmed_deaths` without an accompanying `attribution_lag_disclosure`
declaring the confirmed-deaths trailing status. The 1-3 week INRB clinical
review queue lag is the load-bearing surface that prevents a reader from
mistaking the lower-bound per-zone confirmed_deaths for the true total.

Cross-binding with `snapshot_contract.validate_contract`: snapshot_contract
checks the disclosure's shape and narrative; this gate checks the cross-field
implication (block presence implies disclosure presence).

Stdlib-only.
"""
from __future__ import annotations

import json
import pathlib
from typing import Any


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_SNAPSHOT_PATH = REPO_ROOT / "data" / "live-bdbv-2026-output.json"


def check_attribution_lag_disclosure(
    snapshot_path: pathlib.Path = DEFAULT_SNAPSHOT_PATH,
) -> list[str]:
    if not snapshot_path.is_file():
        return [f"snapshot file missing at {snapshot_path}"]
    try:
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"{snapshot_path}: invalid JSON: {exc}"]

    problems: list[str] = []
    insp_block: Any = snapshot.get("insp_per_zone_block")
    lag: Any = snapshot.get("attribution_lag_disclosure")
    if insp_block is None:
        # No per-zone surface; attribution-lag is not required by this gate.
        return problems
    if not isinstance(insp_block, dict):
        return ["insp_per_zone_block must be an object"]
    by_lovs_zone = insp_block.get("by_lovs_zone") or {}
    has_per_zone_confirmed_deaths = any(
        isinstance(row, dict) and row.get("confirmed_deaths", 0) > 0
        for row in by_lovs_zone.values()
    )
    has_unallocated_deaths_residual = (
        (insp_block.get("unallocated_residual") or {}).get("confirmed_deaths", 0) > 0
    )
    surface_requires_lag = (
        has_per_zone_confirmed_deaths or has_unallocated_deaths_residual
    )
    if not surface_requires_lag:
        return problems
    if lag is None:
        problems.append(
            "insp_per_zone_block carries per-zone confirmed_deaths or a "
            "non-zero deaths residual; attribution_lag_disclosure must be "
            "present (spec §2.3)"
        )
        return problems
    if not isinstance(lag, dict):
        return ["attribution_lag_disclosure must be an object"]
    per_metric = lag.get("per_metric") or []
    declared_metrics = {
        row.get("metric")
        for row in per_metric
        if isinstance(row, dict)
    }
    if "confirmed_deaths" not in declared_metrics:
        problems.append(
            "attribution_lag_disclosure.per_metric does not include "
            "confirmed_deaths (required when per-zone deaths surface is present)"
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
    args = parser.parse_args(argv)
    problems = check_attribution_lag_disclosure(args.snapshot)
    for line in problems:
        sys.stderr.write(f"[FAIL] attribution_lag_disclosure: {line}\n")
    if problems:
        return 1
    print("attribution_lag_disclosure_gate ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
