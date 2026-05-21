#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Run the LOVS model on your OWN point-of-care data, locally.

A friction-free "bring your own data" entrypoint for forks. Unlike the public
release pipeline (refresh_pipeline.py), this requires NO manifest provenance
entries and NO calibration ledger. You give one JSON file of your on-the-ground
figures and this runs the same visibility, transmission, and corridor-risk
models, then prints:

  1. the visibility-adjusted underlying-case view (how many cases you are likely
     missing), and
  2. a ranked corridor table: where onward spread is most likely, to prioritise
     where you survey and deploy next.

Per-zone case counts are honoured: a heavily affected health zone drives more
corridor risk than a lightly affected one. That per-zone signal is the single
biggest discrimination lever the public method is missing, because the public
sources only publish one aggregate count.

Nothing leaves your machine. No network calls, no third-party packages (just the
Python standard library and this repo's own lovs/ package).

  python3 run_local.py --input point_of_care_input.example.json
  python3 run_local.py --input my_data.json --json-out my_run.json

See FORKING.md for the walkthrough and input format.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
from dataclasses import replace

from lovs import lovs_next_zone
from lovs import lovs_priors_bundibugyo
from lovs import lovs_reconciler
from lovs import lovs_transmission
from lovs import lovs_visibility


REPO_ROOT = pathlib.Path(__file__).parent.resolve()


def _count(value: int) -> lovs_reconciler.ReconciledCount:
    """Wrap a single observed integer as a degenerate ReconciledCount."""
    v = int(value)
    return lovs_reconciler.ReconciledCount(
        minimum=v,
        maximum=v,
        primary_value=v,
        primary_source_id="point-of-care",
        conflicting_source_ids=(),
    )


def _as_of(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        return "1970-01-01T00:00:00Z"
    return raw if "T" in raw else f"{raw}T23:59:59Z"


def _edge_weights(raw: dict | None) -> dict | None:
    """Convert {'source->target': weight} into {(source, target): weight}."""
    if not raw:
        return None
    out: dict[tuple[str, str], float] = {}
    for key, val in raw.items():
        if "->" in str(key):
            source, target = (part.strip() for part in str(key).split("->", 1))
            out[(source, target)] = float(val)
    return out or None


def build_snapshot(poc: dict) -> lovs_reconciler.OutbreakSnapshot:
    """Build an OutbreakSnapshot from a point-of-care input dict.

    Aggregate counts (the sum across your zones) drive the visibility and
    transmission estimates; per-zone counts drive corridor risk (see run()).
    """
    zones = poc.get("source_zones") or []
    if not zones:
        raise ValueError(
            "input needs at least one source_zones entry with a zone_id and a confirmed count"
        )
    for i, zone in enumerate(zones):
        if not str(zone.get("zone_id", "")).strip():
            raise ValueError(f"source_zones[{i}] is missing a zone_id")

    def total(field: str) -> int:
        # Tolerate missing or null fields (a common hand-edit); treat as 0.
        return sum(int(z.get(field) or 0) for z in zones)

    return lovs_reconciler.OutbreakSnapshot(
        outbreak_id=poc.get("outbreak_id", "local-run"),
        as_of=_as_of(poc.get("as_of", "")),
        pathogen=poc.get("pathogen", "BDBV"),
        country_scope=tuple(poc.get("country_scope", ())),
        reported_counts={
            "confirmed": _count(total("confirmed")),
            "suspected": _count(total("suspected")),
        },
        reported_deaths=_count(total("deaths")),
        affected_zones=tuple(str(z["zone_id"]) for z in zones),
        sources=("point-of-care",),
        case_definition_version=None,
        source_conflict_notes=(),
        deaths_to_confirmed_tension_flag=False,
        model_version="lovs-local-run",
    )


def _localize_corridor(
    corridor: lovs_next_zone.CorridorRiskEstimate, confirmed: int
) -> lovs_next_zone.CorridorRiskEstimate:
    """Relabel a corridor's drivers/caveats for the local per-zone context.

    next_zone_risk emits public-pipeline language ("aggregate confirmed count
    ... applied to this source zone") because the public method has only one
    aggregate count to spread across every zone. run_local feeds each zone its
    OWN observed count, so that wording is rewritten to say "per-zone", and the
    matching "counts are aggregate, not source-zone-attributed" caveat is
    dropped (it is false here).
    """
    drivers = tuple(
        f"per-zone confirmed count {confirmed} (your point-of-care figure)"
        if d.startswith("aggregate confirmed count ")
        else d
        for d in corridor.drivers
    )
    caveats = tuple(
        c for c in corridor.caveats if not c.startswith("confirmed cases are aggregate")
    )
    return replace(corridor, drivers=drivers, caveats=caveats)


def run(poc: dict) -> dict:
    """Run visibility, transmission, and per-zone corridor risk on a PoC input."""
    base = build_snapshot(poc)
    targets = tuple(poc.get("candidate_target_zones") or ())
    if not targets:
        raise ValueError(
            "input needs candidate_target_zones (the zones to rank for onward-spread risk)"
        )
    raw_horizon = poc.get("horizon_days", 30)
    horizon = int(30 if raw_horizon is None else raw_horizon)
    if horizon not in lovs_next_zone.VALID_HORIZONS:
        allowed = ", ".join(str(h) for h in sorted(lovs_next_zone.VALID_HORIZONS))
        raise ValueError(
            f"horizon_days must be one of {{{allowed}}} (the model's validated "
            f"look-ahead windows); got {horizon}"
        )
    edge_weights = _edge_weights(poc.get("corridor_edge_weights"))

    visibility = lovs_visibility.nowcast(base, history=(), n_samples=1000)
    transmission = lovs_transmission.transmission_plausibility(
        base,
        n_trajectories=1000,
        priors=lovs_priors_bundibugyo.BUNDIBUGYO_PRIORS_STAGE_TWO,
    )

    # Per-zone corridor risk. The public pipeline applies one aggregate confirmed
    # count to every source zone; here each zone is run with its OWN observed
    # count, so a more-affected zone produces more corridor risk. Results are
    # merged and re-ranked by ascertainment-adjusted upper-50 risk.
    corridors: list = []
    for zone in poc["source_zones"]:
        zone_confirmed = int(zone.get("confirmed") or 0)
        zone_snapshot = replace(
            base,
            affected_zones=(str(zone["zone_id"]),),
            reported_counts={
                **base.reported_counts,
                "confirmed": _count(zone_confirmed),
            },
        )
        zone_corridors = lovs_next_zone.next_zone_risk(
            snapshot=zone_snapshot,
            visibility=visibility,
            candidate_targets=targets,
            horizon_days=horizon,
            edge_weights=edge_weights,
            n_samples=500,
        )
        corridors.extend(_localize_corridor(c, zone_confirmed) for c in zone_corridors)
    # Rank by ascertainment-adjusted upper-50 risk (desc), then source/target
    # for a stable, input-order-independent ordering.
    corridors.sort(
        key=lambda c: (
            -c.risk_visibility_adjusted.upper_50,
            c.source_geography_id,
            c.target_geography_id,
        )
    )

    return {
        "snapshot": base,
        "visibility": visibility,
        "transmission": transmission,
        "corridors": corridors,
    }


def to_json(result: dict) -> dict:
    """Serialize a run() result to a plain JSON-friendly dict."""
    base = result["snapshot"]
    vis = result["visibility"]
    tp = result["transmission"]
    return {
        "outbreak_id": base.outbreak_id,
        "as_of": base.as_of,
        "model_version": base.model_version,
        "observed": {
            "confirmed": base.reported_counts["confirmed"].primary_value,
            "suspected": base.reported_counts["suspected"].primary_value,
            "deaths": base.reported_deaths.primary_value,
            "zones": list(base.affected_zones),
        },
        "visibility": {
            "grade": vis.visibility_grade,
            "reporting_completeness_50": [
                vis.reporting_completeness.lower_50,
                vis.reporting_completeness.upper_50,
            ],
        },
        "transmission": {
            "latent_active_chains_95": [
                tp.latent_active_chains.lower_95,
                tp.latent_active_chains.upper_95,
            ],
            "generations": {
                str(k): tp.generations_before_detection.get(k, 0.0)
                for k in range(1, lovs_transmission.MAX_GENERATIONS + 1)
            },
        },
        "corridors": [
            {
                "source": c.source_geography_id,
                "target": c.target_geography_id,
                "horizon_days": c.horizon_days,
                "risk_adj_lower_50": c.risk_visibility_adjusted.lower_50,
                "risk_adj_upper_50": c.risk_visibility_adjusted.upper_50,
                "drivers": list(c.drivers),
                "caveats": list(c.caveats),
            }
            for c in result["corridors"]
        ],
    }


def print_report(result: dict) -> None:
    base = result["snapshot"]
    vis = result["visibility"]
    tp = result["transmission"]
    corridors = result["corridors"]
    confirmed = base.reported_counts["confirmed"].primary_value
    suspected = base.reported_counts["suspected"].primary_value
    comp_lo = vis.reporting_completeness.lower_50
    comp_hi = vis.reporting_completeness.upper_50

    bar = "=" * 70
    print(bar)
    print(f"LOVS local run  |  {base.outbreak_id}  |  as of {base.as_of[:10]}")
    print(bar)
    print(
        f"Your observed totals: {confirmed} confirmed, {suspected} suspected "
        f"across {len(base.affected_zones)} zone(s)"
    )
    print(f"Visibility grade: {vis.visibility_grade}")
    print(f"  reporting completeness (50%): {comp_lo * 100:.0f}% to {comp_hi * 100:.0f}%")
    if comp_lo > 0 and comp_hi > 0:
        true_lo = confirmed / comp_hi
        true_hi = confirmed / comp_lo
        print(f"  implied underlying confirmed (50%): {true_lo:.0f} to {true_hi:.0f}")
    p_three = sum(
        tp.generations_before_detection.get(k, 0.0)
        for k in range(3, lovs_transmission.MAX_GENERATIONS + 1)
    )
    lac = tp.latent_active_chains
    print(
        f"Transmission: latent active chains (95%) {lac.lower_95:.0f} to {lac.upper_95:.0f}; "
        f"P(>=3 silent generations) {p_three:.2f}"
    )
    print("")
    print("Corridor deployment ranking (highest onward-spread risk first):")
    print(f"  {'#':>2}  {'corridor':<32} {'risk 50%':>16}  drivers")
    for i, c in enumerate(corridors, 1):
        band = f"[{c.risk_visibility_adjusted.lower_50:.2f}, {c.risk_visibility_adjusted.upper_50:.2f}]"
        corridor = f"{c.source_geography_id} -> {c.target_geography_id}"
        drivers = ", ".join(c.drivers[:2]) if c.drivers else ""
        print(f"  {i:>2}  {corridor:<32} {band:>16}  {drivers}")
    print(bar)
    print(
        "This is your internal situational estimate. It is NOT a pre-committed, "
        "scored prediction (that is what the public release pipeline is for)."
    )


def _atomic_write_text(path: pathlib.Path, text: str) -> None:
    """Write text to path atomically: temp file in the same dir, then os.replace."""
    path = pathlib.Path(path)
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--input",
        required=True,
        type=pathlib.Path,
        help="Your point-of-care JSON (start from point_of_care_input.example.json).",
    )
    parser.add_argument(
        "--json-out",
        type=pathlib.Path,
        default=None,
        help="Optional path to also write the full result as JSON.",
    )
    args = parser.parse_args(argv)

    if not args.input.exists():
        sys.stderr.write(f"input not found: {args.input}\n")
        return 2
    try:
        poc = json.loads(args.input.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        sys.stderr.write(f"could not parse {args.input} as JSON: {exc}\n")
        return 2
    try:
        result = run(poc)
    except ValueError as exc:
        # ValueError covers our input checks and lovs CorridorRiskError.
        sys.stderr.write(f"input error: {exc}\n")
        return 2
    print_report(result)
    if args.json_out:
        _atomic_write_text(args.json_out, json.dumps(to_json(result), indent=2) + "\n")
        print(f"wrote {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
