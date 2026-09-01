"""CFR read as an ascertainment detector rather than a severity measure.

Why this exists. The operational forecaster rejected `confirmed_cfr_percent` as
a bootstrap target: the bootstrap priced "ever exceeds 50 percent" at 1.000 by
extrapolating drift in a ratio whose denominator is growing, and it had no way
to know the drift decays. That rejection left a real question unasked. Within
one outbreak, one pathogen and one national response, the province split shows
Nord-Kivu running roughly twenty points of CFR above Ituri, and the LOVS zone
capacity diagnostic independently flags Nord-Kivu `laboratory_confirmation` as
severity=critical with gapBasis=no_evidenced_delivery. Those two facts have an
obvious joint explanation that is not biological: where confirmation is scarce,
the people who get confirmed are the people whose illness forces the system to
look at them, and dying is the loudest way to force that. A province that can
only confirm its deaths reports a CFR approaching 100 percent no matter what
its true CFR is. On that reading a high CFR does not mark a province where the
disease is worse; it marks a province whose mild and moderate cases are
invisible.

This module tests that reading against the frozen series and refuses to settle
it. It is an instrument for sizing a measurement artifact, not a body count.

Method. Four pieces, each with its own failure mode surfaced rather than
smoothed:

1. Per-province cumulative CFR with a Wilson 95 percent interval, and a
   precision gate stated in the terms of the question being asked. A province
   whose CFR cannot be placed inside a band narrower than the gap under
   investigation cannot speak to that gap, so it is flagged and excluded rather
   than silently ranked. Sud-Kivu has three confirmed cases; a CFR on three
   cases is not a small number, it is not a number.
2. Interval CFR: deaths added over a window divided by cases added over the
   same window. Cumulative CFR in a growing epidemic is biased downward by
   right censoring, because recently confirmed cases have not yet had time to
   die, and that bias shrinks as a province's cohort ages. Two provinces at
   different epidemic ages therefore differ in cumulative CFR for reasons that
   have nothing to do with either ascertainment or severity. The interval
   measure does not remove that bias but it stops the cohort's whole history
   from dominating the comparison.
3. Divergence: the gap between two provinces over time, on either basis, with a
   verdict that is allowed to say the window does not resolve it. Three trend
   measures must agree in sign before the instrument will call a direction.
4. An implied-unconfirmed estimator, reported only ever as a range across a
   reference band, and only ever as the consequent of an explicit conditional.

What this can and cannot support. It can support "is the gap consistent with a
measurement artifact", "how large would the artifact have to be", and "what
would tell the two readings apart". It cannot support any statement about how
many people are infected, how many have died, or how fast the outbreak is
growing. The implied-unconfirmed range is what a stated assumption implies, not
a count of anyone; nothing here is an observation of an unconfirmed case, and
no incidence or growth claim may be derived from it. Deaths are the one
quantity treated as observed, and even that assumes a province's death
ascertainment is better than its case ascertainment -- which is the very
asymmetry the instrument is about, and which it cannot itself verify.

Deterministic. Stdlib only. No network. No clock -- the as-of day is passed in
and defaults to the last data day present in the series.
"""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from lovs.forecast.opsforecast import DEFAULT_SERIES, Observation, load_rows, series

# Wilson at 95 percent. Written out rather than imported so the module keeps its
# stdlib-only promise without pulling statistics machinery for one constant.
Z95 = 1.959963984540054

# The gap under investigation is roughly twenty points. A province whose own CFR
# cannot be placed inside a band narrower than thirty points cannot testify
# about a twenty-point difference, whatever its point estimate looks like. The
# gate is tied to the question, not to a conventional minimum cell size.
DEFAULT_MAX_CI_WIDTH_PP = 30.0

# Interval windows, in observations rather than days, because the series is
# published per SitRep and a missed packet should shorten the calendar span
# rather than silently drop an observation out of the window.
DEFAULT_INTERVAL_WINDOWS = (7, 14, 21, 28)
DEFAULT_INTERVAL_WINDOW = 14

# Province split and national totals disagree by one unit on two days in the
# frozen extract, from reclassification, and by up to 396 across a six-day
# window in August when the split froze while the national line kept moving.
# A tolerance of two admits the former and rejects the latter; the observed
# distribution of disagreements is reported by `reconciliation` so the choice
# can be re-examined against a future extract rather than inherited.
DEFAULT_RECONCILE_TOLERANCE = 2

# A gap drifting slower than two points per thirty days would need most of a
# year to change the answer to a twenty-point question, so inside this window it
# is a hold, not a trend.
DEFAULT_HOLD_BAND_PP_PER_30D = 2.0

# Below thirty recent confirmations one case moves the isolation ratio by more
# than three percent, which is the same order as the effect being looked for.
DEFAULT_MIN_RECENT_CONFIRMED = 30

# The isolation census is reported per province as a stock, not a cohort, so the
# only sane denominator is the confirmations of the recent past.
DEFAULT_ISOLATION_LOOKBACK = 14

# Not a province. Early SitReps carry a summed row inside the isolation map.
_ISOLATION_TOTAL_KEY = "Total"


# --- primitives --------------------------------------------------------------

def wilson(successes: int, trials: int, z: float = Z95) -> tuple[float, float]:
    """A CFR interval that stays inside [0, 1] at the small counts this data has.

    The normal approximation puts Sud-Kivu's one-death-in-three CFR interval
    partly below zero, which would let a nonsense province look scoreable.
    Wilson does not have that failure, so the precision gate can be trusted at
    exactly the counts where a gate matters.
    """
    if trials <= 0:
        raise ValueError("Wilson interval undefined with no trials")
    n, k = float(trials), float(successes)
    denom = n + z * z
    centre = (k + z * z / 2.0) / denom
    half = (z / denom) * math.sqrt(k * (n - k) / n + z * z / 4.0)
    return max(0.0, centre - half), min(1.0, centre + half)


