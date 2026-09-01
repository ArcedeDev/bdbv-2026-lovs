# SPDX-License-Identifier: Apache-2.0
"""Integrity monitor for the SitRep reporting stream itself.

Why this exists. Every operational number in this repository is a reading taken
off one feed: the INSP/INRB SitRep packets. The forecasters model what the
indicators say. Nothing models whether the indicators are still being said. In
2026 that gap cost this programme 58 days -- the calibration-evidence feed
stopped being written on 2026-06-20, two blocks sat unresolved past their dates,
and the whole suite stayed green because no test asserted that the input was
still alive (see tests/test_calibration_feed_liveness.py, which now guards that
one feed). This module guards the substrate feed, and it does so with the same
apparatus the forecasters use: the reliability of the reporting stream is itself
a measurable, distributed, forecastable quantity.

The three states this module refuses to conflate. An indicator that is not in
front of you is in exactly one of three situations, and they demand opposite
responses:

  1. THE SITREP WAS NOT PUBLISHED. No packet exists for that slot. Response:
     nothing is wrong with us; the outbreak response did not report that day.
  2. THE SITREP WAS PUBLISHED WITHOUT THIS INDICATOR. The packet exists, other
     fields are populated, this one is empty. Response: a surveillance function
     stopped reporting. This is the silent loss, and it is the dangerous one --
     the series simply stops and every downstream chart keeps rendering.
  3. OUR EXTRACT FAILED TO CAPTURE IT. The publication carried it; the tidy
     extract does not. Response: fix our pipeline. Reading this as (2) would
     manufacture a surveillance failure that never happened.

A frozen extract cannot always separate (2) from (3) by inspection, so this
module reports the evidence rather than guessing. Two derived signals do most of
the work. First, CO-STOPPING: when several indicators go dark on the same packet
and stay dark, a single upstream change is a far better explanation than several
independent extraction failures. Second, the DERIVED-FIELD CHECK: a ratio whose
numerator and denominator are both present in the packet is reconstructible, so
its absence can only be ours. Where neither signal fires, the module says the
distinction is unresolved from this extract and names what would resolve it.

Delivery is measured on two clocks that must not be merged. The SITREP NUMBER is
the delivery ledger: it is sequential, so a hole in it is a missing delivery. The
DATA DAY is the content clock, and per data/operational-series-2026-09-01.json's
own reading note, every time-since estimator is clamped to it -- a publication
gap otherwise reads as observed improvement. The two disagree in both directions
here: two SitReps sometimes share one data day (a batch), and one data day goes
uncovered while the numbering runs unbroken. A monitor on either clock alone
would miss one of those.

No thresholds are authored. Severity is defined against what this feed has
already done: a silence longer than the longest silence on record is a silence
the feed has never produced, which is the only threshold that does not encode an
analyst's mood. The same rule sets each indicator's own staleness bar.

Deterministic: bootstrap and permutation draws are seeded, so a run reproduces
exactly. Stdlib only. No network. NO CLOCK -- the as-of date is a parameter of
every function that needs one, because a monitor that reads the system clock
cannot be backtested, and a liveness monitor that cannot be backtested has never
been shown to fire.
"""
from __future__ import annotations

import argparse
import datetime as dt
import math
import random
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Sequence

from .opsforecast import DEFAULT_SERIES, load_rows, series

# Envelope fields describe the packet; everything else in a row is an indicator.
ENVELOPE = ("sitrep", "published_at", "data_as_of", "ready_for_model_use")

# Ratios the packets publish that are also reconstructible from fields in the
# same packet. Used only to attribute an absence: if both inputs are present and
# the ratio is not, the packet had everything needed and the loss is ours.
DERIVED: dict[str, tuple[str, str]] = {
    "alert_investigation_rate_percent": ("alerts_investigated", "alerts_reported"),
    "lab_positivity_percent": ("samples_positive", "samples_analyzed"),
    "confirmed_cfr_percent": ("confirmed_deaths_total", "confirmed_total"),
}

# Matches opsforecast.DEFAULT_BLOCK for the same reason: reporting stress arrives
# in runs, not as independent daily coin flips, so blocks preserve what single-day
# resampling would destroy.
DEFAULT_BLOCK = 5
DEFAULT_PATHS = 20000
DEFAULT_SEED = 20260901
# Sixty days of arrivals is long enough to contain the feed's slow weeks and
# short enough that the outbreak's ramp-up cadence -- when SitReps were not yet
# daily -- does not masquerade as the current regime.
DEFAULT_REFERENCE_DAYS = 60
DEFAULT_HORIZON_DAYS = 30
# Coverage trends are screened at 5 percent, Holm-corrected across the indicators
# actually tested, because twenty independent looks at 5 percent will hand you a
# degrading indicator whether or not one exists.
DEFAULT_ALPHA = 0.05


class CadenceIntegrityError(ValueError):
    """Raised when the monitor is asked something the extract cannot answer."""


# --- packet-level plumbing ---------------------------------------------------


@dataclass(frozen=True, slots=True)
class Packet:
    """One delivered SitRep, normalised to the fields the monitor reasons over."""

    number: int
    sitrep: str
    data_day: dt.date
    published_day: dt.date
    ready: bool
    values: dict[str, object]

    @property
    def lag_days(self) -> int:
        """Publication lag: how long after the data day the packet appeared."""
        return (self.published_day - self.data_day).days


@dataclass(frozen=True, slots=True)
class ExtractAnomalies:
    """Defects in our own extract, kept separate from defects in the feed.

    Every count here is a reason to distrust a conclusion about the outbreak
    response, so they are surfaced beside the findings rather than swallowed.
    """

    duplicate_sitreps: tuple[str, ...]
    unparseable_sitreps: tuple[str, ...]
    rows_missing_dates: tuple[str, ...]
    rows_not_ready: tuple[str, ...]
    negative_lag_sitreps: tuple[str, ...]
    derived_absent_though_computable: tuple[tuple[str, str], ...]
    derived_present_though_uncomputable: tuple[tuple[str, str], ...]


def indicators(rows: Sequence[dict]) -> tuple[str, ...]:
    """Every non-envelope field any row carries, in first-seen order."""
    out: list[str] = []
    for row in rows:
        for key in row:
            if key not in ENVELOPE and key not in out:
                out.append(key)
    return tuple(out)


