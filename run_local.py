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
import hashlib
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


# Minimum number of history snapshots that flips the visibility nowcast from
# single_snapshot to empirical_history. Mirrors the threshold inside
# lovs_visibility._uncertainty_drivers (history_count < 2 keeps the
# "single as-of snapshot" driver). If that threshold ever changes, update
# both call sites.
EMPIRICAL_HISTORY_MIN_SNAPSHOTS = 2

# Recognised priors-override fields. An override dict that lists none of
# these is rejected as a no-op; the audit trail must not advertise
# priors_overridden=True when no recognised field was actually overridden.
_RECOGNISED_OVERRIDE_FIELDS: frozenset[str] = frozenset(
    {
        "serial_interval_gamma",
        "r_prior_gamma",
        "incubation_gamma",
        "under_ascertainment_uniform",
        "species",
        "notes",
        "citations",
    }
)


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

    case_definition_version = poc.get("case_definition_version")
    if case_definition_version is not None:
        case_definition_version = str(case_definition_version).strip() or None

    return lovs_reconciler.OutbreakSnapshot(
        outbreak_id=poc.get("outbreak_id", "local-run"),
        as_of=_as_of(poc.get("as_of", "")),
        pathogen=poc.get("pathogen", "BDBV"),
        country_scope=tuple(poc.get("country_scope", ())),
        reported_counts={
            "confirmed": _count(total("confirmed")),
            # Local point-of-care input continues to accept a single
            # "suspected" total; we route it to the canonical cumulative key
            # so downstream consumers reading either suspected_cumulative or
            # the legacy "suspected" find the same value.
            "suspected_cumulative": _count(total("suspected")),
        },
        reported_deaths={
            # Local input historically carried a single deaths total; we
            # route it to the suspected class because point-of-care totals
            # almost always reflect clinically-classified-but-not-lab-
            # confirmed counts. A future point-of-care template can declare
            # a separate deaths_confirmed bucket.
            "suspected": _count(total("deaths")),
        },
        affected_zones=tuple(str(z["zone_id"]) for z in zones),
        sources=("point-of-care",),
        case_definition_version=case_definition_version,
        source_conflict_notes=(),
        deaths_to_confirmed_tension_flag=False,
        model_version="lovs-local-run",
    )


def build_history(
    poc: dict,
) -> tuple[lovs_reconciler.OutbreakSnapshot, ...]:
    """Build a tuple of prior OutbreakSnapshots from poc['history'] if present.

    Each history entry uses the same source_zones schema as the top-level
    snapshot, so a partner with daily/weekly snapshots can drop them in
    directly. The visibility nowcast keys off the earliest history as_of to
    replace the conservative 7-day default observation window with the actual
    cadence; with two or more snapshots the visibility module also drops the
    "single as-of snapshot in window" uncertainty driver.
    """
    history_raw = poc.get("history") or []
    if not isinstance(history_raw, list):
        raise ValueError("history must be a list of prior snapshots")
    snapshots: list[lovs_reconciler.OutbreakSnapshot] = []
    base_outbreak_id = poc.get("outbreak_id", "local-run")
    base_pathogen = poc.get("pathogen", "BDBV")
    base_country_scope = tuple(poc.get("country_scope", ()))
    base_as_of = _as_of(poc.get("as_of", ""))
    seen_as_of: set[str] = set()
    for i, entry in enumerate(history_raw):
        if not isinstance(entry, dict):
            raise ValueError(f"history[{i}] must be an object")
        zones = entry.get("source_zones") or []
        if not zones:
            raise ValueError(
                f"history[{i}] must include source_zones (same schema as top-level)"
            )
        prior_poc = {
            "outbreak_id": base_outbreak_id,
            "as_of": entry.get("as_of", ""),
            "pathogen": base_pathogen,
            "country_scope": list(base_country_scope),
            "source_zones": zones,
            "case_definition_version": entry.get(
                "case_definition_version", poc.get("case_definition_version")
            ),
        }
        prior_snapshot = build_snapshot(prior_poc)
        # History snapshots must be strictly earlier than the top-level as_of.
        # An entry at or after the base as_of would push the visibility nowcast
        # into negative-days territory, where the max(0.5, ...) clamp silently
        # collapses the completeness band; the partner would see a misleadingly
        # tight estimate with no surfaced error.
        if prior_snapshot.as_of >= base_as_of:
            raise ValueError(
                f"history[{i}].as_of={prior_snapshot.as_of!r} must be strictly "
                f"earlier than the top-level as_of={base_as_of!r}; history is "
                f"for PRIOR snapshots only"
            )
        if prior_snapshot.as_of in seen_as_of:
            raise ValueError(
                f"history[{i}].as_of={prior_snapshot.as_of!r} duplicates an "
                f"earlier history entry; each prior snapshot needs a unique as_of"
            )
        seen_as_of.add(prior_snapshot.as_of)
        snapshots.append(prior_snapshot)
    # Order is stable on as_of so the visibility module's min(history.as_of)
    # picks the genuine earliest, regardless of how the partner laid them out.
    snapshots.sort(key=lambda s: s.as_of)
    return tuple(snapshots)


