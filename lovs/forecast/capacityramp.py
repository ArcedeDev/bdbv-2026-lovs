"""Response-capacity ramp curves: how long a response takes to stand a pillar up.

Why this exists. Every outbreak response ramps its pillars -- contact tracing,
alert triage, laboratory throughput, isolation beds -- from nothing to
functional. It is the first thing a health ministry or a funder asks, and
nobody publishes it. The frozen operational series carries 101 INSP/INRB SitRep
packets across the first fifteen weeks of the 2026 BDBV outbreak, which is
enough to measure the ramp for five pillar proxies and, just as usefully, to
say exactly where the record refuses to answer.

The output is a LIBRARY, not a report. One row per (pillar, threshold), with
the declaration date, the two clocks, and the censoring state carried on every
row, so the next outbreak appends its own rows and the two are read on the same
axis. A single outbreak has no error bar; the point of the shape is that the
second one gives it one.

Method. For each pillar-proxy metric, walk the observed series and record:
(a) the first observation that meets a threshold, with the width of the
unobserved window preceding it; (b) whether the threshold then HELD for
`hold_n` consecutive observations; (c) the slope over the ramp phase; and
(d) the level and variability of the plateau that follows.

Why a hold and not a touch. contact_followup_percent printed 71.8 on
2026-06-10 and 28.4 on 2026-06-11. One observation at a threshold is a data
point, not a capacity milestone, and a library built on single-day touches
would export that 71.8 as "70 percent contact tracing reached on day 26". A run
of `hold_n` consecutive observations is the smallest rule that survives a
single-day collapse in either direction.

Why two clocks. Days since declaration and days since the pillar's first
observation are different numbers, and here they differ by up to twenty days:
alert investigation was not reported at all until 2026-06-04. Reporting on the
declaration clock alone charges a reporting lag to the response as if it were a
delay in standing the pillar up. Reporting on the pillar clock alone hides the
lag entirely. Both are carried on every row; neither is a substitute.

What this cannot support.
  - It cannot date a crossing inside a reporting gap. Nothing here is
    interpolated. Where a gap straddles a threshold the milestone is reported
    as the first date it was SEEN met, with the width of the window it could
    have happened in, and `first_reach_bound` set to "interval".
  - It cannot recover a crossing that happened before a pillar was first
    reported. Where the first observation already meets the threshold the
    declaration-clock figure is an UPPER BOUND and nothing else
    (`first_reach_bound` = "left_censored").
  - Count metrics are not scale free. "500 samples a day" is a milestone for an
    outbreak of this size and meaningless for one a tenth as large. Those rows
    carry `scale_free=False`; across outbreaks compare their slopes, not their
    milestone dates.
  - hospital_isolation_total is occupancy, not capacity. It is a lower bound on
    beds that existed and it rises with demand as well as with supply.
  - lab_positivity_percent falls when testing widens AND when incidence falls.
    A milestone on it is consistent with either; it is an ascertainment proxy,
    not an ascertainment measurement.
  - n=1. Every number here is one outbreak's realisation with no dispersion
    around it. Treat the table as a first prior, not as a distribution.

Deterministic. Stdlib only. No network. No clock of its own: the declaration
date and the as-of date are parameters, and when as-of is omitted it falls back
to the extract's own stamped extraction date rather than to today, so a run in
2027 reports the same staleness as a run today.
"""
from __future__ import annotations

import datetime as dt
import json
import statistics
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence

from lovs.forecast import opsforecast as of

# The 2026-05-15 index-cluster declaration. A parameter everywhere below; this
# is only the default, so the same code runs against another outbreak.
DECLARATION = dt.date(2026, 5, 15)
OUTBREAK = "BDBV-2026-DRC"

RISING = "rising"      # higher is more functional
FALLING = "falling"    # lower is more functional

# Five consecutive observations. Long enough that the 2026-06-11 single-day
# collapse in contact follow-up can neither create nor destroy a milestone,
# short enough that a pillar reported on four days in five still registers a
# hold inside a fifteen-week record.
DEFAULT_HOLD = 5

# A reporting silence longer than a long weekend is worth saying out loud: it is
# the point past which "the pillar held" starts to rest on days nobody observed.
# Also the floor under the stopped-reporting rule, so a pillar that reports every
# single day is not declared dead the first time it skips a Monday.
NOTABLE_GAP_DAYS = 3

# One observation in five failing a threshold after it was held is the point at
# which "reached and held" stops describing the level and starts describing a
# single attainment the response then oscillated around.
UNSTABLE_MISS_RATE = 0.2


