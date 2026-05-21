"""LOVS Module G: visibility-gap analysis.

Produces a typed `VisibilityGapAnalysis` from the union of Module B, C, D
outputs. Outputs an observability-gap inventory and recommended data
requests with reasoning. Explicitly does NOT output a response-adequacy
grade; whether a response is adequate is the MoH or WHO judgment to make.

Stdlib only. Deterministic.
"""
from __future__ import annotations

import dataclasses

from lovs import lovs_reconciler
from lovs import lovs_visibility
from lovs import lovs_transmission


MODEL_VERSION = "lovs_gap-v0.1.0"


@dataclasses.dataclass(frozen=True)
class IntervalProportion:
    lower_50: float
    upper_50: float
    lower_95: float
    upper_95: float


@dataclasses.dataclass(frozen=True)
class ObservabilityGap:
    indicator: str
    current_observability: str   # "observable" | "partial" | "unobservable"
    required_cadence: str
    actual_cadence: str | None


@dataclasses.dataclass(frozen=True)
class DataRequest:
    request: str
    reasoning: str


@dataclasses.dataclass(frozen=True)
class VisibilityGapAnalysis:
    outbreak_id: str
    as_of: str
    observability_gaps: tuple[ObservabilityGap, ...]
    contact_tracing_proxy: IntervalProportion | None
    isolation_proxy: IntervalProportion | None
    lab_bottleneck_signal: str   # "clear" | "constrained" | "saturated" | "unknown"
    vaccine_feasibility: str     # "available" | "constrained" | "unavailable" | "unknown"
    recommended_data_requests: tuple[DataRequest, ...]
    model_version: str
    provenance_ids: tuple[str, ...]


# Required response indicators (canonical, per Stage One product spec §7.G).
REQUIRED_INDICATORS: tuple[tuple[str, str], ...] = (
    ("contacts_listed", "daily by health zone"),
    ("contacts_followed", "daily by health zone"),
    ("cases_isolated", "daily by health zone"),
    ("deaths_before_isolation", "weekly by health zone"),
    ("lab_confirmation_cadence", "daily by laboratory"),
    ("hcw_infections", "weekly by health zone"),
    ("vaccine_doses_administered", "weekly by health zone"),
    ("conflict_access_constraints", "weekly by health zone"),
    ("risk_communication_indicators", "weekly by population"),
    ("safe_burial_compliance", "weekly by health zone"),
)


def _observability_from_snapshot(
    snapshot: lovs_reconciler.OutbreakSnapshot,
) -> tuple[ObservabilityGap, ...]:
    """Derive observability for each required indicator from snapshot evidence."""
    # For Stage One: the snapshot's normalized_content (downstream of Module B)
    # carries reported_counts and affected_zones. The required indicators are
    # mostly NOT in the public T1 sources at current cadence; the gap
    # inventory is mostly "unobservable" or "partial" by default.
    counts_present = set(snapshot.reported_counts.keys())
    has_zone_breakdown = len(snapshot.affected_zones) > 0
    gaps: list[ObservabilityGap] = []

    for indicator, required_cadence in REQUIRED_INDICATORS:
        if indicator == "contacts_listed":
            actual = "unknown"
            observability = "unobservable"
        elif indicator == "contacts_followed":
            actual = "unknown"
            observability = "unobservable"
        elif indicator == "cases_isolated":
            if "confirmed" in counts_present and has_zone_breakdown:
                actual = "implicit from confirmed count by zone"
                observability = "partial"
            else:
                actual = "unknown"
                observability = "unobservable"
        elif indicator == "deaths_before_isolation":
            actual = "unknown"
            observability = "unobservable"
        elif indicator == "lab_confirmation_cadence":
            if "suspected" in counts_present and "confirmed" in counts_present:
                actual = "implied by suspected-to-confirmed ratio"
                observability = "partial"
            else:
                actual = "unknown"
                observability = "unobservable"
        elif indicator == "hcw_infections":
            actual = "unknown"
            observability = "unobservable"
        elif indicator == "vaccine_doses_administered":
            actual = "unknown"
            observability = "unobservable"
        elif indicator == "conflict_access_constraints":
            actual = "T3 covariate (ACLED) available; not in current snapshot"
            observability = "partial"
        elif indicator == "risk_communication_indicators":
            actual = "unknown"
            observability = "unobservable"
        elif indicator == "safe_burial_compliance":
            actual = "unknown"
            observability = "unobservable"
        else:
            actual = "unknown"
            observability = "unobservable"

        gaps.append(
            ObservabilityGap(
                indicator=indicator,
                current_observability=observability,
                required_cadence=required_cadence,
                actual_cadence=actual,
            )
        )
    return tuple(gaps)