def _derive_seed(
    base: lovs_reconciler.OutbreakSnapshot,
    history: tuple[lovs_reconciler.OutbreakSnapshot, ...],
) -> int:
    """Combine the base snapshot's content seed with the history fingerprint.

    Without this, two runs with the same base snapshot but different history
    tuples derive the same nowcast seed from snapshot_content_seed(base)
    alone, yet produce different completeness bands because days_since_earliest
    depends on history. That breaks the "same seed implies same draws"
    invariant the visibility module advertises. Mixing the history as_of
    fingerprint into the seed restores the invariant without touching the
    visibility module's public API.
    """
    base_seed = lovs_reconciler.snapshot_content_seed(base)
    if not history:
        return base_seed
    h = hashlib.blake2b(digest_size=8)
    h.update(str(base_seed).encode("utf-8"))
    for snapshot in history:
        h.update(b"|")
        h.update(snapshot.as_of.encode("utf-8"))
    # blake2b is deterministic and stdlib; XOR with base_seed keeps the
    # contribution of the base snapshot's content hash visible in the seed.
    return base_seed ^ int.from_bytes(h.digest(), "big")


_PRIOR_FIELD_SHAPES: dict[str, tuple[str, ...]] = {
    "serial_interval_gamma": ("alpha", "beta"),
    "r_prior_gamma": ("alpha", "beta"),
    "incubation_gamma": ("alpha", "beta"),
    "under_ascertainment_uniform": ("lo", "hi"),
}


def build_priors_override(
    poc: dict,
) -> lovs_priors_bundibugyo.TransmissionPriors | None:
    """Build a TransmissionPriors override from poc['transmission_priors_override'].

    Any field omitted falls back to the BUNDIBUGYO_PRIORS_STAGE_TWO default,
    so a partner who has measured only a serial interval can drop in only
    that one field. The validator inside TransmissionPriors will still reject
    invalid shapes (non-positive gamma parameters, lo>=hi, empty citations).
    """
    raw = poc.get("transmission_priors_override")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("transmission_priors_override must be an object")
    # An override dict with no recognised field is a no-op. Returning the
    # species default while flagging priors_overridden=True would emit a
    # misleading audit trail. Treat it as "no override" so the report and
    # the JSON method block both reflect reality.
    if not (set(raw) & _RECOGNISED_OVERRIDE_FIELDS):
        return None
    default = lovs_priors_bundibugyo.BUNDIBUGYO_PRIORS_STAGE_TWO
    fields: dict[str, object] = {
        "serial_interval_gamma": default.serial_interval_gamma,
        "r_prior_gamma": default.r_prior_gamma,
        "under_ascertainment_uniform": default.under_ascertainment_uniform,
        "incubation_gamma": default.incubation_gamma,
        "citations": default.citations,
        "species": default.species,
        "notes": default.notes,
        "version": default.version,
        "evidence_chain_ids": default.evidence_chain_ids,
    }
    for key, expected_shape in _PRIOR_FIELD_SHAPES.items():
        if key not in raw:
            continue
        value = raw[key]
        if (
            not isinstance(value, (list, tuple))
            or len(value) != 2
            or not all(isinstance(v, (int, float)) for v in value)
        ):
            raise ValueError(
                f"transmission_priors_override.{key} must be a two-number list "
                f"({expected_shape[0]}, {expected_shape[1]}); got {value!r}"
            )
        fields[key] = (float(value[0]), float(value[1]))
    if "species" in raw:
        species = str(raw["species"]).strip()
        if species:
            fields["species"] = species
    notes_override = raw.get("notes")
    if notes_override is not None:
        if isinstance(notes_override, str):
            override_notes: tuple[str, ...] = (notes_override.strip(),)
        elif isinstance(notes_override, (list, tuple)):
            override_notes = tuple(str(n).strip() for n in notes_override if str(n).strip())
        else:
            raise ValueError(
                "transmission_priors_override.notes must be a string or list of strings"
            )
        if override_notes:
            # Prepend the override note so the audit trail shows the partner's
            # rationale first, with the species default carried after for
            # comparability.
            fields["notes"] = override_notes + tuple(default.notes)
    citations_override = raw.get("citations")
    if citations_override is not None:
        if isinstance(citations_override, str):
            cit_tuple: tuple[str, ...] = (citations_override.strip(),)
        elif isinstance(citations_override, (list, tuple)):
            cit_tuple = tuple(str(c).strip() for c in citations_override if str(c).strip())
        else:
            raise ValueError(
                "transmission_priors_override.citations must be a string or list of strings"
            )
        if cit_tuple:
            fields["citations"] = cit_tuple + tuple(default.citations)
    return lovs_priors_bundibugyo.TransmissionPriors(**fields)


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
    history = build_history(poc)
    priors_override = build_priors_override(poc)
    priors = priors_override or lovs_priors_bundibugyo.BUNDIBUGYO_PRIORS_STAGE_TWO

    nowcast_seed = _derive_seed(base, history)
    visibility = lovs_visibility.nowcast(
        base, history=history, n_samples=1000, seed=nowcast_seed
    )
    transmission = lovs_transmission.transmission_plausibility(
        base,
        n_trajectories=1000,
        priors=priors,
    )
    method_basis = (
        "empirical_history"
        if len(history) >= EMPIRICAL_HISTORY_MIN_SNAPSHOTS
        else "single_snapshot"
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
        "history": history,
        "priors": priors,
        "priors_overridden": priors_override is not None,
        "method_basis": method_basis,
    }