@dataclass(frozen=True, slots=True)
class PillarSpec:
    """One response pillar, the metric that proxies it, and its threshold ladder.

    `caveat` is part of the spec rather than the prose because it travels with
    the row: a proxy's weakness has to reach whoever reads the library next,
    not only whoever reads this file.
    """

    pillar: str
    metric: str
    direction: str
    thresholds: tuple[float, ...]
    unit: str
    scale_free: bool
    threshold_rationale: str
    caveat: str
    hold_n: int = DEFAULT_HOLD


PILLARS: tuple[PillarSpec, ...] = (
    PillarSpec(
        pillar="contact tracing",
        metric="contact_followup_percent",
        direction=RISING,
        thresholds=(50.0, 70.0, 80.0, 85.0),
        unit="percent",
        scale_free=True,
        threshold_rationale=(
            "80 percent is the follow-up level filovirus response plans conventionally "
            "write down as the target. This module does not verify that convention "
            "against a source and does not treat it as a finding. 50 and 70 bracket it "
            "from below so the ladder shows the SHAPE of the approach rather than one "
            "pass/fail, and 85 sits above it so a response that overshoots the "
            "convention is distinguishable from one that merely meets it."
        ),
        caveat=(
            "The denominator is contacts on the register. A register that misses "
            "contacts raises the rate; this metric cannot see that."
        ),
    ),
    PillarSpec(
        pillar="alert triage",
        metric="alert_investigation_rate_percent",
        direction=RISING,
        thresholds=(50.0, 70.0, 80.0, 85.0),
        unit="percent",
        scale_free=True,
        threshold_rationale=(
            "Deliberately the same ladder as contact tracing. Two percent pillars "
            "on one ladder is the only way the library can say which pillar stood up "
            "first without the answer being an artifact of two threshold choices."
        ),
        caveat=(
            "Alerts investigated over alerts reported. Both move; a fall in alerts "
            "reported raises the rate with no change in investigative capacity."
        ),
    ),
    PillarSpec(
        pillar="testing breadth (ascertainment proxy)",
        metric="lab_positivity_percent",
        direction=FALLING,
        thresholds=(30.0, 25.0, 20.0, 15.0),
        unit="percent",
        scale_free=True,
        threshold_rationale=(
            "The ladder descends because improvement is downward: high positivity "
            "means testing is confined to the most obviously ill. The rungs are the "
            "quartiles of the conventional 10-to-30 band rather than a published "
            "target, because no positivity target exists for filovirus outbreak "
            "testing that this module could cite."
        ),
        caveat=(
            "Positivity falls when the testing net widens and when incidence falls. "
            "A milestone here is consistent with both, which is why it is read "
            "alongside samples_analyzed: a fall in positivity against a rising "
            "denominator is the only version of it that is about ascertainment."
        ),
    ),
    PillarSpec(
        pillar="laboratory throughput",
        metric="samples_analyzed",
        direction=RISING,
        thresholds=(100.0, 200.0, 300.0, 400.0, 500.0),
        unit="samples per day",
        scale_free=False,
        threshold_rationale=(
            "Round hundred-sample capacity steps. There is no defensible per-outbreak "
            "target to anchor to, so the rungs are deliberately arbitrary and evenly "
            "spaced: the transferable quantity is the SPACING between them (how long a "
            "lab takes to add a hundred samples a day), not the rungs themselves."
        ),
        caveat=(
            "Throughput achieved, not throughput available. It is bounded above by "
            "demand: a lab that could run 600 and receives 400 reports 400."
        ),
    ),
    PillarSpec(
        pillar="isolation capacity",
        metric="hospital_isolation_total",
        direction=RISING,
        thresholds=(300.0, 500.0, 750.0),
        unit="patients in isolation",
        scale_free=False,
        threshold_rationale=(
            "Three rungs rather than five because the series spans 258 to 896 and a "
            "finer ladder would report noise as milestones. 300/500/750 are round "
            "steps inside the observed range."
        ),
        caveat=(
            "OCCUPANCY, NOT CAPACITY. This counts patients in isolation, so it is a "
            "lower bound on beds that existed and it rises with demand as readily as "
            "with supply. A rising ramp here is not unambiguously good news."
        ),
    ),
)


# --- small derived quantities -------------------------------------------------

def _meets(value: float, threshold: float, direction: str) -> bool:
    if direction == RISING:
        return value >= threshold
    if direction == FALLING:
        return value <= threshold
    raise ValueError(f"unknown direction {direction!r}")


def _times(n: int) -> str:
    return "once" if n == 1 else f"{n} times"


def _delta_unit(unit: str) -> str:
    """The unit a CHANGE in this metric is measured in.

    A percentage metric that moves by 5 has moved five percentage points, not
    five percent, and a note that says "percent" invites exactly the relative /
    absolute confusion this library exists to avoid.
    """
    return "percentage points" if unit == "percent" else unit


