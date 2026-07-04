"""LOVS convergent-signal burden nowcast (true-burden level model + ascertainment + Module D).

Rebuilt 2026-06-09 from the documented equations after the original module was lost
with an ephemeral working tree (see specs/2026-06-09-june7-snapshot-delta-audit.md).

HEADLINE true burden (2026-07-04 rev): GROUND-DERIVED death anchor, recomputed each cycle
from the live death:case signal, NOT a frozen confirmed-multiplier. The prior model froze
M_stock at 2.0/2.5/3.6 (validated once on 2026-06-21) so ascertainment was mechanically
0.40 every cycle regardless of the data; this replaces that with the spreadsheet's own
death-anchor equation, evaluated per cycle:
    true deaths     = confirmed_deaths / death_ascertainment
    true infections = true deaths / IFR = confirmed_deaths / (death_ascertainment x IFR)
    M_stock         = true infections / confirmed   (DERIVED output, floats)
    ascertainment   = confirmed / true infections   (DERIVED output, floats)
death_ascertainment and IFR are the cited, tunable ground parameters (IFR anti-correlated
with the death undercount). Deaths are the well-ascertained binding signal, so the true
burden now tracks the death series, not a fixed ratio on confirmed. Source spreadsheet:
specs/2026-06-22-bdbv-true-burden-capacity-model.xlsx (Multiplier sheet, Cross-check 1).
CAVEAT (regime): the death anchor is valid at/near the Rt~1 PLATEAU (the current and
validated regime) because cumulative deaths have caught up to cumulative infections. During
EXPLOSIVE GROWTH deaths lag infections, so the anchor understates the stock and reads
artificially HIGH ascertainment (see the June growth-phase regression fixtures). A
re-acceleration (e.g. a Nord-Kivu flare) would transiently understate until deaths resolve;
the delay-adjusted (severity_cfr) death timing is the robust upgrade if that regime returns.
Imperial Method 2 (deaths back-projection, with a growth correction) is retained as an
independent EXTERNAL cross-check under estimated_total_cases.cross_check.

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
# Death ascertainment (low, central, high) = 1/U for the spreadsheet's ADVERSARIALLY-
# VALIDATED death-undercount band U = (1.9, 1.5, 1.2) -> da = (0.526, 0.667, 0.833). Central
# 0.667 (U=1.5) is SDB-anchored ("safe-and-dignified-burial worst-covered pillar, Mongbwalu";
# community deaths outside the line list) and reproduces the validated M_stock 2.5 and true
# deaths 404 at the SR38 validation point. This replaces the prior looser (0.696, 0.95)
# band (central 0.823), which was never consistent with the validated model (it implied
# M_stock 2.0 / true deaths 327 at SR38). NOTE: this is an EXPLICIT 3-tuple (low, central,
# high), not a 2-tuple midpoint, because 1/1.5 = 0.667 is not the midpoint of the edges.
DEATH_ASCERTAINMENT_BAND = (0.526, 0.667, 0.833)  # low, central, high (= 1/U for U 1.9/1.5/1.2)
SECONDARY_ATTACK_RATE = (0.03, 0.037, 0.09)  # low, spine (Mulongo 2025 BMC 3.7%), high

# LOVS death-anchored TRUE-BURDEN true IFR band (low, central, high) — the ground
# parameter (with death-ascertainment) that DRIVES the headline true burden each cycle.
# Source: specs/2026-06-22-bdbv-true-burden-capacity-model.xlsx (adversarial review
# 2026-06-21), "IFR for anchor": Bundibugyo-plausible true infection-fatality over ALL
# infections. It is ANTI-CORRELATED with the death undercount (a higher undercount implies
# milder missed cases -> lower IFR), so the scenario band pairs (best death-ascertainment,
# high IFR) -> fewest infections and (worst death-ascertainment, low IFR) -> most. The low
# edge is 0.13 (not the anchor sheet's raw 0.12) to honor the review's capping of the
# pessimistic M_stock tail (3.6, not the raw 4.0) at the SR38 validation point.
#
# HEADLINE true burden is now GROUND-DERIVED, not a frozen confirmed-multiplier:
#   true deaths      = confirmed_deaths / death_ascertainment          (the death anchor)
#   true infections  = true deaths / IFR = confirmed_deaths / (death_ascertainment * IFR)
# The implied level multiplier M_stock = true_infections / confirmed and the case
# ascertainment = confirmed / true_infections are DERIVED OUTPUTS that float with THIS
# cycle's confirmed:death ratio; there is no hardcoded 2.5x. This replaces the frozen
# CASE_LEVEL_MULTIPLIER (2.0/2.5/3.6) validated once on 2026-06-21, which mechanically
# pinned ascertainment at 0.40 every cycle regardless of the death signal.
# TUNABLE: death_ascertainment (above) is the death-undercount knob, set to the validated
# SDB-anchored 0.667 (U=1.5). IFR and death-ascertainment are the two cited ground roots.
TRUE_IFR_BAND = (0.13, 0.15, 0.16)  # low, central, high; anti-correlated with death undercount

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


def estimate_growth_rate(
    confirmed_series: list[dict[str, Any]],
    as_of: str,
    *,
    window_days: int = 21,
) -> dict[str, Any]:
    """Trailing-window epidemic growth rate from the confirmed incidence series.

    Replaces the frozen doubling time in the Imperial cross-check with a value
    re-estimated each cycle. Daily incidence (cumulative increments normalized by
    the gap between reports) over the last ``window_days`` is split at the window
    midpoint and the second-half mean is compared to the first-half mean:

        r = ln(inc_2nd_half / inc_1st_half) / (window_days / 2)

    ``r`` is floored at 0 so a plateau or decline yields a growth correction of 1
    rather than inflating the back-projection. The window (default 21 days, the
    operational active-case horizon) smooths single-cycle report-day noise, so the
    estimate is grounded in the live series but not oversensitive to one report.
    """
    method = (
        "trailing-window incidence growth rate: r = ln(inc2/inc1)/(window/2), floored at 0"
    )
    insufficient = {
        "r_per_day": None,
        "doubling_time_days": None,
        "regime": "insufficient_data",
        "window_days": window_days,
        "incidence_first_half_per_day": None,
        "incidence_second_half_per_day": None,
        "method": method,
    }
    pts = sorted(
        (
            (date.fromisoformat(str(p["date"])[:10]), int(p["value"]))
            for p in confirmed_series
            if p.get("date") and isinstance(p.get("value"), int)
            and not isinstance(p.get("value"), bool)
        ),
        key=lambda t: t[0],
    )
    if len(pts) < 2:
        return insufficient
    target = date.fromisoformat(as_of[:10])
    incidence: list[tuple[int, float]] = []  # (report ordinal, new confirmed per day)
    for (d0, v0), (d1, v1) in zip(pts, pts[1:]):
        dt_before = (target - d1).days
        if dt_before < 0 or dt_before > window_days:
            continue
        gap = (d1 - d0).days or 1
        incidence.append((d1.toordinal(), (v1 - v0) / gap))
    if len(incidence) < 2:
        return insufficient
    mid = target.toordinal() - window_days / 2.0
    first = [n for o, n in incidence if o < mid]
    second = [n for o, n in incidence if o >= mid]
    if not first or not second:
        return insufficient
    inc1 = sum(first) / len(first)
    inc2 = sum(second) / len(second)
    if inc1 <= 0 or inc2 <= 0:
        return insufficient
    r = max(0.0, math.log(inc2 / inc1) / (window_days / 2.0))
    doubling = (math.log(2.0) / r) if r > 0 else None
    if r <= 0:
        regime = "plateau"
    elif doubling is not None and doubling <= 14.0:
        regime = "growing"
    else:
        regime = "slow_growth"
    return {
        "r_per_day": round(r, 5),
        "doubling_time_days": round(doubling, 1) if doubling is not None else None,
        "regime": regime,
        "window_days": window_days,
        "incidence_first_half_per_day": round(inc1, 2),
        "incidence_second_half_per_day": round(inc2, 2),
        "method": method,
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

    # (0) Delay-adjusted confirmed CFR (Nishiura 2009) — eventual lethality among confirmed,
    # correcting the right-censoring in the crude deaths/confirmed ratio. Computed only when
    # the confirmed-case time series is supplied (national scope); it drives BOTH the
    # delay-adjusted death-anchor endpoint below and the death-resolution regime signal.
    severity_cfr = (
        delay_adjusted_cfr(
            confirmed_series, confirmed_deaths, alpha=alpha, beta=beta, as_of=as_of
        )
        if confirmed_series
        else None
    )

    # (1) Imperial Method 2 cross-check. The doubling time is FLOATED from the incidence
    # series each cycle (was frozen at 7d, an explosive-growth assumption); a plateau
    # collapses the growth correction to 1 instead of inflating the back-projection.
    growth_est = estimate_growth_rate(confirmed_series, as_of) if confirmed_series else None
    if growth_est and growth_est.get("regime") == "plateau":
        doubling_used, growth_regime = float("inf"), "plateau"
    elif growth_est and growth_est.get("doubling_time_days"):
        doubling_used, growth_regime = float(growth_est["doubling_time_days"]), growth_est["regime"]
    else:
        doubling_used = float(doubling)
        growth_regime = "insufficient_data" if growth_est is not None else "assumed_frozen"
    r = 0.0 if math.isinf(doubling_used) else math.log(2.0) / doubling_used
    growth = (1.0 + r / beta) ** alpha

    def _infections(c: float) -> float:
        return (confirmed_deaths / c) * growth

    imperial_central = _round(_infections(cfr_central))
    imperial_low = _round(_infections(cfr_high))  # higher CFR -> fewer infections (band low)
    imperial_high = _round(_infections(cfr_low))  # lower CFR -> more infections (band high)

    # (3) estimated total deaths via death under-ascertainment (THE DEATH ANCHOR).
    # deaths are the well-ascertained, binding signal; this recomputes every cycle.
    # Explicit (low, central, high) band; central 0.667 is not the edge midpoint.
    da_lo, da_central, da_hi = DEATH_ASCERTAINMENT_BAND
    deaths_central = _round(confirmed_deaths / da_central)
    deaths_low = _round(confirmed_deaths / da_hi)   # best ascertainment -> fewest true deaths
    deaths_high = _round(confirmed_deaths / da_lo)  # worst ascertainment -> most true deaths

    # (1b) CRUDE death-anchored level model (LOWER death-timing endpoint): true infections =
    # true deaths / IFR, on OBSERVED cumulative deaths. IFR is anti-correlated with the death
    # undercount (best death-ascertainment pairs with high IFR -> fewest infections).
    ifr_lo, ifr_central, ifr_high = TRUE_IFR_BAND
    crude_low = _round(deaths_low / ifr_high)
    crude_central = _round(deaths_central / ifr_central)
    crude_high = _round(deaths_high / ifr_lo)
    crude_anchor = {"low": crude_low, "central": crude_central, "high": crude_high}

    if severity_cfr is not None:
        # (1b') DELAY-ADJUSTED death-anchored level model (UPPER death-timing endpoint):
        # eventual confirmed deaths = confirmed * delay-adjusted cCFR correct the deaths lag.
        adj_central_pct = severity_cfr["confirmed_cfr_delay_adjusted_pct"]["central"]
        eventual_deaths = _round(confirmed * adj_central_pct / 100.0)
        da_deaths_central = _round(eventual_deaths / da_central)
        da_deaths_low = _round(eventual_deaths / da_hi)
        da_deaths_high = _round(eventual_deaths / da_lo)
        delay_anchor = {
            "low": _round(da_deaths_low / ifr_high),
            "central": _round(da_deaths_central / ifr_central),
            "high": _round(da_deaths_high / ifr_lo),
        }
        # HEADLINE = death-TIMING bracket: crude central (lower, deaths lag) to delay-adjusted
        # central (upper, eventual deaths); geometric-mean central (a stable midpoint, NOT a
        # regime-weighted point, to avoid an oversensitive goalpost).
        cases_low = crude_central
        cases_high = delay_anchor["central"]
        cases_central = _round(math.sqrt(cases_low * cases_high))
        deaths_display = {
            "low": deaths_central,                        # crude true-deaths central
            "central": _round(cases_central * ifr_central),  # consistent with infections central
            "high": da_deaths_central,                    # delay-adjusted true-deaths central
        }
        # (1b'') National CARE-vs-ASCERTAINMENT scenario. When the delay-adjusted confirmed
        # lethality exceeds the historical BDBV CFR 95% high, the clearly-above-historical excess
        # is a candidate for care-strain (late presentation, CTE saturation): if it is care-driven
        # rather than a missing-mild-case artifact, the effective IFR is higher and the hidden
        # burden is LOWER. This is a bounded, DEAD-BANDED downside SCENARIO, not the headline and
        # not a measured correction (the excess could equally be an intrinsically more lethal
        # strain or a low historical baseline, n=169). It can only lower, never raise, the burden.
        dacfr = adj_central_pct / 100.0
        excess_cfr = max(0.0, dacfr - cfr_high)  # dead-band at the historical 95% high
        care_factor = min(0.20 / ifr_central, 1.0 + excess_cfr / cfr_central)  # cap eff. IFR <= 0.20
        ifr_care = round(ifr_central * care_factor, 4)
        care_adjusted = {
            "central": _round(deaths_central / ifr_care),
            "effective_ifr": ifr_care,
            "care_factor": round(care_factor, 4),
            "provenance": "lovs",
            "method": (
                "scenario downside: if above-historical lethality (delay-adjusted cCFR over the "
                "historical BDBV CFR 95% high) is care-driven, effective IFR rises and hidden "
                "infections fall; NOT the headline and NOT a measured care correction"
            ),
        }
        # (1b''') Position of the delay-adjusted confirmed CFR relative to the historical BDBV CFR
        # band, in death-equivalents. This is NOT a causal decomposition and NOT an ascertainment
        # measurement (poor case-ascertainment shrinks the denominator, it does not produce
        # deaths). Only `beyond_historical_ci_deaths` (above the 95% high) is clearly outside
        # historical variation, and it is the care-SCENARIO candidate, capped consistently with
        # the care_adjusted 0.20 effective-IFR ceiling so the two siblings cannot diverge.
        # `historical_ci_band_width_deaths` is a FIXED reference (confirmed * the width of the
        # historical CFR 95% CI), reported as a constant, never presented as an inference.
        cap_excess = (0.20 / ifr_central - 1.0) * cfr_central  # excess that maps to the eff-IFR 0.20 cap
        excess_fatality_decomposition = {
            "excess_deaths_over_historical_central": _round(confirmed * max(0.0, dacfr - cfr_central)),
            "beyond_historical_ci_deaths": _round(confirmed * min(excess_cfr, cap_excess)),
            "historical_ci_band_width_deaths": _round(confirmed * (cfr_high - cfr_central)),
            "above_historical_high_cfr_points": round(min(excess_cfr, cap_excess) * 100.0, 1),
            "note": (
                "the delay-adjusted confirmed CFR relative to the historical BDBV CFR band, in "
                "death-equivalents; NOT a causal split and NOT an ascertainment measurement. "
                "beyond_historical_ci_deaths (above the 95% high) is the only portion clearly "
                "outside historical variation and is the care-SCENARIO candidate, capped "
                "consistently with the care_adjusted effective-IFR ceiling. "
                "historical_ci_band_width_deaths is the fixed width of the historical CFR 95% CI "
                "(confirmed times high minus central), a reference, not an inference about this outbreak."
            ),
            "provenance": "lovs",
        }
    else:
        delay_anchor = None
        care_adjusted = None
        excess_fatality_decomposition = None
        cases_low, cases_central, cases_high = crude_low, crude_central, crude_high
        deaths_display = {"low": deaths_low, "central": deaths_central, "high": deaths_high}

    # (1c) DERIVED level multiplier M_stock = true infections / confirmed (an OUTPUT, not a
    # frozen input) and (2) case ascertainment = confirmed / estimated total infections.
    m_low = round(cases_low / confirmed, 2) if confirmed else 0.0
    m_central = round(cases_central / confirmed, 2) if confirmed else 0.0
    m_high = round(cases_high / confirmed, 2) if confirmed else 0.0
    asc_central = round(confirmed / cases_central, 4) if cases_central else 0.0
    asc_low = round(confirmed / cases_high, 4) if cases_high else 0.0
    asc_high = round(confirmed / cases_low, 4) if cases_low else 0.0

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
    doubling_band = (
        f"floated {doubling_used:g}d ({growth_regime}); sensitivity {d_lo}-{d_hi}"
        if not math.isinf(doubling_used)
        else f"plateau, r=0 (growth correction 1); sensitivity {d_lo}-{d_hi}"
    )
    gamma_str = f"alpha={alpha}, beta={beta}/day (mean {mean_days}d)"

    # (6) Convergence signals — grounded operational reads that indicate WHICH death-timing
    # endpoint to trust this cycle. These are NOT burden estimates: the reporting-completeness
    # and positivity modules are, by design, not case-count denominators, so no burden number
    # is derived from them here.
    convergence_signals = None
    if severity_cfr is not None:
        crude_pct = severity_cfr["confirmed_cfr_crude_pct"]
        resolving = adj_central_pct > crude_pct
        convergence_signals = {
            "death_resolution": {
                "crude_cfr_pct": crude_pct,
                "delay_adjusted_cfr_pct": adj_central_pct,
                "state": "deaths_still_resolving" if resolving else "resolved",
                "implication": (
                    "delay-adjusted lethality exceeds crude: deaths are still resolving, so "
                    "the crude death anchor understates; weight the delay-adjusted upper endpoint"
                    if resolving
                    else "crude and delay-adjusted lethality agree: the death anchor is at steady state"
                ),
            },
            "growth": growth_est,
            "contact_coverage_pct": followup_coverage_pct,
            "note": (
                "operational regime signals indicating which death-timing endpoint to trust; "
                "NOT burden estimates (reporting-completeness and positivity are, by design, "
                "not case-count denominators)"
            ),
        }

    if math.isinf(doubling_used):
        imp_worked = f"({confirmed_deaths}/{cfr_central}) * 1 (plateau, r=0) = {imperial_central}"
    else:
        imp_worked = (
            f"({confirmed_deaths}/{cfr_central}) * "
            f"(1 + (ln2/{doubling_used:g})/{beta})^{alpha} = {imperial_central}"
        )
    if delay_anchor:
        head_worked = (
            f"crude {crude_central} (={confirmed_deaths}/({da_central:.3f} x {ifr_central})) "
            f"to delay-adjusted {delay_anchor['central']}; geom-mean central {cases_central} "
            f"(implied {m_central}x on {confirmed})"
        )
        head_result = (
            f"{cases_low}-{cases_high} (central {cases_central}; "
            f"crude->delay-adjusted death-timing bracket)"
        )
        deaths_worked = (
            f"crude {deaths_central} to delay-adjusted {da_deaths_central}; central "
            f"{deaths_display['central']} (= {cases_central} x {ifr_central})"
        )
    else:
        head_worked = (
            f"{confirmed_deaths} / ({da_central:.3f} x {ifr_central}) = {cases_central}"
            f"  (implied {m_central}x on {confirmed} confirmed)"
        )
        head_result = f"{cases_low}-{cases_high} (central {cases_central})"
        deaths_worked = f"{confirmed_deaths} / {da_central:.3f} = {deaths_display['central']}"

    result: dict[str, Any] = {
        "as_of": as_of,
        "severity_cfr": severity_cfr,
        "true_burden_nowcast": {
            "estimated_total_cases": {
                "low": cases_low,
                "central": cases_central,
                "high": cases_high,
                "provenance": "lovs",
                "method": (
                    "LOVS death-anchored range: crude death anchor (lower, deaths lag) to "
                    "delay-adjusted death anchor (upper, eventual deaths), geometric-mean "
                    "central; recomputed each cycle, multiplier derived"
                    if delay_anchor
                    else "LOVS death-anchored level model (true cases = confirmed_deaths / (death_ascertainment x IFR); recomputed each cycle, multiplier derived)"
                ),
                "multipliers": {"low": m_low, "central": m_central, "high": m_high},
                "crude_anchor": crude_anchor,
                **({"delay_adjusted_anchor": delay_anchor} if delay_anchor else {}),
                "cross_check": {
                    "low": imperial_low,
                    "central": imperial_central,
                    "high": imperial_high,
                    "provenance": "external",
                    "method": (
                        "Imperial College MRC GIDA, Method 2 (deaths back-projection; total "
                        "symptomatic cases via CFR, a different denominator from the infection "
                        "headline)"
                    ),
                    "doubling_time_days_used": (None if math.isinf(doubling_used) else doubling_used),
                    "growth_regime": growth_regime,
                },
            },
            "estimated_total_deaths": {
                "low": deaths_display["low"],
                "central": deaths_display["central"],
                "high": deaths_display["high"],
                "death_ascertainment_band": [da_lo, da_hi],
                "provenance": "lovs",
                "method": "LOVS death under-ascertainment correction"
                + (" (range mirrors the infection bracket)" if delay_anchor else ""),
            },
            "ascertainment_gap": {
                "case_ascertainment": asc_central,
                "confirmed_vs_estimated_total_cases": [confirmed, cases_central],
                "estimated_unreported_cases": unreported,
                "provenance": "lovs",
            },
            **({"care_adjusted": care_adjusted} if care_adjusted else {}),
            **({"excess_fatality_decomposition": excess_fatality_decomposition} if excess_fatality_decomposition else {}),
            **({"convergence_signals": convergence_signals} if convergence_signals else {}),
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
                "equation": "true infections = confirmed_deaths / (death_ascertainment x IFR); implied M_stock = true infections / confirmed (derived, floats)",
                "inputs": {
                    "confirmed_deaths": confirmed_deaths,
                    "death_ascertainment": f"{da_lo}-{da_hi} (central {da_central:.3f})",
                    "IFR_true": f"{ifr_lo}-{ifr_high} (central {ifr_central})",
                    "derived_M_stock": f"{m_low}-{m_high} (central {m_central})",
                },
                "worked_central": head_worked,
                "result": head_result,
                "sources": [
                    "Arcede LOVS true-burden capacity model (validated 2026-06-21; death anchor + anti-correlated IFR band, now recomputed each cycle from the live death:case ratio)",
                    "Death anchor: deaths are the well-ascertained binding signal; positivity fell as testing rose (a widening net on a plateau, not hidden growth)",
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
                "worked_central": imp_worked,
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
                "worked_central": deaths_worked,
                "result": f"{deaths_display['low']}-{deaths_display['high']} (central {deaths_display['central']})",
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
