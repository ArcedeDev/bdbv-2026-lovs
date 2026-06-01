"""LOVS Module B: case-state reconciler.

Consumes a tuple of ArchivedSnapshots (from Module A's `query_as_of`) and
produces a typed `OutbreakSnapshot` with reconciled-count intervals across
T1 sources, surfaced source conflicts, case-definition-change detection,
and deaths-to-confirmed tension flagging.

Spec: ops/consulting/idb-latent-outbreak-visibility-product-spec.md §5.2.

Tier discipline:
 - T1 sources are ground-truth for counts. Reconciled-count intervals span
   across T1 sources; the primary/headline value is the strongest current
   cumulative signal across active T1 sources, not mechanically the freshest
   publisher update. This prevents asynchronous release cadence from looking
   like a real down-revision. Explicit deconfirmations or denominator
   corrections should be normalized before reconciliation, so their corrected
   values enter the active source set directly.
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
    "official_who_afro",
    "official_africa_cdc",
    "official_continental_body",
    "official_cdc",
    "national_moh",
    "regional_body",
    "laboratory",
})

_CASE_CLASSES: tuple[str, ...] = (
    "suspected_active",
    "suspected_cumulative",
    "probable",
    "confirmed",
)

# Mapping from upstream normalized-content field names to the case-class keys
# emitted in OutbreakSnapshot.reported_counts. A single physical source field
# may produce multiple logical keys; multiple physical fields may collapse to
# one logical key. The order here matters for legacy-field precedence: a
# concrete cases_suspected_cumulative wins over a legacy cases_suspected.
_CASE_FIELD_SOURCES: tuple[tuple[str, str], ...] = (
    ("cases_confirmed", "confirmed"),
    ("cases_probable", "probable"),
    ("cases_suspected_active", "suspected_active"),
    ("cases_suspected_cumulative", "suspected_cumulative"),
    # Legacy fallback: an upstream source that still emits a single
    # `cases_suspected` field is treated as cumulative, because that has been
    # the operational meaning of the legacy field through May 28 2026.
    ("cases_suspected", "suspected_cumulative"),
)

_DEATH_CLASSES: tuple[str, ...] = ("confirmed", "suspected")

# Mapping from upstream normalized-content field names to the death-class keys
# emitted in OutbreakSnapshot.reported_deaths.
_DEATH_FIELD_SOURCES: tuple[tuple[str, str], ...] = (
    ("deaths_confirmed", "confirmed"),
    ("deaths_suspected", "suspected"),
)

_DEATHS_TO_CONFIRMED_TENSION_THRESHOLD: float = 0.80


class ReconcilerError(ValueError):
    """Raised when reconciliation cannot proceed."""


CARRIED_FORWARD_REASONS: frozenset[str] = frozenset({
    "source_schema_evolved",
    "awaiting_next_publication",
})


@dataclasses.dataclass(frozen=True)
class ReconciledCount:
    minimum: int
    maximum: int
    primary_value: int
    primary_source_id: str
    conflicting_source_ids: tuple[str, ...]
    carried_forward_from: str = ""
    carried_forward_reason: str = ""

    def __post_init__(self) -> None:
        if self.carried_forward_from and self.carried_forward_reason not in CARRIED_FORWARD_REASONS:
            raise ReconcilerError(
                f"carried_forward_from={self.carried_forward_from!r} requires "
                f"carried_forward_reason in {sorted(CARRIED_FORWARD_REASONS)}; "
                f"got {self.carried_forward_reason!r}"
            )
        if self.carried_forward_reason and not self.carried_forward_from:
            raise ReconcilerError(
                "carried_forward_reason set without carried_forward_from date"
            )

    def with_carry_forward(self, from_date: str, reason: str) -> "ReconciledCount":
        """Return a copy of this ReconciledCount tagged as carried-forward.

        The values (primary, min, max, conflicts) are unchanged: LOCF preserves
        the prior cumulative attestation. The flag is what tells downstream
        consumers this row is zero-information for trend deltas.
        """
        return dataclasses.replace(
            self,
            carried_forward_from=from_date,
            carried_forward_reason=reason,
        )


@dataclasses.dataclass(frozen=True)
class OutbreakSnapshot:
    outbreak_id: str
    as_of: str
    pathogen: str
    country_scope: tuple[str, ...]
    # Reported case counts keyed by case-class. Recognized keys post 2026-06-01
    # schema split: "confirmed", "probable", "suspected_active",
    # "suspected_cumulative". Empty / missing keys are permitted when the
    # upstream sources do not declare that class for the as-of cycle.
    reported_counts: dict[str, ReconciledCount]
    # Reported deaths keyed by death-class. Recognized keys: "confirmed"
    # (clinically lab-confirmed deaths) and "suspected" (deaths under
    # clinical investigation, not yet confirmed). Empty dict means no T1
    # source supplied deaths data for the cycle.
    reported_deaths: dict[str, ReconciledCount]
    affected_zones: tuple[str, ...]
    sources: tuple[str, ...]
    case_definition_version: str | None
    source_conflict_notes: tuple[str, ...]
    deaths_to_confirmed_tension_flag: bool
    model_version: str
    zone_attributed_counts: dict[str, dict[str, Any]] = dataclasses.field(default_factory=dict)


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
    # Counts are cumulative outbreak signals. A fresher source with a lower
    # value is not, by itself, evidence that the outbreak burden fell; it may
    # reflect publisher cadence, denominator scope, or delayed line-list flow.
    # Use the largest active count as the headline/model value, with recency
    # only breaking ties. Explicit corrections should already have been
    # normalized into the source's field value before this point.
    primary = max(values, key=lambda v: (v[0], v[2]))
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
    reported_deaths: dict[str, ReconciledCount],
    case_definition_change_detected: bool,
) -> tuple[str, ...]:
    notes: list[str] = []
    for case_class in sorted(reported_counts):
        rc = reported_counts[case_class]
        if rc.conflicting_source_ids:
            notes.append(
                f"T1 sources disagree on {case_class} count: "
                f"primary {rc.primary_value} (from {rc.primary_source_id!r}), "
                f"interval [{rc.minimum}, {rc.maximum}], "
                f"conflicting sources: {list(rc.conflicting_source_ids)}"
            )
    for death_class in sorted(reported_deaths):
        rd = reported_deaths[death_class]
        if rd.conflicting_source_ids:
            notes.append(
                f"T1 sources disagree on {death_class} deaths count: "
                f"primary {rd.primary_value} (from {rd.primary_source_id!r}), "
                f"interval [{rd.minimum}, {rd.maximum}], "
                f"conflicting sources: {list(rd.conflicting_source_ids)}"
            )
    if case_definition_change_detected:
        notes.append(
            "case-definition version changed during the as-of window; "
            "counts across the boundary are not directly comparable"
        )
    return tuple(notes)


def _deaths_to_confirmed_tension(
    confirmed: ReconciledCount | None,
    deaths_confirmed: ReconciledCount | None,
) -> bool:
    """Flag when confirmed deaths exceed a fraction of confirmed cases.

    For Ebola, CFR is typically 25-90% (WHO 2014 NEJM Table 2); a
    deaths_confirmed / confirmed ratio above the threshold suggests either
    severe under-ascertainment of confirmed cases or a divergence between
    the lab-confirmed death count and the lab-confirmed case denominator.

    Apples-to-apples: this function deliberately uses the confirmed-only
    death series (not summed confirmed+suspected) because suspected deaths
    have not yet cleared lab confirmation and would inflate the numerator
    against a denominator that has cleared confirmation.
    """
    if confirmed is None or deaths_confirmed is None or confirmed.primary_value <= 0:
        return False
    ratio = deaths_confirmed.primary_value / confirmed.primary_value
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
        zone_counts = snap.normalized_content.get("affected_health_zones")
        if isinstance(zone_counts, dict):
            for z, counts in zone_counts.items():
                if isinstance(z, str) and isinstance(counts, dict):
                    confirmed = counts.get("confirmed")
                    if isinstance(confirmed, int) and confirmed > 0:
                        seen.add(z)
    return tuple(sorted(seen))


def _gather_zone_attributed_counts(
    t1_snapshots: tuple[lovs_archive.ArchivedSnapshot, ...],
) -> dict[str, dict[str, Any]]:
    """Latest T1 per-zone count table keyed by source health-zone id.

    Publication date is the ordering key. Retrieval time is only an archive
    operation timestamp and must not make an older line-list table look newer.
    """
    candidates: list[tuple[str, str, dict[str, dict[str, Any]]]] = []
    for snap in t1_snapshots:
        zone_counts = snap.normalized_content.get("affected_health_zones")
        if not isinstance(zone_counts, dict):
            continue
        clean: dict[str, dict[str, Any]] = {}
        for zone_id, counts in zone_counts.items():
            if not isinstance(zone_id, str) or not isinstance(counts, dict):
                continue
            confirmed = counts.get("confirmed")
            if not isinstance(confirmed, int) or confirmed <= 0:
                continue
            clean[zone_id] = {
                **counts,
                "source_id": snap.provenance.source_id,
                "source_published_at": snap.provenance.published_at or snap.provenance.retrieved_at,
            }
        if clean:
            published_at = snap.provenance.published_at or snap.provenance.retrieved_at
            candidates.append((published_at, snap.provenance.source_id, clean))
    if not candidates:
        return {}
    _, _, latest = max(candidates, key=lambda item: (item[0], item[1]))
    return latest


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
    for field_name, case_class in _CASE_FIELD_SOURCES:
        # Skip a legacy fallback when a concrete split key already filled
        # the same logical case_class. This is what gives
        # cases_suspected_cumulative precedence over a legacy cases_suspected.
        if case_class in reported_counts:
            continue
        rc = _reconcile_count(field_name, t1_snapshots)
        if rc is not None:
            reported_counts[case_class] = rc

    reported_deaths: dict[str, ReconciledCount] = {}
    for field_name, death_class in _DEATH_FIELD_SOURCES:
        rd = _reconcile_count(field_name, t1_snapshots)
        if rd is not None:
            reported_deaths[death_class] = rd

    case_definition_version, case_definition_change = _detect_case_definition_version(
        t1_snapshots
    )

    affected_zones = _gather_affected_zones(t1_snapshots)
    zone_attributed_counts = _gather_zone_attributed_counts(t1_snapshots)
    sources = tuple(sorted(s.provenance.source_id for s in snapshots))

    conflict_notes = _build_conflict_notes(
        reported_counts, reported_deaths, case_definition_change
    )

    tension_flag = _deaths_to_confirmed_tension(
        reported_counts.get("confirmed"), reported_deaths.get("confirmed")
    )

    return OutbreakSnapshot(
        outbreak_id=outbreak_id,
        as_of=as_of,
        pathogen=pathogen,
        country_scope=tuple(sorted(country_scope)),
        reported_counts=dict(reported_counts),
        reported_deaths=dict(reported_deaths),
        affected_zones=affected_zones,
        sources=sources,
        case_definition_version=case_definition_version,
        source_conflict_notes=conflict_notes,
        deaths_to_confirmed_tension_flag=tension_flag,
        model_version=MODEL_VERSION,
        zone_attributed_counts=zone_attributed_counts,
    )


def snapshot_content_seed(snapshot: OutbreakSnapshot) -> int:
    """Derive a deterministic integer seed from an OutbreakSnapshot content hash.

    Determinism contract: dict keys are sorted via json.dumps(sort_keys=True);
    the seed is stable across runs as long as the same case-classes and
    death-classes are populated. Adding a new class key produces a new seed,
    which is intended: the seed is content-addressable.
    """
    payload = {
        "outbreak_id": snapshot.outbreak_id,
        "as_of": snapshot.as_of,
        "pathogen": snapshot.pathogen,
        "reported_counts": {
            k: dataclasses.asdict(v) for k, v in snapshot.reported_counts.items()
        },
        "reported_deaths": {
            k: dataclasses.asdict(v) for k, v in snapshot.reported_deaths.items()
        },
        "sources": list(snapshot.sources),
        "affected_zones": list(snapshot.affected_zones),
        "zone_attributed_counts": snapshot.zone_attributed_counts,
        "model_version": snapshot.model_version,
    }
    serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    digest = hashlib.sha256(serialized).hexdigest()
    return int(digest[:16], 16)