def _gaps(obs: Sequence[of.Observation]) -> list[int]:
    """Calendar days between consecutive observations. 1 means no gap."""
    return [(cur.date - prev.date).days for prev, cur in zip(obs, obs[1:])]


def _ols_slope(obs: Sequence[of.Observation], origin: dt.date) -> float | None:
    """Least squares slope in units per calendar day.

    Least squares rather than endpoint-to-endpoint because an endpoint slope is
    hostage to its two endpoints, and this series is known to produce single-day
    artifacts at exactly the kind of place a ramp phase ends.
    """
    if len(obs) < 2:
        return None
    xs = [float((o.date - origin).days) for o in obs]
    ys = [o.value for o in obs]
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return sxy / sxx


def _median_step(obs: Sequence[of.Observation]) -> float | None:
    """Median per-elapsed-day change, reusing the forecaster's gap normalisation.

    Reported next to the least squares slope because they disagree in an
    informative way: the median step is unmoved by one collapsed day, the OLS
    slope is not, and a large difference between them is a statement about how
    lumpy the ramp was.
    """
    steps = of.diffs(obs)
    return statistics.median(steps) if steps else None


# --- the emitted shapes -------------------------------------------------------

@dataclass(frozen=True, slots=True)
class PillarSummary:
    """Per-pillar coverage, ramp and plateau. Joins to RampCurve on `metric`."""

    outbreak: str
    pillar: str
    metric: str
    direction: str
    unit: str
    scale_free: bool
    declaration: str
    as_of: str

    n_observations: int
    n_packets: int
    first_observation_date: str
    last_observation_date: str
    first_value: float
    last_value: float
    observation_lag_days: int
    record_span_days: int
    largest_internal_gap_days: int
    largest_internal_gap_ends: str | None
    stale_days: int
    reporting_stopped: bool

    terminal_threshold: float | None
    ramp_from: str | None
    ramp_to: str | None
    ramp_days: int | None
    ramp_n: int | None
    ramp_slope_per_day: float | None
    ramp_median_step_per_day: float | None

    plateau_from: str | None
    plateau_n: int | None
    plateau_mean: float | None
    plateau_sd: float | None
    plateau_min: float | None
    plateau_max: float | None
    plateau_slope_per_day: float | None
    plateau_drift: float | None
    plateau_median_abs_step: float | None

    threshold_rationale: str
    caveat: str
    notes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RampCurve:
    """One (pillar, threshold) milestone, with its censoring state attached.

    Every field a cross-outbreak comparison needs is on the row: the outbreak
    label, both clocks, the width of the window the crossing could have happened
    in, and whether the threshold was held or only touched. A consumer that
    filters on `scale_free` and `first_reach_bound` can compare across outbreaks
    without reading this file.
    """

    outbreak: str
    pillar: str
    metric: str
    direction: str
    unit: str
    scale_free: bool
    threshold: float
    declaration: str
    as_of: str
    n_observations: int
    first_observation_date: str
    observation_lag_days: int

    reached: bool
    first_reach_date: str | None
    days_to_first_reach_declaration: int | None
    days_to_first_reach_pillar: int | None
    first_reach_window_days: int | None
    first_reach_bound: str

    hold_n: int
    held: bool
    hold_start_date: str | None
    hold_confirmed_date: str | None
    days_to_held_declaration: int | None
    days_to_held_pillar: int | None
    hold_window_days: int | None
    hold_span_days: int | None
    hold_max_gap_days: int | None

    touch_episodes_before_hold: int
    observations_after_hold: int | None
    failures_after_hold: int | None
    retained_at_last_observation: bool
    notes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RampLibrary:
    outbreak: str
    declaration: str
    as_of: str
    source: str
    pillars: tuple[PillarSummary, ...]
    curves: tuple[RampCurve, ...]

    def to_rows(self) -> list[dict]:
        """Flat tidy rows, one per (pillar, threshold). The appendable shape."""
        return [asdict(c) for c in self.curves]

    def pillar_rows(self) -> list[dict]:
        return [asdict(p) for p in self.pillars]

    def curve(self, metric: str, threshold: float) -> RampCurve:
        for c in self.curves:
            if c.metric == metric and c.threshold == threshold:
                return c
        raise KeyError(f"no curve for {metric} at {threshold}")

    def summary(self, metric: str) -> PillarSummary:
        for p in self.pillars:
            if p.metric == metric:
                return p
        raise KeyError(f"no pillar summary for {metric}")


# --- the measurement ----------------------------------------------------------

def _first_reach(
    obs: Sequence[of.Observation], threshold: float, direction: str
) -> int | None:
    for i, o in enumerate(obs):
        if _meets(o.value, threshold, direction):
            return i
    return None


