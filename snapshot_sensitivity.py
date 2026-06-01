#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Sensitivity of the 2026-05-20 BDBV corridor ranking to the new public leverages.

Runs the run_local engine under three input scenarios and reports how the
corridor ranking moves when the mobility and geography leverages from
data/external_sources/bdbv-2026.observed.json are applied:

  S0  baseline       released geography, uniform edge weights
  S1  +mobility+geo  add Arua/Nebbi targets, apply WHO/IOM PoE-throughput edge
                     weights (source-province-aware; falls back to IOM-DTM
                     territory shares when the restricted PoE file is absent)
  S2  +Mahagi src    add Mahagi as a source zone (EQUAL-BURDEN counterfactual:
                     Mahagi assumed as affected as a sibling zone) to show where
                     the structurally-omitted Mahagi -> Arua corridor would rank

This is a NON-DESTRUCTIVE sensitivity check. It does not modify the pinned
2026-05-20 snapshot or the calibration ledger. Absolute risk values differ from
the released pipeline because run_local uses true per-zone counts (summed for
visibility) whereas the released pipeline applied the aggregate confirmed count
to every source zone (an upper-envelope assumption). The DELTAS across
S0 -> S1 -> S2 isolate the effect of the new data.

  python3 snapshot_sensitivity.py
