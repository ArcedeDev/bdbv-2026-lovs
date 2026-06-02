"""LOVS Module C: visibility nowcast.

Produces a typed `VisibilityPosterior` from an OutbreakSnapshot plus an
optional series of prior OutbreakSnapshots. Output intervals are calibrated
from a peer-reviewed onset-to-notification delay distribution. The product
framing is descriptive-not-predictive: this module estimates the visibility
gap (reporting completeness, publication latency, confirmation backlog),
not hidden case counts.

Prior (default onset-to-notification delay, BDBV-specific historical):
 - Onset-to-notification gamma, method-of-moments matched to mean 8.83 days,
   s.d. 8.29 days; therefore alpha = mean^2/var = 1.1345 and
   beta = mean/var = 0.1285 in shape-rate parameterization. Source:
   Rosello A, et al. eLife 2015 (10.7554/eLife.09015), Table 5, DRC Isiro
   2012 BDBV outbreak fit (n=52), corroborated by WHO grEPI as symptom onset
   to reporting. This is a prior-outbreak BDBV estimate, not a fitted 2026
   reporting-delay estimate.
 - Sensitivity comparator: Camacho A, et al. PLOS Currents Outbreaks 2015
   (10.1371/currents.outbreaks.406ae55e83ec0b5193e30856b9235ed2), EBOV-Zaire
   Sierra Leone 2014 onset-to-notification mean 4.5 days, s.d. 5 days
   (gamma alpha=0.81, beta=0.18). This was the former default and is retained
   to quantify how much faster-reporting assumptions change visibility.
 - Reporting completeness: Beta(2, 2), weakly-informative, centered at 0.5.
   A Beta-Binomial suspect-queue positivity is computed against confirmed and
   the operational suspected-active pool when present, but it is held at
   DATA_TERM_WEIGHT 0.0 and never enters the completeness blend (see the method
   note in nowcast()).

Scope acknowledgment (2026-05-23):
 Rosello 2015 is species-matched to BDBV and measures the right event pair
 (symptom onset to reporting), but it is a small single prior outbreak from
 Isiro 2012. The model therefore treats it as a graded prior for public
 visibility analysis, not as a measured 2026 reporting-delay distribution.

Method:
 - Reporting completeness at as_of t is approximated by the gamma CDF
   evaluated at "elapsed days since latest event" with the onset-to-
   notification gamma. Intervals from Monte Carlo samples of the shape
   parameter (alpha_sigma = max(0.10, 12% of the selected prior alpha)).
 - Publication latency intervals from samples of the same gamma.
 - Confirmation backlog intervals from (operational suspected-active minus
   confirmed) reconciled counts when an operational suspected-active pool is
   present, propagating the reconciled-count interval; absent that pool the
   backlog is reported as a degenerate zero interval.

Stdlib only. Deterministic when seeded.
"""
from __future__ import annotations

import dataclasses
import datetime
import math
import random
from typing import Any

from lovs import lovs_reconciler


MODEL_VERSION = "lovs_visibility-v0.3.0"

# Default onset-to-notification delay distribution (shape-rate gamma).
# Source: Rosello A, et al. eLife 2015 Table 5, DRC Isiro 2012 BDBV
# onset-to-reporting fit (n=52), corroborated by WHO grEPI. The parameter
# library records this as derived_supported and caps it because it is a
# prior-outbreak historical estimate, not a fitted 2026 delay.
ROSELLO_BDBV_DELAY_GAMMA = (1.1345, 0.1285)
ROSELLO_BDBV_DELAY_MEAN_SD = (8.83, 8.29)
ROSELLO_BDBV_DELAY_LABEL = "Rosello 2015 BDBV Isiro onset-to-notification"
ROSELLO_BDBV_DELAY_EVIDENCE_CHAIN_ID = "ec:lovs:grepi:reporting-delay-update:2026-05-23"

# Former default, retained as a named sensitivity comparator.
CAMACHO_EBOV_ZAIRE_DELAY_GAMMA = (0.81, 0.18)
CAMACHO_EBOV_ZAIRE_DELAY_MEAN_SD = (4.5, 5.0)
CAMACHO_EBOV_ZAIRE_DELAY_LABEL = "Camacho 2015 EBOV-Zaire onset-to-notification sensitivity"
CAMACHO_EBOV_ZAIRE_DELAY_EVIDENCE_CHAIN_ID = "ec:lovs:module-c:reporting-delay-priors:2026-05-20"

