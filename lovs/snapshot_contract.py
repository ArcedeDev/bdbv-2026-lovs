"""Canonical snapshot contract and release gates.

The contract is the narrow, generated truth every public surface must agree
with: headline counts, zone-attributed model inputs, unallocated counts, and
current corridor-watchlist ranges.  It is derived from the pinned snapshot JSON;
operators should not hand-edit it.
"""
from __future__ import annotations

import argparse
import csv
import json
import pathlib
import sys
from typing import Any


SCHEMA_VERSION = 1

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_SNAPSHOT_PATH = REPO_ROOT / "data" / "live-bdbv-2026-output.json"
DEFAULT_CONTRACT_PATH = REPO_ROOT / "data" / "snapshot_contract.json"
DEFAULT_DATASET_DIR = REPO_ROOT / "deliverables" / "public-health-dataset"


class SnapshotContractError(ValueError):
    """Raised when a snapshot or public artifact violates the contract."""


def load_json(path: pathlib.Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SnapshotContractError(f"{path}: invalid JSON: {exc}") from exc


def build_contract(snapshot: dict[str, Any]) -> dict[str, Any]:
    reported = snapshot.get("reported_counts") or {}
    reported_contract = {
        metric: {
            "primary": _required_int(row, "primary", f"reported_counts.{metric}"),
            "min": _optional_int(row, "min"),
            "max": _optional_int(row, "max"),
            "primary_source_id": str(row.get("primary_source_id", "")),
            "conflicting_source_ids": list(row.get("conflicting_source_ids") or []),
        }
        for metric, row in reported.items()
        if isinstance(row, dict)
    }
    if "confirmed" not in reported_contract:
        raise SnapshotContractError("reported_counts.confirmed is required")

    zone_counts = snapshot.get("zone_attributed_counts") or {}
    if not isinstance(zone_counts, dict):
        raise SnapshotContractError("zone_attributed_counts must be an object")
    zone_rows: dict[str, dict[str, Any]] = {}
    for zone_id, row in sorted(zone_counts.items()):
        if not isinstance(row, dict):
            raise SnapshotContractError(f"zone_attributed_counts.{zone_id} must be an object")
        zone_rows[zone_id] = {
            "confirmed": _required_int(row, "confirmed", f"zone_attributed_counts.{zone_id}"),
            "source_id": _required_str(row, "source_id", f"zone_attributed_counts.{zone_id}"),
            "source_published_at": _required_str(
                row, "source_published_at", f"zone_attributed_counts.{zone_id}"
            ),
            "province": row.get("province", ""),
            "original_zone_id": row.get("original_zone_id", zone_id),
        }

    confirmed_headline = reported_contract["confirmed"]["primary"]
    zone_confirmed = sum(row["confirmed"] for row in zone_rows.values())
    unallocated = confirmed_headline - zone_confirmed

    corridors = snapshot.get("corridors") or []
    if not isinstance(corridors, list) or not corridors:
        raise SnapshotContractError("corridors must be a non-empty list")
    lower_bounds = [_required_number(c, "risk_adj_lower_50", f"corridors[{idx}]") for idx, c in enumerate(corridors)]
    upper_bounds = [_required_number(c, "risk_adj_upper_50", f"corridors[{idx}]") for idx, c in enumerate(corridors)]
    corridor_sources = sorted({str(c.get("source", "")) for c in corridors})
    corridor_targets = sorted({str(c.get("target", "")) for c in corridors})

    zone_source_ids = sorted({row["source_id"] for row in zone_rows.values()})
    source_zone_label = _source_zone_label(zone_source_ids)
    contract = {
        "schema_version": SCHEMA_VERSION,
        "as_of": str(snapshot.get("as_of", ""))[:10],
        "outbreak_id": snapshot.get("outbreak_id"),
        "reported_counts": reported_contract,
        "confirmed_case_partition": {
            "headline_confirmed_total": confirmed_headline,
            "zone_attributed_confirmed_total": zone_confirmed,
            "unallocated_confirmed_total": unallocated,
            "zone_attribution_basis": "official per-health-zone source table"
            if zone_rows
            else "no official per-zone table in snapshot",
            "zone_attribution_source_ids": zone_source_ids,
        },
        "zone_attributed_counts": zone_rows,
        "corridor_watchlist": {
            "corridor_count": len(corridors),
            "source_zone_count": len(corridor_sources),
            "target_zone_count": len(corridor_targets),
            "source_zones": corridor_sources,
            "target_zones": corridor_targets,
            "adjusted_50_lower_range_pct": [_pct(min(lower_bounds)), _pct(max(lower_bounds))],
            "adjusted_50_upper_range_pct": [_pct(min(upper_bounds)), _pct(max(upper_bounds))],
            "top_corridor": {
                "source": str(corridors[0].get("source", "")),
                "target": str(corridors[0].get("target", "")),
                "adjusted_50_lower_pct": _pct(float(corridors[0]["risk_adj_lower_50"])),
                "adjusted_50_upper_pct": _pct(float(corridors[0]["risk_adj_upper_50"])),
            },
        },
        "method_status": {
            "corridor_interpretation": "descriptive_watchlist_not_forecast",
            "source_load_policy": (
                "use newest officially zone-attributed per-health-zone table; "
                "treat headline-vs-zone-table differences as source-attribution lag; "
                "do not scale or smear headline aggregate counts across source zones"
            ),
            "calibration_policy": (
                "active calibration points are immutable pre-commitments and are "
                "not re-derived from later current-watchlist rankings"
            ),
            "known_limitations": [
                "current-outbreak corridor constants are transparent engineering heuristics, not fitted BDBV estimates",
                "current-outbreak corridor intervals are not deployment recommendations",
            ],
        },
        "visibility_method": _visibility_method_contract(snapshot),
        "narrative_required_fragments": {
            "headline_zone_unallocated": narrative_required_fragments_from_values(
                confirmed_headline=confirmed_headline,
                zone_confirmed=zone_confirmed,
                unallocated=unallocated,
                source_zone_count=len(zone_rows),
                source_zone_label=source_zone_label,
                corridor_count=len(corridors),
                lower_range_pct=(_pct(min(lower_bounds)), _pct(max(lower_bounds))),
                upper_range_pct=(_pct(min(upper_bounds)), _pct(max(upper_bounds))),
            )
        },
    }
    validate_contract(contract)
    return contract


def validate_contract(contract: dict[str, Any]) -> None:
    if contract.get("schema_version") != SCHEMA_VERSION:
        raise SnapshotContractError(
            f"schema_version must be {SCHEMA_VERSION}, got {contract.get('schema_version')!r}"
        )
    partition = contract.get("confirmed_case_partition") or {}
    headline = _required_int(partition, "headline_confirmed_total", "confirmed_case_partition")
    zone_total = _required_int(partition, "zone_attributed_confirmed_total", "confirmed_case_partition")
    unallocated = _required_int(partition, "unallocated_confirmed_total", "confirmed_case_partition")
    if headline < zone_total:
        raise SnapshotContractError(
            f"zone-attributed confirmed total {zone_total} exceeds headline confirmed {headline}"
        )
    if headline - zone_total != unallocated:
        raise SnapshotContractError(
            "confirmed partition mismatch: headline - zone_attributed != unallocated"
        )

    corridors = contract.get("corridor_watchlist") or {}
    corridor_count = _required_int(corridors, "corridor_count", "corridor_watchlist")
    source_count = _required_int(corridors, "source_zone_count", "corridor_watchlist")
    target_count = _required_int(corridors, "target_zone_count", "corridor_watchlist")
    if source_count and target_count:
        # A zone that appears in BOTH the source and target lists has its self-edge
        # (e.g. goma-cod -> goma-cod) excluded from the corridor set, because a
        # spillover risk from a zone to itself is not a meaningful watch corridor.
        # The expected corridor count is therefore sources * targets minus the
        # intersection count. Added 2026-05-26 when goma-cod was graduated to a
        # pinned target while remaining a confirmed source zone.
        source_zones = set(corridors.get("source_zones") or [])
        target_zones = set(corridors.get("target_zones") or [])
        self_edge_count = len(source_zones & target_zones)
        expected = source_count * target_count - self_edge_count
        if corridor_count != expected:
            raise SnapshotContractError(
                f"corridor count {corridor_count} does not equal "
                f"source zones {source_count} * target zones {target_count} "
                f"minus {self_edge_count} self-edge(s) for zones in both lists"
            )
    _range_pair(corridors, "adjusted_50_lower_range_pct")
    _range_pair(corridors, "adjusted_50_upper_range_pct")
    method_status = contract.get("method_status") or {}
    if method_status.get("corridor_interpretation") != "descriptive_watchlist_not_forecast":
        raise SnapshotContractError(
            "method_status.corridor_interpretation must be descriptive_watchlist_not_forecast"
        )
    source_load_policy = str(method_status.get("source_load_policy", "")).lower()
    for required in (
        "officially zone-attributed",
        "source-attribution lag",
        "do not scale",
        "headline aggregate",
    ):
        if required not in source_load_policy:
            raise SnapshotContractError(
                "method_status.source_load_policy does not state the source-load guardrail"
            )
    visibility_method = contract.get("visibility_method") or {}
    history_count = _required_int(visibility_method, "history_snapshot_count", "visibility_method")
    method_basis = str(visibility_method.get("method_basis", "")).lower()
    method_caveat = str(visibility_method.get("method_caveat", "")).lower()
    if history_count == 0:
        for required in ("single", "prior", "proxy"):
            if required not in method_basis and required not in method_caveat:
                raise SnapshotContractError(
                    "visibility_method must disclose single-snapshot prior/proxy basis"
                )
    if "bdbv_specific" in method_basis:
        delay_prior = visibility_method.get("delay_prior") or {}
        delay_label = str(delay_prior.get("label", "")).lower()
        delay_evidence = str(delay_prior.get("evidence_chain_id", "")).lower()
        delay_gamma = delay_prior.get("gamma_shape_rate") or []
        for required in ("rosello", "bdbv", "onset-to-notification"):
            if required not in delay_label:
                raise SnapshotContractError(
                    "visibility_method.delay_prior must name the BDBV Rosello onset-to-notification prior"
                )
        if delay_evidence != "ec:lovs:grepi:reporting-delay-update:2026-05-23":
            raise SnapshotContractError(
                "visibility_method.delay_prior must carry the grEPI/Rosello evidence-chain id"
            )
        if len(delay_gamma) != 2 or abs(delay_gamma[0] - 1.1345) > 1e-6 or abs(delay_gamma[1] - 0.1285) > 1e-6:
            raise SnapshotContractError(
                "visibility_method.delay_prior gamma must match the Rosello BDBV shape-rate prior"
            )
        if "not a fitted 2026" not in method_caveat:
            raise SnapshotContractError(
                "visibility_method must caveat the Rosello prior as historical, not fitted to 2026"
            )
        sensitivity_text = " ".join(
            str(item.get("label", ""))
            for item in visibility_method.get("sensitivity_delay_priors") or []
            if isinstance(item, dict)
        ).lower()
        if "camacho" not in sensitivity_text or "sensitivity" not in sensitivity_text:
            raise SnapshotContractError(
                "visibility_method must retain Camacho as a named sensitivity comparator"
            )


def validate_snapshot(snapshot: dict[str, Any], contract: dict[str, Any] | None = None) -> None:
    generated = build_contract(snapshot)
    if contract is not None and contract != generated:
        raise SnapshotContractError("data/snapshot_contract.json is stale relative to live snapshot")
    validate_contract(generated)

    zone_counts = generated["zone_attributed_counts"]
    corridor_sources = set(generated["corridor_watchlist"]["source_zones"])
    zone_ids = set(zone_counts)
    if zone_ids and corridor_sources != zone_ids:
        raise SnapshotContractError(
            "corridor source zones must equal zone_attributed_counts: "
            f"missing={sorted(zone_ids - corridor_sources)}, extra={sorted(corridor_sources - zone_ids)}"
        )
    affected = set(snapshot.get("affected_zones") or [])
    if zone_ids and affected != zone_ids:
        raise SnapshotContractError(
            "affected_zones must equal zone_attributed_counts when a per-zone table is present"
        )

    targets = set(generated["corridor_watchlist"]["target_zones"])
    by_source: dict[str, set[str]] = {zone_id: set() for zone_id in zone_ids}
    for idx, corridor in enumerate(snapshot.get("corridors") or []):
        source = str(corridor.get("source", ""))
        target = str(corridor.get("target", ""))
        if source in by_source:
            by_source[source].add(target)
            expected = f"zone-attributed confirmed count {zone_counts[source]['confirmed']}"
            drivers = " ".join(str(d) for d in corridor.get("drivers") or [])
            if expected not in drivers:
                raise SnapshotContractError(
                    f"corridors[{idx}] {source}->{target} lacks source-load driver {expected!r}"
                )
            if "headline confirmed" in drivers.lower():
                raise SnapshotContractError(
                    f"corridors[{idx}] {source}->{target} appears to use headline aggregate"
                )
    for source, seen_targets in sorted(by_source.items()):
        expected_targets = targets - {source} if source in targets else targets
        if seen_targets != expected_targets:
            raise SnapshotContractError(
                f"source zone {source} has target set {sorted(seen_targets)}, expected {sorted(expected_targets)}"
            )


def _visibility_method_contract(snapshot: dict[str, Any]) -> dict[str, Any]:
    visibility = snapshot.get("visibility") or {}
    if not isinstance(visibility, dict):
        raise SnapshotContractError("visibility must be an object")
    delay_prior = visibility.get("delay_prior") or {}
    sensitivity_priors = visibility.get("sensitivity_delay_priors") or []
    return {
        "history_snapshot_count": _optional_int(visibility, "history_snapshot_count") or 0,
        "method_basis": str(visibility.get("method_basis", "")),
        "method_caveat": str(visibility.get("method_caveat", "")),
        "delay_prior": {
            "label": str(delay_prior.get("label", "")),
            "gamma_shape_rate": list(delay_prior.get("gamma_shape_rate") or []),
            "evidence_chain_id": str(delay_prior.get("evidence_chain_id", "")),
        },
        "sensitivity_delay_priors": [
            {
                "label": str(item.get("label", "")),
                "gamma_shape_rate": list(item.get("gamma_shape_rate") or []),
                "evidence_chain_id": str(item.get("evidence_chain_id", "")),
            }
            for item in sensitivity_priors
            if isinstance(item, dict)
        ],
    }


def validate_narrative(text: str, contract: dict[str, Any], label: str = "narrative") -> None:
    required = contract["narrative_required_fragments"]["headline_zone_unallocated"]
    missing = [fragment for fragment in required if fragment.lower() not in text.lower()]
    if missing:
        raise SnapshotContractError(f"{label} is stale or incomplete; missing {missing}")

    upper_max = contract["corridor_watchlist"]["adjusted_50_upper_range_pct"][1]
    if upper_max < 60.0:
        stale_needles = ("69.5%", "69.2%", "68.4%", "67.6%", "65.2%", "64.7% to 69.5%")
        present = [needle for needle in stale_needles if needle in text]
        if present:
            raise SnapshotContractError(f"{label} contains stale high-corridor values: {present}")
    disallowed_claims = (
        "corridor deployment ranking",
        "deployment ranking",
        "predicts where the outbreak will spread",
    )
    lower_text = text.lower()
    for claim in disallowed_claims:
        if claim in lower_text and "not " + claim not in lower_text:
            raise SnapshotContractError(
                f"{label} contains overclaiming corridor language: {claim!r}"
            )


def validate_visibility_prior_attribution(
    text: str, contract: dict[str, Any], label: str = "narrative"
) -> None:
    """Guard the reporting-delay prior attribution in human-facing prose.

    When the snapshot runs a BDBV-specific default (Rosello), any narrative that
    discusses the reporting delay must name that default and must not present a
    sensitivity comparator (Camacho) or a superseded historical distribution as
    the default.  This is the class that the numeric narrative gate misses: the
    completeness *number* is correct but its *attribution* is stale.
    """
    visibility = contract.get("visibility_method") or {}
    if "bdbv_specific" not in str(visibility.get("method_basis", "")).lower():
        return

    lower_text = text.lower()
    delay_terms = (
        "onset-to-notification",
        "reporting delay",
        "reporting-delay",
        "reporting completeness",
        "reporting-completeness",
        "delay distribution",
    )
    if not any(term in lower_text for term in delay_terms):
        return

    default_label = str((visibility.get("delay_prior") or {}).get("label", ""))
    default_name = default_label.split()[0] if default_label else ""
    if default_name and default_name.lower() not in lower_text:
        raise SnapshotContractError(
            f"{label} discusses the reporting delay but does not name the current "
            f"default prior ({default_name})"
        )

    for prior in visibility.get("sensitivity_delay_priors") or []:
        sens_label = str(prior.get("label", "")) if isinstance(prior, dict) else ""
        sens_name = sens_label.split()[0] if sens_label else ""
        if sens_name and f"delay ({sens_name.lower()}" in lower_text:
            raise SnapshotContractError(
                f"{label} frames the sensitivity comparator {sens_name} as the "
                f"reporting-delay default"
            )

    stale_attributions = (
        "assumed 2014 west-africa delay",
        "2014 west-africa delay distribution",
        "delay distribution drawn from 2014 west africa",
    )
    present = [phrase for phrase in stale_attributions if phrase in lower_text]
    if present:
        raise SnapshotContractError(
            f"{label} carries stale reporting-delay attribution: {present}"
        )


def validate_text_artifacts(contract: dict[str, Any], repo_root: pathlib.Path = REPO_ROOT) -> None:
    """Gate the primary human-facing narrative surfaces.

    This is intentionally narrower than a full editorial pass.  It catches the
    dangerous contradiction class: public prose omitting the headline-vs-zone
    count partition, carrying stale corridor ranges, or attributing the
    reporting-delay prior to a superseded default.
    """
    paths = (
        repo_root / "README.md",
        repo_root / "NUMBERS_AUDIT.md",
        repo_root / "brief" / "brief.html",
    )
    for path in paths:
        if path.exists():
            content = path.read_text(encoding="utf-8", errors="ignore")
            validate_narrative(content, contract, str(path))
            validate_visibility_prior_attribution(content, contract, str(path))


def validate_dataset_exports(
    contract: dict[str, Any],
    dataset_dir: pathlib.Path = DEFAULT_DATASET_DIR,
) -> None:
    reported_rows = _read_csv(dataset_dir / "reported_counts.csv")
    snapshot_rows = {
        row["row_id"].rsplit(":", 1)[-1]: row
        for row in reported_rows
        if row.get("row_id", "").startswith("snapshot:reported_counts:")
    }
    for metric, expected in contract["reported_counts"].items():
        row = snapshot_rows.get(metric)
        if not row:
            raise SnapshotContractError(f"reported_counts.csv lacks snapshot row for {metric}")
        if int(float(row["value"])) != int(expected["primary"]):
            raise SnapshotContractError(
                f"reported_counts.csv {metric}={row['value']} but contract has {expected['primary']}"
            )
        if row.get("source_id") != expected.get("primary_source_id"):
            raise SnapshotContractError(
                f"reported_counts.csv {metric} source_id={row.get('source_id')!r} "
                f"but contract has {expected.get('primary_source_id')!r}"
            )

    for row in reported_rows:
        if row.get("row_type") != "source_extracted_metric":
            continue
        row_id = row.get("row_id", "")
        metric = row.get("metric", "")
        if ":deaths" in row_id and metric != "deaths":
            raise SnapshotContractError(
                f"{row_id} is a death source metric but exported as {metric!r}"
            )

    corridor_rows = _read_csv(dataset_dir / "corridors.csv")
    watch = contract["corridor_watchlist"]
    if len(corridor_rows) != watch["corridor_count"]:
        raise SnapshotContractError(
            f"corridors.csv has {len(corridor_rows)} rows but contract has {watch['corridor_count']}"
        )
    lower = [float(row["risk_adj_lower_50"]) * 100 for row in corridor_rows]
    upper = [float(row["risk_adj_upper_50"]) * 100 for row in corridor_rows]
    if [_pct(min(lower) / 100), _pct(max(lower) / 100)] != watch["adjusted_50_lower_range_pct"]:
        raise SnapshotContractError("corridors.csv lower 50% range disagrees with contract")
    if [_pct(min(upper) / 100), _pct(max(upper) / 100)] != watch["adjusted_50_upper_range_pct"]:
        raise SnapshotContractError("corridors.csv upper 50% range disagrees with contract")
    for row in corridor_rows:
        source = row.get("source", "")
        zone = contract["zone_attributed_counts"].get(source)
        if zone:
            expected_driver = f"zone-attributed confirmed count {zone['confirmed']}"
            if expected_driver not in row.get("drivers", ""):
                raise SnapshotContractError(
                    f"corridors.csv {source}->{row.get('target')} lacks {expected_driver!r}"
                )
        note = row.get("correction_note", "").lower()
        if "not a forecast" not in note or "not a forecast or response recommendation" not in note:
            raise SnapshotContractError(
                f"corridors.csv {source}->{row.get('target')} does not disclose watchlist limits"
            )

    claim_rows = _read_csv(dataset_dir / "public_claim_audit.csv")
    claim_by_id = {row.get("public_claim_id"): row for row in claim_rows}
    zone_claim = claim_by_id.get("BDBV-CLAIM-018")
    if not zone_claim:
        raise SnapshotContractError("public_claim_audit.csv lacks BDBV-CLAIM-018")
    zone_claim_text = " ".join(
        zone_claim.get(key, "")
        for key in ("claim", "value", "public_action", "public_note")
    ).lower()
    zone_partition = contract["confirmed_case_partition"]
    corridor_watchlist = contract["corridor_watchlist"]
    required_terms = {
        str(zone_partition["headline_confirmed_total"]),
        str(zone_partition["zone_attributed_confirmed_total"]),
        str(zone_partition["unallocated_confirmed_total"]),
        "unallocated",
        "not the current headline confirmed aggregate",
        str(corridor_watchlist["corridor_count"]),
        "corridor",
    }
    for required_term in required_terms:
        if required_term not in zone_claim_text:
            raise SnapshotContractError(
                f"BDBV-CLAIM-018 does not preserve source-load partition term {required_term!r}"
            )

    gap_rows = _read_csv(dataset_dir / "corrections_gaps.csv")
    gaps_by_id = {row.get("gap_id"): row for row in gap_rows}
    exponent_gap = gaps_by_id.get("BDBV-CLAIM-005")
    if not exponent_gap:
        raise SnapshotContractError("corrections_gaps.csv lacks BDBV-CLAIM-005")
    gap_text = " ".join(
        exponent_gap.get(key, "")
        for key in ("status", "public_action", "note", "topic")
    ).lower()
    for required in ("unsupported attribution", "not fitted", "heuristic"):
        if required not in gap_text:
            raise SnapshotContractError(
                f"BDBV-CLAIM-005 does not disclose corridor-constant limitation {required!r}"
            )


def narrative_required_fragments_from_values(
    *,
    confirmed_headline: int,
    zone_confirmed: int,
    unallocated: int,
    source_zone_count: int,
    source_zone_label: str = "official source zones",
    corridor_count: int,
    lower_range_pct: tuple[float, float],
    upper_range_pct: tuple[float, float],
) -> list[str]:
    return [
        f"{confirmed_headline} confirmed cases",
        f"{zone_confirmed} confirmed cases",
        f"{unallocated} confirmed cases",
        "officially zone-attributed",
        "source-attribution lag",
        "unallocated",
        f"{source_zone_count} {source_zone_label}",
        f"{corridor_count}-corridor watchlist",
        f"{lower_range_pct[0]:.1f}-{lower_range_pct[1]:.1f}% lower",
        f"{upper_range_pct[0]:.1f}-{upper_range_pct[1]:.1f}% upper",
    ]


def _source_zone_label(source_ids: list[str]) -> str:
    if source_ids and all(
        source_id.startswith("drc-moh-epidemie-dashboard") for source_id in source_ids
    ):
        return "DRC MoH source zones"
    if source_ids and all(source_id.startswith("afro-sitrep") for source_id in source_ids):
        return "WHO AFRO source zones"
    return "official source zones"


def _read_csv(path: pathlib.Path) -> list[dict[str, str]]:
    if not path.exists():
        raise SnapshotContractError(f"{path} is missing")
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _pct(value: float) -> float:
    return round(float(value) * 100.0, 1)


def _required_int(row: dict[str, Any], key: str, path: str) -> int:
    value = row.get(key)
    if not isinstance(value, int):
        raise SnapshotContractError(f"{path}.{key} must be an int, got {value!r}")
    return value


def _optional_int(row: dict[str, Any], key: str) -> int | None:
    value = row.get(key)
    if value is None:
        return None
    if not isinstance(value, int):
        raise SnapshotContractError(f"{key} must be an int when present, got {value!r}")
    return value


def _required_number(row: dict[str, Any], key: str, path: str) -> float:
    value = row.get(key)
    if not isinstance(value, (int, float)):
        raise SnapshotContractError(f"{path}.{key} must be numeric, got {value!r}")
    return float(value)


def _required_str(row: dict[str, Any], key: str, path: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SnapshotContractError(f"{path}.{key} must be a non-empty string")
    return value


def _range_pair(row: dict[str, Any], key: str) -> tuple[float, float]:
    value = row.get(key)
    if (
        not isinstance(value, list)
        or len(value) != 2
        or not all(isinstance(v, (int, float)) for v in value)
        or value[0] > value[1]
    ):
        raise SnapshotContractError(f"{key} must be a numeric [min, max] pair")
    return float(value[0]), float(value[1])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=pathlib.Path, default=DEFAULT_SNAPSHOT_PATH)
    parser.add_argument("--contract", type=pathlib.Path, default=DEFAULT_CONTRACT_PATH)
    parser.add_argument("--dataset-dir", type=pathlib.Path, default=DEFAULT_DATASET_DIR)
    parser.add_argument("--write", action="store_true", help="Write the generated contract JSON.")
    parser.add_argument("--check-text", action="store_true", help="Validate README, NUMBERS_AUDIT, and brief narrative.")
    parser.add_argument("--check-dataset", action="store_true", help="Validate public dataset CSVs against the contract.")
    args = parser.parse_args(argv)

    snapshot = load_json(args.snapshot)
    contract = build_contract(snapshot)
    if args.write:
        args.contract.write_text(json.dumps(contract, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"snapshot_contract={args.contract}")
    elif args.contract.exists():
        validate_snapshot(snapshot, load_json(args.contract))
    else:
        validate_snapshot(snapshot)

    if args.check_text:
        validate_text_artifacts(contract)
    if args.check_dataset:
        validate_dataset_exports(contract, args.dataset_dir)
    print("snapshot contract ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