def packets(rows: Sequence[dict]) -> tuple[tuple[Packet, ...], ExtractAnomalies]:
    """The delivery ledger, one entry per SitRep number, plus what went wrong.

    Deduplicating by SitRep number rather than by row is the whole point: the
    extract carries SitRep 006 twice, and counting it twice would inflate every
    coverage denominator by a packet that was only ever delivered once.
    """
    seen: dict[int, Packet] = {}
    duplicates: list[str] = []
    unparseable: list[str] = []
    missing_dates: list[str] = []
    not_ready: list[str] = []
    negative_lag: list[str] = []
    for row in rows:
        raw = str(row.get("sitrep", ""))
        try:
            number = int(raw)
        except ValueError:
            unparseable.append(raw)
            continue
        try:
            data_day = dt.date.fromisoformat(str(row["data_as_of"])[:10])
            published_day = dt.date.fromisoformat(str(row["published_at"])[:10])
        except (KeyError, TypeError, ValueError):
            missing_dates.append(raw)
            continue
        if published_day < data_day:
            # A packet published before its own data day is a transcription
            # error somewhere; the lag series must not average it in.
            negative_lag.append(raw)
        if not row.get("ready_for_model_use", False):
            not_ready.append(raw)
        if number in seen:
            duplicates.append(raw)
            continue
        seen[number] = Packet(
            number=number,
            sitrep=raw,
            data_day=data_day,
            published_day=published_day,
            ready=bool(row.get("ready_for_model_use", False)),
            values={k: v for k, v in row.items() if k not in ENVELOPE and v is not None},
        )
    ordered = tuple(seen[n] for n in sorted(seen))
    absent, present = _derived_field_audit(ordered)
    return ordered, ExtractAnomalies(
        duplicate_sitreps=tuple(duplicates),
        unparseable_sitreps=tuple(unparseable),
        rows_missing_dates=tuple(missing_dates),
        rows_not_ready=tuple(not_ready),
        negative_lag_sitreps=tuple(negative_lag),
        derived_absent_though_computable=absent,
        derived_present_though_uncomputable=present,
    )


def _derived_field_audit(
    delivered: Sequence[Packet],
) -> tuple[tuple[tuple[str, str], ...], tuple[tuple[str, str], ...]]:
    """Attribute a missing ratio to the publisher or to us.

    A ratio absent from a packet that carries both its inputs was reconstructible
    at extraction time, so its absence is ours and must never be read as a
    surveillance loss. The mirror case -- a ratio present while an input is not --
    proves the ratio is transcribed from the publication rather than computed
    here, which means its disappearance tracks the publisher, not our arithmetic.
    """
    absent: list[tuple[str, str]] = []
    present: list[tuple[str, str]] = []
    for packet in delivered:
        for ratio, (numerator, denominator) in DERIVED.items():
            num = packet.values.get(numerator)
            den = packet.values.get(denominator)
            has_inputs = num is not None and den not in (None, 0)
            if ratio not in packet.values and has_inputs:
                absent.append((packet.sitrep, ratio))
            elif ratio in packet.values and not has_inputs:
                present.append((packet.sitrep, ratio))
    return tuple(absent), tuple(present)


def rows_as_of(rows: Sequence[dict], as_of: dt.date) -> list[dict]:
    """Rows that had been PUBLISHED on or before `as_of`.

    The only look-ahead-free way to ask this module a historical question, and it
    filters on publication rather than on the data day for a reason that is easy
    to get wrong: a packet describing 22 August was published on 23 August, so a
    reading taken on the 22nd that includes it is reading a document that did not
    exist. Filtering on the data day quietly imports one publication lag's worth
    of the future into every backtest -- the same shape of defect as the corridor
    benchmark's backfilled response state, where historical reads could see
    values assigned after the fact.

    The reported staleness is still measured against the DATA day, per the
    extract's own reading note. Knowability and recency are different clocks.
    """
    out = []
    for row in rows:
        stamp = row.get("published_at")
        if not stamp:
            continue
        try:
            when = dt.date.fromisoformat(str(stamp)[:10])
        except ValueError:
            continue
        if when <= as_of:
            out.append(row)
    return out


def populated_days(rows: Sequence[dict], indicator: str) -> list[dt.date]:
    """Distinct data days on which `indicator` carries a value.

    Numeric indicators route through opsforecast.series so the monitor and the
    forecaster can never disagree about which days an indicator covers: if the
    monitor says a series is live and the forecaster cannot find its last
    observation, one of them is wrong, and sharing the function removes the
    possibility. Structured indicators (the per-province maps) are not numeric,
    so they get the same day-deduplication logic applied to presence alone.
    """
    if _is_numeric(rows, indicator):
        return [obs.date for obs in series(rows, indicator)]
    seen: set[dt.date] = set()
    out: list[dt.date] = []
    for row in rows:
        if row.get(indicator) is None or not row.get("data_as_of"):
            continue
        try:
            when = dt.date.fromisoformat(str(row["data_as_of"])[:10])
        except ValueError:
            continue
        if when in seen:
            continue
        seen.add(when)
        out.append(when)
    out.sort()
    return out


def _is_numeric(rows: Sequence[dict], indicator: str) -> bool:
    for row in rows:
        value = row.get(indicator)
        if value is None:
            continue
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return False


# --- 1. delivery integrity ---------------------------------------------------

#: A SitRep number is absent and the data-day calendar is short by the matching
#: number of days: the delivery is missing and so is its content.
NOT_PUBLISHED = "not_published"
#: A SitRep number is absent but the surrounding data days are contiguous. The
#: content stream lost nothing, so this is either a numbering skip by the
#: publisher or a packet our extract dropped. Unresolved from this extract.
NUMBER_SKIPPED_CONTENT_INTACT = "number_skipped_content_intact"
#: A packet was delivered carrying no indicator at all.
DELIVERED_EMPTY = "delivered_empty"


@dataclass(frozen=True, slots=True)
class SitRepGap:
    """A contiguous run of absent SitRep numbers, with its content consequence."""

    missing: tuple[int, ...]
    before: str | None
    after: str | None
    before_data_day: str | None
    after_data_day: str | None
    calendar_gap_days: int | None
    uncovered_days: tuple[str, ...]
    verdict: str


@dataclass(frozen=True, slots=True)
class DeliveryIntegrity:
    first_sitrep: int
    last_sitrep: int
    delivered: int
    expected: int
    gaps: tuple[SitRepGap, ...]
    missing_numbers: tuple[int, ...]
    empty_packets: tuple[str, ...]
    thin_packets: tuple[str, ...]
    first_data_day: str
    last_data_day: str
    data_days_covered: int
    calendar_span_days: int
    uncovered_data_days: tuple[str, ...]
    uncovered_without_number_gap: tuple[str, ...]
    shared_data_days: tuple[tuple[str, tuple[str, ...]], ...]
    anomalies: ExtractAnomalies