def _parse_day(stamp: object) -> dt.date | None:
    if not stamp:
        return None
    try:
        return dt.date.fromisoformat(str(stamp)[:10])
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class ProvinceDay:
    """One data day's province split, with its agreement to the national line."""

    date: dt.date
    sitrep: str
    split: dict[str, tuple[int, int]]
    national_confirmed: int | None
    national_deaths: int | None
    split_confirmed: int
    split_deaths: int
    reconciled: bool
    confirmed_gap: int | None
    deaths_gap: int | None


def province_days(
    rows: Sequence[dict],
    *,
    tolerance: int = DEFAULT_RECONCILE_TOLERANCE,
) -> list[ProvinceDay]:
    """Chronological, de-duplicated province splits with a reconciliation verdict.

    De-duplication keeps the first packet for a repeated data day, matching
    `opsforecast.series`, so a province series and a national series built from
    the same extract line up observation for observation. 2026-08-10 appears in
    two SitReps; counting it twice would double-weight one day of a comparison
    that has eighty.

    Reconciliation is carried rather than enforced. A day whose split has
    stopped tracking the national totals is still a real reported day, and the
    right response is to refuse to compute increments across it, not to drop it
    and leave a hole nobody can see.
    """
    national_confirmed = {o.date: o.value for o in series(rows, "confirmed_total")}
    national_deaths = {o.date: o.value for o in series(rows, "confirmed_deaths_total")}

    out: list[ProvinceDay] = []
    seen: set[dt.date] = set()
    for row in rows:
        split_raw = row.get("province_split")
        when = _parse_day(row.get("data_as_of"))
        if not split_raw or when is None or when in seen:
            continue
        seen.add(when)
        split: dict[str, tuple[int, int]] = {}
        for name, cell in split_raw.items():
            confirmed, deaths = cell.get("confirmed"), cell.get("confirmed_deaths")
            if confirmed is None or deaths is None:
                continue
            split[name] = (int(confirmed), int(deaths))
        if not split:
            continue
        sc = sum(c for c, _ in split.values())
        sd = sum(d for _, d in split.values())
        nc = national_confirmed.get(when)
        nd = national_deaths.get(when)
        cgap = None if nc is None else sc - int(nc)
        dgap = None if nd is None else sd - int(nd)
        reconciled = all(
            gap is None or abs(gap) <= tolerance for gap in (cgap, dgap)
        )
        out.append(
            ProvinceDay(
                date=when,
                sitrep=str(row.get("sitrep", "")),
                split=split,
                national_confirmed=None if nc is None else int(nc),
                national_deaths=None if nd is None else int(nd),
                split_confirmed=sc,
                split_deaths=sd,
                reconciled=reconciled,
                confirmed_gap=cgap,
                deaths_gap=dgap,
            )
        )
    out.sort(key=lambda d: d.date)
    return out


def provinces_seen(days: Sequence[ProvinceDay]) -> list[str]:
    """Every province ever split out, ordered by its burden on the last day it appears."""
    last: dict[str, int] = {}
    for day in days:
        for name, (confirmed, _) in day.split.items():
            last[name] = confirmed
    return sorted(last, key=lambda n: (-last[n], n))


def _index_of(days: Sequence[ProvinceDay], as_of: dt.date | str | None) -> int:
    """Position of the as-of day. No clock: absent an argument, the last data day."""
    if not days:
        raise ValueError("no province splits in this series")
    if as_of is None:
        return len(days) - 1
    target = as_of if isinstance(as_of, dt.date) else dt.date.fromisoformat(str(as_of)[:10])
    for i in range(len(days) - 1, -1, -1):
        if days[i].date == target:
            return i
    raise ValueError(f"no province split for data day {target.isoformat()}")


# --- 1. per-province cumulative CFR ------------------------------------------

@dataclass(frozen=True, slots=True)
class ProvinceCfr:
    province: str
    date: str
    confirmed: int
    deaths: int
    cfr_percent: float
    ci_low_percent: float
    ci_high_percent: float
    ci_width_percent: float
    precision_ok: bool
    note: str


def cumulative_cfr(
    days: Sequence[ProvinceDay],
    *,
    as_of: dt.date | str | None = None,
    max_ci_width_pp: float = DEFAULT_MAX_CI_WIDTH_PP,
) -> list[ProvinceCfr]:
    """The CFR table for one data day, with unscoreable provinces marked, not dropped.

    Dropping a thin province would hide it; ranking it would let three cases
    outvote five thousand. It is listed, given its interval, and told it cannot
    testify.
    """
    day = days[_index_of(days, as_of)]
    out: list[ProvinceCfr] = []
    for name in sorted(day.split, key=lambda n: (-day.split[n][0], n)):
        confirmed, deaths = day.split[name]
        if confirmed <= 0:
            out.append(
                ProvinceCfr(
                    province=name,
                    date=day.date.isoformat(),
                    confirmed=confirmed,
                    deaths=deaths,
                    cfr_percent=float("nan"),
                    ci_low_percent=float("nan"),
                    ci_high_percent=float("nan"),
                    ci_width_percent=float("inf"),
                    precision_ok=False,
                    note="no confirmed cases; CFR undefined",
                )
            )
            continue
        lo, hi = wilson(deaths, confirmed)
        width = (hi - lo) * 100.0
        ok = width <= max_ci_width_pp
        out.append(
            ProvinceCfr(
                province=name,
                date=day.date.isoformat(),
                confirmed=confirmed,
                deaths=deaths,
                cfr_percent=100.0 * deaths / confirmed,
                ci_low_percent=100.0 * lo,
                ci_high_percent=100.0 * hi,
                ci_width_percent=width,
                precision_ok=ok,
                note=(
                    ""
                    if ok
                    else f"denominator too small: 95% interval spans {width:.1f} pp, "
                    f"wider than the {max_ci_width_pp:.0f} pp gate"
                ),
            )
        )
    return out


