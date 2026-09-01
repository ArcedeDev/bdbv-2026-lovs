"""Forecasting the end of the outbreak, not the middle of it.

Why this exists. Every principal asks the same question -- when is this over --
and almost nobody answers it, because WHO's answer is a bright line and being
wrong against a bright line is embarrassing in a way a wrong nowcast is not.
WHO declares an Ebola outbreak over 42 days (two maximum incubation periods)
after the second negative test or the safe burial of the last confirmed case.
There is already a live scoring precedent for this outbreak: the Uganda arm was
declared over on 2026-08-26. A forecaster that will not price the DRC arm is
declining the only question with a published resolution rule.

The countdown, and the trap in it. The clock is a run of days with no confirmed
case, so the whole mechanic turns on what counts as a zero-case day. A SitRep
that reports zero confirmed cases is a zero-case day. A day with no SitRep is
not: it is an unobserved day, and crediting it to a countdown invents evidence
of exactly the thing the countdown is supposed to establish. This module
therefore clamps every elapsed-time figure to the last DATA day, never the
publication day and never a system clock, and refuses to start a clock unless
the last observed day actually reported zero. The failure mode being blocked is
live in this system: a reporting gap otherwise reads as observed improvement.

What is forecast, and how. Three nested events over each horizon, from the
block bootstrap in `opsforecast`:

    (a) the outbreak records a first zero-case day;
    (b) a 42-day clock is running or has completed at the horizon;
    (c) the outbreak is declared over.

Nested by construction -- (c) implies (b) implies (a) -- because a completed
clock is a zero run and a zero run contains a zero day. (b) is deliberately not
read as "any zero day starts a clock", which would make it identical to (a) and
therefore worthless; a single zero day mid-outbreak is noise, and the clock that
matters is one that is still standing when the horizon arrives.

The zero boundary is a modelling choice, so both choices are reported. A pure
difference bootstrap has no absorbing state at zero: a path that touches zero
bounces straight back up, which makes sustained termination nearly impossible by
construction. Treating zero as absorbing makes continuation impossible by
construction instead. Neither is true, and this series contains no zero days
from which a persistence could be estimated, so inventing one would be authoring
a number. Both variants are run on the SAME seeded draws and reported as a
bracket: `reflecting` is a floor on termination, `absorbing` a ceiling.

Honesty about small numbers. At the last data day the outbreak was recording 82
confirmed cases a day, so most of these probabilities are small, and small is
the correct answer. But the walk-forward backtest in
`docs/operational-forecaster-validation.md` measured this bootstrap as
OVERCONFIDENT at the low end: forecasts priced 0.05 occurred 23% of the time,
forecasts priced 0.15 occurred 42% of the time. Every probability emitted inside
that measured band carries the caveat with it. Nothing is silently inflated to
compensate -- the raw model value is reported, with the bias stated, exactly as
the operational pin block does with its bias tests.

Deterministic: every draw is seeded, so a run reproduces exactly. Stdlib only.
No network. No clock of its own -- the as-of date is passed in.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence

from lovs.forecast import opsforecast as of

METRIC = "new_confirmed_today"

# WHO's rule, not a fitted parameter: two maximum incubation periods of 21 days.
CLOCK_DAYS = 42

# The countdown legally starts at the second negative test or the safe burial of
# the last case, both of which fall at or after the day that case was last
# counted. Zero is therefore the most termination-favourable lag available,
# which is the point: it makes every date here an EARLIEST-possible date rather
# than a guess, and every declaration probability an upper bound.
DEFAULT_CLOCK_START_LAG_DAYS = 0

DEFAULT_HORIZONS = (30, 60, 90)
DEFAULT_SEED = 6042
# Trailing windows for the decay read. Two weeks, four weeks, eight weeks: short
# enough to catch a turn, long enough that the sign flipping between them is
# itself the finding.
DEFAULT_DECAY_WINDOWS = (14, 28, 56)
DEFAULT_WITHIN_DAYS = 365
# The level the daily count has to fall below before zero-case days are the
# expected outcome rather than a fluctuation. One case a day is that level.
TERMINATION_LEVEL = 1.0

# Reliability of this bootstrap, transcribed from the walk-forward backtest in
# docs/operational-forecaster-validation.md: (priced, n, observed frequency).
# Kept here so the caveat band is DERIVED from the measurement rather than
# asserted by whoever writes the next forecast.
MEASURED_RELIABILITY: tuple[tuple[float, int, float], ...] = (
    (0.05, 81, 0.23),
    (0.15, 19, 0.42),
    (0.25, 31, 0.19),
    (0.35, 43, 0.40),
    (0.45, 55, 0.42),
    (0.55, 51, 0.61),
    (0.65, 47, 0.66),
    (0.75, 33, 0.70),
    (0.85, 29, 0.93),
    (0.95, 61, 0.90),
)
# A bin counts as materially miscalibrated when the thing happened at least
# twice as often as it was priced. That is a judgement about when to WARN, not a
# number entering any forecast: no probability below is adjusted by it.
MATERIAL_MISCALIBRATION_RATIO = 2.0
LOW_BAND_MAX = max(
    priced
    for priced, _n, observed in MEASURED_RELIABILITY
    if observed >= MATERIAL_MISCALIBRATION_RATIO * priced
)


def reliability_bin(probability: float) -> tuple[float, int, float]:
    """The backtest bin a probability falls in, for quoting alongside it."""
    return min(MEASURED_RELIABILITY, key=lambda row: abs(row[0] - probability))


def low_band_caveat(probability: float) -> str | None:
    """The overconfidence warning, or None above the band where it was measured.

    Attached to the number rather than printed once at the top of a document,
    because the number is what gets quoted onward and the caveat has to travel
    with it.
    """
    if probability > LOW_BAND_MAX:
        return None
    priced, n, observed = reliability_bin(probability)
    return (
        f"RAW MODEL VALUE, NOT ADJUSTED. The walk-forward backtest measured this "
        f"bootstrap as overconfident at the low end: {n} forecasts priced "
        f"{priced:.2f} occurred {observed:.0%} of the time. Read any figure at or "
        f"below {LOW_BAND_MAX:.2f} as a floor on the true probability, not as an "
        f"estimate of it. See docs/operational-forecaster-validation.md."
    )


# --- the countdown -----------------------------------------------------------

@dataclass(frozen=True, slots=True)
class CountdownState:
    """Where the 42-day clock actually stands, and how much of it is observed."""

    as_of: str
    last_data_day: str
    effective_as_of: str
    unobserved_trailing_days: int
    last_positive_day: str | None
    last_positive_value: float | None
    observed_zero_days: int
    clock_running: bool
    clock_start: str | None
    clock_day: int | None
    clock_days_remaining: int | None
    observed_days_on_clock: int
    unobserved_days_on_clock: int
    earliest_declaration: str
    days_from_last_data_day: int
    days_from_as_of: int
    note: str


def countdown(
    obs: Sequence[of.Observation],
    as_of: dt.date,
    *,
    clock_days: int = CLOCK_DAYS,
    clock_start_lag_days: int = DEFAULT_CLOCK_START_LAG_DAYS,
) -> CountdownState:
    """The countdown state on `as_of`, counted only over days that were reported.

    Two rules do all the work here.

    First, the clamp. Elapsed time is measured to the last day the series carries
    DATA, never to `as_of` itself. If four days have passed with no SitRep, those
    four days are unobserved; counting them as elapsed countdown would turn a
    surveillance failure into apparent progress toward termination.

    Second, the start condition. A clock starts only if the last observed day
    reported zero confirmed cases. A trailing reporting gap after a day that
    reported cases leaves the clock stopped, however long the gap runs. This is
    the difference between "no cases were reported" and "no cases occurred", and
    it is the single thing this module exists to get right.
    """
    if not obs:
        raise ValueError("no observations of the case series")
    visible = [o for o in obs if o.date <= as_of]
    if not visible:
        raise ValueError(f"the series begins after as_of {as_of.isoformat()}")

    last_data = visible[-1].date
    positives = [o for o in visible if o.value > 0]
    last_pos = positives[-1] if positives else None

    if last_pos is None:
        # Every observed day is a zero. The clock can start no earlier than the
        # first day actually observed; days before the series began are unknown.
        clock_start = visible[0].date + dt.timedelta(days=clock_start_lag_days)
    else:
        clock_start = last_pos.date + dt.timedelta(days=1 + clock_start_lag_days)

    zero_obs = [o for o in visible if o.value <= 0 and o.date >= clock_start]
    running = clock_start <= last_data and visible[-1].value <= 0
    window_days = (last_data - clock_start).days + 1 if clock_start <= last_data else 0
    unobserved_on_clock = window_days - len(zero_obs)

    earliest = clock_start + dt.timedelta(days=clock_days)
    notes: list[str] = []
    if not running:
        notes.append(
            f"No countdown is running: the last observed day {last_data.isoformat()} "
            f"reported {visible[-1].value:g} confirmed cases."
        )
    trailing = (as_of - last_data).days
    if trailing > 0:
        notes.append(
            f"{trailing} calendar day(s) after the last data day are unreported. "
            f"They are not zero-case days and are credited to no countdown."
        )
    if running and unobserved_on_clock > 0:
        notes.append(
            f"{unobserved_on_clock} of the {window_days} calendar days on the clock "
            f"carry no SitRep observation, so the run is not fully verified."
        )

    return CountdownState(
        as_of=as_of.isoformat(),
        last_data_day=last_data.isoformat(),
        effective_as_of=min(as_of, last_data).isoformat(),
        unobserved_trailing_days=trailing,
        last_positive_day=last_pos.date.isoformat() if last_pos else None,
        last_positive_value=last_pos.value if last_pos else None,
        observed_zero_days=len(zero_obs),
        clock_running=running,
        clock_start=clock_start.isoformat() if running else None,
        clock_day=(last_data - clock_start).days if running else None,
        clock_days_remaining=(
            clock_days - (last_data - clock_start).days if running else None
        ),
        observed_days_on_clock=len(zero_obs),
        unobserved_days_on_clock=max(0, unobserved_on_clock),
        earliest_declaration=earliest.isoformat(),
        days_from_last_data_day=(earliest - last_data).days,
        days_from_as_of=(earliest - as_of).days,
        note=" ".join(notes) if notes else "Countdown running and fully observed.",
    )


# --- path shapes -------------------------------------------------------------

def _zero_runs(seq: Sequence[float]) -> list[tuple[int, int]]:
    """Half-open index ranges of consecutive zero-case days."""
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for i, value in enumerate(seq):
        if value <= 0.0:
            if start is None:
                start = i
        elif start is not None:
            runs.append((start, i))
            start = None
    if start is not None:
        runs.append((start, len(seq)))
    return runs


def _absorb(seq: Sequence[float]) -> list[float]:
    """The same path with zero made absorbing: once at zero, it stays there.

    Not a claim that outbreaks never resurge. It is the deliberate opposite
    extreme to the bootstrap's reflecting boundary, so the pair brackets the
    answer instead of pretending one boundary is the truth.
    """
    for i, value in enumerate(seq):
        if value <= 0.0:
            return list(seq[: i + 1]) + [0.0] * (len(seq) - i - 1)
    return list(seq)


def _outcomes(seq: Sequence[float], clock_days: int) -> tuple[bool, bool, bool]:
    """(first zero day, clock standing at the horizon, declared over) for one path.

    A declaration needs the clock start day, the `clock_days` days that elapse,
    and the declaration day itself to be free of confirmed cases -- a case on the
    declaration day resets the clock like any other. That is `clock_days + 1`
    consecutive zero days.
    """
    runs = _zero_runs(seq)
    if not runs:
        return (False, False, False)
    declared = any(end - start >= clock_days + 1 for start, end in runs)
    standing = seq[-1] <= 0.0 or declared
    return (True, standing, declared)


# --- the forecast ------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class TerminationForecast:
    """One horizon, one boundary variant, three nested probabilities."""

    horizon_days: int
    horizon_end: str
    variant: str
    p_first_zero_day: float
    p_clock_standing: float
    p_declared_over: float
    declaration_arithmetically_possible: bool
    anchor: str
    last_value: float
    n_observations: int
    n_paths: int
    block: int
    seed: int
    clock_days: int
    head_zero_days: int
    caveats: dict[str, str]


def _forecast_from_sequences(
    seqs: Sequence[Sequence[float]],
    *,
    variant: str,
    horizon_days: int,
    anchor: dt.date,
    obs: Sequence[of.Observation],
    n_paths: int,
    block: int,
    seed: int,
    clock_days: int,
    head: int,
) -> TerminationForecast:
    zero = standing = declared = 0
    for seq in seqs:
        a, b, c = _outcomes(seq, clock_days)
        zero += a
        standing += b
        declared += c
    n = len(seqs)
    values = {
        "p_first_zero_day": round(zero / n, 4),
        "p_clock_standing": round(standing / n, 4),
        "p_declared_over": round(declared / n, 4),
    }
    # A horizon shorter than the clock cannot contain a declaration however the
    # epidemic behaves. That zero is arithmetic, not a forecast, so it must not
    # carry the overconfidence caveat -- the caveat says "this may be too low",
    # and a structural zero cannot be too low.
    possible = horizon_days + head >= clock_days + 1
    caveats = {k: c for k, v in values.items() if (c := low_band_caveat(v))}
    if not possible:
        caveats["p_declared_over"] = (
            f"STRUCTURAL ZERO, NOT A FORECAST. A declaration needs {clock_days + 1} "
            f"consecutive case-free days and this horizon offers "
            f"{horizon_days + head}. No epidemic trajectory can produce one."
        )
    return TerminationForecast(
        horizon_days=horizon_days,
        horizon_end=(anchor + dt.timedelta(days=horizon_days)).isoformat(),
        variant=variant,
        declaration_arithmetically_possible=possible,
        anchor=anchor.isoformat(),
        last_value=obs[-1].value,
        n_observations=len(obs),
        n_paths=n_paths,
        block=block,
        seed=seed,
        clock_days=clock_days,
        head_zero_days=head,
        caveats=caveats,
        **values,
    )


def forecast_horizon(
    obs: Sequence[of.Observation],
    state: CountdownState,
    horizon_days: int,
    *,
    seed: int = DEFAULT_SEED,
    n_paths: int = of.DEFAULT_PATHS,
    block: int = of.DEFAULT_BLOCK,
    clock_days: int = CLOCK_DAYS,
) -> dict:
    """Both boundary variants for one horizon, off one set of seeded draws.

    Sharing the draws is what makes the pair a bracket rather than two
    unrelated runs: the variants differ only in what happens after a path first
    reaches zero, so the gap between them is the cost of the boundary assumption
    and nothing else.

    Any zero days already banked at the anchor are prepended to every path, so a
    forecast made mid-countdown accounts for the clock it inherits.
    """
    anchor = dt.date.fromisoformat(state.last_data_day)
    visible = [o for o in obs if o.date <= anchor]
    head = (state.clock_day + 1) if state.clock_running else 0
    prefix = [0.0] * head
    paths = of._paths(visible, horizon_days, n_paths, block, seed, 0.0, None)
    seqs = [prefix + p for p in paths]

    shared = dict(
        horizon_days=horizon_days, anchor=anchor, obs=visible, n_paths=n_paths,
        block=block, seed=seed, clock_days=clock_days, head=head,
    )
    reflecting = _forecast_from_sequences(seqs, variant="reflecting", **shared)
    absorbing = _forecast_from_sequences(
        [_absorb(s) for s in seqs], variant="absorbing", **shared
    )
    ends = sorted(p[-1] for p in paths)
    return {
        "horizon_days": horizon_days,
        "horizon_end": reflecting.horizon_end,
        "reflecting": asdict(reflecting),
        "absorbing": asdict(absorbing),
        "daily_cases_at_horizon": {
            "p10": round(_percentile(ends, 0.10), 1),
            "median": round(_percentile(ends, 0.50), 1),
            "p90": round(_percentile(ends, 0.90), 1),
        },
    }


def _percentile(ordered: Sequence[float], q: float) -> float:
    """Nearest-rank percentile. Deterministic, no interpolation, no numpy."""
    if not ordered:
        raise ValueError("no values")
    idx = min(len(ordered) - 1, max(0, math.ceil(q * len(ordered)) - 1))
    return ordered[idx]


# --- decay phase -------------------------------------------------------------

def _ols_slope(xs: Sequence[float], ys: Sequence[float]) -> float:
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        raise ValueError("no spread in x")
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx


@dataclass(frozen=True, slots=True)
class TrendWindow:
    window_days: int | None
    n_observations: int
    first_day: str
    last_day: str
    linear_slope_cases_per_day: float
    log_slope_per_day: float
    halving_days: float | None
    doubling_days: float | None
    days_to_below_one_case: float | None
    days_to_declaration_at_this_rate: float | None
    declaration_day_at_this_rate: str | None


@dataclass(frozen=True, slots=True)
class DecayDiagnostic:
    """Is the case count falling, and would it fall fast enough to matter."""

    metric: str
    anchor: str
    last_value: float
    windows: tuple[TrendWindow, ...]
    trend_sign_consistent: bool
    within_days: int
    days_available_for_decline: int
    required_log_slope_per_day: float
    required_halving_days: float
    fastest_observed_halving_days: float | None
    verdict: str


def _trend_window(
    obs: Sequence[of.Observation], window_days: int | None, clock_days: int = CLOCK_DAYS
) -> TrendWindow | None:
    """One trailing window's linear and log-linear trend, plus what it implies.

    The log-linear fit is the one that matters: an epidemic declines
    multiplicatively, so a halving time is the number a response can be held to,
    where a cases-per-day slope changes meaning every week. The linear slope is
    carried anyway because it is the figure people quote.
    """
    anchor = obs[-1].date
    win = (
        list(obs)
        if window_days is None
        else [o for o in obs if (anchor - o.date).days < window_days]
    )
    usable = [o for o in win if o.value > 0]
    if len(win) < 2 or len(usable) < 2:
        return None
    origin = win[0].date
    xs = [float((o.date - origin).days) for o in win]
    lin = _ols_slope(xs, [o.value for o in win])
    lxs = [float((o.date - origin).days) for o in usable]
    log = _ols_slope(lxs, [math.log(o.value) for o in usable])
    last = obs[-1].value
    to_zero = (
        math.log(last / TERMINATION_LEVEL) / -log
        if log < 0 and last > TERMINATION_LEVEL
        else None
    )
    # The last case has to land before the clock can start, so a declaration is
    # the decline plus the clock plus the declaration day itself.
    to_declaration = None if to_zero is None else to_zero + clock_days + 1
    return TrendWindow(
        window_days=window_days,
        n_observations=len(win),
        first_day=win[0].date.isoformat(),
        last_day=win[-1].date.isoformat(),
        linear_slope_cases_per_day=round(lin, 4),
        log_slope_per_day=round(log, 5),
        halving_days=round(math.log(2) / -log, 1) if log < 0 else None,
        doubling_days=round(math.log(2) / log, 1) if log > 0 else None,
        days_to_below_one_case=None if to_zero is None else round(to_zero, 1),
        days_to_declaration_at_this_rate=(
            None if to_declaration is None else round(to_declaration, 1)
        ),
        declaration_day_at_this_rate=(
            None
            if to_declaration is None
            else (obs[-1].date + dt.timedelta(days=math.ceil(to_declaration))).isoformat()
        ),
    )


def decay_diagnostic(
    obs: Sequence[of.Observation],
    *,
    windows: Sequence[int] = DEFAULT_DECAY_WINDOWS,
    within_days: int = DEFAULT_WITHIN_DAYS,
    clock_days: int = CLOCK_DAYS,
) -> DecayDiagnostic:
    """The arithmetic a decline has to satisfy before termination is on the table.

    Reported over several trailing windows on purpose. A single window is a
    two-point diff with extra steps: this series' most recent fortnight and its
    most recent two months disagree about the SIGN of the trend, and a
    diagnostic that quoted only the flattering one would be worse than none.

    The requirement is arithmetic, not statistical. Declaration by day
    `within_days` needs the last case on or before day
    `within_days - clock_days - 1`, so the count must fall from its current
    level to below one case a day inside that many days. Solving the exponential
    gives the decay rate, and hence the halving time, the response would have to
    achieve and hold.
    """
    built = [
        w
        for w in (_trend_window(obs, d, clock_days) for d in windows)
        if w is not None
    ]
    full = _trend_window(obs, None, clock_days)
    if full is not None:
        built.append(full)
    if not built:
        raise ValueError("no window carries enough observations to fit a trend")

    last = obs[-1].value
    available = within_days - clock_days - 1
    required = math.log(last / TERMINATION_LEVEL) / available
    halvings = [w.halving_days for w in built if w.halving_days is not None]
    fastest = min(halvings) if halvings else None
    required_halving = math.log(2) / required

    signs = {w.log_slope_per_day < 0 for w in built}
    consistent = len(signs) == 1

    if fastest is None:
        verdict = (
            "No trailing window is declining: on every window fitted the daily "
            "count is flat or growing, so there is no decay phase to extrapolate."
        )
    elif not consistent:
        verdict = (
            f"The sign of the trend flips with the window. The fastest declining "
            f"window halves in {fastest:.1f} days, faster than the "
            f"{required_halving:.1f} days termination inside {within_days} days "
            f"requires, but other windows over the same series are still growing, "
            f"so that decline is not established as the phase."
        )
    elif fastest <= required_halving:
        verdict = (
            f"Every fitted window is declining, fastest halving {fastest:.1f} days "
            f"against the {required_halving:.1f} days required. Termination inside "
            f"{within_days} days is arithmetically reachable if this holds."
        )
    else:
        verdict = (
            f"Every fitted window is declining, but the fastest halves in "
            f"{fastest:.1f} days against the {required_halving:.1f} days required. "
            f"The decline would have to roughly "
            f"{fastest / required_halving:.1f}x in speed."
        )

    return DecayDiagnostic(
        metric=METRIC,
        anchor=obs[-1].date.isoformat(),
        last_value=last,
        windows=tuple(built),
        trend_sign_consistent=consistent,
        within_days=within_days,
        days_available_for_decline=available,
        required_log_slope_per_day=round(required, 5),
        required_halving_days=round(required_halving, 1),
        fastest_observed_halving_days=fastest,
        verdict=verdict,
    )


# --- assembly ----------------------------------------------------------------

def report(
    rows: Sequence[dict],
    as_of: dt.date,
    *,
    horizons: Sequence[int] = DEFAULT_HORIZONS,
    seed: int = DEFAULT_SEED,
    n_paths: int = of.DEFAULT_PATHS,
    block: int = of.DEFAULT_BLOCK,
    clock_days: int = CLOCK_DAYS,
    clock_start_lag_days: int = DEFAULT_CLOCK_START_LAG_DAYS,
    within_days: int = DEFAULT_WITHIN_DAYS,
) -> dict:
    """Everything the document quotes, computed in one pass from the series."""
    obs = of.series(rows, METRIC)
    state = countdown(
        obs, as_of, clock_days=clock_days, clock_start_lag_days=clock_start_lag_days
    )
    visible = [o for o in obs if o.date <= dt.date.fromisoformat(state.last_data_day)]
    horizon_blocks = [
        forecast_horizon(
            obs, state, h, seed=seed + i, n_paths=n_paths, block=block,
            clock_days=clock_days,
        )
        for i, h in enumerate(horizons)
    ]
    return {
        "metric": METRIC,
        "as_of": as_of.isoformat(),
        "clock_days": clock_days,
        "clock_start_lag_days": clock_start_lag_days,
        "n_observations": len(visible),
        "first_data_day": visible[0].date.isoformat(),
        "countdown": asdict(state),
        "horizons": horizon_blocks,
        "decay": asdict(decay_diagnostic(
            visible, within_days=within_days, clock_days=clock_days
        )),
        "low_band_max": LOW_BAND_MAX,
        "low_band_caveat": low_band_caveat(0.0),
    }


def doc_figures(rep: dict) -> dict[str, str]:
    """The figures `docs/termination-forecast.md` must quote, as printed strings.

    Exists so the document cannot drift from the model: the test asserts every
    string here appears in the document, which makes a hand-typed number a test
    failure rather than a reading-comprehension exercise.
    """
    out: dict[str, str] = {
        "last_data_day": rep["countdown"]["last_data_day"],
        "as_of": rep["as_of"],
        "unobserved_trailing_days": str(rep["countdown"]["unobserved_trailing_days"]),
        "last_positive_day": str(rep["countdown"]["last_positive_day"]),
        "last_positive_value": f"{rep['countdown']['last_positive_value']:g}",
        "earliest_declaration": rep["countdown"]["earliest_declaration"],
        "days_from_last_data_day": str(rep["countdown"]["days_from_last_data_day"]),
        "required_halving_days": f"{rep['decay']['required_halving_days']:.1f}",
        "required_log_slope": f"{rep['decay']['required_log_slope_per_day']:.5f}",
        "days_available_for_decline": str(rep["decay"]["days_available_for_decline"]),
        "low_band_max": f"{rep['low_band_max']:.2f}",
        "decay_verdict": rep["decay"]["verdict"],
    }
    if rep["decay"]["fastest_observed_halving_days"] is not None:
        out["fastest_halving_days"] = (
            f"{rep['decay']['fastest_observed_halving_days']:.1f}"
        )
    for block in rep["horizons"]:
        h = block["horizon_days"]
        out[f"h{h}_end"] = block["horizon_end"]
        for variant in ("reflecting", "absorbing"):
            for key in ("p_first_zero_day", "p_clock_standing", "p_declared_over"):
                out[f"h{h}_{variant}_{key}"] = f"{block[variant][key]:.4f}"
        out[f"h{h}_median"] = f"{block['daily_cases_at_horizon']['median']:.1f}"
    for window in rep["decay"]["windows"]:
        tag = window["window_days"] or "full"
        out[f"trend_{tag}_log_slope"] = f"{window['log_slope_per_day']:.5f}"
        out[f"trend_{tag}_linear_slope"] = (
            f"{window['linear_slope_cases_per_day']:.4f}"
        )
        if window["halving_days"] is not None:
            out[f"trend_{tag}_halving"] = f"{window['halving_days']:.1f}"
        if window["doubling_days"] is not None:
            out[f"trend_{tag}_doubling"] = f"{window['doubling_days']:.1f}"
        if window["days_to_below_one_case"] is not None:
            out[f"trend_{tag}_days_to_below_one"] = (
                f"{window['days_to_below_one_case']:.1f}"
            )
        if window["declaration_day_at_this_rate"] is not None:
            out[f"trend_{tag}_declaration_day"] = window["declaration_day_at_this_rate"]
    return out


def _cli(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--as-of", required=True,
        help="ISO date the forecast is made on. Required: this module has no clock.",
    )
    parser.add_argument("--series", default=str(of.DEFAULT_SERIES))
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--paths", type=int, default=of.DEFAULT_PATHS)
    parser.add_argument("--figures", action="store_true",
                        help="print only the figures the document must quote")
    args = parser.parse_args(argv)
    rep = report(
        of.load_rows(Path(args.series)),
        dt.date.fromisoformat(args.as_of),
        seed=args.seed,
        n_paths=args.paths,
    )
    print(json.dumps(doc_figures(rep) if args.figures else rep, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