def _hold_start(
    obs: Sequence[of.Observation], threshold: float, direction: str, hold_n: int
) -> int | None:
    """First index beginning `hold_n` consecutive OBSERVATIONS that meet.

    Consecutive observations, not consecutive days: the series is not daily, and
    requiring consecutive days would make a hold impossible for any pillar that
    skips a Sunday. The cost is that a run can span a reporting gap, so the
    calendar span and the largest gap inside the run are reported alongside it
    and the caller can refuse a hold that rests on unobserved days.
    """
    if len(obs) < hold_n:
        return None
    for i in range(len(obs) - hold_n + 1):
        if all(_meets(o.value, threshold, direction) for o in obs[i : i + hold_n]):
            return i
    return None


def _touch_episodes(
    obs: Sequence[of.Observation], threshold: float, direction: str, before: int | None
) -> int:
    """Maximal runs of meeting observations strictly before `before`.

    The count a reader needs to tell "it reached 70 and stayed" from "it touched
    70 four times and fell back each time". Both have the same first-reach date.
    """
    stop = len(obs) if before is None else before
    episodes, inside = 0, False
    for o in obs[:stop]:
        if _meets(o.value, threshold, direction):
            if not inside:
                episodes += 1
                inside = True
        else:
            inside = False
    return episodes


def _curve(
    obs: Sequence[of.Observation],
    spec: PillarSpec,
    threshold: float,
    *,
    outbreak: str,
    declaration: dt.date,
    as_of: dt.date,
    reporting_stopped: bool,
    stale_days: int,
) -> RampCurve:
    first, last = obs[0], obs[-1]
    lag = (first.date - declaration).days

    i = _first_reach(obs, threshold, spec.direction)
    if i is None:
        reach_date = d_decl = d_pillar = window = None
        bound = "not_reached"
    elif i == 0:
        # The pillar was already there the first time anyone reported it. The
        # crossing is inside the unobserved window between declaration and first
        # report, so the declaration-clock figure is an upper bound and the
        # pillar-clock figure is zero by construction and means nothing.
        reach_date, d_decl, d_pillar = first.date.isoformat(), lag, 0
        window, bound = lag, "left_censored"
    else:
        reach_date = obs[i].date.isoformat()
        d_decl = (obs[i].date - declaration).days
        d_pillar = (obs[i].date - first.date).days
        window = (obs[i].date - obs[i - 1].date).days
        bound = "exact" if window == 1 else "interval"

    j = _hold_start(obs, threshold, spec.direction, spec.hold_n)
    if j is None:
        hold_start = hold_confirmed = None
        hd_decl = hd_pillar = hold_window = hold_span = hold_gap = None
        after_n = after_fail = None
    else:
        run = obs[j : j + spec.hold_n]
        hold_start = run[0].date.isoformat()
        hold_confirmed = run[-1].date.isoformat()
        hd_decl = (run[0].date - declaration).days
        hd_pillar = (run[0].date - first.date).days
        hold_window = lag if j == 0 else (obs[j].date - obs[j - 1].date).days
        hold_span = (run[-1].date - run[0].date).days
        hold_gap = max(_gaps(run))
        tail = obs[j:]
        after_n = len(tail)
        after_fail = sum(
            1 for o in tail if not _meets(o.value, threshold, spec.direction)
        )

    episodes = _touch_episodes(obs, threshold, spec.direction, j)

    notes: list[str] = []
    if bound == "interval":
        notes.append(
            f"crossing lies in the {window} days ending {reach_date}; the preceding "
            f"{window - 1} day(s) are unobserved and are not interpolated"
        )
    if bound == "left_censored":
        notes.append(
            f"already met at the pillar's first observation "
            f"({first.date.isoformat()}); the crossing happened during the {lag} "
            f"unobserved days after declaration, so {lag} is an upper bound and the "
            f"pillar-clock figure is not meaningful"
        )
    if bound == "not_reached":
        notes.append(
            f"never observed at this threshold in {len(obs)} observations "
            f"(range {min(o.value for o in obs):g} to {max(o.value for o in obs):g})"
        )
    if j is None and i is not None:
        notes.append(
            f"reached but never held: touched {_times(episodes)}, no episode "
            f"{spec.hold_n} consecutive observations long"
        )
    if j is not None and episodes:
        notes.append(f"touched and lost it {_times(episodes)} before it held")
    if hold_gap is not None and hold_gap > NOTABLE_GAP_DAYS:
        notes.append(
            f"the qualifying run spans a {hold_gap}-day reporting gap, so the hold is "
            f"asserted partly across unobserved days"
        )
    if hold_window is not None and hold_window > 1 and j is not None and j > 0:
        notes.append(
            f"the hold start date is uncertain to {hold_window} days for the same "
            f"reason the first reach is"
        )
    if after_n and after_fail / after_n > UNSTABLE_MISS_RATE:
        notes.append(
            f"first attainment, not a stable level: after the hold started it failed "
            f"this threshold on {after_fail} of {after_n} observations "
            f"({after_fail / after_n:.0%})"
        )
    if reporting_stopped:
        notes.append(
            f"this metric stopped reporting {stale_days} days before the as-of date; "
            f"nothing on this row extends past {last.date.isoformat()}"
        )
    if not spec.scale_free:
        notes.append(
            "scale dependent threshold: not transferable to an outbreak of a different "
            "size, compare the pillar's slope instead"
        )

    return RampCurve(
        outbreak=outbreak,
        pillar=spec.pillar,
        metric=spec.metric,
        direction=spec.direction,
        unit=spec.unit,
        scale_free=spec.scale_free,
        threshold=threshold,
        declaration=declaration.isoformat(),
        as_of=as_of.isoformat(),
        n_observations=len(obs),
        first_observation_date=first.date.isoformat(),
        observation_lag_days=lag,
        reached=i is not None,
        first_reach_date=reach_date,
        days_to_first_reach_declaration=d_decl,
        days_to_first_reach_pillar=d_pillar,
        first_reach_window_days=window,
        first_reach_bound=bound,
        hold_n=spec.hold_n,
        held=j is not None,
        hold_start_date=hold_start,
        hold_confirmed_date=hold_confirmed,
        days_to_held_declaration=hd_decl,
        days_to_held_pillar=hd_pillar,
        hold_window_days=hold_window,
        hold_span_days=hold_span,
        hold_max_gap_days=hold_gap,
        touch_episodes_before_hold=episodes,
        observations_after_hold=after_n,
        failures_after_hold=after_fail,
        retained_at_last_observation=_meets(last.value, threshold, spec.direction),
        notes=tuple(notes),
    )