def cumulative_cfr_series(
    days: Sequence[ProvinceDay], province: str
) -> list[Observation]:
    """One province's cumulative CFR as dated observations, for trend work."""
    out: list[Observation] = []
    for day in days:
        cell = day.split.get(province)
        if cell is None or cell[0] <= 0:
            continue
        out.append(Observation(day.date, 100.0 * cell[1] / cell[0]))
    return out


# --- 2. interval CFR ---------------------------------------------------------

@dataclass(frozen=True, slots=True)
class IntervalCfr:
    province: str
    start_date: str
    end_date: str
    window_obs: int
    span_days: int
    new_confirmed: int
    new_deaths: int
    cfr_percent: float | None
    usable: bool
    note: str


def interval_cfr(
    days: Sequence[ProvinceDay],
    province: str,
    *,
    window_obs: int = DEFAULT_INTERVAL_WINDOW,
    as_of: dt.date | str | None = None,
) -> IntervalCfr:
    """Deaths added over cases added, refusing every way this can go wrong.

    Both endpoints must reconcile with the national line. An interior reporting
    freeze is harmless here -- a cumulative difference does not care that the
    counter stalled and caught up in between -- but an endpoint sitting inside
    the stall makes the increment fiction.

    A non-positive case increment or a negative death increment means the
    reported cumulative went backwards, which happens in this extract when
    Nord-Kivu and Haut-Uele reclassified cases. That is a real event and the
    right answer is that no rate can be computed across it.
    """
    end_i = _index_of(days, as_of)
    start_i = end_i - window_obs
    if start_i < 0:
        return IntervalCfr(
            province=province,
            start_date="",
            end_date=days[end_i].date.isoformat(),
            window_obs=window_obs,
            span_days=0,
            new_confirmed=0,
            new_deaths=0,
            cfr_percent=None,
            usable=False,
            note=f"only {end_i} observations precede the as-of day; need {window_obs}",
        )
    start, end = days[start_i], days[end_i]
    span = (end.date - start.date).days
    a, b = start.split.get(province), end.split.get(province)
    if a is None or b is None:
        return IntervalCfr(
            province, start.date.isoformat(), end.date.isoformat(), window_obs, span,
            0, 0, None, False, "province absent from one endpoint's split",
        )
    if not (start.reconciled and end.reconciled):
        stale = [d.date.isoformat() for d in (start, end) if not d.reconciled]
        return IntervalCfr(
            province, start.date.isoformat(), end.date.isoformat(), window_obs, span,
            0, 0, None, False,
            "endpoint does not reconcile with the national total: " + ", ".join(stale),
        )
    dc, dd = b[0] - a[0], b[1] - a[1]
    if dc <= 0 or dd < 0:
        return IntervalCfr(
            province, start.date.isoformat(), end.date.isoformat(), window_obs, span,
            dc, dd, None, False,
            "cumulative counts did not increase across the window "
            f"(cases {dc:+d}, deaths {dd:+d}); a reclassification, not a rate",
        )
    return IntervalCfr(
        province, start.date.isoformat(), end.date.isoformat(), window_obs, span,
        dc, dd, 100.0 * dd / dc, True, "",
    )


def interval_cfr_series(
    days: Sequence[ProvinceDay],
    province: str,
    *,
    window_obs: int = DEFAULT_INTERVAL_WINDOW,
) -> list[Observation]:
    """Rolling interval CFR, dated at the window's end. Unusable windows omitted."""
    out: list[Observation] = []
    for i in range(window_obs, len(days)):
        got = interval_cfr(days, province, window_obs=window_obs, as_of=days[i].date)
        if got.usable and got.cfr_percent is not None:
            out.append(Observation(days[i].date, got.cfr_percent))
    return out


# --- 3. divergence -----------------------------------------------------------

def _ols_slope_per_30d(points: Sequence[Observation]) -> float | None:
    """Least-squares slope in units per thirty days, or None if the x-axis is flat."""
    if len(points) < 3:
        return None
    origin = points[0].date
    xs = [float((p.date - origin).days) for p in points]
    ys = [p.value for p in points]
    n = float(len(xs))
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0.0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return 30.0 * sxy / sxx


@dataclass(frozen=True, slots=True)
class Divergence:
    high: str
    low: str
    basis: str
    n_candidates: int
    n_points: int
    n_refused: int
    first_date: str
    first_gap_pp: float
    last_date: str
    last_gap_pp: float
    min_gap_pp: float
    max_gap_pp: float
    slope_full_pp_per_30d: float | None
    slope_recent_pp_per_30d: float | None
    endpoint_pp_per_30d: float | None
    hold_band_pp_per_30d: float
    verdict: str
    reason: str


