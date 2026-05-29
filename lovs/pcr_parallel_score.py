# SPDX-License-Identifier: Apache-2.0
"""PCR-modulator parallel-scoring pre-commitment (spec section 8.2).

The PCR-capacity ascertainment modulator ships as a `shadow_in_v1` surface
(see `pcr_capacity_prior_modulator.py` and `pcr_modulator_shadow_gate.py`): it
is computed and disclosed but feeds no published number. Spec section 8.2
requires that any promotion of the modulator to a primary burden surface be
gated behind a pre-committed parallel-scoring outcome cycle, and explicitly
defers the scoring "specifics" to the per-cycle plan. This module is that
per-cycle plan, made executable.

Design
------
Two estimators of per-zone case ascertainment (reported / true), both frozen at
pin time, are scored head to head at the resolution checkpoint:

- ``E0`` (null): the uniform species-default band, applied to every zone. This
  is the model's current behaviour.
- ``E1`` (candidate): the PCR-capacity-modulated per-zone band, taken verbatim
  from the snapshot's ``per_zone_under_ascertainment_bands`` (already content-
  hashed into the live snapshot, so E1 cannot be retrofitted after outcomes are
  known).

The in-scope zones are exactly those where E1 differs from E0 (the modulated
zones); species-default-fallback zones carry E1 == E0 and cannot discriminate.

Resolution target
-----------------
The empirical per-zone ascertainment is proxied by the suspected-to-confirmed
restatement: for each in-scope zone, ``confirmed(cohort cutoff) /
confirmed(restated at checkpoint)`` from a later INRB-UMIE build that restates
the same <= cohort period. NOTE (limitation): restated-confirmed is itself
ascertainment-limited, so this proxy is a conservative UPPER proxy for true
ascertainment (true infections >= eventually-confirmed). The test therefore
asks "does PCR capacity anticipate the per-zone completeness pattern better
than a uniform prior", not "does E1 recover true infection burden". Final
WHO/INRB totals or seroprevalence, if published, are longer-horizon resolvables.

Scoring
-------
Each estimator's per-zone band is scored against the empirical proxy with the
repository's interval score (`lovs_validation.interval_score`, Bracher 2021) at
``alpha = SCORING_ALPHA``. Lower is better. The estimator-level score is the
mean over the in-scope zones that have resolution data at the checkpoint.

Promotion bar (deliberately stricter than spec section 8.2's "not worse")
------------------------------------------------------------------------
On the founder's ethics direction, promotion of the modulator out of
`shadow_in_v1` is NOT granted on mere non-regression. E1 must demonstrably BEAT
E0 by at least ``PROMOTION_RELATIVE_MARGIN`` (relative) and the result must
replicate across ``PROMOTION_CYCLES_REQUIRED`` consecutive resolution
checkpoints. Otherwise the modulator stays a disclosed diagnostic-access shadow.

Stdlib only except for the repository's own `lovs_validation` primitives. Pure
and deterministic on input; no clock, no network.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from lovs import lovs_validation


PRECOMMIT_ID = "pcr-ascertainment-parallel-scoring:bdbv-uga-cod-2026:2026-05-28"
SCHEMA_VERSION = 1

# 50% interval convention for the band score. The under-ascertainment band is a
# (lo, hi) range without a stated nominal coverage; alpha = 0.5 is a pinned
# scoring convention (interquartile style) applied identically to E0 and E1, so
# the comparison is fair regardless of the band's nominal coverage.
SCORING_ALPHA = 0.5

# Promotion requires E1 to beat E0 by at least this relative margin on the mean
# interval score, replicated across the required number of resolution cycles.
PROMOTION_RELATIVE_MARGIN = 0.10
PROMOTION_CYCLES_REQUIRED = 2


class PCRParallelScoreError(ValueError):
    """Raised when the pre-commitment artifact is malformed."""


def _canonical_hash(artifact: Mapping[str, Any]) -> str:
    """SHA-256 over the canonical artifact, excluding the content_hash field."""
    payload = {k: v for k, v in artifact.items() if k != "content_hash"}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_precommit(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Construct the parallel-scoring pre-commitment from a snapshot.

    E1 is read verbatim from the snapshot's modulated bands so the candidate
    estimator is pinned to the content-hashed live snapshot. The returned
    artifact carries a content_hash over its own canonical form.
    """
    bands = snapshot.get("per_zone_under_ascertainment_bands")
    if not isinstance(bands, dict):
        raise PCRParallelScoreError(
            "snapshot lacks per_zone_under_ascertainment_bands; cannot build pre-commitment"
        )
    species = bands.get("species_default_band") or {}
    if species.get("lo") is None or species.get("hi") is None:
        raise PCRParallelScoreError(
            "per_zone_under_ascertainment_bands.species_default_band must carry numeric lo and hi"
        )
    e0_lo = float(species.get("lo"))
    e0_hi = float(species.get("hi"))
    by_zone = bands.get("by_lovs_zone") or {}

    in_scope = sorted(
        zone_id
        for zone_id, row in by_zone.items()
        if isinstance(row, dict) and row.get("lo") is not None and row.get("hi") is not None
    )
    if not in_scope:
        raise PCRParallelScoreError(
            "no modulated zones in snapshot bands; nothing to parallel-score"
        )

    e0_band_by_zone = {zone_id: {"lo": e0_lo, "hi": e0_hi} for zone_id in in_scope}
    e1_band_by_zone = {
        zone_id: {
            "lo": float(by_zone[zone_id]["lo"]),
            "hi": float(by_zone[zone_id]["hi"]),
        }
        for zone_id in in_scope
    }

    artifact: dict[str, Any] = {
        "precommit_id": PRECOMMIT_ID,
        "schema_version": SCHEMA_VERSION,
        "outbreak_id": snapshot.get("outbreak_id"),
        "created_at_snapshot": str(snapshot.get("as_of", ""))[:10],
        "data_cohort_as_of": str(snapshot.get("data_as_of", ""))[:10],
        "resolution_checkpoint": str(snapshot.get("resolves_at", ""))[:10],
        "claim": (
            "The PCR-capacity-modulated per-zone ascertainment band (E1) predicts "
            "per-zone reporting completeness better than the uniform species-default "
            "band (E0). This is the only mechanism by which the modulator may graduate "
            "from shadow_in_v1 to a primary surface."
        ),
        "method_basis": bands.get("method_basis"),
        "scored_surface_role_at_pin": bands.get("surface_role"),
        "estimators": {
            "E0_species_default": {
                "label": "uniform species-default ascertainment band (current model behaviour)",
                "band_by_zone": e0_band_by_zone,
            },
            "E1_pcr_modulated": {
                "label": "PCR-capacity-modulated per-zone ascertainment band (candidate)",
                "band_by_zone": e1_band_by_zone,
            },
        },
        "in_scope_zones": in_scope,
        "resolution_target": {
            "quantity": "per_zone_empirical_ascertainment_proxy",
            "definition": (
                "confirmed(cohort cutoff) / confirmed(restated at checkpoint) per zone, "
                "from the first INRB-UMIE build at or after the resolution checkpoint that "
                "restates the <= cohort period"
            ),
            "is_proxy": True,
            "proxy_direction": "conservative_upper_proxy_for_true_ascertainment",
        },
        "scoring_rule": {
            "primary": {
                "metric": "interval_score",
                "reference": "Bracher 2021 eq.5 (lovs_validation.interval_score)",
                "alpha": SCORING_ALPHA,
                "aggregate": "mean over in-scope zones with resolution data; lower is better",
            },
            "secondary_documented": {
                "metric": "corridor brier and log scores per spec section 8.2",
                "status": (
                    "documented, computed at resolution if corridor outcomes are available; "
                    "propagates each estimator's band through lovs_visibility.nowcast and "
                    "lovs_next_zone.next_zone_risk for the pinned calibration corridors"
                ),
            },
        },
        "promotion_bar": {
            "rule": (
                "E1 graduates from shadow_in_v1 only if its mean interval score beats E0 "
                "by at least the relative margin AND the result replicates across the "
                "required number of consecutive resolution cycles. Stricter than spec "
                "section 8.2's 'not worse' framing, on the founder's ethics direction: "
                "promotion must be earned by demonstrated improvement, not non-regression."
            ),
            "relative_margin": PROMOTION_RELATIVE_MARGIN,
            "cycles_required": PROMOTION_CYCLES_REQUIRED,
            "on_failure": "modulator remains shadow_in_v1 (a disclosed diagnostic-access surface)",
        },
        "limitations": [
            "Restated-confirmed is ascertainment-limited; the resolution proxy is a "
            "conservative upper proxy for true ascertainment (true infections >= "
            "eventually-confirmed).",
            "Zones with zero confirmed cases at the checkpoint cannot be scored and are "
            "excluded at scoring time.",
            "PCR capacity is a planning/budget figure, not tests performed; the candidate "
            "tests a capacity proxy for ascertainment, not measured ascertainment.",
        ],
        "mutation_guard": (
            "Append-only pre-commitment. Mutates no pinned calibration block or mode_b "
            "hypothesis; introduces no published number. Registered before any resolution "
            "data exists."
        ),
    }
    artifact["content_hash"] = _canonical_hash(artifact)
    return artifact