def _stringency_order(spec: PillarSpec) -> list[float]:
    """Thresholds from least to most demanding, in the pillar's own direction."""
    return sorted(spec.thresholds, reverse=(spec.direction == FALLING))


def pillar_ramp(
    rows: Sequence[dict],
    spec: PillarSpec,
    *,
    outbreak: str = OUTBREAK,
    declaration: dt.date = DECLARATION,
    as_of: dt.date,
) -> tuple[PillarSummary, list[RampCurve]]:
    """Measure one pillar: coverage, every threshold's milestone, ramp, plateau."""
    obs = [o for o in of.series(rows, spec.metric) if o.date <= as_of]
    if not obs:
        raise ValueError(f"{spec.metric} has no observations on or before {as_of}")

    first, last = obs[0], obs[-1]
    gaps = _gaps(obs)
    largest_gap = max(gaps) if gaps else 0
    gap_end = (
        obs[gaps.index(largest_gap) + 1].date.isoformat() if gaps and largest_gap > 1
        else None
    )
    stale = (as_of - last.date).days
    # A pillar has STOPPED reporting, rather than paused, when the trailing
    # silence is at least twice the longest silence it has previously recovered
    # from -- with a six-day floor, so a series that reports every single day is
    # not declared dead over a long weekend. Derived from the record's own
    # cadence rather than declared, so it stays true if the extract is refreshed.
    stopped = stale >= 2 * max(largest_gap, NOTABLE_GAP_DAYS)

    curves = [
        _curve(
            obs, spec, t,
            outbreak=outbreak, declaration=declaration, as_of=as_of,
            reporting_stopped=stopped, stale_days=stale,
        )
        for t in _stringency_order(spec)
    ]

    # The ramp phase ends where the pillar reached its terminal state: the start
    # of the hold of the most demanding threshold it ever held. Everything from
    # there on is the plateau. A pillar that never held anything has no measured
    # plateau and its "ramp" is the whole record, still in progress.
    terminal = next(
        (c for c in reversed(curves) if c.held), None
    )
    notes: list[str] = []
    if terminal is None:
        ramp_obs, plateau_obs = list(obs), []
        terminal_threshold = None
        notes.append(
            "no threshold on this ladder ever held, so the ramp phase is the whole "
            "record and no plateau is measured"
        )
    else:
        terminal_threshold = terminal.threshold
        k = next(i for i, o in enumerate(obs)
                 if o.date.isoformat() == terminal.hold_start_date)
        ramp_obs, plateau_obs = obs[: k + 1], obs[k:]
        if k == 0:
            notes.append(
                "the pillar was already at its terminal threshold when first reported, "
                "so no ramp was observed and the slope is not computed"
            )

    ramp_slope = _ols_slope(ramp_obs, declaration) if len(ramp_obs) > 1 else None
    ramp_step = _median_step(ramp_obs) if len(ramp_obs) > 1 else None
    plateau_vals = [o.value for o in plateau_obs]
    plateau_steps = of.diffs(plateau_obs) if len(plateau_obs) > 1 else []
    plateau_days = (
        (plateau_obs[-1].date - plateau_obs[0].date).days if len(plateau_obs) > 1 else 0
    )
    plateau_slope = (
        _ols_slope(plateau_obs, declaration) if len(plateau_obs) > 1 else None
    )
    plateau_sd = (
        statistics.stdev(plateau_vals) if len(plateau_vals) > 1 else None
    )
    # A plateau that is still trending is not a plateau. Drift is the total move
    # the fitted plateau slope implies across the plateau's own span; comparing
    # it to the plateau's own standard deviation asks whether the trend is
    # bigger than the noise it sits in.
    drift = None if plateau_slope is None else plateau_slope * plateau_days
    if drift is not None and plateau_sd and abs(drift) > plateau_sd:
        improving = (drift > 0) == (spec.direction == RISING)
        notes.append(
            f"the plateau is not flat: it drifts {drift:+.1f} "
            f"{_delta_unit(spec.unit)} over {plateau_days} days, more than its own "
            f"standard deviation ({plateau_sd:.2f}), in the "
            f"{'improving' if improving else 'DEGRADING'} direction"
        )

    if stopped:
        notes.append(
            f"reporting stopped on {last.date.isoformat()}, {stale} days before the "
            f"as-of date; the plateau is measured to that day and not beyond it"
        )
    if largest_gap > NOTABLE_GAP_DAYS:
        notes.append(
            f"largest internal reporting gap is {largest_gap} days, ending {gap_end}"
        )

    summary = PillarSummary(
        outbreak=outbreak,
        pillar=spec.pillar,
        metric=spec.metric,
        direction=spec.direction,
        unit=spec.unit,
        scale_free=spec.scale_free,
        declaration=declaration.isoformat(),
        as_of=as_of.isoformat(),
        n_observations=len(obs),
        n_packets=len(rows),
        first_observation_date=first.date.isoformat(),
        last_observation_date=last.date.isoformat(),
        first_value=first.value,
        last_value=last.value,
        observation_lag_days=(first.date - declaration).days,
        record_span_days=(last.date - first.date).days,
        largest_internal_gap_days=largest_gap,
        largest_internal_gap_ends=gap_end,
        stale_days=stale,
        reporting_stopped=stopped,
        terminal_threshold=terminal_threshold,
        ramp_from=ramp_obs[0].date.isoformat() if ramp_obs else None,
        ramp_to=ramp_obs[-1].date.isoformat() if ramp_obs else None,
        ramp_days=(ramp_obs[-1].date - ramp_obs[0].date).days if ramp_obs else None,
        ramp_n=len(ramp_obs) or None,
        ramp_slope_per_day=None if ramp_slope is None else round(ramp_slope, 3),
        ramp_median_step_per_day=None if ramp_step is None else round(ramp_step, 3),
        plateau_from=plateau_obs[0].date.isoformat() if plateau_obs else None,
        plateau_n=len(plateau_obs) or None,
        plateau_mean=round(statistics.fmean(plateau_vals), 2) if plateau_vals else None,
        plateau_sd=None if plateau_sd is None else round(plateau_sd, 2),
        plateau_min=min(plateau_vals) if plateau_vals else None,
        plateau_max=max(plateau_vals) if plateau_vals else None,
        plateau_slope_per_day=(
            None if plateau_slope is None else round(plateau_slope, 3)
        ),
        plateau_drift=None if drift is None else round(drift, 1),
        plateau_median_abs_step=(
            round(statistics.median([abs(s) for s in plateau_steps]), 3)
            if plateau_steps else None
        ),
        threshold_rationale=spec.threshold_rationale,
        caveat=spec.caveat,
        notes=tuple(notes),
    )
    return summary, curves