def divergence(
    days: Sequence[ProvinceDay],
    high: str,
    low: str,
    *,
    basis: str = "cumulative",
    window_obs: int = DEFAULT_INTERVAL_WINDOW,
    recent_obs: int = 30,
    hold_band_pp_per_30d: float = DEFAULT_HOLD_BAND_PP_PER_30D,
) -> Divergence:
    """The gap between two provinces, with a verdict allowed to abstain.

    Three trend measures are taken: least squares over the whole gap series,
    least squares over the recent tail, and the endpoint-to-endpoint change
    scaled to the same units. A direction is declared only when all three agree
    in sign and the full-window slope clears the hold band.

    No p-value is offered and none should be. A cumulative CFR gap is a running
    ratio and a rolling interval gap reuses most of its data between
    consecutive points; both are autocorrelated by construction, so any nominal
    standard error here would be a confident-looking lie. Sign agreement across
    three differently-weighted views of the same series is the honest test this
    data can carry, and when they disagree the correct output is that eighty
    observations did not settle it.
    """
    if basis == "cumulative":
        hi_obs = cumulative_cfr_series(days, high)
        lo_obs = cumulative_cfr_series(days, low)
        candidates = sum(
            1 for d in days if high in d.split and low in d.split
        )
    elif basis == "interval":
        hi_obs = interval_cfr_series(days, high, window_obs=window_obs)
        lo_obs = interval_cfr_series(days, low, window_obs=window_obs)
        # Every window both provinces could in principle have answered, so the
        # cost of the endpoint and increment guards is reported rather than
        # showing up only as a shorter series nobody questions.
        candidates = sum(
            1
            for i in range(window_obs, len(days))
            if all(
                p in days[i - window_obs].split and p in days[i].split
                for p in (high, low)
            )
        )
    else:
        raise ValueError(f"unknown basis {basis!r}; use 'cumulative' or 'interval'")

    lo_by_date = {o.date: o.value for o in lo_obs}
    gap = [
        Observation(o.date, o.value - lo_by_date[o.date])
        for o in hi_obs
        if o.date in lo_by_date
    ]
    if len(gap) < 3:
        return Divergence(
            high, low, basis, candidates, len(gap), candidates - len(gap),
            "", float("nan"), "", float("nan"),
            float("nan"), float("nan"), None, None, None, hold_band_pp_per_30d,
            "undetermined", "fewer than three days where both provinces are scoreable",
        )

    full = _ols_slope_per_30d(gap)
    recent = _ols_slope_per_30d(gap[-recent_obs:]) if len(gap) >= recent_obs else None
    span = (gap[-1].date - gap[0].date).days
    endpoint = 30.0 * (gap[-1].value - gap[0].value) / span if span else None

    measures = [m for m in (full, recent, endpoint) if m is not None]
    agree = len(measures) >= 2 and (
        all(m > 0 for m in measures) or all(m < 0 for m in measures)
    )
    if full is None:
        verdict, reason = "undetermined", "no trend computable"
    elif not agree:
        shown = ", ".join(
            f"{label} {'n/a' if m is None else format(m, '+.2f')}"
            for label, m in (("full", full), ("recent", recent), ("endpoint", endpoint))
        )
        verdict = "holding"
        reason = (
            f"the trend measures do not agree in sign ({shown} pp/30d) so this "
            "window does not resolve a direction"
        )
    elif abs(full) <= hold_band_pp_per_30d:
        verdict = "holding"
        reason = (
            f"trend {full:+.2f} pp/30d sits inside the {hold_band_pp_per_30d:.1f} pp "
            "hold band"
        )
    else:
        verdict = "widening" if full > 0 else "converging"
        reason = (
            f"all three measures agree in sign and the full-window trend "
            f"{full:+.2f} pp/30d clears the {hold_band_pp_per_30d:.1f} pp hold band"
        )
    return Divergence(
        high=high,
        low=low,
        basis=basis,
        n_candidates=candidates,
        n_points=len(gap),
        n_refused=candidates - len(gap),
        first_date=gap[0].date.isoformat(),
        first_gap_pp=gap[0].value,
        last_date=gap[-1].date.isoformat(),
        last_gap_pp=gap[-1].value,
        min_gap_pp=min(g.value for g in gap),
        max_gap_pp=max(g.value for g in gap),
        slope_full_pp_per_30d=full,
        slope_recent_pp_per_30d=recent,
        endpoint_pp_per_30d=endpoint,
        hold_band_pp_per_30d=hold_band_pp_per_30d,
        verdict=verdict,
        reason=reason,
    )


@dataclass(frozen=True, slots=True)
class GapDecomposition:
    """Which province moved. A closing gap is not the same event as a falling CFR."""

    high: str
    low: str
    first_date: str
    last_date: str
    high_first_pp: float
    high_last_pp: float
    low_first_pp: float
    low_last_pp: float
    gap_change_pp: float
    high_contribution_pp: float
    low_contribution_pp: float


def decompose_gap(days: Sequence[ProvinceDay], high: str, low: str) -> GapDecomposition:
    """Attribute the change in a cumulative gap to each province's own movement.

    A gap that closes because the reference province's CFR climbed toward the
    other one is a different fact from a gap that closes because the outlier
    came down, and the headline number cannot tell them apart.
    """
    hi = cumulative_cfr_series(days, high)
    lo = cumulative_cfr_series(days, low)
    lo_by_date = {o.date: o.value for o in lo}
    both = [(o.date, o.value, lo_by_date[o.date]) for o in hi if o.date in lo_by_date]
    if len(both) < 2:
        raise ValueError("need at least two shared days to decompose a gap")
    (d0, h0, l0), (d1, h1, l1) = both[0], both[-1]
    return GapDecomposition(
        high=high,
        low=low,
        first_date=d0.isoformat(),
        last_date=d1.isoformat(),
        high_first_pp=h0,
        high_last_pp=h1,
        low_first_pp=l0,
        low_last_pp=l1,
        gap_change_pp=(h1 - l1) - (h0 - l0),
        high_contribution_pp=h1 - h0,
        low_contribution_pp=-(l1 - l0),
    )


