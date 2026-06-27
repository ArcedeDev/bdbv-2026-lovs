"""LOVS convergent-signal burden nowcast (true-burden level model + ascertainment + Module D).

Rebuilt 2026-06-09 from the documented equations after the original module was lost
with an ephemeral working tree (see specs/2026-06-09-june7-snapshot-delta-audit.md).

HEADLINE true burden (2026-06-22): the LOVS death-anchored LEVEL model
(true infections = confirmed x M_stock; validated
specs/2026-06-22-bdbv-true-burden-capacity-model.xlsx). This is the single source of
truth read by the website (chart/cards/map) and the Cloud Run evidence MCP, recomputed
every cycle from this cycle's confirmed count, so confirmed-count drift propagates the
true burden to every surface automatically. Imperial Method 2 (deaths back-projection)
is retained as an independent EXTERNAL cross-check under estimated_total_cases.cross_check,
no longer the headline. Case ascertainment + unreported are derived from the level central.

Pure stdlib and deterministic. Emits the snake_case shape that
apps/site/lib/scripts/sync-bdbv-lovs.py::_translate_convergence consumes. Wired into
refresh_pipeline.build_snapshot as output["convergence"] and emitted every cycle the
national contact axis is present, so a future regen cannot silently drop it again.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Any

# Convergence-specific priors. These are NOT in methodology_constants (which carries the
# shared CFR, onset-to-death gamma, and doubling time); they are cited per methodology row.
DEATH_ASCERTAINMENT_BAND = (0.696, 0.95)  # central = midpoint 0.823
SECONDARY_ATTACK_RATE = (0.03, 0.037, 0.09)  # low, spine (Mulongo 2025 BMC 3.7%), high

# LOVS death-anchored TRUE-BURDEN level multiplier M_stock (low, central, high). The
# validated "improved model" (specs/2026-06-22-bdbv-true-burden-capacity-model.xlsx,
# adversarial review 2026-06-21): true cumulative infections = confirmed * M_stock. The
# multiplier is death-anchored (fixed so confirmed_deaths * U / IFR reconciles to the same
# stock) and is the HEADLINE true burden; it replaces the prior ascertainment-from-Imperial
# central. A scenario band, not a confidence interval (the three detection views share a
# common p_reach cause). Keep in sync with the spreadsheet generator's Mstock inputs.
CASE_LEVEL_MULTIPLIER = (2.0, 2.5, 3.6)

# Shared CFR / onset-to-death gamma / doubling. Mirrors
# refresh_pipeline.build_methodology_constants() (which is nested inside build_snapshot
# and not reachable from main, where this block is assembled); kept here so the
# convergence block is self-contained and cannot be silently dropped. Update both if the
# published CFR / gamma / doubling are ever revised.
DEFAULT_METHODOLOGY_CONSTANTS = {
    "cfr": {"low_95": 0.26, "central": 0.33, "high_95": 0.4},
    "onset_to_death_gamma": {"alpha": 4.42, "beta_per_day": 0.388, "mean_days": 11.37},
    "central_doubling_time_days": 7.0,
    "observed_doubling_times_days": [5.0, 7.0, 11.0],
}


def _round(value: float) -> int:
    return int(round(value))


def _gammap(a: float, x: float) -> float:
    """Regularized lower incomplete gamma P(a, x) = Gamma(shape a, rate 1) CDF at x.

    Pure-stdlib Numerical Recipes ``gammp`` (series for x < a+1, continued fraction
    otherwise); matches scipy.stats.gamma.cdf to ~1e-15. Used for the onset-to-death
    CDF in the delay-adjusted CFR so the module stays stdlib-only and deterministic.
    """
    if x <= 0.0:
        return 0.0
    if x < a + 1.0:
        ap, term, total = a, 1.0 / a, 1.0 / a
        for _ in range(2000):
            ap += 1.0
            term *= x / ap
            total += term
            if abs(term) < abs(total) * 1e-15:
                break
        return total * math.exp(-x + a * math.log(x) - math.lgamma(a))
    fpmin = 1e-300
    b, c, d = x + 1.0 - a, 1.0 / fpmin, 1.0 / (x + 1.0 - a)
    h = d
    for i in range(1, 2000):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        if abs(d) < fpmin:
            d = fpmin
        c = b + an / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-15:
            break
    q = math.exp(-x + a * math.log(x) - math.lgamma(a)) * h
    return 1.0 - q


def delay_adjusted_cfr(
    confirmed_series: list[dict[str, Any]],
    confirmed_deaths: int,
    *,
    alpha: float,
    beta: float,
    as_of: str,
    mean_band_days: tuple[float, float] = (9.0, 14.0),
) -> dict[str, Any] | None:
    """Nishiura 2009 delay-adjusted confirmed CFR (eventual lethality among confirmed).

    Reweights the confirmed denominator by the fraction of each day's new cases that
    has already had time to die, using the cited onset-to-death gamma CDF:

        cCFR_adj = D / sum_t [ new_confirmed_t * F(T - t) ],  F = Gamma(alpha, beta) CDF

    where ``new_confirmed_t`` is the daily increment of the country-scope confirmed
    series and ``T`` is the snapshot date. This corrects the right-censoring that makes
    the crude deaths/confirmed ratio understate eventual lethality during ongoing
    accrual. A sensitivity band is produced by varying the onset-to-death mean over
    ``mean_band_days`` (a longer mean leaves fewer cases resolved -> higher cCFR).

    Returns None when the series cannot support the estimate (empty / no positive
    accrual), so the caller omits the block rather than fabricating it.
    """
    pts = sorted(
        (p for p in confirmed_series if p.get("date") and isinstance(p.get("value"), int)
         and not isinstance(p.get("value"), bool)),
        key=lambda p: str(p["date"])[:10],
    )
    if not pts:
        return None
    target = date.fromisoformat(as_of[:10])
    increments: list[tuple[int, int]] = []  # (days_before_T, new_confirmed)
    prev = 0
    final_cumulative = 0
    for p in pts:
        cum = int(p["value"])
        new = cum - prev
        prev = cum
        final_cumulative = cum
        if new <= 0:
            continue
        dt = (target - date.fromisoformat(str(p["date"])[:10])).days
        if dt < 0:
            continue
        increments.append((dt, new))
    if final_cumulative <= 0 or not increments:
        return None

    def _adjusted(mean_days: float) -> float:
        rate = alpha / mean_days
        denom = sum(new * _gammap(alpha, rate * dt) for dt, new in increments)
        return (confirmed_deaths / denom) if denom > 0 else 0.0

    mean_central = alpha / beta
    crude = confirmed_deaths / final_cumulative
    adj_central = _adjusted(mean_central)
    lo_mean, hi_mean = mean_band_days
    adj_low = _adjusted(lo_mean)   # shorter mean -> more resolved -> lower cCFR
    adj_high = _adjusted(hi_mean)  # longer mean -> fewer resolved -> higher cCFR
    denom_central = sum(new * _gammap(alpha, beta * dt) for dt, new in increments)

    def _pct(v: float) -> float:
        return round(v * 100.0, 1)

    return {
        "as_of": as_of,
        "scope": "country",
        "confirmed_cfr_crude_pct": _pct(crude),
        "confirmed_cfr_delay_adjusted_pct": {
            "central": _pct(adj_central),
            "low": _pct(adj_low),
            "high": _pct(adj_high),
        },
        "confirmed_deaths": confirmed_deaths,
        "confirmed_cumulative": final_cumulative,
        "resolved_denominator": _round(denom_central),
        "onset_to_death_mean_days": round(mean_central, 1),
        "onset_to_death_band_days": [int(lo_mean), int(hi_mean)],
        "method": (
            "Nishiura 2009 delay-adjusted confirmed CFR: confirmed deaths / "
            "sum_t(new confirmed_t * F(T - t)); F = onset-to-death gamma CDF"
        ),
        "provenance": "lovs",
        "sources": [
            "Nishiura et al. 2009 (early-epidemic CFR bias from onset-to-death/reporting delay)",
            "Rosello 2015 eLife onset-to-death gamma (alpha 4.42, beta 0.388/day, mean 11.4d)",
            "Independent corroboration: Epiforecasts BVDOutbreakSize delay-corrected confirmed CFR 41.3%",
        ],
    }


def build_convergence(
    *,
    as_of: str,
    confirmed: int,
    confirmed_deaths: int,
    contacts_under_follow_up: int,
    followup_coverage_pct: float,
    methodology_constants: dict[str, Any] | None = None,
    confirmed_series: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Compute the convergent-signal burden nowcast (snake_case, for the website sync).

    Equations:
      true infections  C   = confirmed * M_stock          (HEADLINE, LOVS level model)
      Imperial X-check  C'  = (D / CFR) * (1 + r/beta)^alpha,  r = ln(2)/doubling_central
      ascertainment        = confirmed / C                (from the level central)
      est. deaths          = D / death_ascertainment
      unreported           = C - confirmed                (from the level central)
      Module-D floor       = contacts * SAR               (cumulative floor = confirmed + low)

    The headline true-burden band is confirmed * M_stock over the (2.0, 2.5, 3.6)
    scenario multipliers (provenance 'lovs'); the Imperial Method-2 interval (CFR varied
    over its 95% range at the central doubling time) is carried under
    estimated_total_cases.cross_check as an external independent validator.
    """
    mc = methodology_constants or DEFAULT_METHODOLOGY_CONSTANTS
    cfr = mc["cfr"]
    cfr_low, cfr_central, cfr_high = cfr["low_95"], cfr["central"], cfr["high_95"]
    gamma = mc["onset_to_death_gamma"]
    alpha, beta = gamma["alpha"], gamma["beta_per_day"]
    doubling = mc["central_doubling_time_days"]
    obs = mc.get("observed_doubling_times_days", [5.0, 7.0, 11.0])
    d_lo, d_hi = int(min(obs)), int(max(obs))
    mean_days = round(gamma.get("mean_days", alpha / beta), 1)

    # (1) Imperial Method 2 estimated total infections. Gamma growth factor at central doubling.
    r = math.log(2.0) / doubling
    growth = (1.0 + r / beta) ** alpha

    def _infections(c: float) -> float:
        return (confirmed_deaths / c) * growth

    # (1a) Imperial Method 2 (deaths back-projection) — retained as an external CROSS-CHECK,
    # no longer the headline true burden.
    imperial_central = _round(_infections(cfr_central))
    imperial_low = _round(_infections(cfr_high))  # higher CFR -> fewer infections (band low)
    imperial_high = _round(_infections(cfr_low))  # lower CFR -> more infections (band high)

    # (1b) LOVS death-anchored LEVEL model — the HEADLINE true burden. Cumulative-stock
    # level correction: true infections = confirmed * M_stock. Recomputed from THIS cycle's
    # confirmed count, so the published true burden tracks the live count every cycle.
    m_low, m_central, m_high = CASE_LEVEL_MULTIPLIER
    cases_low = _round(confirmed * m_low)
    cases_central = _round(confirmed * m_central)
    cases_high = _round(confirmed * m_high)

    # (2) case ascertainment = confirmed / estimated total infections (the level central)
    asc_central = round(confirmed / cases_central, 4)
    asc_low = round(confirmed / cases_high, 4)
    asc_high = round(confirmed / cases_low, 4)

    # (3) estimated total deaths via death under-ascertainment
    da_lo, da_hi = DEATH_ASCERTAINMENT_BAND
    da_central = (da_lo + da_hi) / 2.0
    deaths_central = _round(confirmed_deaths / da_central)
    deaths_low = _round(confirmed_deaths / da_hi)
    deaths_high = _round(confirmed_deaths / da_lo)

    # (4) estimated unreported cases
    unreported = cases_central - confirmed

    # (5) Module-D known-chain transmission floor
    sar_lo, sar_spine, sar_hi = SECONDARY_ATTACK_RATE
    floor_low = _round(contacts_under_follow_up * sar_lo)
    floor_spine = _round(contacts_under_follow_up * sar_spine)
    floor_high = _round(contacts_under_follow_up * sar_hi)
    cumulative_floor = confirmed + floor_low
    unobserved_pct = round(100.0 - followup_coverage_pct, 1)

    cfr_band = f"{cfr_low}-{cfr_high} (central {cfr_central})"
    doubling_band = f"{d_lo}-{d_hi} (central {int(doubling)})"
    gamma_str = f"alpha={alpha}, beta={beta}/day (mean {mean_days}d)"

    # (6) Delay-adjusted confirmed CFR (Nishiura 2009) — eventual lethality among
    # confirmed, correcting the right-censoring in the crude deaths/confirmed ratio.
    # Computed only when the confirmed-case time series is supplied (national scope).
    severity_cfr = (
        delay_adjusted_cfr(
            confirmed_series, confirmed_deaths, alpha=alpha, beta=beta, as_of=as_of
        )
        if confirmed_series
        else None
    )

    result: dict[str, Any] = {
        "as_of": as_of,
        "severity_cfr": severity_cfr,
        "true_burden_nowcast": {
            "estimated_total_cases": {
                "low": cases_low,
                "central": cases_central,
                "high": cases_high,
                "provenance": "lovs",
                "method": "LOVS death-anchored level model (confirmed x M_stock, validated 2026-06-21)",
                "multipliers": {"low": m_low, "central": m_central, "high": m_high},
                "cross_check": {
                    "low": imperial_low,
                    "central": imperial_central,
                    "high": imperial_high,
                    "provenance": "external",
                    "method": "Imperial College MRC GIDA, Method 2 (deaths back-projection)",
                },
            },
            "estimated_total_deaths": {
                "low": deaths_low,
                "central": deaths_central,
                "high": deaths_high,
                "death_ascertainment_band": [da_lo, da_hi],
                "provenance": "lovs",
                "method": "LOVS death under-ascertainment correction",
            },
            "ascertainment_gap": {
                "case_ascertainment": asc_central,
                "confirmed_vs_estimated_total_cases": [confirmed, cases_central],
                "estimated_unreported_cases": unreported,
                "provenance": "lovs",
            },
        },
        "transmission_floor": {
            "new_cases_from_roster": {
                "low": floor_low,
                "spine": floor_spine,
                "high": floor_high,
            },
            "implied_cumulative_floor": cumulative_floor,
            "coverage_panel": {
                "followup_rate_pct": followup_coverage_pct,
                "unobserved_pct": unobserved_pct,
            },
        },
        "methodology": [
            {
                "quantity": "Estimated total infections (death-anchored level model)",
                "attribution": "Arcede LOVS",
                "provenance": "lovs",
                "equation": "true infections = confirmed x M_stock (M_stock 2.0 / 2.5 / 3.6, death-anchored level multiplier)",
                "inputs": {
                    "confirmed": confirmed,
                    "M_stock": f"{m_low}-{m_high} (central {m_central})",
                },
                "worked_central": f"{confirmed} * {m_central} = {cases_central}",
                "result": f"{cases_low}-{cases_high} (central {cases_central})",
                "sources": [
                    "Arcede LOVS true-burden capacity model (validated 2026-06-21)",
                    "Death anchor + three correlated detection views; positivity fell as testing rose (a widening net on a plateau, not hidden growth)",
                ],
            },
            {
                "quantity": "Estimated total infections (Imperial Method 2, external cross-check)",
                "attribution": "Imperial College MRC GIDA (external method, shown as an independent validator)",
                "provenance": "external",
                "equation": "C = (D / CFR) * (1 + r/beta)^alpha,   r = ln(2) / doubling",
                "inputs": {
                    "D_confirmed_deaths": confirmed_deaths,
                    "CFR": cfr_band,
                    "doubling_days": doubling_band,
                    "onset_to_death_gamma": gamma_str,
                },
                "worked_central": (
                    f"({confirmed_deaths}/{cfr_central}) * "
                    f"(1 + (ln2/{int(doubling)})/{beta})^{alpha} = {imperial_central}"
                ),
                "result": f"{imperial_low}-{imperial_high} (central {imperial_central})",
                "sources": [
                    "Imperial College MRC GIDA, Method 2 (deaths back-projection)",
                    "Rosello 2015 eLife onset-to-death gamma",
                    "US CDC outbreak-history CFR 55/169",
                ],
            },
            {
                "quantity": "Case ascertainment (reporting completeness)",
                "attribution": "Arcede LOVS",
                "provenance": "lovs",
                "equation": "ascertainment = confirmed cases / estimated total infections",
                "inputs": {"confirmed": confirmed, "estimated_total_central": cases_central},
                "worked_central": f"{confirmed} / {cases_central} = {asc_central:.3f}",
                "result": f"{asc_central:.3f} [{asc_low:.3f}-{asc_high:.3f}]",
                "sources": ["LOVS deaths-anchored ascertainment (Nishiura 2009; Epiverse-TRACE cfr)"],
            },
            {
                "quantity": "Estimated total deaths (death under-ascertainment)",
                "attribution": "Arcede LOVS",
                "provenance": "lovs",
                "equation": "estimated deaths = confirmed deaths / death-ascertainment",
                "inputs": {
                    "confirmed_deaths": confirmed_deaths,
                    "death_ascertainment": f"{da_lo}-{da_hi}",
                },
                "worked_central": f"{confirmed_deaths} / {da_central:.3f} = {deaths_central}",
                "result": f"{deaths_low}-{deaths_high} (central {deaths_central})",
                "sources": ["LOVS: deaths at least as well ascertained as cases, up to near-complete"],
            },
            {
                "quantity": "Estimated unreported cases (under-ascertainment)",
                "attribution": "Arcede LOVS",
                "provenance": "lovs",
                "equation": "under-ascertainment = estimated total infections - confirmed cases",
                "inputs": {"estimated_total_central": cases_central, "confirmed": confirmed},
                "worked_central": f"{cases_central} - {confirmed} = {unreported}",
                "result": f"{unreported} cases not yet reported (central)",
                "sources": ["LOVS: tracked across snapshots as the surveillance-response signal"],
            },
            {
                "quantity": "Known-chain transmission floor (Module D)",
                "attribution": "Arcede LOVS",
                "provenance": "lovs",
                "equation": "floor = contacts under follow-up * per-contact secondary attack rate (adds to confirmed)",
                "inputs": {
                    "contacts": contacts_under_follow_up,
                    "secondary_attack_rate": (
                        f"{int(sar_lo * 100)}%-{int(sar_hi * 100)}% (spine {sar_spine * 100:.1f}%)"
                    ),
                },
                "worked_central": (
                    f"{contacts_under_follow_up} * [{sar_lo}, {sar_hi}] = "
                    f"[{floor_low}, {floor_high}]; floor on cumulative confirmed = "
                    f"{confirmed} + {floor_low} = {cumulative_floor}"
                ),
                "result": f"+{floor_low}-{floor_high} expected cases among known contacts (floor {cumulative_floor})",
                "sources": ["LOVS Module D; Mulongo 2025 BMC (3.7% spine); Dean 2016 / Bower 2016 exposure bounds"],
            },
        ],
    }

    # Delay-adjusted confirmed CFR methodology row (only when the series produced one),
    # so the public brief carries the worked, reproducible derivation alongside the rest.
    if severity_cfr is not None:
        adj = severity_cfr["confirmed_cfr_delay_adjusted_pct"]
        result["methodology"].append(
            {
                "quantity": "Delay-adjusted confirmed CFR (eventual lethality among confirmed)",
                "attribution": "Arcede LOVS",
                "provenance": "lovs",
                "equation": (
                    "cCFR_adj = confirmed deaths / sum_t [new confirmed_t * F(T - t)],  "
                    "F = onset-to-death gamma CDF (Nishiura 2009 delay adjustment)"
                ),
                "inputs": {
                    "confirmed_deaths": confirmed_deaths,
                    "confirmed_cumulative": severity_cfr["confirmed_cumulative"],
                    "onset_to_death_gamma": gamma_str,
                    "resolved_denominator": severity_cfr["resolved_denominator"],
                },
                "worked_central": (
                    f"{confirmed_deaths} / {severity_cfr['resolved_denominator']} "
                    f"(delay-resolved denominator) = {adj['central']}%  "
                    f"(crude {severity_cfr['confirmed_cfr_crude_pct']}%)"
                ),
                "result": (
                    f"{adj['central']}% delay-adjusted (band {adj['low']}-{adj['high']}% over "
                    f"a {severity_cfr['onset_to_death_band_days'][0]}-"
                    f"{severity_cfr['onset_to_death_band_days'][1]}d onset-to-death mean); "
                    f"crude {severity_cfr['confirmed_cfr_crude_pct']}%"
                ),
                "sources": severity_cfr["sources"],
            }
        )

    return result