def as_of_from_extract(path: Path | str = of.DEFAULT_SERIES) -> dt.date:
    """The extract's own stamped extraction date.

    Used instead of a wall clock so staleness is a property of the data, not of
    when the module happens to run.
    """
    with open(path, encoding="utf-8") as handle:
        return dt.date.fromisoformat(json.load(handle)["_meta"]["extracted_at"][:10])


def library(
    rows: Sequence[dict] | None = None,
    *,
    outbreak: str = OUTBREAK,
    declaration: dt.date = DECLARATION,
    as_of: dt.date | None = None,
    pillars: Sequence[PillarSpec] = PILLARS,
    path: Path | str = of.DEFAULT_SERIES,
) -> RampLibrary:
    """The whole set: every pillar, every threshold, one appendable structure.

    `as_of` is a parameter. Omitted, it is the extract's stamped extraction date
    when reading from the file, and the last data day present when rows are
    handed in directly. Never today's date.
    """
    source = str(path)
    if rows is None:
        rows = of.load_rows(path)
        if as_of is None:
            as_of = as_of_from_extract(path)
    elif as_of is None:
        source = "<rows supplied by caller>"
        days = [
            o.date
            for spec in pillars
            for o in of.series(rows, spec.metric)
        ]
        if not days:
            raise ValueError("no observations for any pillar in the supplied rows")
        as_of = max(days)

    summaries, curves = [], []
    for spec in pillars:
        summary, spec_curves = pillar_ramp(
            rows, spec, outbreak=outbreak, declaration=declaration, as_of=as_of
        )
        summaries.append(summary)
        curves.extend(spec_curves)
    return RampLibrary(
        outbreak=outbreak,
        declaration=declaration.isoformat(),
        as_of=as_of.isoformat(),
        source=source,
        pillars=tuple(summaries),
        curves=tuple(curves),
    )