def delivery_integrity(rows: Sequence[dict]) -> DeliveryIntegrity:
    """Which SitReps arrived, which did not, and what their absence cost.

    The verdict on each hole is the load-bearing part. A missing number whose
    neighbours' data days are a day apart removed nothing from the content
    stream; a missing number that leaves a hole in the calendar removed a day of
    surveillance. Only the second is a reporting failure, and reporting both as
    'missing SitReps' would put a numbering quirk and a lost day in one bucket.
    """
    delivered, anomalies = packets(rows)
    if not delivered:
        raise CadenceIntegrityError("no parseable SitRep packets in the extract")
    by_number = {p.number: p for p in delivered}
    first, last = delivered[0].number, delivered[-1].number
    missing = tuple(n for n in range(first, last + 1) if n not in by_number)

    covered = sorted({p.data_day for p in delivered})
    covered_set = set(covered)
    span_start, span_end = covered[0], covered[-1]
    uncovered = tuple(
        (span_start + dt.timedelta(days=i)).isoformat()
        for i in range((span_end - span_start).days + 1)
        if (span_start + dt.timedelta(days=i)) not in covered_set
    )

    gaps: list[SitRepGap] = []
    explained: set[str] = set()
    for run in _runs(missing):
        before = by_number.get(run[0] - 1)
        after = by_number.get(run[-1] + 1)
        if before is None or after is None:
            calendar_gap = None
            holes: tuple[str, ...] = ()
        else:
            calendar_gap = (after.data_day - before.data_day).days
            holes = tuple(
                (before.data_day + dt.timedelta(days=i)).isoformat()
                for i in range(1, calendar_gap)
            )
        explained.update(holes)
        gaps.append(
            SitRepGap(
                missing=run,
                before=before.sitrep if before else None,
                after=after.sitrep if after else None,
                before_data_day=before.data_day.isoformat() if before else None,
                after_data_day=after.data_day.isoformat() if after else None,
                calendar_gap_days=calendar_gap,
                uncovered_days=holes,
                verdict=NOT_PUBLISHED if holes else NUMBER_SKIPPED_CONTENT_INTACT,
            )
        )

    shared: dict[dt.date, list[str]] = {}
    for packet in delivered:
        shared.setdefault(packet.data_day, []).append(packet.sitrep)

    return DeliveryIntegrity(
        first_sitrep=first,
        last_sitrep=last,
        delivered=len(delivered),
        expected=last - first + 1,
        gaps=tuple(gaps),
        missing_numbers=missing,
        empty_packets=tuple(p.sitrep for p in delivered if not p.values),
        thin_packets=tuple(p.sitrep for p in delivered if 0 < len(p.values) <= 1),
        first_data_day=span_start.isoformat(),
        last_data_day=span_end.isoformat(),
        data_days_covered=len(covered),
        calendar_span_days=(span_end - span_start).days + 1,
        uncovered_data_days=uncovered,
        uncovered_without_number_gap=tuple(d for d in uncovered if d not in explained),
        shared_data_days=tuple(
            (day.isoformat(), tuple(ids))
            for day, ids in sorted(shared.items())
            if len(ids) > 1
        ),
        anomalies=anomalies,
    )


def _runs(numbers: Sequence[int]) -> list[tuple[int, ...]]:
    """Group a sorted sequence into maximal contiguous runs."""
    out: list[tuple[int, ...]] = []
    current: list[int] = []
    for n in numbers:
        if current and n == current[-1] + 1:
            current.append(n)
        else:
            if current:
                out.append(tuple(current))
            current = [n]
    if current:
        out.append(tuple(current))
    return out


# --- publication lag ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LagDistribution:
    """How long the feed takes to publish a day it has already observed.

    Drift here is an early warning that the delivery integrity checks cannot
    give: a feed under strain slows before it stops.
    """

    n: int
    minimum: int
    p50: float
    p90: float
    maximum: int
    mean: float
    histogram: tuple[tuple[int, int], ...]
    first_half_mean: float
    second_half_mean: float
    drift_days: float
    drift_p_value: float
    drift_verdict: str
    n_permutations: int
    seed: int


def publication_lag(
    rows: Sequence[dict],
    *,
    seed: int = DEFAULT_SEED,
    n_permutations: int = DEFAULT_PATHS,
    alpha: float = DEFAULT_ALPHA,
) -> LagDistribution:
    """The lag series, its shape, and whether it is drifting.

    Drift is tested by permutation rather than by a regression slope. Lag here is
    a small integer bounded below by zero and taking four distinct values; a
    least-squares slope over that would report a direction with a standard error
    that assumes a continuum it does not have. Shuffling the observed lags
    between the two halves makes no distributional assumption at all, and answers
    exactly the question asked: could a split this uneven arise from a feed whose
    lag behaviour never changed?
    """
    delivered, _ = packets(rows)
    lags = [p.lag_days for p in delivered if p.lag_days >= 0]
    if len(lags) < 4:
        raise CadenceIntegrityError(
            f"only {len(lags)} usable publication lags; need at least 4 to split and test"
        )
    ordered = sorted(lags)
    half = len(lags) // 2
    first, second = lags[:half], lags[half:]
    first_mean = sum(first) / len(first)
    second_mean = sum(second) / len(second)
    p_value = _permutation_difference_p(
        first, second, seed=seed, n_permutations=n_permutations
    )
    drift = second_mean - first_mean
    if p_value > alpha:
        verdict = "stable"
    else:
        verdict = "slowing" if drift > 0 else "quickening"
    counts: dict[int, int] = {}
    for lag in lags:
        counts[lag] = counts.get(lag, 0) + 1
    return LagDistribution(
        n=len(lags),
        minimum=ordered[0],
        p50=_quantile(ordered, 0.5),
        p90=_quantile(ordered, 0.9),
        maximum=ordered[-1],
        mean=sum(lags) / len(lags),
        histogram=tuple(sorted(counts.items())),
        first_half_mean=first_mean,
        second_half_mean=second_mean,
        drift_days=drift,
        drift_p_value=p_value,
        drift_verdict=verdict,
        n_permutations=n_permutations,
        seed=seed,
    )


def _quantile(ordered: Sequence[float], q: float) -> float:
    """Nearest-rank quantile: the smallest observation at or above rank q.

    Chosen over an interpolating definition because every quantile this module
    reports is compared against an integer day count, and an interpolated 1.4-day
    threshold is not a thing a daily feed can miss by.
    """
    if not ordered:
        raise CadenceIntegrityError("quantile of an empty series")
    rank = min(len(ordered), max(1, math.ceil(q * len(ordered))))
    return float(ordered[rank - 1])


def _permutation_difference_p(
    first: Sequence[float],
    second: Sequence[float],
    *,
    seed: int,
    n_permutations: int,
) -> float:
    """Two-sided p for the difference in means, by relabelling.

    The +1 in numerator and denominator counts the observed labelling as one of
    the outcomes, so the p-value can never be exactly zero -- a monitor that
    reports p=0 from 20,000 draws is claiming more than it sampled.
    """
    pooled = list(first) + list(second)
    observed = abs(sum(second) / len(second) - sum(first) / len(first))
    rng = random.Random(seed)
    cut = len(first)
    extreme = 0
    for _ in range(n_permutations):
        rng.shuffle(pooled)
        left = sum(pooled[:cut]) / cut
        right = sum(pooled[cut:]) / (len(pooled) - cut)
        if abs(right - left) >= observed:
            extreme += 1
    return (extreme + 1) / (n_permutations + 1)


