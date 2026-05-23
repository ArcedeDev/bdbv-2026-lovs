"""LOVS onset-to-death gamma distribution (Bundibugyo-specific).

Rosello et al. 2015 eLife (10.7554/eLife.09015) fitted onset-to-death
gamma distributions per outbreak. The 2012 DRC Isiro outbreak (52 BDBV
cases) gives:

    mean  = 11.37 days
    sd    = 5.41 days
    shape (alpha) = 4.42
    rate  (beta)  = 0.388 per day

These parameters are reused by the Imperial College MRC GIDA 18 May 2026
report for the death-back-projection method (Method 2) applied to the
2026 DRC BDBV outbreak. LOVS adopts the same parameters for consistency
across the two methodology contributions.

Cite: Rosello A, et al. eLife 2015 (10.7554/eLife.09015), Table 5,
Isiro 2012 outbreak fit.

Stdlib only. Pure math.
"""
from __future__ import annotations

import dataclasses
import math


MODEL_VERSION = "lovs_onset_to_death-v0.1.0"


@dataclasses.dataclass(frozen=True)
class OnsetToDeathParams:
    """Gamma-distribution parameters for BDBV onset-to-death delay."""

    mean_days: float
    std_days: float
    alpha: float          # gamma shape
    beta_per_day: float   # gamma rate
    source_doi: str
    source_outbreak: str

    def variance_days(self) -> float:
        """Variance is alpha / beta^2 for gamma(alpha, beta) shape-rate form."""
        return self.alpha / (self.beta_per_day ** 2)


BDBV_ONSET_TO_DEATH = OnsetToDeathParams(
    mean_days=11.37,
    std_days=5.41,
    alpha=4.42,
    beta_per_day=0.388,
    source_doi="10.7554/eLife.09015",
    source_outbreak="2012 DRC Isiro outbreak (52 BDBV cases)",
)

# Resolvable URL and short label, exposed for the snapshot writer so public
# release artifacts can render a single source-of-truth methodology constants
# block.
BDBV_ONSET_TO_DEATH_URL = "https://doi.org/10.7554/eLife.09015"
BDBV_ONSET_TO_DEATH_SHORT = "Rosello 2015 eLife, Isiro 2012 fit"


def bdbv_onset_to_death_params() -> OnsetToDeathParams:
    """Return the BDBV-specific onset-to-death gamma parameters."""
    return BDBV_ONSET_TO_DEATH


def _lower_incomplete_gamma_regularized(alpha: float, x: float) -> float:
    """Regularized lower incomplete gamma P(alpha, x).

    Numerical Recipes section 6.2: series form for x < alpha + 1, otherwise
    continued-fraction form's complement. Stdlib-only.
    """
    if x < 0.0 or alpha <= 0.0:
        raise ValueError(
            f"_lower_incomplete_gamma_regularized requires x>=0 and alpha>0; "
            f"got x={x}, alpha={alpha}"
        )
    if x == 0.0:
        return 0.0
    if x < alpha + 1.0:
        term = 1.0 / alpha
        total = term
        for n in range(1, 1000):
            term *= x / (alpha + n)
            total += term
            if abs(term) < 1e-12 * abs(total):
                break
        prefix = math.exp(-x + alpha * math.log(x) - math.lgamma(alpha))
        return prefix * total
    # Continued fraction for upper, return 1 - upper
    b = x + 1.0 - alpha
    c = 1e30
    d = 1.0 / b
    h = d
    for n in range(1, 1000):
        an = -n * (n - alpha)
        b += 2.0
        d = an * d + b
        if abs(d) < 1e-30:
            d = 1e-30
        c = b + an / c
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-12:
            break
    prefix = math.exp(-x + alpha * math.log(x) - math.lgamma(alpha))
    upper = prefix * h
    return 1.0 - upper


def bdbv_onset_to_death_pdf(t: float) -> float:
    """Gamma PDF at t (days) for BDBV onset-to-death.

    f(t) = beta^alpha * t^(alpha-1) * exp(-beta*t) / Gamma(alpha)
    """
    if t < 0:
        return 0.0
    p = BDBV_ONSET_TO_DEATH
    alpha = p.alpha
    beta = p.beta_per_day
    if t == 0:
        return 0.0 if alpha > 1 else float("inf")
    log_pdf = (
        alpha * math.log(beta)
        + (alpha - 1) * math.log(t)
        - beta * t
        - math.lgamma(alpha)
    )
    return math.exp(log_pdf)


def bdbv_onset_to_death_cdf(t: float) -> float:
    """Gamma CDF at t (days). P(alpha, beta * t)."""
    if t < 0:
        return 0.0
    p = BDBV_ONSET_TO_DEATH
    return _lower_incomplete_gamma_regularized(p.alpha, p.beta_per_day * t)


def bdbv_onset_to_death_survival(t: float) -> float:
    """Survival 1 - CDF(t). Probability of dying after t days post onset."""
    return 1.0 - bdbv_onset_to_death_cdf(t)


def bdbv_onset_to_death_mean() -> float:
    """Distribution mean in days. Rosello Isiro 2012 fit: 11.37."""
    return BDBV_ONSET_TO_DEATH.mean_days


def bdbv_onset_to_death_variance() -> float:
    """Variance in days^2 = alpha / beta^2."""
    return BDBV_ONSET_TO_DEATH.variance_days()