def compare(*libraries: RampLibrary) -> list[dict]:
    """Line up the same (pillar, threshold) across outbreaks.

    The reason the row shape looks the way it does. `comparable` is False when a
    rung is scale dependent or when any outbreak's figure is left censored,
    because the honest answer there is "these two numbers are not the same
    measurement" rather than a difference nobody should take.
    """
    keyed: dict[tuple[str, float], list[RampCurve]] = {}
    for lib in libraries:
        for c in lib.curves:
            keyed.setdefault((c.pillar, c.threshold), []).append(c)
    out = []
    for (pillar, threshold), group in sorted(keyed.items()):
        out.append(
            {
                "pillar": pillar,
                "threshold": threshold,
                "n_outbreaks": len(group),
                "comparable": all(c.scale_free for c in group)
                and all(c.first_reach_bound in ("exact", "interval") for c in group),
                "outbreaks": [
                    {
                        "outbreak": c.outbreak,
                        "days_to_first_reach_declaration":
                            c.days_to_first_reach_declaration,
                        "days_to_held_declaration": c.days_to_held_declaration,
                        "first_reach_bound": c.first_reach_bound,
                        "held": c.held,
                    }
                    for c in group
                ],
            }
        )
    return out


# --- rendering. Every figure in docs/response-capacity-ramp.md comes from here --

def _thr(c: RampCurve) -> str:
    op = ">=" if c.direction == RISING else "<="
    return f"{op} {c.threshold:g}"


def _n(value) -> str:
    return "-" if value is None else str(value)


def milestone_table(lib: RampLibrary) -> str:
    head = (
        "| pillar | threshold | first reach | days (decl) | days (pillar) | window | "
        "bound | held | hold start | days (decl) | days (pillar) | misses after | "
        "still met |"
    )
    rule = "|" + "---|" * 13
    lines = [head, rule]
    for c in lib.curves:
        after = (
            "-" if c.observations_after_hold is None
            else f"{c.failures_after_hold}/{c.observations_after_hold}"
        )
        lines.append(
            f"| {c.pillar} | {_thr(c)} | {_n(c.first_reach_date)} | "
            f"{_n(c.days_to_first_reach_declaration)} | "
            f"{_n(c.days_to_first_reach_pillar)} | {_n(c.first_reach_window_days)} | "
            f"{c.first_reach_bound} | {'yes' if c.held else 'no'} | "
            f"{_n(c.hold_start_date)} | {_n(c.days_to_held_declaration)} | "
            f"{_n(c.days_to_held_pillar)} | {after} | "
            f"{'yes' if c.retained_at_last_observation else 'no'} |"
        )
    return "\n".join(lines)


def coverage_table(lib: RampLibrary) -> str:
    head = (
        "| pillar | metric | obs | first | last | first value | last value | "
        "lag (days) | largest gap | stale | reporting |"
    )
    rule = "|" + "---|" * 11
    lines = [head, rule]
    for p in lib.pillars:
        lines.append(
            f"| {p.pillar} | `{p.metric}` | {p.n_observations}/{p.n_packets} | "
            f"{p.first_observation_date} | {p.last_observation_date} | "
            f"{p.first_value:g} | {p.last_value:g} | "
            f"{p.observation_lag_days} | {p.largest_internal_gap_days} | "
            f"{p.stale_days} | {'stopped' if p.reporting_stopped else 'live'} |"
        )
    return "\n".join(lines)