# Backward-compatible name consumed by older checks.
TOTAL_DELAY_GAMMA = ROSELLO_BDBV_DELAY_GAMMA
TOTAL_DELAY_LABEL = ROSELLO_BDBV_DELAY_LABEL
TOTAL_DELAY_EVIDENCE_CHAIN_ID = ROSELLO_BDBV_DELAY_EVIDENCE_CHAIN_ID
SENSITIVITY_DELAY_GAMMAS = {
    "camacho_ebov_zaire": CAMACHO_EBOV_ZAIRE_DELAY_GAMMA,
}
PRIOR_EVIDENCE_CHAIN_IDS: tuple[str, ...] = (
    ROSELLO_BDBV_DELAY_EVIDENCE_CHAIN_ID,
    CAMACHO_EBOV_ZAIRE_DELAY_EVIDENCE_CHAIN_ID,
)

# Reporting-completeness prior (Beta), weakly-informative, centered at 0.5.
REPORTING_COMPLETENESS_PRIOR_BETA = (2.0, 2.0)

# Citations carried through to the report.
PRIOR_CITATIONS: tuple[str, ...] = (
    "Rosello A, et al. eLife 2015 (10.7554/eLife.09015): "
    "BDBV DRC Isiro 2012 symptom-onset-to-reporting mean 8.83 d, "
    "s.d. 8.29 d (n=52); default prior for BDBV visibility analysis, "
    "not a fitted 2026 reporting-delay estimate.",
    "Camacho A, et al. PLOS Currents Outbreaks 2015 "
    "(10.1371/currents.outbreaks.406ae55e83ec0b5193e30856b9235ed2): "
    "EBOV-Zaire Sierra Leone 2014 onset-to-notification mean 4.5 d, "
    "s.d. 5 d; retained as faster-reporting sensitivity comparator.",
)


class VisibilityNowcastError(ValueError):
    """Raised when nowcast cannot proceed."""


@dataclasses.dataclass(frozen=True)
class IntervalProportion:
    lower_50: float
    upper_50: float
    lower_95: float
    upper_95: float


@dataclasses.dataclass(frozen=True)
class IntervalDays:
    lower_50: float
    upper_50: float
    lower_95: float
    upper_95: float


@dataclasses.dataclass(frozen=True)
class IntervalCount:
    lower_50: int
    upper_50: int
    lower_95: int
    upper_95: int


@dataclasses.dataclass(frozen=True)
class VisibilityPosterior:
    outbreak_id: str
    geography_id: str
    as_of: str
    visibility_grade: str
    reporting_completeness: IntervalProportion
    publication_latency_days: IntervalDays
    confirmation_backlog: IntervalCount
    uncertainty_drivers: tuple[str, ...]
    missing_data_requests: tuple[str, ...]
    priors_cited: tuple[str, ...]
    model_version: str
    provenance_ids: tuple[str, ...]
    status: str


def _gamma_pdf(x: float, alpha: float, beta: float) -> float:
    """Gamma pdf in shape-rate parameterization. Returns 0 for x <= 0."""
    if x <= 0:
        return 0.0
    log_pdf = alpha * math.log(beta) - math.lgamma(alpha) + (alpha - 1) * math.log(x) - beta * x
    return math.exp(log_pdf)


def _gamma_cdf(x: float, alpha: float, beta: float) -> float:
    """Gamma CDF in shape-rate parameterization. Uses the regularized lower
    incomplete gamma function P(α, βx) computed via series expansion plus
    continued fraction expansion (Numerical Recipes 6.2).
    """
    if x <= 0:
        return 0.0
    a = alpha
    z = beta * x
    if z < a + 1.0:
        # Series expansion
        ap = a
        cur = 1.0 / a
        total = cur
        for _ in range(200):
            ap += 1.0
            cur *= z / ap
            total += cur
            if abs(cur) < 1e-15 * abs(total):
                break
        return total * math.exp(-z + a * math.log(z) - math.lgamma(a))
    # Continued fraction (Lentz's method)
    fpmin = 1e-30
    b_cf = z + 1.0 - a
    c_cf = 1.0 / fpmin
    d_cf = 1.0 / b_cf
    h = d_cf
    for i in range(1, 200):
        an = -i * (i - a)
        b_cf += 2.0
        d_cf = an * d_cf + b_cf
        if abs(d_cf) < fpmin:
            d_cf = fpmin
        c_cf = b_cf + an / c_cf
        if abs(c_cf) < fpmin:
            c_cf = fpmin
        d_cf = 1.0 / d_cf
        delta = d_cf * c_cf
        h *= delta
        if abs(delta - 1.0) < 1e-15:
            break
    q = math.exp(-z + a * math.log(z) - math.lgamma(a)) * h
    return 1.0 - q


