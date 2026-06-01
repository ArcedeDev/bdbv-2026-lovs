"""LOVS report generator.

Consolidates Module B/C/D/E/F/G outputs plus the Mode A backtest result
into a deterministic markdown report. The three-zone framing is locked at
the template level:
 - Zone 1: Mode A retrospective (WA 2014 substrate) -- academically validated
 - Zone 2: Mode B live shadow (registered hypotheses) -- accumulating
 - Zone 3: Worked example on current fixture -- illustrative; no skill claim

Stdlib only. Deterministic.
"""
from __future__ import annotations

import dataclasses
import pathlib
from typing import Any

from lovs import lovs_archive
from lovs import lovs_reconciler
from lovs import lovs_visibility
from lovs import lovs_transmission
from lovs import lovs_next_zone
from lovs import lovs_spillover
from lovs import lovs_gap
from lovs import lovs_validation


MODEL_VERSION = "lovs_report-v0.1.0"

FRAMING_DISCLAIM = (
    "Three-zone framing locked. Zone 1: Mode A retrospective backtest on the WA 2014 "
    "substrate is the academically validated result. Zone 2: Mode B live shadow forecasts "
    "(if registered) accumulate evidence over time; resolved scores are reported separately. "
    "Zone 3: the worked example below applies the module stack to a current-situation "
    "fixture for illustrative purposes only; it is not a forecasting-skill claim and not a "
    "real-time-fetched analysis. No blending of the three zones."
)


def _format_interval_proportion(label: str, ip) -> str:
    if ip is None:
        return f"- {label}: n/a"
    return (
        f"- {label}: 50% [{ip.lower_50:.2f}, {ip.upper_50:.2f}]; "
        f"95% [{ip.lower_95:.2f}, {ip.upper_95:.2f}]"
    )


def _format_interval_days(label: str, ip) -> str:
    if ip is None:
        return f"- {label}: n/a"
    return (
        f"- {label}: 50% [{ip.lower_50:.1f}, {ip.upper_50:.1f}] days; "
        f"95% [{ip.lower_95:.1f}, {ip.upper_95:.1f}] days"
    )


def _format_interval_count(label: str, ip) -> str:
    if ip is None:
        return f"- {label}: n/a"
    return (
        f"- {label}: 50% [{ip.lower_50}, {ip.upper_50}]; "
        f"95% [{ip.lower_95}, {ip.upper_95}]"
    )


def render_visibility_strip(visibility: lovs_visibility.VisibilityPosterior) -> str:
    lines = [
        f"### Visibility Posterior Strip ({visibility.as_of}, {visibility.geography_id})",
        "",
        f"- Visibility grade: **{visibility.visibility_grade}**",
        _format_interval_proportion("Reporting completeness", visibility.reporting_completeness),
        _format_interval_days("Publication latency", visibility.publication_latency_days),
        _format_interval_count("Confirmation backlog", visibility.confirmation_backlog),
        "",
        "Uncertainty drivers:",
    ]
    for driver in visibility.uncertainty_drivers:
        lines.append(f"- {driver}")
    lines.append("")
    lines.append("Missing data requests:")
    for req in visibility.missing_data_requests:
        lines.append(f"- {req}")
    return "\n".join(lines)


def render_reconciliation(snapshot: lovs_reconciler.OutbreakSnapshot) -> str:
    lines = [
        f"### Reconciliation (Module B)",
        "",
        f"- Outbreak: {snapshot.outbreak_id}",
        f"- As of: {snapshot.as_of}",
        f"- Pathogen: {snapshot.pathogen}",
        f"- Country scope: {', '.join(snapshot.country_scope)}",
        f"- Affected zones: {', '.join(snapshot.affected_zones) if snapshot.affected_zones else 'none reported'}",
        f"- Case-definition version: {snapshot.case_definition_version or 'not declared'}",
        f"- Deaths-to-confirmed tension: {snapshot.deaths_to_confirmed_tension_flag}",
        "",
        "Reconciled counts (interval across T1 sources):",
    ]
    for case_class in (
        "suspected_active",
        "suspected_cumulative",
        "probable",
        "confirmed",
    ):
        rc = snapshot.reported_counts.get(case_class)
        if rc is None:
            lines.append(f"- {case_class}: not reported")
        else:
            lines.append(
                f"- {case_class}: primary {rc.primary_value} "
                f"(from {rc.primary_source_id!r}), interval [{rc.minimum}, {rc.maximum}]"
            )
    for death_class in ("confirmed", "suspected"):
        rd = snapshot.reported_deaths.get(death_class)
        if rd is None:
            lines.append(f"- deaths ({death_class}): not reported")
        else:
            lines.append(
                f"- deaths ({death_class}): primary {rd.primary_value} "
                f"(from {rd.primary_source_id!r}), interval [{rd.minimum}, {rd.maximum}]"
            )
    lines.append("")
    if snapshot.source_conflict_notes:
        lines.append("Source-conflict notes:")
        for note in snapshot.source_conflict_notes:
            lines.append(f"- {note}")
        lines.append("")
    return "\n".join(lines)