def shape_table(lib: RampLibrary) -> str:
    head = (
        "| pillar | terminal | ramp | ramp days | slope/day | median step/day | "
        "plateau n | plateau mean | plateau sd | plateau slope/day | plateau drift |"
    )
    rule = "|" + "---|" * 11
    lines = [head, rule]
    for p in lib.pillars:
        term = "none held" if p.terminal_threshold is None else (
            f"{'>=' if p.direction == RISING else '<='} {p.terminal_threshold:g}"
        )
        span = "-" if p.ramp_from is None else f"{p.ramp_from} to {p.ramp_to}"
        lines.append(
            f"| {p.pillar} | {term} | {span} | {_n(p.ramp_days)} | "
            f"{_n(p.ramp_slope_per_day)} | {_n(p.ramp_median_step_per_day)} | "
            f"{_n(p.plateau_n)} | {_n(p.plateau_mean)} | {_n(p.plateau_sd)} | "
            f"{_n(p.plateau_slope_per_day)} | {_n(p.plateau_drift)} |"
        )
    return "\n".join(lines)


def rationale_block(lib: RampLibrary) -> str:
    """Why each ladder is what it is, and what its proxy cannot see.

    Rendered from the specs rather than retyped into the doc, because a
    rationale that can drift from the thresholds it justifies is worse than no
    rationale at all.
    """
    lines = []
    for p in lib.pillars:
        rungs = ", ".join(
            f"{'>=' if p.direction == RISING else '<='} {c.threshold:g}"
            for c in lib.curves if c.metric == p.metric
        )
        lines.append(f"**{p.pillar}** (`{p.metric}`, {p.unit}, {rungs})")
        lines.append("")
        lines.append(f"- Ladder: {p.threshold_rationale}")
        lines.append(f"- Proxy limit: {p.caveat}")
        lines.append("")
    return "\n".join(lines).rstrip()


def notes_block(lib: RampLibrary) -> str:
    """Every generated caveat, verbatim. The part of the output that says no.

    Curve notes are grouped by their text: four thresholds on one pillar that
    are all left censored for the same reason produce one line naming four
    rungs, not four lines. The row-level copies stay on the rows, where a
    consumer reading a single row still gets the warning.
    """
    lines = []
    for p in lib.pillars:
        lines.append(f"**{p.pillar}**")
        lines.append("")
        for note in p.notes:
            lines.append(f"- {note}")
        grouped: dict[str, list[str]] = {}
        for c in lib.curves:
            if c.metric != p.metric:
                continue
            for note in c.notes:
                if note.startswith("scale dependent"):
                    continue  # stated once, in the pillar's proxy limit
                grouped.setdefault(note, []).append(_thr(c))
        for note, rungs in grouped.items():
            lines.append(f"- {', '.join(rungs)}: {note}")
        lines.append("")
    return "\n".join(lines).rstrip()


BLOCKS = {
    "coverage-table": coverage_table,
    "rationale-block": rationale_block,
    "milestone-table": milestone_table,
    "shape-table": shape_table,
    "notes-block": notes_block,
}


REPO = Path(__file__).resolve().parent.parent.parent
DOC = REPO / "docs" / "response-capacity-ramp.md"


def block(name: str, lib: RampLibrary) -> str:
    """One generated block, fences included. The unit the document embeds."""
    return (
        f"<!-- generated: {name} -->\n{BLOCKS[name](lib)}\n"
        f"<!-- /generated: {name} -->"
    )


def rewrite_doc(lib: RampLibrary, path: Path | str = DOC) -> int:
    """Refresh the generated blocks in the document, in place.

    The prose is left alone. Only the fenced blocks are replaced, so the
    document can never carry a table this module did not produce, and the
    inline figures the prose does state are checked separately by the tests.
    """
    text = Path(path).read_text(encoding="utf-8")
    replaced = 0
    for name in BLOCKS:
        start = text.find(f"<!-- generated: {name} -->")
        end = text.find(f"<!-- /generated: {name} -->")
        if start < 0 or end < 0:
            raise ValueError(f"{path} has no fenced block for {name!r}")
        end += len(f"<!-- /generated: {name} -->")
        text = text[:start] + block(name, lib) + text[end:]
        replaced += 1
    Path(path).write_text(text, encoding="utf-8")
    return replaced


def main(argv: Sequence[str]) -> int:
    lib = library()
    if "--json" in argv:
        print(json.dumps(
            {"pillars": lib.pillar_rows(), "curves": lib.to_rows()},
            indent=2, default=str,
        ))
        return 0
    if "--write" in argv:
        n = rewrite_doc(lib)
        print(f"refreshed {n} generated blocks in {DOC}")
        return 0
    for name in BLOCKS:
        print(block(name, lib))
        print()
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(main(sys.argv[1:]))