# --- 2. indicator completeness ----------------------------------------------

NEVER_REPORTED = "never_reported"
LIVE = "live"
INTERRUPTED = "interrupted"
LOST = "lost"

DEGRADING = "degrading"
IMPROVING = "improving"
STABLE = "stable"
UNTESTED = "untested"


@dataclass(frozen=True, slots=True)
class IndicatorCoverage:
    """One indicator's delivery record, split into level, trend and status."""

    indicator: str
    n_populated: int
    n_packets_since_first: int
    coverage_overall: float
    coverage_since_first: float
    first_sitrep: str | None
    first_data_day: str | None
    last_sitrep: str | None
    last_data_day: str | None
    first_half_coverage: float | None
    second_half_coverage: float | None
    coverage_delta: float | None
    trend_p_value: float | None
    trend: str
    longest_prior_dark_run: int
    current_dark_run: int
    status: str
    attribution: str


@dataclass(frozen=True, slots=True)
class CoverageReport:
    indicators: tuple[IndicatorCoverage, ...]
    co_stop_cohorts: tuple[tuple[str, tuple[str, ...]], ...]
    alpha: float
    n_tested: int


def indicator_coverage(
    rows: Sequence[dict], *, alpha: float = DEFAULT_ALPHA
) -> CoverageReport:
    """Per-indicator completeness, its direction, and whether it has stopped.

    Coverage is measured from an indicator's first appearance, not from the first
    packet. Most of these fields did not exist until the packet schema widened on
    2026-06-04; charging them for the packets that predate them would report a
    surveillance gap where there was only a younger indicator, and would bury the
    indicators that genuinely stopped underneath twenty that never started on time.

    Trend is tested with Fisher's exact test on the two halves of that window.
    Exact rather than sampled because the tables are small and the answer should
    not depend on a seed; two-sided because an indicator that suddenly appears in
    every packet is as much a change in the feed as one that vanishes.
    """
    delivered, _ = packets(rows)
    names = indicators(rows)
    entries: list[IndicatorCoverage] = []
    raw_p: dict[str, float] = {}
    for name in names:
        entry = _coverage_for(delivered, name)
        entries.append(entry)
        if entry.trend_p_value is not None:
            raw_p[name] = entry.trend_p_value

    significant = _holm(raw_p, alpha)
    resolved: list[IndicatorCoverage] = []
    for entry in entries:
        trend = entry.trend
        if entry.trend_p_value is not None:
            if entry.indicator not in significant or not entry.coverage_delta:
                trend = STABLE
            else:
                trend = DEGRADING if entry.coverage_delta < 0 else IMPROVING
        resolved.append(_replace_trend(entry, trend))

    cohorts: dict[str, list[str]] = {}
    for entry in resolved:
        if entry.status in (LOST, INTERRUPTED) and entry.last_sitrep:
            cohorts.setdefault(entry.last_sitrep, []).append(entry.indicator)
    return CoverageReport(
        indicators=tuple(resolved),
        co_stop_cohorts=tuple(
            (sitrep, tuple(members))
            for sitrep, members in sorted(cohorts.items())
            if len(members) > 1
        ),
        alpha=alpha,
        n_tested=len(raw_p),
    )


def _coverage_for(delivered: Sequence[Packet], name: str) -> IndicatorCoverage:
    present = [i for i, p in enumerate(delivered) if name in p.values]
    if not present:
        return IndicatorCoverage(
            indicator=name,
            n_populated=0,
            n_packets_since_first=0,
            coverage_overall=0.0,
            coverage_since_first=0.0,
            first_sitrep=None,
            first_data_day=None,
            last_sitrep=None,
            last_data_day=None,
            first_half_coverage=None,
            second_half_coverage=None,
            coverage_delta=None,
            trend_p_value=None,
            trend=UNTESTED,
            longest_prior_dark_run=0,
            current_dark_run=len(delivered),
            status=NEVER_REPORTED,
            attribution="no packet in the extract has ever carried this field",
        )

    start, end = present[0], present[-1]
    window = delivered[start:]
    flags = [1 if name in p.values else 0 for p in window]
    current_dark = len(delivered) - 1 - end

    # Dark runs strictly inside the observed window: the run that is still open
    # at the end of the extract is the thing being judged, not part of the bar.
    prior_dark = _longest_internal_dark_run(flags)

    if current_dark == 0:
        status = LIVE
    elif current_dark > prior_dark:
        status = LOST
    else:
        status = INTERRUPTED

    if len(window) >= 4:
        half = len(window) // 2
        h1, h2 = flags[:half], flags[half:]
        a, b = sum(h1), len(h1) - sum(h1)
        c, d = sum(h2), len(h2) - sum(h2)
        first_cov = a / len(h1)
        second_cov = c / len(h2)
        p_value = _fisher_exact_two_sided(a, b, c, d)
        delta = second_cov - first_cov
        trend = STABLE
    else:
        first_cov = second_cov = delta = p_value = None
        trend = UNTESTED

    return IndicatorCoverage(
        indicator=name,
        n_populated=len(present),
        n_packets_since_first=len(window),
        coverage_overall=len(present) / len(delivered),
        coverage_since_first=len(present) / len(window),
        first_sitrep=delivered[start].sitrep,
        first_data_day=delivered[start].data_day.isoformat(),
        last_sitrep=delivered[end].sitrep,
        last_data_day=delivered[end].data_day.isoformat(),
        first_half_coverage=first_cov,
        second_half_coverage=second_cov,
        coverage_delta=delta,
        trend_p_value=p_value,
        trend=trend,
        longest_prior_dark_run=prior_dark,
        current_dark_run=current_dark,
        status=status,
        attribution=_attribute(name, delivered, status, end),
    )


