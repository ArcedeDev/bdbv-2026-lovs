"""LOVS Module C: visibility nowcast.

Produces a typed `VisibilityPosterior` from an OutbreakSnapshot plus an
optional series of prior OutbreakSnapshots. Output intervals are calibrated
from a peer-reviewed onset-to-notification delay distribution. The product
framing is descriptive-not-predictive: this module estimates the visibility
gap (reporting completeness, publication latency, confirmation backlog),
not hidden case counts.

Prior (single onset-to-notification delay, EBOV-Zaire empirical):
 - Onset-to-notification gamma, method-of-moments matched to mean 4.5 days,
   s.d. 5 days; therefore α = mean^2/var = 0.81, β = mean/var = 0.18 in
   shape-rate parameterization. Source: Camacho A, et al. PLOS Currents
   Outbreaks 2015 (10.1371/currents.outbreaks.406ae55e83ec0b5193e30856b9235ed2),
   Appendix 1 Model and parameters: "the time from onset to notification of
   EVD cases is over-dispersed, with a mean of 4.5 days and a standard
   deviation (s.d.) of 5 days." Camacho's own dynamic model uses an
   over-dispersed delay with average 4.3 days (Methods).
 - Reporting completeness: Beta(2, 2), weakly-informative, centered at 0.5.
   Beta-Binomial update against (confirmed, suspected) counts from the
   reconciled snapshot.

Cross-species acknowledgment (2026-05-20):
 Camacho 2015's reported delay describes EBOV-Zaire reporting in Sierra
 Leone 2014. No Bundibugyo-virus-specific onset-to-notification delay
 distribution is available in the literature accessible to this module
 (Wamala 2010 on the BDBV discovery outbreak does not report one). The
 model applies the EBOV-Zaire distribution as the best available proxy
 and surfaces this through `priors_cited`.

Method:
 - Reporting completeness at as_of t is approximated by the gamma CDF
   evaluated at "elapsed days since latest event" with the onset-to-
   notification gamma. Intervals from Monte Carlo samples of the shape
   parameter (alpha_sigma = 0.10 around alpha = 0.81).
 - Publication latency intervals from samples of the same gamma.
 - Confirmation backlog intervals from (suspected - confirmed) reconciled
   counts, propagating the reconciled-count interval.

Stdlib only. Deterministic when seeded.
"""
from __future__ import annotations

import dataclasses
import datetime
import math
import random
from typing import Any

from lovs import lovs_reconciler


MODEL_VERSION = "lovs_visibility-v0.2.0"

# Onset-to-notification delay distribution (shape-rate gamma).
# Source: Camacho A, et al. PLOS Currents Outbreaks 2015 reports the
# empirical EBOV-Zaire delay as over-dispersed with mean 4.5 d, s.d. 5 d.
# Method-of-moments gives alpha = 0.81, beta = 0.18.
# Mean = alpha/beta = 4.5; variance = alpha/beta^2 = 25; s.d. = 5.
TOTAL_DELAY_GAMMA = (0.81, 0.18)

# Reporting-completeness prior (Beta), weakly-informative, centered at 0.5.
REPORTING_COMPLETENESS_PRIOR_BETA = (2.0, 2.0)

# Citations carried through to the report. The single primary source for
# the onset-to-notification gamma is Camacho 2015 (EBOV-Zaire empirical
# distribution; applied cross-species to BDBV as the best available proxy,
# see module docstring).
PRIOR_CITATIONS: tuple[str, ...] = (
    "Camacho A, et al. PLOS Currents Outbreaks 2015 "
    "(10.1371/currents.outbreaks.406ae55e83ec0b5193e30856b9235ed2): "
    "onset-to-notification mean 4.5 d, s.d. 5 d (EBOV-Zaire, Sierra Leone 2014); "
    "applied cross-species to BDBV as best available proxy.",
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


def _missing_data_requests(
    snapshot: lovs_reconciler.OutbreakSnapshot,
    completeness: IntervalProportion,
) -> tuple[str, ...]:
    requests: list[str] = []
    if completeness.upper_50 < 0.65:
        requests.append("daily reporting cadence by health zone (current cadence is sparse)")
    if "suspected" in snapshot.reported_counts and "confirmed" in snapshot.reported_counts:
        s = snapshot.reported_counts["suspected"].primary_value
        c = snapshot.reported_counts["confirmed"].primary_value
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
    suspected = snapshot.reported_counts.get("suspected")

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
    # to propagate parameter uncertainty (alpha_sigma scaled to roughly 12%
    # of alpha = 0.81, matching the original module's relative uncertainty
    # at the previous prior).
    alpha_total, beta_total = TOTAL_DELAY_GAMMA
    alpha_sigma = 0.10
    completeness_samples: list[float] = []
    latency_samples: list[float] = []
    for _ in range(n_samples):
        a = max(0.1, rng.gauss(alpha_total, alpha_sigma))
        b = beta_total
        comp = _gamma_cdf(days_since_earliest, a, b)
        completeness_samples.append(comp)
        latency_samples.append(_sample_gamma(rng, a, b))

    # Beta-Binomial update of completeness using observed report count as
    # a noisy data signal. We treat the prior interval midpoint as the
    # expected, and Beta(2, 2) updated with (observed, expected - observed).
    prior_alpha, prior_beta = REPORTING_COMPLETENESS_PRIOR_BETA
    if confirmed is not None and suspected is not None:
        observed = confirmed.primary_value
        total = max(suspected.primary_value, observed + 1)
        unreported = max(0, total - observed)
        post_alpha = prior_alpha + observed
        post_beta = prior_beta + unreported
        # Update samples by mixing with the data-driven posterior mean.
        data_mean = post_alpha / (post_alpha + post_beta)
        completeness_samples = [
            (3.0 * s + data_mean) / 4.0 for s in completeness_samples
        ]

    reporting_completeness = _interval_proportion(completeness_samples)
    publication_latency = _interval_days(latency_samples)

    # Confirmation backlog: suspected - confirmed, propagating reconciled-count
    # intervals. When the upstream reconciler emits a degenerate interval for
    # either input (suspected.minimum == suspected.maximum AND confirmed.minimum
    # == confirmed.maximum), as is the case for early WHO DON snapshots that
    # report point-estimate case counts only, the Monte Carlo draws collapse to
    # the same value and the resulting IntervalCount has lower_50 == upper_50.
    # This is expected (not a bug), and signals "no uncertainty in the public
    # picture's count fields" rather than "no backlog."
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
