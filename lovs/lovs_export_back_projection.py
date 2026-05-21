"""LOVS export-back-projection (Imperial Method 1, after Imai et al. 2020).

Imperial College MRC GIDA's 18 May 2026 report implements an
Imai-et-al.-2020-style export-back-projection for BDBV:

    total_cases = cases_detected_overseas / p(detected_overseas)

where p(detected_overseas) is the joint probability that a case in the
source population (1) travels to the target country during the detection
window and (2) is detected after arrival:

    p(detected_overseas) = daily_p(travel_to_target)  *  mean_detection_window
    daily_p(travel_to_target) = daily_outbound_travelers / source_population

The Imai-2020 method was originally developed for the early-2020 Wuhan
outbreak using the same export-population-ratio inference.

Cite: Imai et al. 2020, Imperial College COVID-19 Response Team Report 1
(https://www.imperial.ac.uk/media/imperial-college/medicine/mrc-gida/2020-01-17-COVID19-Report-1.pdf).
The 2026 BDBV PoE traveler counts used by this repository are
source-attributed factual values reported by Imperial from WHO sitreps for
epiweeks 10, 11, 15, 18 of 2026. They are not relicensed here.

Confidence intervals here use a Poisson approximation to the
negative-binomial likelihood for the observed export count. This is a
known simplification: the exact negative-binomial CI would require
scipy. For small detection probabilities (p_detected << 1) the Poisson
approximation is the limiting case and is accurate to within rounding.

Stdlib only.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


MODEL_VERSION = "lovs_export_back_projection-v0.1.0"


@dataclass(frozen=True)
class ExportProjection:
    point_estimate: int
    ci_low_95: int
    ci_high_95: int
    daily_p_travel: float
    p_detected: float
    method_note: str


def _normal_inverse_cdf(p: float) -> float:
    """Beasley-Springer-Moro inverse standard-normal CDF.

    Stdlib-friendly, accurate to ~1e-9 for p in (0.001, 0.999).
    """
    if p <= 0.0 or p >= 1.0:
        raise ValueError(f"_normal_inverse_cdf requires p in (0, 1), got {p}")
    a = (
        -3.969683028665376e+01,
        2.209460984245205e+02,
        -2.759285104469687e+02,
        1.383577518672690e+02,
        -3.066479806614716e+01,
        2.506628277459239e+00,
    )
    b = (
        -5.447609879822406e+01,
        1.615858368580409e+02,
        -1.556989798598866e+02,
        6.680131188771972e+01,
        -1.328068155288572e+01,
    )
    c = (
        -7.784894002430293e-03,
        -3.223964580411365e-01,
        -2.400758277161838e+00,
        -2.549732539343734e+00,
        4.374664141464968e+00,
        2.938163982698783e+00,
    )
    d = (
        7.784695709041462e-03,
        3.224671290700398e-01,
        2.445134137142996e+00,
        3.754408661907416e+00,
    )
    p_low = 0.02425
    p_high = 1.0 - p_low
    if p < p_low:
        q = math.sqrt(-2.0 * math.log(p))
        num = ((((c[0]*q + c[1])*q + c[2])*q + c[3])*q + c[4])*q + c[5]
        den = (((d[0]*q + d[1])*q + d[2])*q + d[3])*q + 1.0
        return num / den
    if p <= p_high:
        q = p - 0.5
        r = q * q
        num = ((((a[0]*r + a[1])*r + a[2])*r + a[3])*r + a[4])*r + a[5]
        den = ((((b[0]*r + b[1])*r + b[2])*r + b[3])*r + b[4])*r + 1.0
        return q * num / den
    q = math.sqrt(-2.0 * math.log(1.0 - p))
    num = ((((c[0]*q + c[1])*q + c[2])*q + c[3])*q + c[4])*q + c[5]
    den = (((d[0]*q + d[1])*q + d[2])*q + d[3])*q + 1.0
    return -num / den


def _poisson_ci_95(observed: int) -> tuple[float, float]:
    """Exact-ish Poisson 95% CI via chi-square quantile inversion.

    Lower bound: chi2(0.025, 2k) / 2 with k = observed.
    Upper bound: chi2(0.975, 2(k+1)) / 2.

    chi-square quantile via Wilson-Hilferty approximation. Stdlib-only.
    Accurate to ~0.1 for k>=2; for k=0 lower bound is 0 exactly.
    """
    if observed < 0:
        raise ValueError(f"_poisson_ci_95 requires observed>=0, got {observed}")

    def chi2_quantile(p_quantile: float, df: int) -> float:
        if df <= 0:
            return 0.0
        z = _normal_inverse_cdf(p_quantile)
        h = 2.0 / (9.0 * df)
        return df * (1.0 - h + z * math.sqrt(h)) ** 3

    lower = 0.0 if observed == 0 else chi2_quantile(0.025, 2 * observed) / 2.0
    upper = chi2_quantile(0.975, 2 * (observed + 1)) / 2.0
    return (max(0.0, lower), upper)


def total_cases_from_exports(
    exports: int,
    source_population: int,
    daily_outbound_travelers: int,
    mean_detection_window_days: float,
) -> ExportProjection:
    """Imperial Method 1: project total cases from observed exports.

    Args:
        exports: number of BDBV cases detected in the target country.
        source_population: size of the source population.
        daily_outbound_travelers: mean daily outbound travelers from source to target.
        mean_detection_window_days: approximate (incubation + onset-to-detection) window.

    Returns:
        ExportProjection with point estimate and 95% CI (via Poisson approx).
    """
    if (
        exports < 0
        or source_population <= 0
        or daily_outbound_travelers < 0
        or mean_detection_window_days <= 0
    ):
        raise ValueError(
            f"total_cases_from_exports invalid input: exports={exports}, "
            f"pop={source_population}, travelers={daily_outbound_travelers}, "
            f"window={mean_detection_window_days}"
        )
    daily_p_travel = daily_outbound_travelers / source_population
    p_detected = daily_p_travel * mean_detection_window_days
    if p_detected <= 0 or p_detected > 1:
        raise ValueError(
            "Computed p_detected must be in (0, 1]; cannot back-project from "
            f"p_detected={p_detected:.6g}."
        )
    if exports == 0:
        _, ci_high_count = _poisson_ci_95(0)
        return ExportProjection(
            point_estimate=0,
            ci_low_95=0,
            ci_high_95=int(round(ci_high_count / p_detected)),
            daily_p_travel=daily_p_travel,
            p_detected=p_detected,
            method_note="Poisson CI approximation; 0 exports gives 0 point estimate.",
        )
    point = exports / p_detected
    ci_low_count, ci_high_count = _poisson_ci_95(exports)
    return ExportProjection(
        point_estimate=int(round(point)),
        ci_low_95=int(round(ci_low_count / p_detected)),
        ci_high_95=int(round(ci_high_count / p_detected)),
        daily_p_travel=daily_p_travel,
        p_detected=p_detected,
        method_note="Poisson CI approximation; exact NB CI would require scipy.",
    )