def _attribute(
    name: str, delivered: Sequence[Packet], status: str, last_index: int
) -> str:
    """Say which of the three states an absence is in, or that it is unresolved.

    This is the module's central claim, so it refuses to guess. A ratio whose
    inputs are still arriving is reconstructible and therefore ours; a ratio whose
    numerator stopped at the same packet it did points squarely upstream; anything
    else is named as unresolved together with what would resolve it.
    """
    if status == LIVE:
        return "populated in the most recent packet"
    if status == NEVER_REPORTED:
        return "never present; not a loss, an absence from the start"

    tail = delivered[last_index + 1 :]
    if not tail:
        return "no packet follows; nothing to attribute"
    non_empty_tail = [p for p in tail if p.values]
    if not non_empty_tail:
        return (
            "every packet since is itself empty; this is a delivery failure, "
            "not an indicator-specific loss"
        )

    if name in DERIVED:
        numerator, denominator = DERIVED[name]
        computable = sum(
            1
            for p in non_empty_tail
            if p.values.get(numerator) is not None
            and p.values.get(denominator) not in (None, 0)
        )
        if computable:
            return (
                f"OURS: {computable} later packet(s) carry both {numerator} and "
                f"{denominator}, so the ratio was reconstructible and its absence "
                "is an extraction defect, not a surveillance loss"
            )
        stopped_with = [
            other
            for other in (numerator, denominator)
            if all(other not in p.values for p in non_empty_tail)
        ]
        if stopped_with:
            return (
                "UPSTREAM: the inputs "
                + ", ".join(stopped_with)
                + " stopped arriving too, so the ratio cannot be formed from any "
                "later packet; the publisher stopped reporting it"
            )

    co_stopped = [
        other
        for other in delivered[last_index].values
        if other != name and all(other not in p.values for p in non_empty_tail)
    ]
    if co_stopped:
        return (
            "UPSTREAM (probable): went dark on the same packet as "
            + ", ".join(sorted(co_stopped))
            + "; one schema change explains them all, independent extraction "
            "failures would not line up"
        )
    return (
        "UNRESOLVED from this extract: later packets are populated but nothing "
        "corroborates whether the publication carried this field. Resolve by "
        "re-reading the source SitRep packets for the affected numbers"
    )


def _longest_internal_dark_run(flags: Sequence[int]) -> int:
    """Longest run of zeros that is closed on both sides by a one.

    The still-open run at the end of the extract is deliberately excluded: it is
    the quantity being judged, and letting it set its own bar would make every
    indicator permanently within tolerance of itself.
    """
    longest = run = 0
    seen_one = False
    for flag in flags:
        if flag:
            if seen_one:
                longest = max(longest, run)
            seen_one = True
            run = 0
        elif seen_one:
            run += 1
    return longest


def _fisher_exact_two_sided(a: int, b: int, c: int, d: int) -> float:
    """Exact two-sided p for the 2x2 table [[a, b], [c, d]].

    Exact enumeration of the hypergeometric tail, so the answer is a property of
    the table rather than of a random seed.
    """
    n = a + b + c + d
    row1, row2, col1 = a + b, c + d, a + c
    if not row1 or not row2 or not col1 or col1 == n:
        return 1.0
    total = math.comb(n, col1)

    def density(x: int) -> float:
        return math.comb(row1, x) * math.comb(row2, col1 - x) / total

    lo, hi = max(0, col1 - row2), min(row1, col1)
    observed = density(a)
    tolerance = observed * (1 + 1e-9)
    return min(1.0, sum(density(x) for x in range(lo, hi + 1) if density(x) <= tolerance))


def _holm(p_values: dict[str, float], alpha: float) -> set[str]:
    """Holm-Bonferroni: control the chance of any false 'this indicator changed'."""
    ordered = sorted(p_values.items(), key=lambda kv: kv[1])
    m = len(ordered)
    kept: set[str] = set()
    for i, (name, p) in enumerate(ordered):
        if p <= alpha / (m - i):
            kept.add(name)
        else:
            break
    return kept


def _replace_trend(entry: IndicatorCoverage, trend: str) -> IndicatorCoverage:
    return replace(entry, trend=trend)


# --- 3. staleness clock ------------------------------------------------------

GREEN = "green"
AMBER = "amber"
RED = "red"

#: The indicator is as current as the extract allows; its staleness is the
#: feed's staleness and clears the moment the feed is refreshed.
FEED_OWNED = "feed"
#: The indicator is staler than the feed. Refreshing the feed will not fix it.
INDICATOR_OWNED = "indicator"


@dataclass(frozen=True, slots=True)
class IndicatorStaleness:
    """One indicator's staleness, with the feed's own staleness factored out.

    `days_behind_feed` is the field that keeps this honest. When the whole
    extract is four days old, every indicator in it is four days stale, and
    reporting twenty alarms for one stale extract would bury the two indicators
    that actually stopped. Zero here means the indicator is as fresh as the feed
    permits: the alarm belongs to the feed, not to this field.
    """

    indicator: str
    last_data_day: str | None
    days_since: int | None
    days_behind_feed: int | None
    reference_p90_days: float | None
    reference_max_days: int | None
    multiple_of_own_worst: float | None
    severity: str
    owner: str


@dataclass(frozen=True, slots=True)
class StalenessReading:
    """Everything the monitor knows at one instant, with no clock of its own."""

    as_of: str
    last_data_day: str
    last_published_day: str
    days_since_data_day: int
    days_since_publication: int
    reference_p90_gap_days: float
    reference_max_gap_days: int
    multiple_of_worst_observed: float
    severity: str
    indicators: tuple[IndicatorStaleness, ...]
    red_indicators: tuple[str, ...]
    indicator_specific_alarms: tuple[str, ...]


def staleness(rows: Sequence[dict], as_of: dt.date) -> StalenessReading:
    """How stale the feed and each indicator are, as of a date you pass in.

    Severity is graded against the feed's own record. GREEN means the silence is
    within the ninetieth percentile of silences this feed routinely produces;
    AMBER means it is longer than routine but has happened; RED means the feed has
    now been quiet longer than it has ever been quiet, which is the first moment
    at which 'it is probably fine' stops being supported by anything. Hard-coding
    'alert after 7 days' would have been an opinion; this is a measurement.
    """
    delivered, _ = packets(rows)
    if not delivered:
        raise CadenceIntegrityError("no parseable SitRep packets in the extract")
    last_day = delivered[-1].data_day
    last_published = max(p.published_day for p in delivered)
    if as_of < last_published:
        raise CadenceIntegrityError(
            f"as-of {as_of} precedes the last publication date {last_published} in "
            "the extract; the reading would be taken against a packet that had not "
            "been published yet. Pass rows_as_of(rows, as_of) rather than the full "
            "extract."
        )

    gaps = _gap_series([p.data_day for p in delivered])
    p90, worst = _gap_thresholds(gaps)
    since = (as_of - last_day).days

    readings: list[IndicatorStaleness] = []
    for name in indicators(rows):
        days = populated_days(rows, name)
        if not days:
            readings.append(
                IndicatorStaleness(
                    indicator=name,
                    last_data_day=None,
                    days_since=None,
                    days_behind_feed=None,
                    reference_p90_days=None,
                    reference_max_days=None,
                    multiple_of_own_worst=None,
                    severity=RED,
                    owner=INDICATOR_OWNED,
                )
            )
            continue
        own_gaps = _gap_series(days)
        own_p90, own_worst = _gap_thresholds(own_gaps)
        elapsed = (as_of - days[-1]).days
        behind = elapsed - since
        readings.append(
            IndicatorStaleness(
                indicator=name,
                last_data_day=days[-1].isoformat(),
                days_since=elapsed,
                days_behind_feed=behind,
                reference_p90_days=own_p90,
                reference_max_days=own_worst,
                multiple_of_own_worst=(
                    round(elapsed / own_worst, 2) if own_worst else float("inf")
                ),
                severity=_severity(elapsed, own_p90, own_worst),
                owner=INDICATOR_OWNED if behind > 0 else FEED_OWNED,
            )
        )

    return StalenessReading(
        as_of=as_of.isoformat(),
        last_data_day=last_day.isoformat(),
        last_published_day=last_published.isoformat(),
        days_since_data_day=since,
        days_since_publication=(as_of - last_published).days,
        reference_p90_gap_days=p90,
        reference_max_gap_days=worst,
        multiple_of_worst_observed=round(since / worst, 2) if worst else float("inf"),
        severity=_severity(since, p90, worst),
        indicators=tuple(readings),
        red_indicators=tuple(r.indicator for r in readings if r.severity == RED),
        indicator_specific_alarms=tuple(
            r.indicator
            for r in readings
            if r.owner == INDICATOR_OWNED and r.severity != GREEN
        ),
    )


