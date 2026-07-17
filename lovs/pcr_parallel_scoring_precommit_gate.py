# SPDX-License-Identifier: Apache-2.0
"""Release gate for the PCR-modulator parallel-scoring pre-commitment.

Belt-and-suspenders alongside `pcr_modulator_shadow_gate`: the shadow gate
keeps the modulator surface at `shadow_in_v1`; this gate ensures the
pre-committed evidence path that could ever graduate it is present and honest.

It refuses a release whose pre-commitment artifact is missing, malformed,
hash-tampered, inconsistent with the live snapshot's modulated bands over its
frozen cohort, scoring a non-shadow surface, or whose resolution checkpoint
precedes the snapshot's own resolution. This makes promotion an EARNED outcome
of a frozen scoring contract rather than an editorial choice.

The scoring cohort is FROZEN at registration; the modulated surface may grow
underneath it as the outbreak reaches new zones that already carry documented
PCR capacity. See the frozen-cohort contract note in
`check_pcr_parallel_scoring_precommit` for why an equality check there was a
re-pin treadmill that prevented any pin from reaching its own checkpoint, and
for what is still enforced (cohort shrinkage, and E1 verbatim over the scored
zones, which is the anti-retrofit property).

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
    snapshot: dict | None = None
    if snapshot_path.is_file():
        try:
            snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            return [f"{snapshot_path}: invalid JSON: {exc}"]
        if snapshot.get("per_zone_under_ascertainment_bands") is None:
            return []

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

    if snapshot is None:
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

    # FROZEN-COHORT CONTRACT (2026-07-17). `in_scope_zones` is frozen at registration and
    # the live modulated surface is allowed to GROW underneath it.
    #
    # This replaces an `in_scope_zones == modulated` equality. That equality looked like
    # anti-retrofit protection but was really a re-pin treadmill: the modulated set is
    # DERIVED (documented Africa CDC PCR capacity, intersected with the affected-zone
    # roster), so it grows whenever the outbreak reaches a zone that ALREADY had documented
    # capacity. No estimator and no editorial choice is involved: the disease moves, the set
    # grows, the equality breaks, and a fresh registration is forced. That happened twice in
    # two cycles (10 zones at 2026-05-28 -> 15 at 2026-07-14 -> 16 at 2026-07-15, when Mahagi
    # confirmed). A "pre-commitment" re-registered every time the data moves is not a
    # pre-commitment, and it can never reach its own checkpoint: the 2026-05-28 artifact duly
    # expired unscored.
    #
    # The pin scores a FROZEN DATA COHORT, not the live surface (see the Resolution-target
    # section of `pcr_parallel_score`): E0 and E1 are pinned over the in-scope zones as of
    # `data_cohort_as_of`, and at the checkpoint each is scored against a restatement of that
    # SAME cohort period. A zone that becomes affected after registration was never part of
    # that experiment and cannot make the frozen comparison wrong.
    #
    # What is still enforced, exactly where it matters:
    #   - the frozen cohort must still be a SUBSET of the modulated set (below): a scored zone
    #     that loses its band breaks the experiment, so cohort SHRINKAGE fails loud. The
    #     modulated set only grows as the outbreak spreads, so shrinkage means the capacity
    #     table or the affected-zone roster regressed.
    #   - E1 must still match the live bands VERBATIM for the in-scope zones, which is the
    #     anti-retrofit property: the zones being scored cannot be re-fitted after outcomes.
    #     If the modulator itself starts producing different bands for the frozen cohort, that
    #     is a METHOD change, it still fails here, and it still demands an append-only
    #     re-registration. A different E1 is a different experiment. Only the outbreak
    #     spreading is now free.
    #   - zones modulated but outside the frozen cohort are REPORTED (`out_of_cohort`), never
    #     silently ignored: growth must stay visible.
    by_zone = bands.get("by_lovs_zone") or {}
    modulated = sorted(
        zone_id
        for zone_id, row in by_zone.items()
        if isinstance(row, dict) and row.get("lo") is not None and row.get("hi") is not None
    )
    in_scope = artifact.get("in_scope_zones")
    if not isinstance(in_scope, list) or not in_scope:
        return problems + ["in_scope_zones must be a non-empty list"]
    if any(not isinstance(z, str) for z in in_scope):
        return problems + ["in_scope_zones must contain only zone-id strings"]
    if in_scope != sorted(in_scope):
        problems.append(
            "in_scope_zones must be sorted; the frozen cohort is part of the content hash"
        )

    dropped = sorted(set(in_scope) - set(modulated))
    if dropped:
        problems.append(
            f"frozen scoring cohort zone(s) {dropped} are NO LONGER modulated in the live "
            "snapshot. The modulated set only grows as the outbreak spreads, so a zone "
            "leaving it means the capacity table or the affected-zone roster regressed, and "
            "the pinned experiment can no longer be scored over its own cohort."
        )

    e1 = (estimators.get("E1_pcr_modulated") or {}).get("band_by_zone") or {}
    for zone_id in in_scope:
        if zone_id in dropped:
            continue  # already reported; there is no live band to compare against
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


def out_of_cohort_zones(
    precommit_path: pathlib.Path = DEFAULT_PRECOMMIT_PATH,
    snapshot_path: pathlib.Path = DEFAULT_SNAPSHOT_PATH,
) -> list[str]:
    """Modulated zones the frozen scoring cohort does NOT cover.

    These are zones the outbreak reached after the pin was registered. They render their
    diagnostic-access ring like any other documented zone, but they are outside the frozen
    experiment and are not scored at the checkpoint. Surfaced so the growth is visible
    rather than implicit: a cohort that silently stops covering the map is how a
    disclosed-but-inert surface turns into an undisclosed one.
    """
    if not (precommit_path.is_file() and snapshot_path.is_file()):
        return []
    try:
        artifact = json.loads(precommit_path.read_text(encoding="utf-8"))
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    bands = snapshot.get("per_zone_under_ascertainment_bands") or {}
    by_zone = bands.get("by_lovs_zone") or {}
    modulated = {
        zone_id
        for zone_id, row in by_zone.items()
        if isinstance(row, dict) and row.get("lo") is not None and row.get("hi") is not None
    }
    in_scope = artifact.get("in_scope_zones")
    if not isinstance(in_scope, list):
        return []
    return sorted(modulated - set(z for z in in_scope if isinstance(z, str)))


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
    # Growth is legal under the frozen-cohort contract but must never be silent: say which
    # modulated zones the pinned experiment does not cover, so a cohort quietly falling
    # behind the map is visible at release time rather than at the checkpoint.
    out_of_cohort = out_of_cohort_zones(args.precommit, args.snapshot)
    if out_of_cohort:
        print(
            f"    info: {len(out_of_cohort)} modulated zone(s) outside the frozen scoring "
            f"cohort (rendered, not scored): {', '.join(out_of_cohort)}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