# --- 4. implied unconfirmed --------------------------------------------------

@dataclass(frozen=True, slots=True)
class ReferenceBand:
    """A CFR band assembled from the reference province's own defensible estimates."""

    province: str
    as_of: str
    low_percent: float
    high_percent: float
    basis: tuple[str, ...]


def reference_band(
    days: Sequence[ProvinceDay],
    province: str,
    *,
    as_of: dt.date | str | None = None,
    windows: Iterable[int] = DEFAULT_INTERVAL_WINDOWS,
) -> ReferenceBand:
    """The spread of what the reference province's CFR could reasonably be called.

    The band is not a confidence interval and is not dressed as one. It is the
    range across estimators that a careful reader could pick between: the
    cumulative CFR, which right censoring biases low, and the interval CFR at
    several windows, which is noisier but does not carry the cohort's whole
    history. Using their spread as the band means the implied-unconfirmed range
    inherits the estimator disagreement instead of hiding it behind one choice.
    """
    day = days[_index_of(days, as_of)]
    cell = day.split.get(province)
    if cell is None or cell[0] <= 0:
        raise ValueError(f"{province} has no scoreable split on {day.date.isoformat()}")
    values = [100.0 * cell[1] / cell[0]]
    basis = [f"cumulative CFR to {day.date.isoformat()}"]
    for w in windows:
        got = interval_cfr(days, province, window_obs=w, as_of=day.date)
        if got.usable and got.cfr_percent is not None:
            values.append(got.cfr_percent)
            basis.append(f"{w}-observation interval CFR ({got.span_days}d)")
    return ReferenceBand(
        province=province,
        as_of=day.date.isoformat(),
        low_percent=min(values),
        high_percent=max(values),
        basis=tuple(basis),
    )


@dataclass(frozen=True, slots=True)
class ImpliedUnconfirmed:
    """The consequent of a conditional. Never a count of anybody.

    Every field below answers "if the assumption held, what would follow"; none
    of them answers "what is". The `conditional` string carries the assumption
    and must travel with the numbers wherever they are quoted.
    """

    province: str
    as_of: str
    observed_confirmed: int
    observed_deaths: int
    observed_cfr_percent: float
    reference_province: str | None
    reference_low_percent: float
    reference_high_percent: float
    reference_basis: tuple[str, ...]
    implied_total_low: int
    implied_total_high: int
    implied_unconfirmed_low: int
    implied_unconfirmed_high: int
    confirmed_share_low: float
    confirmed_share_high: float
    conditional: str
    caveats: tuple[str, ...]
    is_inference: bool = True


def implied_unconfirmed(
    days: Sequence[ProvinceDay],
    province: str,
    *,
    reference_province: str | None = None,
    band: tuple[float, float] | None = None,
    as_of: dt.date | str | None = None,
    windows: Iterable[int] = DEFAULT_INTERVAL_WINDOWS,
) -> ImpliedUnconfirmed:
    """How large the artifact would have to be for the assumption to hold.

    Exactly one of `reference_province` or `band` must be given. There is no
    default reference, because the choice of reference is the whole assumption
    and a default would let it be inherited silently by someone who never
    considered it.

    The arithmetic is one line -- deaths divided by an assumed CFR -- and its
    entire content is the assumption. Two things it explicitly does not do:
    it does not treat the result as a case count, and it does not report a
    negative shortfall. A reference CFR at or above the province's observed CFR
    implies no missing confirmations at all, which is a real answer and is
    reported as zero with the reason attached, not as a negative number.
    """
    if (reference_province is None) == (band is None):
        raise ValueError(
            "give exactly one of reference_province or band; the reference CFR is "
            "the assumption under test and must be named at the call site"
        )
    day = days[_index_of(days, as_of)]
    cell = day.split.get(province)
    if cell is None or cell[0] <= 0:
        raise ValueError(f"{province} has no scoreable split on {day.date.isoformat()}")
    confirmed, deaths = cell

    if reference_province is not None:
        ref = reference_band(days, reference_province, as_of=day.date, windows=windows)
        lo_pct, hi_pct, basis = ref.low_percent, ref.high_percent, ref.basis
    else:
        lo_pct, hi_pct = float(min(band)), float(max(band))
        basis = (f"caller-supplied band {lo_pct:.1f}-{hi_pct:.1f}%",)
    if lo_pct <= 0.0:
        raise ValueError("a reference CFR of zero implies an unbounded case count")

    observed_cfr = 100.0 * deaths / confirmed
    # A lower assumed CFR implies more cases, so the band inverts.
    total_high = int(round(deaths / (lo_pct / 100.0)))
    total_low = int(round(deaths / (hi_pct / 100.0)))
    total_low, total_high = max(total_low, confirmed), max(total_high, confirmed)

    caveats = [
        "This is what an assumption implies, not an observation. No unconfirmed "
        "case is counted here and none may be reported as counted.",
        "No incidence, growth rate or projection may be derived from this range; "
        "the instrument speaks to ascertainment only.",
        "Deaths are treated as observed. That assumes death ascertainment in "
        f"{province} is better than case ascertainment, which is the same "
        "asymmetry under test and is not verified here.",
        "A CFR difference can also come from case mix, treatment access or time "
        "to care. Ascertainment is one candidate explanation, not the residual.",
        "Both sides of the ratio are censored: cumulative deaths lag cumulative "
        "cases, so the implied total understates what the already-confirmed cohort "
        "will eventually record. The range is conservative at both ends and is not "
        "a bound.",
    ]
    if hi_pct >= observed_cfr:
        caveats.append(
            f"The upper end of the reference band ({hi_pct:.1f}%) is at or above "
            f"{province}'s observed CFR ({observed_cfr:.1f}%), so at that end the "
            "assumption implies no missing confirmations at all."
        )
    return ImpliedUnconfirmed(
        province=province,
        as_of=day.date.isoformat(),
        observed_confirmed=confirmed,
        observed_deaths=deaths,
        observed_cfr_percent=observed_cfr,
        reference_province=reference_province,
        reference_low_percent=lo_pct,
        reference_high_percent=hi_pct,
        reference_basis=basis,
        implied_total_low=total_low,
        implied_total_high=total_high,
        implied_unconfirmed_low=total_low - confirmed,
        implied_unconfirmed_high=total_high - confirmed,
        confirmed_share_low=100.0 * confirmed / total_high,
        confirmed_share_high=100.0 * confirmed / total_low,
        conditional=(
            f"IF {province}'s true CFR were {lo_pct:.1f}-{hi_pct:.1f}% "
            + (f"(the band {reference_province} itself occupies) " if reference_province else "")
            + f"THEN its {deaths} confirmed deaths to {day.date.isoformat()} would "
            f"imply {total_low}-{total_high} cases, against {confirmed} confirmed."
        ),
        caveats=tuple(caveats),
    )