def _gap_series(days: Sequence[dt.date]) -> list[int]:
    return [(b - a).days for a, b in zip(days, days[1:])]


def _gap_thresholds(gaps: Sequence[int]) -> tuple[float, int]:
    """The p90 and worst silence this series has produced.

    A series with a single observation has produced no silences at all, so it
    gets the only defensible bar: one day. Anything longer is unprecedented for
    it, because everything is.
    """
    if not gaps:
        return 1.0, 1
    ordered = sorted(gaps)
    return _quantile(ordered, 0.9), ordered[-1]


def _severity(elapsed: int, p90: float, worst: int) -> str:
    if elapsed > worst:
        return RED
    if elapsed > p90:
        return AMBER
    return GREEN


# --- 4. continuity forecast --------------------------------------------------


@dataclass(frozen=True, slots=True)
class ContinuityForecast:
    """Probability the stream keeps behaving, and the honest size of that claim."""

    as_of: str
    horizon_days: int
    p_sustains_cadence: float
    p_no_longer_silence: float
    p_volume_at_least_recent: float
    recent_arrivals: int
    recent_worst_silence_days: int
    expected_arrivals: float
    arrivals_p05: float
    arrivals_p50: float
    arrivals_p95: float
    worst_silence_p50: float
    worst_silence_p95: float
    reference_days: int
    n_reference_gaps: int
    bootstrap_max_possible_silence_days: int
    non_overlapping_windows: int
    windows_over_benchmark: int
    windows_with_unprecedented_silence: int
    stall_upper_bound_95: float
    n_paths: int
    block: int
    seed: int
    method: str


def continuity_forecast(
    rows: Sequence[dict],
    as_of: dt.date,
    *,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    reference_days: int = DEFAULT_REFERENCE_DAYS,
    seed: int = DEFAULT_SEED,
    n_paths: int = DEFAULT_PATHS,
    block: int = DEFAULT_BLOCK,
) -> ContinuityForecast:
    """P(the next `horizon_days` sustain the recent delivery cadence).

    Method. Stationary block bootstrap over the inter-arrival gaps between data
    days in the reference window, walking arrivals forward until the horizon is
    exhausted. Blocks rather than single gaps because reporting stress is
    autocorrelated: a week when the response is overwhelmed produces several slow
    days in a row, and resampling gaps independently would price a sustained
    slowdown as the product of independent unlikely days.

    opsforecast's bootstrap is not reused directly because it walks a level
    forward from first differences; an inter-arrival gap is the quantity itself,
    not a difference of one, and cumulating gaps into a fixed horizon is a
    different walk. The resampling scheme, block length and seeding are the same.

    Sustaining is two conditions, both benchmarked against the most recent
    horizon-length window, which is what 'the recent cadence' means: at least as
    many arrivals, and no silence longer than the worst one in that window. Both
    benchmarks are measured, not chosen. The reference window the bootstrap draws
    from is deliberately wider than the benchmark window, so it contains slow
    stretches the recent window does not -- otherwise the question would be
    scored against its own answer.

    A trailing silence still open when the horizon ends is counted only at its
    observed length, because that is all anyone would have seen by then.

    LIMITS, and they are structural rather than decorative. A gap bootstrap can
    never draw a gap longer than the longest gap it resamples, so the worst
    silence this method can produce is bounded at
    `bootstrap_max_possible_silence_days`. It prices variation inside a regime
    that never broke; it cannot price the regime breaking. That is not a
    hypothetical for this programme -- an input feed stopping unannounced is the
    exact failure that cost it 58 days. The record holds too few independent
    horizon-length windows to bound that tail, which is what
    `stall_upper_bound_95` reports: read it beside the probability or not at all.
    """
    delivered, _ = packets(rows)
    if not delivered:
        raise CadenceIntegrityError("no parseable SitRep packets in the extract")
    days = sorted({p.data_day for p in delivered})
    last_day = days[-1]
    last_published = max(p.published_day for p in delivered)
    if as_of < last_published:
        raise CadenceIntegrityError(
            f"as-of {as_of} precedes the last publication date {last_published}; "
            "forecasting from an extract that already contains packets nobody could "
            "have read yet is look-ahead"
        )

    window = [d for d in days if (last_day - d).days < reference_days]
    gaps = _gap_series(window)
    if len(gaps) < block + 1:
        raise CadenceIntegrityError(
            f"only {len(gaps)} inter-arrival gaps in the last {reference_days} days; "
            f"need more than the block length ({block}) to bootstrap"
        )

    recent_days = [d for d in days if (last_day - d).days < horizon_days]
    recent_gaps = _gap_series(recent_days)
    if not recent_gaps:
        raise CadenceIntegrityError(
            f"only {len(recent_days)} arrival(s) in the last {horizon_days} days; "
            "no recent cadence to benchmark against"
        )
    benchmark_silence = max(recent_gaps)
    benchmark_arrivals = len(recent_days)

    simulated = _simulate_arrivals(gaps, horizon_days, n_paths, block, seed)
    quiet = sum(1 for _, worst in simulated if worst <= benchmark_silence)
    volume = sum(1 for count, _ in simulated if count >= benchmark_arrivals)
    both = sum(
        1
        for count, worst in simulated
        if worst <= benchmark_silence and count >= benchmark_arrivals
    )
    counts = sorted(float(c) for c, _ in simulated)
    worsts = sorted(float(w) for _, w in simulated)

    record_gaps = _gap_series(days)
    record_worst = max(record_gaps)
    over, unprecedented = _windows_breaching(days, horizon_days, benchmark_silence)
    windows = (days[-1] - days[0]).days // horizon_days

    return ContinuityForecast(
        as_of=as_of.isoformat(),
        horizon_days=horizon_days,
        p_sustains_cadence=round(both / n_paths, 4),
        p_no_longer_silence=round(quiet / n_paths, 4),
        p_volume_at_least_recent=round(volume / n_paths, 4),
        recent_arrivals=benchmark_arrivals,
        recent_worst_silence_days=benchmark_silence,
        expected_arrivals=round(sum(counts) / len(counts), 2),
        arrivals_p05=_quantile(counts, 0.05),
        arrivals_p50=_quantile(counts, 0.5),
        arrivals_p95=_quantile(counts, 0.95),
        worst_silence_p50=_quantile(worsts, 0.5),
        worst_silence_p95=_quantile(worsts, 0.95),
        reference_days=reference_days,
        n_reference_gaps=len(gaps),
        bootstrap_max_possible_silence_days=max(gaps),
        non_overlapping_windows=windows,
        windows_over_benchmark=over,
        windows_with_unprecedented_silence=unprecedented,
        stall_upper_bound_95=round(min(1.0, 3 / windows), 4) if windows else 1.0,
        n_paths=n_paths,
        block=block,
        seed=seed,
        method=(
            f"stationary block bootstrap over {len(gaps)} inter-arrival gaps from the "
            f"last {reference_days} days; {n_paths} paths, {block}-day blocks, seed "
            f"{seed}. Sustains = at least {benchmark_arrivals} arrivals and no silence "
            f"longer than {benchmark_silence}d, both benchmarked on the last "
            f"{horizon_days} days. Bootstrap cannot draw a silence longer than "
            f"{max(gaps)}d, and the record's worst is {record_worst}d"
        ),
    )