def _sample_gamma(rng: random.Random, alpha: float, beta: float) -> float:
    """Sample from gamma using random's gammavariate."""
    # random.gammavariate uses (alpha, beta) where beta is the SCALE not rate.
    # We use shape-rate; scale = 1/rate.
    return rng.gammavariate(alpha, 1.0 / beta)


def _quantile(samples: list[float], q: float) -> float:
    """Linear-interpolated quantile."""
    if not samples:
        return float("nan")
    s = sorted(samples)
    idx = q * (len(s) - 1)
    lo = int(math.floor(idx))
    hi = int(math.ceil(idx))
    if lo == hi:
        return s[lo]
    return s[lo] + (s[hi] - s[lo]) * (idx - lo)


def _interval_proportion(samples: list[float]) -> IntervalProportion:
    return IntervalProportion(
        lower_50=_quantile(samples, 0.25),
        upper_50=_quantile(samples, 0.75),
        lower_95=_quantile(samples, 0.025),
        upper_95=_quantile(samples, 0.975),
    )


def _interval_days(samples: list[float]) -> IntervalDays:
    return IntervalDays(
        lower_50=_quantile(samples, 0.25),
        upper_50=_quantile(samples, 0.75),
        lower_95=_quantile(samples, 0.025),
        upper_95=_quantile(samples, 0.975),
    )


def _interval_count(samples: list[float]) -> IntervalCount:
    return IntervalCount(
        lower_50=int(round(_quantile(samples, 0.25))),
        upper_50=int(round(_quantile(samples, 0.75))),
        lower_95=int(round(_quantile(samples, 0.025))),
        upper_95=int(round(_quantile(samples, 0.975))),
    )


def _days_between(later: str, earlier: str) -> float:
    """Days between two ISO 8601 UTC timestamps."""
    later_dt = datetime.datetime.fromisoformat(later.replace("Z", "+00:00"))
    earlier_dt = datetime.datetime.fromisoformat(earlier.replace("Z", "+00:00"))
    return (later_dt - earlier_dt).total_seconds() / 86400.0


def _classify_visibility_grade(completeness: IntervalProportion) -> str:
    midpoint = (completeness.lower_50 + completeness.upper_50) / 2.0
    if midpoint >= 0.85:
        return "high"
    if midpoint >= 0.65:
        return "moderate"
    if midpoint >= 0.40:
        return "low"
    if midpoint >= 0.0:
        return "very_low"
    return "unknown"


def _uncertainty_drivers(
    snapshot: lovs_reconciler.OutbreakSnapshot,
    history_count: int,
) -> tuple[str, ...]:
    drivers: list[str] = []
    if snapshot.source_conflict_notes:
        drivers.append(
            f"{len(snapshot.source_conflict_notes)} active source-conflict note(s) "
            f"surfaced by Module B reconciler"
        )
    if snapshot.deaths_to_confirmed_tension_flag:
        drivers.append(
            "deaths-to-confirmed tension flag: reported deaths exceed the "
            "expected case fatality ratio against confirmed cases"
        )
    if history_count < 2:
        drivers.append(
            "single as-of snapshot in window; backlog inference relies on "
            "prior delay distributions rather than observed cadence"
        )
    if snapshot.case_definition_version is None:
        drivers.append(
            "case-definition version not declared by sources; comparability "
            "across the as-of window cannot be confirmed"
        )
    if not drivers:
        drivers.append("no specific driver flagged; uncertainty bounded by prior delay distribution")
    return tuple(drivers)


