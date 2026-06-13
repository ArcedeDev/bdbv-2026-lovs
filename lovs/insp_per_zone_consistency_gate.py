# SPDX-License-Identifier: Apache-2.0
"""INSP per-zone consistency release gate (spec §7.2 + §6.7).

This gate runs over the canonical live snapshot and the snapshot_contract.
It is intentionally **redundant** with `snapshot_contract.validate_contract`
but binds at a different choke point: snapshot_contract is the build-time
gate (contract derivation refuses), this is the release-time gate (release
script refuses regardless of how the contract was derived).

What it enforces:

1. Scale-resilience invariant (spec §6.7): every snapshot must declare
   `data_scale_used`; scales that imply per-zone availability
   (`per_zone`, `partial_per_zone`, `mixed_with_metric_floor`) must carry an
   `insp_per_zone_block`; `national` is allowed to omit the block.

2. Reconciliation contract (spec §5.1): for every metric in the four-metric
   set, `sum(by_lovs_zone[zone][metric]) + unallocated_residual[metric] ==
   national_at_data_date[metric]`.

3. Source-id/method match: INRB-UMIE CSV source-load must reference an
   INRB-UMIE consortium release; reviewed-SitRep source-load must reference
   an `inrb-sitrep-*` primary source. Method basis must be the declared
   per-zone vocabulary.

4. Komanda `mixed_with_metric_floor` case (Phase 2 finding): when the snapshot
   declares `mixed_with_metric_floor`, the gate accepts per-metric asymmetric
   attribution (a zone may be present for one metric and absent for another)
   AND the reconciliation contract still holds metric-by-metric.

Stdlib-only. Findings shape mirrors `cross_surface_parity.check_*`: returns
a list[str] of human-readable problem lines; an empty list means the gate is
clean.
"""
from __future__ import annotations

import json
import pathlib
from typing import Any

from lovs import snapshot_contract


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_SNAPSHOT_PATH = REPO_ROOT / "data" / "live-bdbv-2026-output.json"


def check_insp_per_zone_consistency(
    snapshot_path: pathlib.Path = DEFAULT_SNAPSHOT_PATH,
) -> list[str]:
    """Return a list of human-readable problem lines (empty = clean gate)."""
    if not snapshot_path.is_file():
        return [f"snapshot file missing at {snapshot_path}"]
    try:
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"{snapshot_path}: invalid JSON: {exc}"]

    problems: list[str] = []
    data_scale_used = snapshot.get("data_scale_used")
    insp_block = snapshot.get("insp_per_zone_block")

    # (1) Scale-resilience invariant
    if data_scale_used is None:
        if insp_block is not None:
            problems.append(
                "snapshot carries insp_per_zone_block but no data_scale_used "
                "declaration (spec §6.7 requires explicit scale)"
            )
        # When neither field is present, this gate stays silent: the snapshot
        # belongs to a transitional cycle. snapshot_contract enforces the
        # broader required-fields contract.
        return problems

    if data_scale_used not in snapshot_contract.VALID_DATA_SCALES:
        problems.append(
            f"data_scale_used={data_scale_used!r} not in "
            f"{snapshot_contract.VALID_DATA_SCALES!r}"
        )
        return problems

    if data_scale_used in snapshot_contract.SCALES_REQUIRING_PER_ZONE_BLOCK:
        if insp_block is None:
            problems.append(
                f"data_scale_used={data_scale_used!r} requires an "
                "insp_per_zone_block to be present (spec §6.7)"
            )
            return problems

    if insp_block is None:
        # data_scale_used == "national" and no INSP block; nothing further to
        # check on this surface.
        return problems

    # (2) + (3) Reconciliation + source-id + method_basis
    problems.extend(_check_insp_block(insp_block, data_scale_used))
    return problems


def _check_insp_block(block: Any, data_scale_used: str) -> list[str]:
    problems: list[str] = []
    if not isinstance(block, dict):
        return ["insp_per_zone_block must be an object"]
    method_basis = block.get("method_basis")
    if not snapshot_contract.is_valid_insp_per_zone_method_basis(method_basis):
        problems.append(
            f"insp_per_zone_block.method_basis={method_basis!r}; expected "
            f"{snapshot_contract.insp_per_zone_method_basis_source_label()}"
        )
    source_id = str(block.get("source_id", ""))
    method_basis_str = str(method_basis or "")
    is_reviewed_sitrep_method = (
        method_basis_str.startswith(snapshot_contract.REVIEWED_INSP_SITREP_METHOD_BASIS_PREFIX)
        and method_basis_str.endswith(snapshot_contract.REVIEWED_INSP_SITREP_METHOD_BASIS_SUFFIX)
    )
    if is_reviewed_sitrep_method:
        valid_source = source_id.lower().startswith("inrb-sitrep-")
        expected_source = "reviewed INSP SitRep source"
    else:
        valid_source = "inrb-umie" in source_id.lower()
        expected_source = "INRB-UMIE consortium release"
    if not valid_source:
        problems.append(
            f"insp_per_zone_block.source_id={source_id!r} does not reference an "
            f"{expected_source}"
        )
    by_lovs_zone = block.get("by_lovs_zone") or {}
    national = block.get("national_at_data_date") or {}
    residual = block.get("unallocated_residual") or {}
    for metric in snapshot_contract.INSP_METRICS:
        zone_sum = sum(
            row.get(metric, 0) for row in by_lovs_zone.values() if isinstance(row, dict)
        )
        nat = national.get(metric, 0)
        res = residual.get(metric, 0)
        if not isinstance(nat, int) or not isinstance(res, int):
            problems.append(
                f"insp_per_zone_block national/residual for {metric!r} must be int; "
                f"got national={nat!r}, residual={res!r}"
            )
            continue
        if zone_sum + res != nat:
            problems.append(
                f"insp_per_zone_block reconciliation violated for {metric!r}: "
                f"sum(by_lovs_zone)={zone_sum} + residual={res} != national={nat}"
            )
        if res < 0:
            problems.append(
                f"insp_per_zone_block.unallocated_residual.{metric}={res} must be >= 0"
            )
    # (4) Komanda mixed_with_metric_floor case: every per-zone row must carry a
    # `present_in_insp_classification` value so per-metric asymmetric
    # attribution is queryable downstream.
    if data_scale_used == "mixed_with_metric_floor":
        for zone_id, row in by_lovs_zone.items():
            if not isinstance(row, dict):
                continue
            if not row.get("present_in_insp_classification"):
                problems.append(
                    f"data_scale_used=mixed_with_metric_floor but by_lovs_zone.{zone_id} "
                    "lacks present_in_insp_classification (Phase 2 finding: required "
                    "for metric-asymmetric cases such as Komanda)"
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
    problems = check_insp_per_zone_consistency(args.snapshot)
    for line in problems:
        sys.stderr.write(f"[FAIL] insp_per_zone_consistency: {line}\n")
    if problems:
        return 1
    print("insp_per_zone_consistency_gate ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