def _windows_breaching(
    days: Sequence[dt.date], horizon_days: int, benchmark: int
) -> tuple[int, int]:
    """How many non-overlapping horizon windows in the record breached the bar.

    The empirical companion to the bootstrap, and the only check on it that does
    not share its assumptions. It is reported with its window count because three
    windows is not a rate, and pretending otherwise is how a monitor talks itself
    into confidence it has not earned. The second count -- windows containing a
    silence longer than anything else in the record -- is zero by construction,
    which is precisely why the tail is unbounded here.
    """
    if len(days) < 2:
        return 0, 0
    record_worst = max(_gap_series(days))
    over = unprecedented = 0
    start = days[0]
    while start + dt.timedelta(days=horizon_days) <= days[-1]:
        stop = start + dt.timedelta(days=horizon_days)
        inside = [d for d in days if start <= d <= stop]
        if len(inside) >= 2:
            worst = max(_gap_series(inside))
            over += worst > benchmark
            unprecedented += worst > record_worst
        start = stop
    return over, unprecedented


def _simulate_arrivals(
    gaps: Sequence[int],
    horizon_days: int,
    n_paths: int,
    block: int,
    seed: int,
) -> list[tuple[int, int]]:
    """Arrivals and worst silence per simulated horizon.

    The trailing gap is right-censored on purpose: when the next resampled gap
    would carry past the horizon, only the part inside the horizon is a silence
    anyone could have observed. Counting the whole of it would let the simulator
    invent silences longer than the question asked about.
    """
    rng = random.Random(seed)
    out: list[tuple[int, int]] = []
    starts = len(gaps) - block + 1
    for _ in range(n_paths):
        elapsed = arrivals = longest = 0
        while True:
            i = rng.randrange(0, starts)
            censored = False
            for gap in gaps[i : i + block]:
                if elapsed + gap > horizon_days:
                    longest = max(longest, horizon_days - elapsed)
                    censored = True
                    break
                elapsed += gap
                arrivals += 1
                longest = max(longest, gap)
            if censored:
                break
        out.append((arrivals, longest))
    return out


# --- assembled report --------------------------------------------------------


@dataclass(frozen=True, slots=True)
class IntegrityReport:
    as_of: str
    source: str
    delivery: DeliveryIntegrity
    lag: LagDistribution
    coverage: CoverageReport
    staleness: StalenessReading
    continuity: ContinuityForecast


def report(
    rows: Sequence[dict],
    as_of: dt.date,
    *,
    source: str = str(DEFAULT_SERIES),
    seed: int = DEFAULT_SEED,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    reference_days: int = DEFAULT_REFERENCE_DAYS,
    alpha: float = DEFAULT_ALPHA,
    n_paths: int = DEFAULT_PATHS,
) -> IntegrityReport:
    """Every reading the monitor produces, from one extract and one as-of date."""
    return IntegrityReport(
        as_of=as_of.isoformat(),
        source=source,
        delivery=delivery_integrity(rows),
        lag=publication_lag(rows, seed=seed, n_permutations=n_paths, alpha=alpha),
        coverage=indicator_coverage(rows, alpha=alpha),
        staleness=staleness(rows, as_of),
        continuity=continuity_forecast(
            rows,
            as_of,
            horizon_days=horizon_days,
            reference_days=reference_days,
            seed=seed,
            n_paths=n_paths,
        ),
    )