def score_estimator(
    band_by_zone: Mapping[str, Mapping[str, float]],
    empirical_by_zone: Mapping[str, float | None],
    alpha: float = SCORING_ALPHA,
) -> dict[str, Any]:
    """Interval-score one estimator's per-zone bands against empirical values.

    Zones absent from `empirical_by_zone` or carrying `None` (no resolution
    data, e.g. zero confirmed at the checkpoint) are skipped.
    """
    per_zone: dict[str, float] = {}
    for zone_id, band in band_by_zone.items():
        observed = empirical_by_zone.get(zone_id)
        if observed is None:
            continue
        per_zone[zone_id] = lovs_validation.interval_score(
            float(band["lo"]), float(band["hi"]), float(observed), alpha
        )
    mean = sum(per_zone.values()) / len(per_zone) if per_zone else None
    return {
        "per_zone_interval_score": per_zone,
        "mean_interval_score": mean,
        "n_scored": len(per_zone),
    }


def decide_promotion(
    e0_mean: float | None,
    e1_mean: float | None,
    *,
    relative_margin: float = PROMOTION_RELATIVE_MARGIN,
    cycles_passed: int = 1,
    cycles_required: int = PROMOTION_CYCLES_REQUIRED,
) -> dict[str, Any]:
    """Apply the pre-committed promotion bar to a pair of estimator scores.

    Promotion requires E1 to beat E0 by at least `relative_margin` (relative to
    E0) on the mean interval score, replicated across `cycles_required`
    consecutive checkpoints.
    """
    if e0_mean is None or e1_mean is None or e0_mean <= 0:
        return {
            "promote": False,
            "rationale": "insufficient resolution data to score this cycle",
            "e0_mean_interval_score": e0_mean,
            "e1_mean_interval_score": e1_mean,
        }
    relative_improvement = (e0_mean - e1_mean) / e0_mean
    cycle_pass = relative_improvement >= relative_margin
    promote = cycle_pass and cycles_passed >= cycles_required
    return {
        "promote": promote,
        "e0_mean_interval_score": e0_mean,
        "e1_mean_interval_score": e1_mean,
        "relative_improvement": relative_improvement,
        "relative_margin": relative_margin,
        "cycle_pass": cycle_pass,
        "cycles_passed": cycles_passed,
        "cycles_required": cycles_required,
        "rationale": (
            "E1 graduates only if it beats E0 by the relative margin and the result "
            "replicates; otherwise the modulator remains shadow_in_v1."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    import argparse
    import pathlib
    import os
    import tempfile

    parser = argparse.ArgumentParser(description=__doc__)
    repo_root = pathlib.Path(__file__).resolve().parent.parent
    parser.add_argument(
        "--snapshot",
        type=pathlib.Path,
        default=repo_root / "data" / "live-bdbv-2026-output.json",
    )
    parser.add_argument(
        "--out",
        type=pathlib.Path,
        default=repo_root / "data" / "pcr_ascertainment_parallel_scoring.json",
    )
    parser.add_argument("--write", action="store_true", help="Write the pre-commitment artifact.")
    args = parser.parse_args(argv)

    snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
    artifact = build_precommit(snapshot)
    rendered = json.dumps(artifact, indent=2, sort_keys=True) + "\n"
    if args.write:
        # Atomic write: temp file in the same directory, then os.replace.
        fd, tmp = tempfile.mkstemp(dir=str(args.out.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(rendered)
            os.replace(tmp, args.out)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        print(f"pcr_ascertainment_parallel_scoring={args.out}")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
