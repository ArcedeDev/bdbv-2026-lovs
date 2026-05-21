"""LOVS Module B: case-state reconciler.

Consumes a tuple of ArchivedSnapshots (from Module A's `query_as_of`) and
produces a typed `OutbreakSnapshot` with reconciled-count intervals across
T1 sources, surfaced source conflicts, case-definition-change detection,
and deaths-to-confirmed tension flagging.

Spec: ops/consulting/idb-latent-outbreak-visibility-product-spec.md §5.2.

Tier discipline:
 - T1 sources are ground-truth for counts. Reconciled-count intervals span
   across T1 sources; the most-recently-updated T1 source supplies the
   primary value.
 - T2 sources never override T1 counts. They inform cadence and provide
   alternative attestations but cannot move a reconciled value.
 - T3 covariate sources are out of scope at this module.

Stdlib only. Deterministic. No network. No clock.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
from typing import Any

from lovs import lovs_archive


MODEL_VERSION = "lovs_reconciler-v0.1.0"

_T1_TIERS: frozenset[str] = frozenset({
    "official_who",
    "official_africa_cdc",
    "official_cdc",
    "national_moh",
    "regional_body",
    "laboratory",
})

_CASE_CLASSES: tuple[str, ...] = ("suspected", "probable", "confirmed")

_DEATHS_TO_CONFIRMED_TENSION_THRESHOLD: float = 0.80


class ReconcilerError(ValueError):
    """Raised when reconciliation cannot proceed."""


@dataclasses.dataclass(frozen=True)
class ReconciledCount:
    minimum: int
    maximum: int
    primary_value: int
    primary_source_id: str
    conflicting_source_ids: tuple[str, ...]


@dataclasses.dataclass(frozen=True)
class OutbreakSnapshot:
    outbreak_id: str
    as_of: str
    pathogen: str
    country_scope: tuple[str, ...]
    reported_counts: dict[str, ReconciledCount]
    reported_deaths: ReconciledCount | None
    affected_zones: tuple[str, ...]
    sources: tuple[str, ...]
    case_definition_version: str | None
    source_conflict_notes: tuple[str, ...]
    deaths_to_confirmed_tension_flag: bool
    model_version: str


def _extract_t1_snapshots(
    snapshots: tuple[lovs_archive.ArchivedSnapshot, ...],
) -> tuple[lovs_archive.ArchivedSnapshot, ...]:
    return tuple(s for s in snapshots if s.provenance.source_tier in _T1_TIERS)


def _reconcile_count(
    field_name: str,
    t1_snapshots: tuple[lovs_archive.ArchivedSnapshot, ...],
) -> ReconciledCount | None:
    """Reconcile a count field across T1 sources, surfacing conflicts."""
    values: list[tuple[int, str, str]] = []
    for snap in t1_snapshots:
        norm = snap.normalized_content
        raw_value = norm.get(field_name)
        if raw_value is None:
            continue
        if not isinstance(raw_value, (int, float)):
            continue
        values.append((int(raw_value), snap.provenance.source_id, snap.provenance.retrieved_at))
    if not values:
        return None
    minimum = min(v[0] for v in values)
    maximum = max(v[0] for v in values)
    primary = max(values, key=lambda v: v[2])
    primary_value, primary_source_id, _ = primary
    conflicting: list[str] = []
    for value, source_id, _ in values:
        if value != primary_value:
            conflicting.append(source_id)
    return ReconciledCount(
        minimum=minimum,
        maximum=maximum,
        primary_value=primary_value,
        primary_source_id=primary_source_id,
        conflicting_source_ids=tuple(sorted(conflicting)),
    )


def _detect_case_definition_version(
    snapshots: tuple[lovs_archive.ArchivedSnapshot, ...],
) -> tuple[str | None, bool]:
    """Returns (case_definition_version_most_recent, change_detected)."""
    versions: list[tuple[str, str]] = []
    for snap in snapshots:
        cd = snap.normalized_content.get("case_definition_version")
        if isinstance(cd, str):
            versions.append((cd, snap.provenance.retrieved_at))
    if not versions:
        return (None, False)
    versions.sort(key=lambda x: x[1])
    distinct = sorted({v for v, _ in versions})
    most_recent = versions[-1][0]
    return (most_recent, len(distinct) > 1)


def _build_conflict_notes(
    reported_counts: dict[str, ReconciledCount],
    reported_deaths: ReconciledCount | None,
    case_definition_change_detected: bool,
) -> tuple[str, ...]:
    notes: list[str] = []
    for case_class, rc in reported_counts.items():
        if rc.conflicting_source_ids:
            notes.append(
                f"T1 sources disagree on {case_class} count: "
                f"primary {rc.primary_value} (from {rc.primary_source_id!r}), "
                f"interval [{rc.minimum}, {rc.maximum}], "
                f"conflicting sources: {list(rc.conflicting_source_ids)}"
            )
    if reported_deaths is not None and reported_deaths.conflicting_source_ids:
        notes.append(
            f"T1 sources disagree on deaths count: "
            f"primary {reported_deaths.primary_value} (from {reported_deaths.primary_source_id!r}), "
            f"interval [{reported_deaths.minimum}, {reported_deaths.maximum}], "
            f"conflicting sources: {list(reported_deaths.conflicting_source_ids)}"
        )
    if case_definition_change_detected:
        notes.append(
            "case-definition version changed during the as-of window; "
            "counts across the boundary are not directly comparable"
        )
    return tuple(notes)


def _deaths_to_confirmed_tension(
    confirmed: ReconciledCount | None,
    deaths: ReconciledCount | None,
) -> bool:
    """Flag when deaths exceed a fraction of confirmed cases.

    For Ebola, CFR is typically 25-90% (WHO 2014 NEJM Table 2); a
    deaths/confirmed ratio above the threshold suggests either severe
    under-ascertainment of confirmed cases or a divergence between the
    death count and the laboratory-confirmed denominator.
    """
    if confirmed is None or deaths is None or confirmed.primary_value <= 0:
        return False
    ratio = deaths.primary_value / confirmed.primary_value
    return ratio >= _DEATHS_TO_CONFIRMED_TENSION_THRESHOLD


def _gather_affected_zones(
    t1_snapshots: tuple[lovs_archive.ArchivedSnapshot, ...],
) -> tuple[str, ...]:
    seen: set[str] = set()
    for snap in t1_snapshots:
        zones = snap.normalized_content.get("affected_zones")
        if isinstance(zones, list):
            for z in zones:
                if isinstance(z, str):
                    seen.add(z)
    return tuple(sorted(seen))


def reconcile(
    archive: lovs_archive.Archive,
    outbreak_id: str,
    as_of: str,
) -> OutbreakSnapshot:
    """Reconcile a per-outbreak per-as-of state from an archive."""
    snapshots = lovs_archive.query_as_of(archive, outbreak_id, as_of)
    if not snapshots:
        raise ReconcilerError(
            f"reconcile: no snapshots for outbreak {outbreak_id!r} at as_of {as_of!r}"
        )

    pathogens = sorted({s.pathogen for s in snapshots})
    if len(pathogens) > 1:
        raise ReconcilerError(
            f"reconcile: multiple pathogens for outbreak {outbreak_id!r}: {pathogens}"
        )
    pathogen = pathogens[0]

    country_scope: set[str] = set()
    for snap in snapshots:
        for country in snap.country_scope:
            country_scope.add(country)

    t1_snapshots = _extract_t1_snapshots(snapshots)
    if not t1_snapshots:
        raise ReconcilerError(
            f"reconcile: no T1 snapshots for outbreak {outbreak_id!r} at as_of {as_of!r}; "
            f"T2 sources are not load-bearing for ground truth"
        )

    reported_counts: dict[str, ReconciledCount] = {}
    for case_class in _CASE_CLASSES:
        field_name = f"cases_{case_class}"
        rc = _reconcile_count(field_name, t1_snapshots)
        if rc is not None:
            reported_counts[case_class] = rc

    reported_deaths = _reconcile_count("deaths", t1_snapshots)

    case_definition_version, case_definition_change = _detect_case_definition_version(
        t1_snapshots
    )

    affected_zones = _gather_affected_zones(t1_snapshots)
    sources = tuple(sorted(s.provenance.source_id for s in snapshots))

    conflict_notes = _build_conflict_notes(
        reported_counts, reported_deaths, case_definition_change
    )

    tension_flag = _deaths_to_confirmed_tension(
        reported_counts.get("confirmed"), reported_deaths
    )

    return OutbreakSnapshot(
        outbreak_id=outbreak_id,
        as_of=as_of,
        pathogen=pathogen,
        country_scope=tuple(sorted(country_scope)),
        reported_counts=dict(reported_counts),
        reported_deaths=reported_deaths,
        affected_zones=affected_zones,
        sources=sources,
        case_definition_version=case_definition_version,
        source_conflict_notes=conflict_notes,
        deaths_to_confirmed_tension_flag=tension_flag,
        model_version=MODEL_VERSION,
    )


def snapshot_content_seed(snapshot: OutbreakSnapshot) -> int:
    """Derive a deterministic integer seed from an OutbreakSnapshot content hash."""
    payload = {
        "outbreak_id": snapshot.outbreak_id,
        "as_of": snapshot.as_of,
        "pathogen": snapshot.pathogen,
        "reported_counts": {
            k: dataclasses.asdict(v) for k, v in snapshot.reported_counts.items()
        },
        "reported_deaths": (
            dataclasses.asdict(snapshot.reported_deaths)
            if snapshot.reported_deaths
            else None
        ),
        "sources": list(snapshot.sources),
        "model_version": snapshot.model_version,
    }
    serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    digest = hashlib.sha256(serialized).hexdigest()
    return int(digest[:16], 16)