def _get_suspected_count(
    reported_counts: dict[str, "lovs_reconciler.ReconciledCount"],
) -> "lovs_reconciler.ReconciledCount | None":
    """Resolve the operational suspected-active ReconciledCount, if present.

    The cumulative suspected tier was retired 2026-06-02, so the legacy
    `suspected` and `suspected_cumulative` keys are no longer reconciled onto the
    cumulative surface. Only the operational point-prevalence pool
    (`suspected_active`, the count under investigation plus in isolation) may
    still appear on a snapshot. This count feeds nothing into reporting
    completeness (DATA_TERM_WEIGHT is 0.0); it is used only for the clearly-
    labeled suspect-queue-positivity diagnostic and the confirmation-backlog
    interval.

    Returns None when no operational suspected-active field is present, which is
    the common case and degrades the diagnostic and backlog gracefully.
    """
    return reported_counts.get("suspected_active")


def _missing_data_requests(
    snapshot: lovs_reconciler.OutbreakSnapshot,
    completeness: IntervalProportion,
) -> tuple[str, ...]:
    requests: list[str] = []
    if completeness.upper_50 < 0.65:
        requests.append("daily reporting cadence by health zone (current cadence is sparse)")
    suspected = _get_suspected_count(snapshot.reported_counts)
    confirmed = snapshot.reported_counts.get("confirmed")
    if suspected is not None and confirmed is not None:
        s = suspected.primary_value
        c = confirmed.primary_value
        if s > 0 and c < s * 0.5:
            requests.append("laboratory confirmation cadence per health zone (suspected-to-confirmed ratio is high)")
    if snapshot.source_conflict_notes:
        requests.append("source-of-truth reconciliation between national MoH and regional WHO/Africa CDC bulletins")
    if snapshot.case_definition_version is None:
        requests.append("explicit case-definition version declaration on each public bulletin")
    if not requests:
        requests.append("no acute data gap; continue current cadence")
    return tuple(requests)