"""
from __future__ import annotations

import json
import os
import pathlib

import run_local
from lovs import lovs_poe_corridor as poe

REPO_ROOT = pathlib.Path(__file__).parent.resolve()
OBSERVED = REPO_ROOT / "data" / "external_sources" / "bdbv-2026.observed.json"
SNAPSHOT = REPO_ROOT / "data" / "live-bdbv-2026-output.json"
OUT = REPO_ROOT / "data" / "external_sources" / "bdbv-2026-05-20.sensitivity.json"
POE_COUNTS_PATH = REPO_ROOT / "data" / "bundibugyo-2026" / "poe_traveler_counts.restricted.json"

# All current source health zones sit in Ituri Province. PoE throughput is only
# credited to a corridor when the crossing's DRC side matches the source's
# province, so an Ituri outbreak is not credited with the Nord-Kivu-side
# Mpondwe/Busunga volume that physically feeds Kasese.
ITURI_TERRITORIES = frozenset({"djugu", "irumu", "bunia", "mahagi", "mambasa", "aru"})
# Targets reached by a direct DRC-Uganda border PoE (throughput lever applies).
# kasese-uga maps to the lovs_poe_corridor "kasese" key.
DIRECT_BORDER_TARGET_KEY = {
    "arua-uga": "arua-uga",
    "bundibugyo-uga": "bundibugyo-uga",
    "kasese-uga": "kasese",
}
# Targets with no direct border PoE: Kampala (inland capital; onward/air) and
# Beni (intra-DRC). The crossing-throughput lever does not apply; they stay at
# the source-movement baseline.
INDIRECT_TARGETS = frozenset({"kampala-uga", "beni-cod"})
# Nebbi has no direct PoE; it sits ~80km inland on the Pakwach-Nebbi-Arua road,
# downstream of the Arua / West Nile axis. Labelled assumption: a quarter of the
# Arua-axis throughput reaches the Nebbi node.
NEBBI_DOWNSTREAM_FRACTION = 0.25
# Edge-weight pull scale: +1.0 to the weight per 1000 daily border crossers into
# the target district.
PULL_SCALE = 1000.0

# Released 2026-05-20 aggregate (confirmed 53 / suspected 653 / deaths 144) split
# evenly across the three affected zones so run_local's summed aggregate matches
# the pinned snapshot. Per-zone confirmed is not public; the split is even.
BASE_ZONES = [
    {"zone_id": "rwampara", "confirmed": 18, "suspected": 218, "deaths": 48},
    {"zone_id": "mongbwalu", "confirmed": 18, "suspected": 218, "deaths": 48},
    {"zone_id": "bunia", "confirmed": 17, "suspected": 217, "deaths": 48},
]
BASE_TARGETS = ["bundibugyo-uga", "beni-cod", "kasese-uga", "kampala-uga"]
NEW_TARGETS = ["arua-uga", "nebbi-uga"]
# Mahagi per-zone confirmed is NOT public. This is an explicit EQUAL-BURDEN
# counterfactual (Mahagi assumed as affected as a sibling zone) so the ranking of
# the omitted Mahagi -> Arua corridor reflects geometry + mobility, not an
# arbitrarily small assumed count. It is a labelled assumption, not a measurement.
MAHAGI_EQUAL_BURDEN = {"zone_id": "mahagi", "confirmed": 18, "suspected": 218, "deaths": 48}
HORIZON = 30


def _province_of(territory: str | None) -> str:
    return "Ituri" if (territory or "").lower() in ITURI_TERRITORIES else "Nord Kivu"


def _target_throughput(province: str, target: str, poe_path: str) -> float:
    """Source-province-compatible daily border throughput into ``target``.

    Sums mean_daily_passengers over the PoEs mapped to the target (via
    lovs_poe_corridor) whose DRC side matches the source's province. Kampala and
    Beni have no direct border PoE (return 0); Nebbi is a labelled downstream
    fraction of the Arua axis.
    """
    if target in INDIRECT_TARGETS:
        return 0.0
    if target == "nebbi-uga":
        return NEBBI_DOWNSTREAM_FRACTION * _target_throughput(province, "arua-uga", poe_path)
    corridor_key = DIRECT_BORDER_TARGET_KEY.get(target)
    if corridor_key is None:
        return 0.0
    try:
        entries = poe.poe_entries_for_corridor(corridor_key, poe_path)
    except (KeyError, FileNotFoundError):
        return 0.0
    return float(
        sum(
            e.get("mean_daily_passengers", 0)
            for e in entries
            if e.get("drc_province") == province
        )
    )


def _edge_weights_from_shares(observed: dict, sources: list[str]) -> dict:
    """Fallback recipe (no PoE file): source movement share x flagged-crossing factor."""
    mob = observed["mobility"]
    shares = mob["admin2_movement_share"]
    zone_terr = mob["zone_to_territory"]
    crossings = {c["corridor"]: c for c in mob.get("border_crossings", [])}
    targets = BASE_TARGETS + NEW_TARGETS
    weights: dict[str, float] = {}
    for src in sources:
        territory = zone_terr.get(src)
        share = shares.get(territory, 0.0) if territory else 0.0
        move_factor = 1.0 + float(share)
        for tgt in targets:
            key = f"{src}->{tgt}"
            high = key in crossings and crossings[key].get("intensity") == "high"
            weight = move_factor * (1.3 if high else 1.0)
            if abs(weight - 1.0) > 1e-9:
                weights[key] = round(weight, 3)
    return weights


def edge_weights_from_observed(
    observed: dict, sources: list[str], poe_path: str | None = None
) -> dict:
    """Build {'src->tgt': weight} edge weights for the corridor model.

    When the restricted PoE traveler-count file is present, the weight combines
    source onward-movement propensity (IOM DTM territory share) with the real,
    source-province-compatible PoE passenger throughput into each target:

        edge_weight(s -> t) = (1 + movement_share[territory(s)])
                              * (1 + compatible_daily_throughput(province(s), t) / 1000)

    When the PoE file is absent (e.g. the public repo / CI), it falls back to the
    documented share + flagged-crossing recipe in observed.json. Both recipes are
    transparent first-pass heuristics, NOT fitted, and feed run_local /
    sensitivity only (the provenance-strict public snapshot holds mobility out).
    """
    path = str(poe_path) if poe_path is not None else str(POE_COUNTS_PATH)
    if not os.path.exists(path):
        return _edge_weights_from_shares(observed, sources)

    mob = observed.get("mobility", {})
    shares = mob.get("admin2_movement_share", {})
    zone_terr = mob.get("zone_to_territory", {})
    targets = BASE_TARGETS + NEW_TARGETS
    weights: dict[str, float] = {}
    for src in sources:
        territory = zone_terr.get(src)
        share = float(shares.get(territory, 0.0)) if territory else 0.0
        province = _province_of(territory)
        move_factor = 1.0 + share
        for tgt in targets:
            pull = _target_throughput(province, tgt, path) / PULL_SCALE
            weight = move_factor * (1.0 + pull)
            if abs(weight - 1.0) > 1e-9:
                weights[f"{src}->{tgt}"] = round(weight, 3)
    return weights


def run_scenario(zones: list[dict], targets: list[str], edge_weights: dict) -> dict:
    poc = {
        "outbreak_id": "bdbv-uga-cod-2026-sensitivity",
        "as_of": "2026-05-20",
        "source_zones": zones,
        "candidate_target_zones": targets,
        "corridor_edge_weights": edge_weights,
        "horizon_days": HORIZON,
    }
    return run_local.to_json(run_local.run(poc))


def _corridor_key(c: dict) -> str:
    return f"{c['source']}->{c['target']}"


def _implied_confirmed(out: dict) -> list[int]:
    lo, hi = out["visibility"]["reporting_completeness_50"]
    confirmed = out["observed"]["confirmed"]
    if lo > 0 and hi > 0:
        return [round(confirmed / hi), round(confirmed / lo)]
    return [confirmed, confirmed]


def _print_scenario(label: str, desc: str, out: dict, baseline_keys: set[str]) -> None:
    print(f"\n{label}  {desc}")
    grade = out["visibility"]["grade"]
    imp = _implied_confirmed(out)
    print(
        f"  visibility: {grade}; observed confirmed {out['observed']['confirmed']} "
        f"-> implied underlying (50%) {imp[0]} to {imp[1]}; zones={len(out['observed']['zones'])}"
    )
    print(f"  {'#':>2}  {'corridor':<26} {'risk adj 50%':>16}  note")
    for i, c in enumerate(out["corridors"][:8], 1):
        key = _corridor_key(c)
        band = f"[{c['risk_adj_lower_50']:.2f}, {c['risk_adj_upper_50']:.2f}]"
        note = "" if key in baseline_keys else "NEW corridor"
        print(f"  {i:>2}  {key:<26} {band:>16}  {note}")


def main() -> int:
    observed = json.loads(OBSERVED.read_text(encoding="utf-8"))
    released = json.loads(SNAPSHOT.read_text(encoding="utf-8"))

    # S0 baseline
    s0 = run_scenario(BASE_ZONES, BASE_TARGETS, {})
    base_keys = {_corridor_key(c) for c in s0["corridors"]}

    # S1 +mobility +new targets
    s1_sources = [z["zone_id"] for z in BASE_ZONES]
    s1 = run_scenario(
        BASE_ZONES, BASE_TARGETS + NEW_TARGETS, edge_weights_from_observed(observed, s1_sources)
    )

    # S2 +Mahagi source (equal-burden counterfactual)
    s2_zones = BASE_ZONES + [MAHAGI_EQUAL_BURDEN]
    s2_sources = [z["zone_id"] for z in s2_zones]
    s2 = run_scenario(
        s2_zones, BASE_TARGETS + NEW_TARGETS, edge_weights_from_observed(observed, s2_sources)
    )

    bar = "=" * 74
    print(bar)
    print("BDBV 2026-05-20 corridor sensitivity to new public leverages (run_local engine)")
    print(bar)
    released_top = sorted(
        released["corridors"], key=lambda c: c["risk_adj_upper_50"], reverse=True
    )[:4]
    print("Released pinned snapshot, top corridors (reference, full pipeline engine):")
    for i, c in enumerate(released_top, 1):
        band = f"[{c['risk_adj_lower_50']:.2f}, {c['risk_adj_upper_50']:.2f}]"
        print(f"  {i:>2}  {c['source']}->{c['target']:<18} {band:>16}")

    _print_scenario("S0", "baseline (released geography, uniform weights)", s0, base_keys)
    _print_scenario("S1", "+PoE throughput (WHO/IOM) +Arua/Nebbi targets", s1, base_keys)
    _print_scenario("S2", "+Mahagi source (equal-burden counterfactual)", s2, base_keys)

    mahagi_arua = next(
        (c for c in s2["corridors"] if _corridor_key(c) == "mahagi->arua-uga"), None
    )
    summary = {
        "s0_top": _corridor_key(s0["corridors"][0]),
        "s1_top": _corridor_key(s1["corridors"][0]),
        "s2_top": _corridor_key(s2["corridors"][0]),
        "new_corridors_s1": [
            _corridor_key(c) for c in s1["corridors"] if _corridor_key(c) not in base_keys
        ],
        "mahagi_arua_band_s2": (
            [mahagi_arua["risk_adj_lower_50"], mahagi_arua["risk_adj_upper_50"]]
            if mahagi_arua
            else None
        ),
    }
    print("\n" + bar)
    print("What the new data changes:")
    print(f"  - S0 top corridor: {summary['s0_top']} (near-uniform; uniform weights)")
    print(f"  - S1 top corridor: {summary['s1_top']} (PoE throughput + Djugu share lift the Arua axis)")
    print(f"  - S1 surfaces {len(summary['new_corridors_s1'])} previously-absent cross-border corridors")
    if mahagi_arua:
        rank = next(
            (i for i, c in enumerate(s2["corridors"], 1) if _corridor_key(c) == "mahagi->arua-uga"),
            None,
        )
        print(
            f"  - S2: the omitted Mahagi -> Arua corridor lands at rank {rank} of "
            f"{len(s2['corridors'])}, band [{mahagi_arua['risk_adj_lower_50']:.2f}, "
            f"{mahagi_arua['risk_adj_upper_50']:.2f}] under an equal-burden assumption"
        )
    print("  - Latency lever (assay mismatch) lowers reporting completeness -> raises")
    print("    visibility-adjusted risk; staged for the refresh visibility prior (not run here).")
    print(bar)

    payload = {
        "_meta": {
            "purpose": "Non-destructive sensitivity of the 2026-05-20 corridor ranking to the new public leverages. Does NOT modify the pinned snapshot or ledger.",
            "engine": "run_local (per-zone counts summed for visibility)",
            "generated_from": "data/external_sources/bdbv-2026.observed.json",
            "licensing": "CC-BY-4.0 (schema + annotations).",
        },
        "released_reference_top": [
            {"corridor": f"{c['source']}->{c['target']}", "risk_adj_50": [c["risk_adj_lower_50"], c["risk_adj_upper_50"]]}
            for c in released_top
        ],
        "scenarios": {"S0_baseline": s0, "S1_mobility_geo": s1, "S2_mahagi_source": s2},
        "summary": summary,
    }
    run_local._atomic_write_text(OUT, json.dumps(payload, indent=2) + "\n")
    print(f"wrote {OUT.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