def to_json(result: dict) -> dict:
    """Serialize a run() result to a plain JSON-friendly dict."""
    base = result["snapshot"]
    vis = result["visibility"]
    tp = result["transmission"]
    history = result.get("history") or ()
    priors = result["priors"]
    return {
        "outbreak_id": base.outbreak_id,
        "as_of": base.as_of,
        "model_version": base.model_version,
        "observed": {
            "confirmed": base.reported_counts["confirmed"].primary_value,
            "suspected_cumulative": (
                base.reported_counts["suspected_cumulative"].primary_value
                if "suspected_cumulative" in base.reported_counts
                else None
            ),
            "deaths_confirmed": (
                base.reported_deaths["confirmed"].primary_value
                if "confirmed" in base.reported_deaths
                else None
            ),
            "deaths_suspected": (
                base.reported_deaths["suspected"].primary_value
                if "suspected" in base.reported_deaths
                else None
            ),
            "zones": list(base.affected_zones),
        },
        "method": {
            "basis": result["method_basis"],
            "history_snapshot_count": len(history),
            "history_earliest_as_of": (min(s.as_of for s in history) if history else None),
            "case_definition_version": base.case_definition_version,
            "priors_overridden": result["priors_overridden"],
            "priors_species": priors.species,
            "priors_r_gamma": list(priors.r_prior_gamma),
            "priors_serial_interval_gamma": list(priors.serial_interval_gamma),
        },
        "visibility": {
            "grade": vis.visibility_grade,
            "reporting_completeness_50": [
                vis.reporting_completeness.lower_50,
                vis.reporting_completeness.upper_50,
            ],
            "uncertainty_drivers": list(vis.uncertainty_drivers),
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
    history = result.get("history") or ()
    confirmed = base.reported_counts["confirmed"].primary_value
    _suspected_rc = base.reported_counts.get("suspected") or base.reported_counts.get(
        "suspected_cumulative"
    )
    suspected = _suspected_rc.primary_value
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
    if history:
        earliest = min(s.as_of for s in history)[:10]
        print(
            f"Method basis: {result['method_basis']}  "
            f"({len(history)} prior snapshot(s) since {earliest})"
        )
    else:
        print("Method basis: single_snapshot  (no history provided)")
    if base.case_definition_version:
        print(f"Case definition: {base.case_definition_version}")
    if result["priors_overridden"]:
        priors = result["priors"]
        a, b = priors.r_prior_gamma
        sa, sb = priors.serial_interval_gamma
        print(
            f"Transmission priors: OVERRIDE ({priors.species}); "
            f"R gamma({a:.2f}, {b:.2f}), serial interval gamma({sa:.2f}, {sb:.2f})"
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
