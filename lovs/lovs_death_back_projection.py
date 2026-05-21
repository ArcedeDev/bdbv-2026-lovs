"""LOVS death-back-projection (Imperial Method 2).

Implements the analytical formula from Imperial College MRC GIDA's
18 May 2026 report on BDBV in DRC (Appendix, Method 2; carried forward
unchanged in the 20 May 2026 update). Under simplifying
assumptions:
  - Outbreak seeded T days ago with a single zoonotic case;
  - Exponential growth at doubling time tau_2 (growth rate r = ln(2)/tau_2);
  - Onset-to-death delay is gamma(alpha, beta) per Rosello et al. 2015
    (Isiro 2012 BDBV outbreak; see lovs.lovs_onset_to_death).

The cumulative case count is back-projected from cumulative deaths as:

    C_T = D_T * (1 + r/beta)^alpha / CFR

The (1 + r/beta)^alpha term corrects for the growth-induced bias: in a
growing epidemic, a disproportionate share of current cases were infected
recently and have not yet died (or will not die). Without this correction,
naive D_T / CFR systematically under-states cumulative cases.

Cite the Imperial College MRC GIDA 20 May 2026 update as the current source
that publicly describes this analytical pathway (the pathway is unchanged
from the 18 May report). The implementation below is
original code and does not relicense Imperial report content; Rosello et al.
2015 eLife (https://doi.org/10.7554/eLife.09015) is the source for the BDBV
gamma fit.

Stdlib only.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable

from lovs.lovs_onset_to_death import BDBV_ONSET_TO_DEATH


MODEL_VERSION = "lovs_death_back_projection-v0.1.0"


def total_cases_from_deaths(
    deaths: int,
    cfr: float,
    doubling_time_days: float,
    alpha: float = BDBV_ONSET_TO_DEATH.alpha,
    beta_per_day: float = BDBV_ONSET_TO_DEATH.beta_per_day,
) -> int:
    """Imperial Method 2: project total cumulative cases from cumulative deaths.

    Args:
        deaths: cumulative reported BDBV deaths to date.
        cfr: assumed case-fatality ratio in (0, 1].
        doubling_time_days: epidemic doubling time in days.
        alpha: gamma shape for onset-to-death (default Rosello Isiro 2012).
        beta_per_day: gamma rate for onset-to-death (default Rosello Isiro 2012).

    Returns:
        Rounded integer estimate of cumulative cases.
    """
    if (
        deaths < 0
        or cfr <= 0
        or cfr > 1
        or doubling_time_days <= 0
        or alpha <= 0
        or beta_per_day <= 0
    ):
        raise ValueError(
            f"total_cases_from_deaths invalid input: deaths={deaths}, cfr={cfr}, "
            f"doubling={doubling_time_days}, alpha={alpha}, beta={beta_per_day}"
        )
    r = math.log(2.0) / doubling_time_days
    growth_correction = (1.0 + r / beta_per_day) ** alpha
    cases = deaths * growth_correction / cfr
    return int(round(cases))


@dataclass(frozen=True)
class SensitivityCell:
    cfr: float
    doubling_time_days: float
    total_cases: int


def sensitivity_grid(
    deaths: int,
    cfrs: Iterable[float],
    doubling_times: Iterable[float],
    alpha: float = BDBV_ONSET_TO_DEATH.alpha,
    beta_per_day: float = BDBV_ONSET_TO_DEATH.beta_per_day,
) -> list[SensitivityCell]:
    """Compute the (CFR x doubling-time) grid of total-case estimates.

    Returns one SensitivityCell per (cfr, doubling) pair. Mirrors the
    Imperial Table 2 grid layout (3 doubling times x 3 CFRs by default).
    """
    cells: list[SensitivityCell] = []
    for cfr in cfrs:
        for dt in doubling_times:
            cells.append(
                SensitivityCell(
                    cfr=cfr,
                    doubling_time_days=dt,
                    total_cases=total_cases_from_deaths(
                        deaths, cfr, dt, alpha, beta_per_day
                    ),
                )
            )
    return cells


# Imperial 20 May 2026 update central scenario: tau_2 = 14 days, CFR = 33%.
# No published BDBV-specific doubling time exists; the 14-day central is
# anchored to the 2014 West Africa NEJM range (Guinea 15.7 d, Liberia
# 23.6 d, Sierra Leone 30.2 d) on the fast side.
#
# CFR scenario set (26%, 33%, 40%): the central 33% is the point CFR across
# the US CDC two-prior-Bundibugyo-outbreak aggregate (55 deaths / 169 cases
# = 32.5%); the 26% and 40% bounds are the Wilson 95% CI of that proportion
# (verified [25.9%, 39.9%] -> 26% / 40%). The Imperial 20 May 2026 update
# corrected the bounds to these values from the 18 May 24% / 30% / 40%,
# whose 30% central did not match the 55/169 = 32.5% point estimate. See
# CDC_BVD_HISTORY_URL.
CENTRAL_DOUBLING_TIME_DAYS = 14.0
CENTRAL_CFR = 0.33
IMPERIAL_DOUBLING_TIMES_DAYS: tuple[float, ...] = (7.0, 14.0, 21.0)
IMPERIAL_CFR_SCENARIOS: tuple[float, ...] = (0.26, 0.33, 0.40)

# Joint WHO + Imperial College MRC GIDA 20 May 2026 update total-case reference
# range. Their two independent methods (population-movement extrapolation and
# deaths-back-projection) yield approximately 400 to 900 total cases in DRC,
# with values over 1,000 not excluded. Used as a horizontal reference on the
# website's inferred-trajectory chart.
IMPERIAL_REFERENCE_LOW = 400
IMPERIAL_REFERENCE_HIGH = 900
IMPERIAL_REFERENCE_AS_OF = "2026-05-20"
IMPERIAL_REFERENCE_URL = (
    "https://www.imperial.ac.uk/mrc-global-infectious-disease-analysis/"
    "research-themes/preparedness-and-response-to-emerging-threats/"
    "report-ebola-update-20-05-2026/"
)
IMPERIAL_REFERENCE_SHORT = "Imperial College MRC GIDA, 20 May 2026"

# US CDC BVD outbreak history aggregate (55 deaths / 169 cases across the
# 2007-08 Uganda and 2012 DRC outbreaks) grounds the CFR scenario set:
# 55/169 = 32.5% central (about 33%), Wilson 95% CI [26%, 40%]. Same source
# Imperial cited in their 20 May 2026 update.
CDC_BVD_HISTORY_URL = "https://www.cdc.gov/ebola/outbreaks/index.html"
CDC_BVD_HISTORY_SHORT = "US CDC outbreak history, 55/169 aggregate"


@dataclass(frozen=True)
class MarginalizedEstimate:
    """Mean and central-50%-interval over a uniform doubling-time prior.

    Returned by marginalized_total_cases. The mean integrates over the
    discrete doubling-time prior at fixed CFR; the central-50% interval
    is the (25th, 75th) percentile of the same discrete prior.
    """

    cfr: float
    doubling_times_days: tuple[float, ...]
    weights: tuple[float, ...]
    mean_cases: float
    q25_cases: float
    q75_cases: float
    per_scenario_cases: tuple[int, ...]


def _weighted_quantile(
    values: list[int], weights: list[float], q: float
) -> float:
    """Weighted quantile (linear interp) of values under discrete weights."""
    if not values:
        raise ValueError("_weighted_quantile requires non-empty values")
    pairs = sorted(zip(values, weights))
    sorted_values = [v for v, _ in pairs]
    sorted_weights = [w for _, w in pairs]
    total = sum(sorted_weights)
    if total <= 0:
        raise ValueError("_weighted_quantile requires positive total weight")
    target = q * total
    cumulative = 0.0
    for i, w in enumerate(sorted_weights):
        cumulative += w
        if cumulative >= target:
            return float(sorted_values[i])
    return float(sorted_values[-1])


def marginalized_total_cases(
    deaths: int,
    cfr: float = CENTRAL_CFR,
    doubling_times_days: Iterable[float] = IMPERIAL_DOUBLING_TIMES_DAYS,
    weights: Iterable[float] | None = None,
    alpha: float = BDBV_ONSET_TO_DEATH.alpha,
    beta_per_day: float = BDBV_ONSET_TO_DEATH.beta_per_day,
) -> MarginalizedEstimate:
    """Total cases marginalized over a discrete doubling-time prior.

    For a fixed CFR, integrates the deaths-back-projection over a discrete
    prior on the doubling time. The default prior is uniform over the
    Imperial three-scenario set {7, 14, 21} days, which is the only
    defensible prior given that NO BDBV-specific doubling-time estimate
    exists in the published literature (verified against Rosello et al.
    2015 eLife, 2026-05-20). Pass an explicit `weights` argument to express a non-
    uniform prior (e.g. heavier weight on the 14-day or 21-day scenarios
    to track the 2014 West Africa NEJM range for Zaire-species
    filoviruses).

    Args:
        deaths: cumulative reported BDBV deaths to date.
        cfr: assumed case-fatality ratio in (0, 1].
        doubling_times_days: discrete support of the doubling-time prior.
        weights: prior weights aligned with doubling_times_days; defaults
            to uniform if None. Need not sum to one; normalized internally.
        alpha, beta_per_day: gamma onset-to-death parameters.

    Returns:
        MarginalizedEstimate with the prior mean, the (25th, 75th)
        percentile interval under the prior, and the per-scenario case
        counts so the caller can inspect the underlying support.
    """
    dts = tuple(float(d) for d in doubling_times_days)
    if not dts:
        raise ValueError("marginalized_total_cases requires >=1 doubling time")
    if weights is None:
        ws = tuple(1.0 / len(dts) for _ in dts)
    else:
        raw = tuple(float(w) for w in weights)
        if len(raw) != len(dts):
            raise ValueError(
                f"marginalized_total_cases: weights length {len(raw)} does "
                f"not match doubling-times length {len(dts)}"
            )
        if any(w < 0 for w in raw):
            raise ValueError("marginalized_total_cases: negative weight")
        total = sum(raw)
        if total <= 0:
            raise ValueError("marginalized_total_cases: zero total weight")
        ws = tuple(w / total for w in raw)
    per_scenario = tuple(
        total_cases_from_deaths(deaths, cfr, dt, alpha, beta_per_day)
        for dt in dts
    )
    mean_cases = sum(w * v for w, v in zip(ws, per_scenario))
    q25 = _weighted_quantile(list(per_scenario), list(ws), 0.25)
    q75 = _weighted_quantile(list(per_scenario), list(ws), 0.75)
    return MarginalizedEstimate(
        cfr=cfr,
        doubling_times_days=dts,
        weights=ws,
        mean_cases=mean_cases,
        q25_cases=q25,
        q75_cases=q75,
        per_scenario_cases=per_scenario,
    )