def render(result: IntegrityReport) -> str:
    """Plain-text rendering. Every figure in docs/cadence-integrity.md comes from here."""
    d, lag, cov, st, cont = (
        result.delivery,
        result.lag,
        result.coverage,
        result.staleness,
        result.continuity,
    )
    lines: list[str] = []
    add = lines.append
    add(f"REPORTING-CADENCE INTEGRITY  as-of {result.as_of}")
    add(f"source: {result.source}")
    add("")
    add("1. DELIVERY INTEGRITY")
    add(
        f"  SitReps {d.first_sitrep:03d}-{d.last_sitrep:03d}: {d.delivered} delivered "
        f"of {d.expected} numbered ({len(d.missing_numbers)} absent)"
    )
    for gap in d.gaps:
        run = ", ".join(f"{n:03d}" for n in gap.missing)
        add(
            f"    {run}  between {gap.before}({gap.before_data_day}) and "
            f"{gap.after}({gap.after_data_day})  calendar gap "
            f"{gap.calendar_gap_days}d -> {gap.verdict}"
        )
    add(
        f"  data days covered: {d.data_days_covered} of {d.calendar_span_days} "
        f"({d.first_data_day} .. {d.last_data_day})"
    )
    add(f"  uncovered days: {', '.join(d.uncovered_data_days) or 'none'}")
    add(
        "  uncovered with the numbering intact: "
        f"{', '.join(d.uncovered_without_number_gap) or 'none'}"
    )
    for day, ids in d.shared_data_days:
        add(f"  shared data day {day}: {', '.join(ids)}")
    add(f"  delivered empty: {', '.join(d.empty_packets) or 'none'}")
    add(f"  delivered with a single indicator: {len(d.thin_packets)}")
    a = d.anomalies
    add(
        f"  extract anomalies: duplicate rows {list(a.duplicate_sitreps) or 'none'}; "
        f"unparseable {list(a.unparseable_sitreps) or 'none'}; not ready "
        f"{list(a.rows_not_ready) or 'none'}; negative lag "
        f"{list(a.negative_lag_sitreps) or 'none'}"
    )
    add(
        f"  derived ratios absent though computable: "
        f"{len(a.derived_absent_though_computable)}; present though uncomputable: "
        f"{len(a.derived_present_though_uncomputable)}"
    )
    add("")
    add("   PUBLICATION LAG (published_at - data_as_of)")
    add(
        f"  n={lag.n}  min={lag.minimum}  p50={lag.p50:g}  p90={lag.p90:g}  "
        f"max={lag.maximum}  mean={lag.mean:.3f}"
    )
    add("  histogram: " + ", ".join(f"{k}d x{v}" for k, v in lag.histogram))
    add(
        f"  first half {lag.first_half_mean:.3f} -> second half "
        f"{lag.second_half_mean:.3f}  ({lag.drift_days:+.3f}d, permutation "
        f"p={lag.drift_p_value:.4f}, n={lag.n_permutations}) -> {lag.drift_verdict}"
    )
    add("")
    add("2. INDICATOR COMPLETENESS")
    add(
        f"  {'indicator':34s} {'n':>4s} {'cov':>5s} {'since':>6s} {'h1':>5s} "
        f"{'h2':>5s} {'p':>7s}  trend      status  last"
    )
    for entry in cov.indicators:
        h1 = f"{entry.first_half_coverage:.2f}" if entry.first_half_coverage is not None else "  - "
        h2 = f"{entry.second_half_coverage:.2f}" if entry.second_half_coverage is not None else "  - "
        p = f"{entry.trend_p_value:.4f}" if entry.trend_p_value is not None else "     - "
        add(
            f"  {entry.indicator:34s} {entry.n_populated:4d} "
            f"{entry.coverage_overall:5.2f} {entry.coverage_since_first:6.2f} "
            f"{h1:>5s} {h2:>5s} {p:>7s}  {entry.trend:10s} {entry.status:11s} "
            f"{entry.last_data_day or '-'}"
        )
    add(f"  Holm-corrected at alpha={cov.alpha} across {cov.n_tested} tested indicators")
    for entry in cov.indicators:
        if entry.status in (LOST, INTERRUPTED, NEVER_REPORTED):
            add(
                f"    {entry.indicator}: dark {entry.current_dark_run} packets "
                f"(worst prior internal run {entry.longest_prior_dark_run}) -> "
                f"{entry.status}"
            )
            add(f"      {entry.attribution}")
    for sitrep, members in cov.co_stop_cohorts:
        add(f"  co-stopped at SitRep {sitrep}: {', '.join(members)}")
    add("")
    add("3. STALENESS CLOCK")
    add(
        f"  as-of {st.as_of}: last data day {st.last_data_day} "
        f"({st.days_since_data_day}d ago), last publication {st.last_published_day} "
        f"({st.days_since_publication}d ago)"
    )
    add(
        f"  feed reference: p90 gap {st.reference_p90_gap_days:g}d, worst gap "
        f"{st.reference_max_gap_days}d -> {st.multiple_of_worst_observed}x worst "
        f"observed -> {st.severity.upper()}"
    )
    own = [r for r in st.indicators if r.owner == INDICATOR_OWNED and r.severity != GREEN]
    inherited = [
        r for r in st.indicators if r.owner == FEED_OWNED and r.severity != GREEN
    ]
    add(f"  indicator-specific alarms ({len(own)}) -- refreshing the feed will NOT clear these:")
    for reading in sorted(own, key=lambda r: -(r.multiple_of_own_worst or 0)):
        add(
            f"    {reading.severity.upper():5s} {reading.indicator:34s} last "
            f"{reading.last_data_day}  {reading.days_since}d "
            f"({reading.days_behind_feed}d behind the feed, "
            f"{reading.multiple_of_own_worst}x its own worst gap of "
            f"{reading.reference_max_days}d)"
        )
    add(
        f"  inherited from the feed's own {st.days_since_data_day}d staleness "
        f"({len(inherited)}): {', '.join(r.indicator for r in inherited) or 'none'}"
    )
    add("")
    add("4. CONTINUITY FORECAST")
    add(f"  method: {cont.method}")
    add(
        f"  benchmark from the last {cont.horizon_days}d: {cont.recent_arrivals} "
        f"arrivals, worst silence {cont.recent_worst_silence_days}d"
    )
    add(f"  P(sustains both) = {cont.p_sustains_cadence}")
    add(
        f"    P(no silence > {cont.recent_worst_silence_days}d) = "
        f"{cont.p_no_longer_silence}"
    )
    add(
        f"    P(at least {cont.recent_arrivals} arrivals) = "
        f"{cont.p_volume_at_least_recent}"
    )
    add(
        f"  arrivals: mean {cont.expected_arrivals}, p05 {cont.arrivals_p05:g}, "
        f"p50 {cont.arrivals_p50:g}, p95 {cont.arrivals_p95:g}"
    )
    add(
        f"  worst silence: p50 {cont.worst_silence_p50:g}d, p95 "
        f"{cont.worst_silence_p95:g}d, structurally capped at "
        f"{cont.bootstrap_max_possible_silence_days}d"
    )
    add(
        f"  empirical check: {cont.windows_over_benchmark} of "
        f"{cont.non_overlapping_windows} non-overlapping {cont.horizon_days}d windows "
        f"in the record breached the silence benchmark; "
        f"{cont.windows_with_unprecedented_silence} contained an unprecedented "
        f"silence -> rule-of-three 95% upper bound on a stall = "
        f"{cont.stall_upper_bound_95}"
    )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Reporting-cadence integrity monitor for the BDBV SitRep stream. "
            "The as-of date is required: this module never reads the system clock."
        )
    )
    parser.add_argument(
        "--as-of",
        required=True,
        help="ISO date the reading is taken at (YYYY-MM-DD). Required, never inferred.",
    )
    parser.add_argument("--series", default=str(DEFAULT_SERIES))
    parser.add_argument("--horizon-days", type=int, default=DEFAULT_HORIZON_DAYS)
    parser.add_argument("--reference-days", type=int, default=DEFAULT_REFERENCE_DAYS)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--paths", type=int, default=DEFAULT_PATHS)
    args = parser.parse_args(argv)

    as_of = dt.date.fromisoformat(args.as_of)
    rows = rows_as_of(load_rows(Path(args.series)), as_of)
    print(
        render(
            report(
                rows,
                as_of,
                source=args.series,
                seed=args.seed,
                horizon_days=args.horizon_days,
                reference_days=args.reference_days,
                n_paths=args.paths,
            )
        )
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