# --- the discriminator -------------------------------------------------------

@dataclass(frozen=True, slots=True)
class IsolationLoad:
    """Isolation census against recent confirmations: clinical load the lab missed.

    Under the ascertainment reading, a province whose lab cannot keep up still
    sees the patients -- they arrive, they are isolated on clinical suspicion,
    and they are never confirmed. That province should therefore hold more
    people in isolation per recent confirmation than a province whose lab keeps
    up. Under the genuine-severity reading both provinces confirm what they see
    and the ratio should be comparable.
    """

    province: str
    date: str
    in_isolation: int
    recent_confirmed: int
    lookback_days: int
    isolated_per_recent_confirmed: float | None
    usable: bool
    note: str


def isolation_load(
    rows: Sequence[dict],
    days: Sequence[ProvinceDay],
    *,
    as_of: dt.date | str | None = None,
    lookback_obs: int = DEFAULT_ISOLATION_LOOKBACK,
    min_recent_confirmed: int = DEFAULT_MIN_RECENT_CONFIRMED,
) -> list[IsolationLoad]:
    """The isolation ratio per province, for the day's split.

    The denominator is recent confirmations rather than the cumulative total,
    because an isolation census is a stock: comparing it to Ituri's entire
    epidemic history would penalise the oldest province for being old.
    """
    end_i = _index_of(days, as_of)
    day = days[end_i]
    iso_by_date: dict[dt.date, dict[str, int]] = {}
    for row in rows:
        when = _parse_day(row.get("data_as_of"))
        raw = row.get("isolation_by_province")
        if when is None or not raw or when in iso_by_date:
            continue
        iso_by_date[when] = {
            k: int(v)
            for k, v in raw.items()
            if k != _ISOLATION_TOTAL_KEY and v is not None
        }
    iso = iso_by_date.get(day.date, {})
    start = days[end_i - lookback_obs] if end_i - lookback_obs >= 0 else None

    out: list[IsolationLoad] = []
    for name in sorted(day.split, key=lambda n: (-day.split[n][0], n)):
        held = iso.get(name)
        if held is None:
            out.append(IsolationLoad(
                name, day.date.isoformat(), 0, 0, 0, None, False,
                "no isolation census reported for this province on this day",
            ))
            continue
        if start is None or name not in start.split:
            out.append(IsolationLoad(
                name, day.date.isoformat(), held, 0, 0, None, False,
                "no comparable earlier day for the recent-confirmation denominator",
            ))
            continue
        if not (start.reconciled and day.reconciled):
            out.append(IsolationLoad(
                name, day.date.isoformat(), held, 0, 0, None, False,
                "an endpoint does not reconcile with the national total",
            ))
            continue
        recent = day.split[name][0] - start.split[name][0]
        span = (day.date - start.date).days
        if recent < min_recent_confirmed:
            out.append(IsolationLoad(
                name, day.date.isoformat(), held, recent, span, None, False,
                f"only {recent} confirmations in the window; below the "
                f"{min_recent_confirmed} needed for a stable ratio",
            ))
            continue
        out.append(IsolationLoad(
            name, day.date.isoformat(), held, recent, span, held / recent, True, "",
        ))
    return out


@dataclass(frozen=True, slots=True)
class Discriminator:
    """What the two readings predict, and which way the available observable leans."""

    subject: str
    reference: str
    date: str
    subject_ratio: float | None
    reference_ratio: float | None
    ratio_of_ratios: float | None
    ascertainment_predicts: str
    severity_predicts: str
    leaning: str
    settles_it: str
    caveats: tuple[str, ...]


