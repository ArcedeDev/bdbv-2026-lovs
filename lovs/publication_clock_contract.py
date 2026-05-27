"""Cross-surface gate for the publication-clock-only primary source pattern.

A "publication-clock-only" primary is a manifest source that explicitly
exposes the structured data/report-date fields (date_rapportage, data_as_of,
report_date, ...) but leaves them all null. The canonical example is the
DRC MoH epidemie dashboard GraphQL aggregate (sitrep 009, 2026-05-24),
which publishes a cumulative deaths count tagged only with a publication
date. Mirrors the null-rule already enforced by lovs.source_dates.

When such a source becomes the primary for a reported_count, downstream
analytic surfaces (trajectory chart, death-back-projection, sensitivity
grid, ...) must not silently treat its publication date as a data/report
date. This gate enforces the same contract at the LOVS release-gate layer
so the pattern is protected even when a website surface is not in scope.

Invariants (run inside release_snapshot.run_release_gates):
  A. Every reported_counts.{metric}.primary_source_id is resolvable in
     data/bundibugyo-2026/manifest.json entries (provenance integrity).
  B. For every publication-clock-only primary, at least one
     analysis_dependency_audit surface whose inputs reference {metric}
     declares the publication clock in clock_basis (any of the recognised
     publication-clock markers).
"""
from __future__ import annotations

import json
import pathlib
import sys
from typing import Any

from lovs.source_dates import DATA_DATE_FIELDS

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_SNAPSHOT_CONTRACT = REPO_ROOT / "data" / "snapshot_contract.json"
DEFAULT_LIVE_OUTPUT = REPO_ROOT / "data" / "live-bdbv-2026-output.json"
DEFAULT_MANIFEST = REPO_ROOT / "data" / "bundibugyo-2026" / "manifest.json"

PUBLICATION_CLOCK_MARKERS = (
    "publication clock",
    "publication-clock",
    "publication_clock",
    "not_recorded",
    "not recorded",
    "no daterapportage",
    "no data/report",
    "without a data/report",
    "lacks a data/report",
)


class PublicationClockContractError(ValueError):
    """Raised when the publication-clock cross-surface contract is violated."""


def _load_json(path: pathlib.Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PublicationClockContractError(f"{path}: file not found") from exc
    except json.JSONDecodeError as exc:
        raise PublicationClockContractError(f"{path}: invalid JSON: {exc}") from exc


def is_publication_clock_only(entry: dict[str, Any]) -> bool:
    """True when a manifest entry has DATA_DATE_FIELDS present but all null.

    Mirrors the null-rule in lovs.source_dates.source_data_date: when
    structured data/report fields are explicitly present but null,
    publication date is a freshness clock only, not a safe substitute.
    """
    normalized = entry.get("normalized_content") or {}
    present = [field for field in DATA_DATE_FIELDS if field in normalized]
    if not present:
        return False
    return all(normalized.get(field) is None for field in present)


def _find_manifest_entry(
    manifest_entries: list[dict[str, Any]], source_id: str
) -> dict[str, Any] | None:
    # Snapshot primaries carry canonical (suffix-stripped) source ids while
    # some manifest entries (e.g. ECDC live captures) carry a "-live" suffix.
    # Match exact first, then fall back to the canonical form on either side.
    candidates = {source_id, source_id + "-live"}
    if source_id.endswith("-live"):
        candidates.add(source_id[: -len("-live")])
    for entry in manifest_entries:
        manifest_id = entry.get("source_id", "")
        if manifest_id in candidates:
            return entry
        if manifest_id.endswith("-live") and manifest_id[: -len("-live")] == source_id:
            return entry
    return None


def _surface_inputs_metric(surface: dict[str, Any], metric: str) -> bool:
    inputs = surface.get("inputs")
    if not isinstance(inputs, dict):
        return False
    if metric in inputs:
        return True
    return any(metric in str(key) for key in inputs.keys())


def validate(
    contract_path: pathlib.Path = DEFAULT_SNAPSHOT_CONTRACT,
    live_output_path: pathlib.Path = DEFAULT_LIVE_OUTPUT,
    manifest_path: pathlib.Path = DEFAULT_MANIFEST,
) -> dict[str, int]:
    contract = _load_json(contract_path)
    live = _load_json(live_output_path)
    manifest = _load_json(manifest_path)

    reported = contract.get("reported_counts") or {}
    if not reported:
        return {"primaries_checked": 0, "publication_clock_only": 0}

    manifest_entries = manifest.get("entries") or []
    publication_clock_primaries: list[tuple[str, str]] = []
    primaries_checked = 0
    for metric, metric_block in reported.items():
        primary_id = metric_block.get("primary_source_id")
        if not isinstance(primary_id, str) or not primary_id.strip():
            raise PublicationClockContractError(
                f"reported_counts.{metric}.primary_source_id missing or empty"
            )
        entry = _find_manifest_entry(manifest_entries, primary_id)
        if entry is None:
            raise PublicationClockContractError(
                f"reported_counts.{metric}.primary_source_id={primary_id!r} "
                f"is not present in manifest entries (provenance gap)"
            )
        primaries_checked += 1
        if is_publication_clock_only(entry):
            publication_clock_primaries.append((metric, primary_id))

    if not publication_clock_primaries:
        return {
            "primaries_checked": primaries_checked,
            "publication_clock_only": 0,
        }

    audit = live.get("analysis_dependency_audit") or []
    for metric, primary_id in publication_clock_primaries:
        matching = [s for s in audit if _surface_inputs_metric(s, metric)]
        if not matching:
            raise PublicationClockContractError(
                f"publication-clock-only primary {primary_id!r} for reported_counts.{metric}: "
                f"no analysis_dependency_audit surface depends on this metric"
            )
        declared = [
            s for s in matching
            if isinstance(s.get("clock_basis"), str)
            and any(marker in s["clock_basis"].lower() for marker in PUBLICATION_CLOCK_MARKERS)
        ]
        if not declared:
            surfaces = [s.get("surface") for s in matching]
            raise PublicationClockContractError(
                f"publication-clock-only primary {primary_id!r} for reported_counts.{metric}: "
                f"no audit surface in {surfaces} declares the publication clock in clock_basis "
                f"(expected one of {list(PUBLICATION_CLOCK_MARKERS)})"
            )

    return {
        "primaries_checked": primaries_checked,
        "publication_clock_only": len(publication_clock_primaries),
    }


def main() -> int:
    try:
        result = validate()
    except PublicationClockContractError as exc:
        print(f"publication-clock contract gate failed: {exc}", file=sys.stderr)
        return 1
    print(
        "publication-clock contract gate ok "
        f"({result['primaries_checked']} primaries checked; "
        f"{result['publication_clock_only']} publication-clock-only "
        f"with cross-surface declaration)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