def render_transmission(transmission: lovs_transmission.TransmissionPlausibility) -> str:
    lines = [
        f"### Transmission Plausibility (Module D)",
        "",
        _format_interval_count("Latent active chains", transmission.latent_active_chains),
        "",
        "Generations-before-detection probability:",
    ]
    gens = transmission.generations_before_detection
    bins_sorted = sorted(int(k) for k in gens)
    max_bin = max(bins_sorted) if bins_sorted else 3
    for k in bins_sorted:
        p = gens.get(k, 0.0)
        # The terminal bin is censored ("k or more"); earlier bins are point bins.
        label = f"{k}+ (censored upper bin)" if k == max_bin else str(k)
        lines.append(f"- {label} generation(s): {p:.2f}")
    p_three_or_more = sum(p for k, p in gens.items() if int(k) >= 3)
    lines.append(f"- (summary) 3+ generation(s): {p_three_or_more:.2f}")
    lines.append("")
    lines.append("Priors cited:")
    for c in transmission.priors_cited:
        lines.append(f"- {c}")
    lines.append("")
    lines.append("Assumptions:")
    for a in transmission.assumptions:
        lines.append(f"- {a}")
    return "\n".join(lines)


def render_next_zone(estimates: tuple[lovs_next_zone.CorridorRiskEstimate, ...]) -> str:
    lines = [f"### Next-Zone Risk (Module E)", ""]
    if not estimates:
        lines.append("No candidate corridors supplied.")
        return "\n".join(lines)
    lines.append(
        "| Rank | Source | Target | Horizon (days) | Risk (visibility-adj 50%) | Status |"
    )
    lines.append("|---|---|---|---|---|---|")
    for rank, e in enumerate(estimates[:20], start=1):
        lines.append(
            f"| {rank} | {e.source_geography_id} | {e.target_geography_id} | "
            f"{e.horizon_days} | "
            f"[{e.risk_visibility_adjusted.lower_50:.3f}, {e.risk_visibility_adjusted.upper_50:.3f}] | "
            f"{e.status} |"
        )
    lines.append("")
    return "\n".join(lines)


def render_spillover(spillover: lovs_spillover.SpilloverNarrative) -> str:
    lines = [
        f"### Spillover Plausibility (Module F)",
        "",
        f"Possible interface categories: "
        f"{', '.join(spillover.possible_interface_categories) if spillover.possible_interface_categories else 'unknown index'}",
        "",
        f"Origin uncertainty: {spillover.origin_uncertainty}",
        "",
        f"Narrative: {spillover.narrative_text}",
        "",
    ]
    if spillover.source_excerpts:
        lines.append("Source excerpts:")
        for ex in spillover.source_excerpts:
            lines.append(f"- {ex}")
        lines.append("")
    return "\n".join(lines)


def render_gap(gap: lovs_gap.VisibilityGapAnalysis) -> str:
    lines = [
        f"### Visibility-Gap Analysis (Module G)",
        "",
        f"- Lab bottleneck signal: **{gap.lab_bottleneck_signal}**",
        f"- Vaccine feasibility: **{gap.vaccine_feasibility}**",
        _format_interval_proportion("Contact-tracing visibility proxy", gap.contact_tracing_proxy),
        _format_interval_proportion("Isolation visibility proxy", gap.isolation_proxy),
        "",
        "Observability gap inventory:",
        "",
        "| Indicator | Observability | Required cadence | Actual cadence |",
        "|---|---|---|---|",
    ]
    for og in gap.observability_gaps:
        lines.append(
            f"| {og.indicator} | {og.current_observability} | {og.required_cadence} | "
            f"{og.actual_cadence or 'unknown'} |"
        )
    lines.append("")
    lines.append("Recommended data requests:")
    lines.append("")
    for dr in gap.recommended_data_requests:
        lines.append(f"- **{dr.request}**  ")
        lines.append(f"  Reasoning: {dr.reasoning}")
    lines.append("")
    return "\n".join(lines)


def render_mode_a_summary(result: lovs_validation.ModeABacktestResult) -> str:
    lines = [
        f"## Zone 1: Mode A retrospective backtest",
        "",
        f"**Substrate**: {result.substrate_label}",
        f"**As-of evaluation points**: {', '.join(result.as_of_dates)}",
        f"**Model version**: {result.model_version}",
        "",
        f"### Visibility-completeness interval coverage",
        "",
        f"- 50% interval coverage: **{result.visibility_interval_50_coverage:.0%}** (target 50%)",
        f"- 95% interval coverage: **{result.visibility_interval_95_coverage:.0%}** (target 95%)",
        "",
        f"### Expected calibration error",
        "",
        f"ECE: **{result.expected_calibration_error:.3f}** (lower is better; 0 is perfect)",
        "",
        f"### Calibration bins (predicted vs observed)",
        "",
        "| Bin | Predicted (mean) | Observed (frequency) | Count |",
        "|---|---|---|---|",
    ]
    for b in result.visibility_calibration_bins:
        lines.append(
            f"| [{b.bin_lower:.2f}, {b.bin_upper:.2f}] | {b.predicted_mean:.3f} | "
            f"{b.observed_frequency:.3f} | {b.count} |"
        )
    lines.append("")
    lines.append("### Methodology notes")
    lines.append("")
    for note in result.methodology_notes:
        lines.append(f"- {note}")
    lines.append("")
    return "\n".join(lines)