def discriminator(
    rows: Sequence[dict],
    days: Sequence[ProvinceDay],
    *,
    subject: str,
    reference: str,
    as_of: dt.date | str | None = None,
    lookback_obs: int = DEFAULT_ISOLATION_LOOKBACK,
) -> Discriminator:
    """State the falsifiable difference between the two readings, and score what we have.

    "Leaning" is deliberately weak language. The isolation ratio is a real
    independent observable -- it uses beds, not deaths, so it cannot be a
    restatement of the CFR arithmetic -- but it is also governed by bed supply
    and length of stay, and it counts suspects. It can point; it cannot settle.
    What settles it is named separately and is not in this extract.
    """
    loads = {l.province: l for l in isolation_load(
        rows, days, as_of=as_of, lookback_obs=lookback_obs
    )}
    s, r = loads.get(subject), loads.get(reference)
    s_ratio = s.isolated_per_recent_confirmed if s and s.usable else None
    r_ratio = r.isolated_per_recent_confirmed if r and r.usable else None
    ror = s_ratio / r_ratio if s_ratio and r_ratio else None

    if ror is None:
        leaning = "no comparable isolation ratio on this day; the observable is silent"
    elif ror > 1.0:
        leaning = (
            f"{subject} holds {ror:.2f}x as many people in isolation per recent "
            f"confirmation as {reference}. That is the direction the ascertainment "
            "reading predicts: clinical load arriving that the lab never confirms."
        )
    else:
        leaning = (
            f"{subject} holds {ror:.2f}x the isolation load of {reference} per recent "
            "confirmation, at or below parity. The ascertainment reading predicted an "
            "excess and does not get one here."
        )
    return Discriminator(
        subject=subject,
        reference=reference,
        date=days[_index_of(days, as_of)].date.isoformat(),
        subject_ratio=s_ratio,
        reference_ratio=r_ratio,
        ratio_of_ratios=ror,
        leaning=leaning,
        ascertainment_predicts=(
            f"{subject} confirms a death-enriched subset, so people it never confirms "
            "still reach care: its isolation census per recent confirmation runs above "
            f"{reference}'s, and its interval CFR falls toward the reference band when "
            "confirmation capacity is delivered, with no break in its death series."
        ),
        severity_predicts=(
            f"{subject} sees and confirms the same spectrum {reference} does and its "
            "patients die more often: its isolation census per recent confirmation is "
            "comparable, and its interval CFR holds near its current level after "
            "confirmation capacity is delivered, while confirmed cases rise no faster "
            "than trend."
        ),
        settles_it=(
            f"The interval CFR of the {subject} cohort confirmed AFTER a dated, "
            "evidenced delivery of laboratory confirmation capacity, read against its "
            "own death series over the same window. Ascertainment predicts confirmed "
            "cases step up while deaths continue on trend, so the interval CFR falls "
            f"toward {reference}'s band. Genuine severity predicts the interval CFR "
            "holds. Absent that delivery, the cheaper decisive observable is the "
            f"{subject} line list: the share of confirmations taken at or after death, "
            f"and the onset-to-confirmation distribution, against {reference}'s. "
            "Neither field is in this extract, which is why this instrument does not "
            "close the question."
        ),
        caveats=(
            "Isolation occupancy is a stock governed by bed supply and length of stay; "
            "a province with more beds isolates more people for reasons unrelated to "
            "ascertainment.",
            "The isolation census counts suspects as well as confirmed cases, which is "
            "what makes it informative here and also what makes it soft.",
            "A single day's ratio is not a series. It points; it does not settle.",
        ),
    )


# --- assembled report --------------------------------------------------------

def report(
    path: Path | str = DEFAULT_SERIES,
    *,
    subject: str = "Nord-Kivu",
    reference: str = "Ituri",
    as_of: dt.date | str | None = None,
    max_ci_width_pp: float = DEFAULT_MAX_CI_WIDTH_PP,
    interval_window: int = DEFAULT_INTERVAL_WINDOW,
) -> dict:
    """Everything the instrument can say, in one deterministic structure.

    The documentation is written from this and only this, so no figure in the
    prose can drift away from the series it claims to come from.
    """
    rows = load_rows(path)
    days = province_days(rows)
    return {
        "as_of": days[_index_of(days, as_of)].date.isoformat(),
        "first_split_day": days[0].date.isoformat(),
        "n_split_days": len(days),
        "provinces": provinces_seen(days),
        "reconciliation": reconciliation(days),
        "cfr_table": cumulative_cfr(days, as_of=as_of, max_ci_width_pp=max_ci_width_pp),
        "interval_table": [
            interval_cfr(days, p, window_obs=interval_window, as_of=as_of)
            for p in provinces_seen(days)
        ],
        "divergence_cumulative": divergence(days, subject, reference, basis="cumulative"),
        "divergence_interval": divergence(
            days, subject, reference, basis="interval", window_obs=interval_window
        ),
        "decomposition": decompose_gap(days, subject, reference),
        "reference_band": reference_band(days, reference, as_of=as_of),
        "implied": implied_unconfirmed(
            days, subject, reference_province=reference, as_of=as_of
        ),
        "isolation": isolation_load(rows, days, as_of=as_of),
        "discriminator": discriminator(
            rows, days, subject=subject, reference=reference, as_of=as_of
        ),
    }


@dataclass(frozen=True, slots=True)
class Reconciliation:
    n_days: int
    n_reconciled: int
    tolerance: int
    unreconciled_days: tuple[str, ...]
    max_confirmed_gap: int
    max_deaths_gap: int
    gap_histogram: tuple[tuple[int, int], ...]


def reconciliation(days: Sequence[ProvinceDay]) -> Reconciliation:
    """How far the province split drifts from the national line, and on which days.

    Published so the tolerance is auditable rather than inherited: a reader can
    see that the disagreements cluster at zero and one, and that the exceptions
    are a single contiguous stall.
    """
    hist: dict[int, int] = {}
    worst_c = worst_d = 0
    bad: list[str] = []
    for day in days:
        gaps = [abs(g) for g in (day.confirmed_gap, day.deaths_gap) if g is not None]
        if gaps:
            hist[max(gaps)] = hist.get(max(gaps), 0) + 1
        worst_c = max(worst_c, abs(day.confirmed_gap or 0))
        worst_d = max(worst_d, abs(day.deaths_gap or 0))
        if not day.reconciled:
            bad.append(day.date.isoformat())
    return Reconciliation(
        n_days=len(days),
        n_reconciled=len(days) - len(bad),
        tolerance=DEFAULT_RECONCILE_TOLERANCE,
        unreconciled_days=tuple(bad),
        max_confirmed_gap=worst_c,
        max_deaths_gap=worst_d,
        gap_histogram=tuple(sorted(hist.items())),
    )


