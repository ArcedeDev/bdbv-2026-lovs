# SPDX-License-Identifier: Apache-2.0
"""Release gate for the PCR-modulator parallel-scoring pre-commitment.

Belt-and-suspenders alongside `pcr_modulator_shadow_gate`: the shadow gate
keeps the modulator surface at `shadow_in_v1`; this gate ensures the
pre-committed evidence path that could ever graduate it is present and honest.

It refuses a release whose pre-commitment artifact is missing, malformed,
hash-tampered, inconsistent with the live snapshot's modulated bands, scoring a
non-shadow surface, or whose resolution checkpoint precedes the snapshot's own
resolution. This makes promotion an EARNED outcome of a frozen scoring contract
rather than an editorial choice.

Stdlib only (plus `lovs.pcr_parallel_score` for the canonical hash recompute).
"""
from __future__ import annotations

import json
import pathlib

from lovs import pcr_parallel_score


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_SNAPSHOT_PATH = REPO_ROOT / "data" / "live-bdbv-2026-output.json"
DEFAULT_PRECOMMIT_PATH = (
    REPO_ROOT / "data" / "pcr_ascertainment_parallel_scoring.json"
)

REQUIRED_FIELDS = (
    "precommit_id",
    "schema_version",
    "resolution_checkpoint",
    "estimators",
    "in_scope_zones",
    "scoring_rule",
    "promotion_bar",
    "content_hash",
)


def check_pcr_parallel_scoring_precommit(
    precommit_path: pathlib.Path = DEFAULT_PRECOMMIT_PATH,
    snapshot_path: pathlib.Path = DEFAULT_SNAPSHOT_PATH,
) -> list[str]:
    if not precommit_path.is_file():
        return [f"pre-commitment artifact missing at {precommit_path}"]
    try:
        artifact = json.loads(precommit_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"{precommit_path}: invalid JSON: {exc}"]

    problems: list[str] = []
    for field in REQUIRED_FIELDS:
        if field not in artifact:
            problems.append(f"missing required field {field!r}")
    if problems:
        return problems

    # Content-hash integrity: the artifact must not have been edited after pinning.
    expected_hash = pcr_parallel_score._canonical_hash(artifact)
    if artifact.get("content_hash") != expected_hash:
        problems.append(
            "content_hash does not match canonical recompute (artifact tampered or stale)"
        )

    # The pre-commitment scores the SHADOW surface, never a primary one.
    if artifact.get("scored_surface_role_at_pin") != "shadow_in_v1":
        problems.append(
            "scored_surface_role_at_pin must be 'shadow_in_v1'; got "
            f"{artifact.get('scored_surface_role_at_pin')!r}"
        )

    # Both estimators must be present and disjointly named.
    estimators = artifact.get("estimators") or {}
    for required in ("E0_species_default", "E1_pcr_modulated"):
        if required not in estimators:
            problems.append(f"estimators missing {required!r}")

    if not snapshot_path.is_file():
        return problems
    try:
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        problems.append(f"{snapshot_path}: invalid JSON: {exc}")
        return problems

    # Resolution must be at or after the snapshot's own resolution (a forward
    # pre-commitment, registered before outcomes exist).
    snap_resolves = str(snapshot.get("resolves_at", ""))[:10]
    if snap_resolves and artifact.get("resolution_checkpoint", "") < snap_resolves:
        problems.append(
            f"resolution_checkpoint {artifact.get('resolution_checkpoint')!r} precedes "
            f"snapshot resolves_at {snap_resolves!r}"
        )

    bands = snapshot.get("per_zone_under_ascertainment_bands") or {}
    if bands.get("surface_role") != artifact.get("scored_surface_role_at_pin"):
        problems.append(
            "scored_surface_role_at_pin disagrees with the live snapshot surface_role "
            f"({bands.get('surface_role')!r})"
        )

    # E1 (candidate) must equal the snapshot's modulated bands verbatim, and the
    # in-scope zones must be exactly the modulated zones. This pins the candidate
    # to the content-hashed live snapshot so it cannot be retrofitted.
    by_zone = bands.get("by_lovs_zone") or {}
    modulated = sorted(
        zone_id
        for zone_id, row in by_zone.items()
        if isinstance(row, dict) and row.get("lo") is not None and row.get("hi") is not None
    )
    if artifact.get("in_scope_zones") != modulated:
        problems.append(
            f"in_scope_zones {artifact.get('in_scope_zones')} does not equal the snapshot's "
            f"modulated zones {modulated}"
        )
    e1 = (estimators.get("E1_pcr_modulated") or {}).get("band_by_zone") or {}
    for zone_id in modulated:
        snap_lo = float(by_zone[zone_id]["lo"])
        snap_hi = float(by_zone[zone_id]["hi"])
        row = e1.get(zone_id) or {}
        try:
            a_lo = float(row.get("lo"))
            a_hi = float(row.get("hi"))
        except (TypeError, ValueError):
            problems.append(f"E1 band for {zone_id} is missing or non-numeric")
            continue
        if abs(a_lo - snap_lo) > 1e-12 or abs(a_hi - snap_hi) > 1e-12:
            problems.append(
                f"E1 band for {zone_id} ({a_lo}, {a_hi}) does not match snapshot band "
                f"({snap_lo}, {snap_hi})"
            )
    return problems


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--precommit", type=pathlib.Path, default=DEFAULT_PRECOMMIT_PATH)
    parser.add_argument("--snapshot", type=pathlib.Path, default=DEFAULT_SNAPSHOT_PATH)
    args = parser.parse_args(argv)
    problems = check_pcr_parallel_scoring_precommit(args.precommit, args.snapshot)
    for line in problems:
        sys.stderr.write(f"[FAIL] pcr_parallel_scoring_precommit: {line}\n")
    if problems:
        return 1
    print("pcr_parallel_scoring_precommit_gate ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