def _contact_tracing_proxy(
    snapshot: lovs_reconciler.OutbreakSnapshot,
    visibility: lovs_visibility.VisibilityPosterior,
) -> IntervalProportion | None:
    """A coarse proxy: visibility-completeness lower bound as a stand-in.

    Honest interpretation: if reporting completeness is low, contact-tracing
    is likely incomplete. This is a stand-in, not a measure.
    """
    if visibility.reporting_completeness.lower_50 < 0:
        return None
    return IntervalProportion(
        lower_50=visibility.reporting_completeness.lower_50,
        upper_50=visibility.reporting_completeness.upper_50,
        lower_95=visibility.reporting_completeness.lower_95,
        upper_95=visibility.reporting_completeness.upper_95,
    )


def _isolation_proxy(
    snapshot: lovs_reconciler.OutbreakSnapshot,
) -> IntervalProportion | None:
    """A coarse proxy: confirmed cases as a fraction of suspected.

    A higher fraction implies more cases are reaching laboratory confirmation,
    which loosely correlates with isolation feasibility.
    """
    suspected = snapshot.reported_counts.get("suspected")
    confirmed = snapshot.reported_counts.get("confirmed")
    if suspected is None or confirmed is None or suspected.primary_value <= 0:
        return None
    point = confirmed.primary_value / suspected.primary_value
    lo50 = max(0.0, point - 0.10)
    hi50 = min(1.0, point + 0.10)
    lo95 = max(0.0, point - 0.20)
    hi95 = min(1.0, point + 0.20)
    return IntervalProportion(lower_50=lo50, upper_50=hi50, lower_95=lo95, upper_95=hi95)


def _lab_bottleneck_signal(snapshot: lovs_reconciler.OutbreakSnapshot) -> str:
    suspected = snapshot.reported_counts.get("suspected")
    confirmed = snapshot.reported_counts.get("confirmed")
    if suspected is None or confirmed is None:
        return "unknown"
    if suspected.primary_value <= 0:
        return "unknown"
    ratio = confirmed.primary_value / suspected.primary_value
    if ratio >= 0.75:
        return "clear"
    if ratio >= 0.40:
        return "constrained"
    return "saturated"


def _vaccine_feasibility(snapshot: lovs_reconciler.OutbreakSnapshot) -> str:
    """Vaccine availability is a function of pathogen species.

    Zaire ebolavirus (EBOV-Z) has Ervebo (rVSV-ZEBOV) licensed.
    Bundibugyo (BDBV) has no licensed vaccine as of 2024; clinical-trial
    candidates exist.
    Sudan ebolavirus (SUDV) has no licensed vaccine; trial-stage products.
    """
    p = snapshot.pathogen.upper()
    if p in ("EBOV-Z", "EBOV", "ZAIRE", "ZEBOV"):
        return "available"
    if p in ("BDBV", "BUNDIBUGYO", "SUDV", "SUDAN"):
        return "constrained"
    return "unknown"