def _render(rep: dict) -> str:
    lines: list[str] = []
    add = lines.append
    add(f"CFR ascertainment instrument -- data as of {rep['as_of']}")
    add(f"province splits: {rep['n_split_days']} data days from {rep['first_split_day']}")
    rec = rep["reconciliation"]
    add(
        f"reconciliation: {rec.n_reconciled}/{rec.n_days} days agree with the national "
        f"line within {rec.tolerance}; |gap| histogram {list(rec.gap_histogram)}; "
        f"worst confirmed {rec.max_confirmed_gap}, worst deaths {rec.max_deaths_gap}"
    )
    add(f"  unreconciled: {', '.join(rec.unreconciled_days) or 'none'}")
    add("")
    add("cumulative CFR by province")
    for c in rep["cfr_table"]:
        flag = "" if c.precision_ok else "   [NOT SCOREABLE] " + c.note
        add(
            f"  {c.province:<11} {c.confirmed:>5} conf {c.deaths:>5} deaths  "
            f"CFR {c.cfr_percent:5.1f}%  95% CI {c.ci_low_percent:5.1f}-"
            f"{c.ci_high_percent:5.1f} (width {c.ci_width_percent:4.1f} pp){flag}"
        )
    add("")
    add("interval CFR (window ends on the as-of day)")
    for i in rep["interval_table"]:
        if i.usable:
            add(
                f"  {i.province:<11} {i.start_date} -> {i.end_date} ({i.span_days}d)  "
                f"+{i.new_confirmed} cases +{i.new_deaths} deaths  "
                f"CFR {i.cfr_percent:5.1f}%"
            )
        else:
            add(f"  {i.province:<11} unusable: {i.note}")
    add("")
    for key in ("divergence_cumulative", "divergence_interval"):
        d = rep[key]
        add(
            f"divergence [{d.basis}] {d.high} minus {d.low}, n={d.n_points} of "
            f"{d.n_candidates} candidate windows ({d.n_refused} refused by the guards)"
        )
        add(
            f"  {d.first_date} {d.first_gap_pp:+.1f} pp -> {d.last_date} "
            f"{d.last_gap_pp:+.1f} pp   range {d.min_gap_pp:+.1f} to {d.max_gap_pp:+.1f}"
        )
        slopes = [d.slope_full_pp_per_30d, d.slope_recent_pp_per_30d, d.endpoint_pp_per_30d]
        add(
            "  trends pp/30d: full {}, recent {}, endpoint {}".format(
                *["n/a" if s is None else f"{s:+.2f}" for s in slopes]
            )
        )
        add(f"  VERDICT: {d.verdict} -- {d.reason}")
        add("")
    dc = rep["decomposition"]
    add(f"gap decomposition, cumulative basis, {dc.first_date} -> {dc.last_date}")
    add(
        f"  {dc.high}: {dc.high_first_pp:.1f}% -> {dc.high_last_pp:.1f}%   "
        f"{dc.low}: {dc.low_first_pp:.1f}% -> {dc.low_last_pp:.1f}%"
    )
    add(
        f"  gap change {dc.gap_change_pp:+.1f} pp = {dc.high_contribution_pp:+.1f} from "
        f"{dc.high} + {dc.low_contribution_pp:+.1f} from {dc.low}"
    )
    add("")
    rb = rep["reference_band"]
    add(f"reference band from {rb.province} at {rb.as_of}: "
        f"{rb.low_percent:.1f}% - {rb.high_percent:.1f}%")
    for b in rb.basis:
        add(f"  - {b}")
    add("")
    im = rep["implied"]
    add("IMPLIED UNCONFIRMED -- an inference, not an observation")
    add(f"  {im.conditional}")
    add(
        f"  implied unconfirmed range: {im.implied_unconfirmed_low}-"
        f"{im.implied_unconfirmed_high}; confirmed share "
        f"{im.confirmed_share_low:.1f}-{im.confirmed_share_high:.1f}%"
    )
    for c in im.caveats:
        add(f"  ! {c}")
    add("")
    add("isolation load (census per recent confirmation)")
    for l in rep["isolation"]:
        if l.usable:
            add(
                f"  {l.province:<11} {l.in_isolation:>4} isolated / {l.recent_confirmed:>4} "
                f"confirmed in {l.lookback_days}d = "
                f"{l.isolated_per_recent_confirmed:.3f}"
            )
        else:
            add(f"  {l.province:<11} not scored: {l.note}")
    add("")
    ds = rep["discriminator"]
    add(f"DISCRIMINATOR {ds.subject} vs {ds.reference} at {ds.date}")
    add(f"  ascertainment reading predicts: {ds.ascertainment_predicts}")
    add(f"  severity reading predicts:      {ds.severity_predicts}")
    add(f"  available observable leans:     {ds.leaning}")
    add(f"  what would settle it:           {ds.settles_it}")
    for c in ds.caveats:
        add(f"  ! {c}")
    return "\n".join(lines)


if __name__ == "__main__":  # pragma: no cover
    print(_render(report()))