def nowcast(
    snapshot: lovs_reconciler.OutbreakSnapshot,
    history: tuple[lovs_reconciler.OutbreakSnapshot, ...] = (),
    n_samples: int = 1000,
    seed: int | None = None,
) -> VisibilityPosterior:
    """Compute a visibility posterior for a reconciled outbreak snapshot.

    Determinism: if `seed` is None, a deterministic seed is derived from
    the content hash of `snapshot`. Otherwise the supplied seed is used.
    """
    if seed is None:
        seed = lovs_reconciler.snapshot_content_seed(snapshot)
    rng = random.Random(seed)

    confirmed = snapshot.reported_counts.get("confirmed")
    suspected = _get_suspected_count(snapshot.reported_counts)

    # Days-since-latest-event: distance from earliest snapshot's as_of to
    # current. If no history, the current as_of is treated as ~7 days past the
    # earliest observation (a conservative default).
    if history:
        earliest_as_of = min(h.as_of for h in history)
        days_since_earliest = max(0.5, _days_between(snapshot.as_of, earliest_as_of))
    else:
        days_since_earliest = 7.0

    # Sample from onset-to-notification gamma to get a distribution of
    # "fraction reported by elapsed days". We sample alpha around the prior
    # to propagate parameter uncertainty, preserving the original module's
    # approximate 12% relative alpha uncertainty with a 0.10 minimum.
    alpha_total, beta_total = TOTAL_DELAY_GAMMA
    alpha_sigma = max(0.10, alpha_total * 0.12)
    completeness_samples: list[float] = []
    latency_samples: list[float] = []
    for _ in range(n_samples):
        a = max(0.1, rng.gauss(alpha_total, alpha_sigma))
        b = beta_total
        comp = _gamma_cdf(days_since_earliest, a, b)
        completeness_samples.append(comp)
        latency_samples.append(_sample_gamma(rng, a, b))

    # Reporting completeness is the reporting-DELAY nowcast only: the gamma-CDF
    # "fraction occurred-and-reported by the elapsed time" computed above. The
    # confirmed-to-suspected ratio is deliberately NOT mixed in.
    #
    # Why (validated 2026-06-01, .process/2026-06-01-method-validation, 3/3
    # adversarial lenses): confirmed over the suspect pool is a lab-positivity /
    # positive-predictive-value quantity (the fraction of the clinical suspect
    # queue that PCR-confirms), NOT case ascertainment (the fraction of true
    # community infections detected). The two are orthogonal corrections: the
    # suspect case definition nets non-Ebola febrile illness, so the ratio
    # measures case-definition specificity, not missed cases (cholera PPV
    # meta-analysis PMC10538743; the canonical Ebola ascertainment estimator
    # anchors on CFR, not suspected counts, per EpiVerse
    # cfr::estimate_ascertainment). Mixing it in made completeness move with
    # INRB's administrative suspect-pool churn by construction and in the wrong
    # direction: a cleaner suspect list mechanically raised the ratio and made
    # visibility look better while the queue was merely worked down. That churn
    # is exactly why the cumulative suspected tier was retired 2026-06-02; the
    # only suspect-pool figure that may still reach here is the operational
    # point-prevalence active pool, which is no more admissible as ascertainment
    # than the cumulative one was. The CFR-anchored deaths back-projection
    # (lovs_death_back_projection) remains the separate, suspect-pool-free
    # latent-total cross-check.
    #
    # DATA_TERM_WEIGHT is the explicit knob: 0.0 = delay-only (grounded default).
    # The Beta-Binomial posterior is still computed so the suspect-queue
    # positivity is available as a clearly-labeled diagnostic downstream, but it
    # never re-enters the completeness blend and must never be labeled
    # ascertainment.
    DATA_TERM_WEIGHT = 0.0
    prior_alpha, prior_beta = REPORTING_COMPLETENESS_PRIOR_BETA
    suspect_queue_positivity: float | None = None
    if confirmed is not None and suspected is not None:
        observed = confirmed.primary_value
        total = max(suspected.primary_value, observed + 1)
        unreported = max(0, total - observed)
        post_alpha = prior_alpha + observed
        post_beta = prior_beta + unreported
        suspect_queue_positivity = post_alpha / (post_alpha + post_beta)
        if DATA_TERM_WEIGHT > 0.0:
            completeness_samples = [
                (1.0 - DATA_TERM_WEIGHT) * s + DATA_TERM_WEIGHT * suspect_queue_positivity
                for s in completeness_samples
            ]

    reporting_completeness = _interval_proportion(completeness_samples)
    publication_latency = _interval_days(latency_samples)

    # Confirmation backlog: operational suspect-active minus confirmed,
    # propagating reconciled-count intervals, computed only when an operational
    # suspect-active pool is present (the cumulative suspected tier was retired
    # 2026-06-02). When the upstream reconciler emits a degenerate interval for
    # either input (suspected.minimum == suspected.maximum AND confirmed.minimum
    # == confirmed.maximum), as is the case for early WHO DON snapshots that
    # report point-estimate case counts only, the Monte Carlo draws collapse to
    # the same value and the resulting IntervalCount has lower_50 == upper_50.
    # This is expected (not a bug), and signals "no uncertainty in the public
    # picture's count fields" rather than "no backlog." Absent the operational
    # pool the backlog is a degenerate zero interval.
    if suspected is not None and confirmed is not None:
        backlog_samples: list[float] = []
        for _ in range(n_samples):
            s_val = rng.uniform(suspected.minimum, suspected.maximum)
            c_val = rng.uniform(confirmed.minimum, confirmed.maximum)
            backlog_samples.append(max(0.0, s_val - c_val))
        confirmation_backlog = _interval_count(backlog_samples)
    else:
        confirmation_backlog = IntervalCount(0, 0, 0, 0)

    visibility_grade = _classify_visibility_grade(reporting_completeness)
    drivers = _uncertainty_drivers(snapshot, history_count=len(history))
    missing = _missing_data_requests(snapshot, reporting_completeness)

    return VisibilityPosterior(
        outbreak_id=snapshot.outbreak_id,
        geography_id=snapshot.affected_zones[0] if snapshot.affected_zones else "unknown",
        as_of=snapshot.as_of,
        visibility_grade=visibility_grade,
        reporting_completeness=reporting_completeness,
        publication_latency_days=publication_latency,
        confirmation_backlog=confirmation_backlog,
        uncertainty_drivers=drivers,
        missing_data_requests=missing,
        priors_cited=PRIOR_CITATIONS,
        model_version=MODEL_VERSION,
        provenance_ids=snapshot.sources,
        status="provisional",
    )