def _recommended_data_requests(
    snapshot: lovs_reconciler.OutbreakSnapshot,
    visibility: lovs_visibility.VisibilityPosterior,
    transmission: lovs_transmission.TransmissionPlausibility | None,
    gaps: tuple[ObservabilityGap, ...],
) -> tuple[DataRequest, ...]:
    requests: list[DataRequest] = []

    # Always: explicit case-definition declaration if not present.
    if snapshot.case_definition_version is None:
        requests.append(
            DataRequest(
                request="Explicit case-definition version on each public bulletin",
                reasoning=(
                    "Cross-window comparability requires the case definition be declared. "
                    "Without it, growth signals may reflect definition changes rather than incidence."
                ),
            )
        )

    # If contacts unobservable: request contacts cadence.
    contact_gap = next(
        (g for g in gaps if g.indicator == "contacts_listed"), None
    )
    if contact_gap and contact_gap.current_observability == "unobservable":
        requests.append(
            DataRequest(
                request="Daily contacts listed and contacts followed by health zone",
                reasoning=(
                    "Contact-tracing visibility is the load-bearing indicator for response posture; "
                    "current snapshot does not surface it at any cadence."
                ),
            )
        )

    # If lab cadence partial: request daily by laboratory.
    lab_gap = next(
        (g for g in gaps if g.indicator == "lab_confirmation_cadence"), None
    )
    if lab_gap and lab_gap.current_observability in ("partial", "unobservable"):
        requests.append(
            DataRequest(
                request="Daily laboratory confirmation cadence by laboratory and health zone",
                reasoning=(
                    "Lab bottleneck signal informs visibility-delay estimation; "
                    "current cadence is implicit only via suspected-to-confirmed ratio."
                ),
            )
        )

    # If reporting completeness is low: request finer-grained cadence.
    if visibility.reporting_completeness.upper_50 < 0.65:
        requests.append(
            DataRequest(
                request="Daily reporting cadence by health zone (current weekly cadence)",
                reasoning=(
                    f"Reporting completeness 50% interval upper bound is "
                    f"{visibility.reporting_completeness.upper_50:.2f}; "
                    f"increasing cadence would substantially reduce visibility-delay uncertainty."
                ),
            )
        )

    # If source-conflict notes present: request reconciliation.
    if snapshot.source_conflict_notes:
        requests.append(
            DataRequest(
                request="Source-of-truth reconciliation between national MoH and regional bulletins",
                reasoning=(
                    f"{len(snapshot.source_conflict_notes)} active T1 source-conflict note(s); "
                    f"unresolved conflicts inflate the visibility-gap interval."
                ),
            )
        )

    # If transmission plausibility suggests multi-generation hidden spread.
    if transmission is not None:
        # Sum probability across all bins >= 3 to handle both the legacy 3-bin
        # output (where the "3" bin is "3 or more") and the current 6-bin output
        # (where "3..MAX" are separate bins and the MAX bin is censored).
        gens = transmission.generations_before_detection
        g3_plus = sum(p for k, p in gens.items() if int(k) >= 3)
        if g3_plus >= 0.20:
            requests.append(
                DataRequest(
                    request="Onset-date line-list extract (de-identified) for retrospective onset reconstruction",
                    reasoning=(
                        f"Module D estimates probability of 3+ generations before detection at "
                        f"{g3_plus:.0%}; onset-date line-list (even partial) would substantially "
                        f"narrow the latent-chains plausibility interval."
                    ),
                )
            )

    if not requests:
        requests.append(
            DataRequest(
                request="Continue current cadence; no acute observability gap",
                reasoning="All required indicators observable at adequate cadence.",
            )
        )

    return tuple(requests)


def gap_analysis(
    snapshot: lovs_reconciler.OutbreakSnapshot,
    visibility: lovs_visibility.VisibilityPosterior,
    transmission: lovs_transmission.TransmissionPlausibility | None = None,
) -> VisibilityGapAnalysis:
    """Produce a visibility-gap analysis from the Module B/C/D union."""
    gaps = _observability_from_snapshot(snapshot)
    return VisibilityGapAnalysis(
        outbreak_id=snapshot.outbreak_id,
        as_of=snapshot.as_of,
        observability_gaps=gaps,
        contact_tracing_proxy=_contact_tracing_proxy(snapshot, visibility),
        isolation_proxy=_isolation_proxy(snapshot),
        lab_bottleneck_signal=_lab_bottleneck_signal(snapshot),
        vaccine_feasibility=_vaccine_feasibility(snapshot),
        recommended_data_requests=_recommended_data_requests(
            snapshot, visibility, transmission, gaps
        ),
        model_version=MODEL_VERSION,
        provenance_ids=snapshot.sources,
    )