def render_worked_example(
    snapshot: lovs_reconciler.OutbreakSnapshot,
    visibility: lovs_visibility.VisibilityPosterior,
    transmission: lovs_transmission.TransmissionPlausibility,
    next_zone_estimates: tuple[lovs_next_zone.CorridorRiskEstimate, ...],
    spillover: lovs_spillover.SpilloverNarrative,
    gap: lovs_gap.VisibilityGapAnalysis,
) -> str:
    lines = [
        f"## Zone 3: Worked example (illustrative; current-situation fixture)",
        "",
        "**Important framing**: the outputs below are illustrative. The substrate is a "
        "committed fixture exercising every module code path; the analysis is not a "
        "real-time-fetched live forecast. Live application requires connector ingestion of "
        "current public sources (out of scope for this build) plus pre-registered "
        "hypotheses via an external hypothesis store.",
        "",
        render_reconciliation(snapshot),
        render_visibility_strip(visibility),
        "",
        render_transmission(transmission),
        render_next_zone(next_zone_estimates),
        render_spillover(spillover),
        render_gap(gap),
    ]
    return "\n".join(lines)


def render_full_deliverable(
    archive: lovs_archive.Archive,
    outbreak_id: str,
    as_of: str,
    candidate_targets: tuple[str, ...],
    mode_a_result: lovs_validation.ModeABacktestResult,
) -> str:
    """Build the full markdown deliverable."""
    snapshot = lovs_reconciler.reconcile(archive, outbreak_id, as_of)
    visibility = lovs_visibility.nowcast(snapshot)
    transmission = lovs_transmission.transmission_plausibility(snapshot)
    next_zone = lovs_next_zone.next_zone_risk(snapshot, visibility, candidate_targets)
    spillover = lovs_spillover.spillover_narrative(archive, outbreak_id, as_of)
    gap = lovs_gap.gap_analysis(snapshot, visibility, transmission)

    lines = [
        "# Latent Outbreak Visibility System (LOVS): Stage One Report",
        "",
        f"Generated for outbreak `{outbreak_id}` as of `{as_of}`.",
        "",
        "## Framing",
        "",
        FRAMING_DISCLAIM,
        "",
        render_mode_a_summary(mode_a_result),
        "",
        "## Zone 2: Mode B live shadow forecasts",
        "",
        "Mode B accumulates evidence over time via pre-registered hypotheses through an "
        "external hypothesis store. At the time of this report, no "
        "live shadow forecasts have been registered through this pipeline; the discipline is "
        "encoded at the Forecast type level, and registration is the analyst's deliberate "
        "step. Provisional estimates (status='provisional') below carry no numeric confidence.",
        "",
        render_worked_example(snapshot, visibility, transmission, next_zone, spillover, gap),
        "",
        "## What this build does and does not claim",
        "",
        "Does claim:",
        "- The architecture passes its 240+-test contract surface with stdlib-only implementation.",
        "- Mode A retrospective backtest on the WA 2014 substrate yields the visibility-completeness coverage and calibration metrics reported above.",
        "- The Module C onset-to-notification delay prior is cited from peer-reviewed literature (Camacho 2015, EBOV-Zaire, applied cross-species to BDBV), and the Module B incubation and serial-interval priors from Wamala 2010 and MacNeil 2010. The Module B reproduction prior and the Module D/E corridor-gravity exponents are interim engineering heuristics, not source-fitted (see data/evidence-chains.json: ec:lovs:module-d:bdbv-r-prior-gamma, ec:lovs:module-d:corridor-gravity-exponents).",
        "- The Forecast type contract enforces pre-registration discipline: no numeric confidence is exposed unless backed by a registered external hypothesis ID.",
        "",
        "Does NOT claim:",
        "- Forecasting skill on any live current outbreak. Mode B requires resolved hypotheses, not yet accumulated.",
        "- A hidden case count for any outbreak. The product surfaces visibility-gap intervals, not point estimates of hidden burden.",
        "- A response-adequacy judgment. Whether response is adequate is the MoH or WHO judgment to make.",
        "- Substitution for national surveillance. The product strengthens national systems by surfacing observability gaps.",
        "",
    ]
    return "\n".join(lines)
