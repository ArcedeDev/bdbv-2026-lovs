"""Isolation capacity and ward events, and an honest census of what is missing.

Why this exists. Isolation occupancy is the one operational stock that has risen
without interruption in aggregate through the whole outbreak: 258 -> 896, and 896
is its all-time high. Block 6 already forecasts it. What Block 6 does not do is
say what that number is a share OF, where it is being driven from, or what the
response system has stopped publishing about it. Those three questions are what
this module answers, and the third turns out to be the load-bearing one.

The finding that came first. The LOVS evidence store defines `isolation_escapes`,
`isolation_admissions` and `isolation_deaths`. All three ARE in the SitRep packet
substrate -- they were never absent from the public record. What they are is
DISCONTINUED. The INSP ward-flow table (patients at bed J-1, admissions, deaths,
non-cases, escapes, total exits, end-of-day census) was published through the
early outbreak and has been progressively dropped column by column, newest
casualty first, while the end-of-day census kept running. `audit_isolation_metrics`
derives the exact last day for each. The shape of the result is the finding: the
response is still publishing the STOCK and has stopped publishing the FLOW.

That is worse than an absence, because an absence is visible. A stock without a
flow looks complete. It is what lets a reader say "occupancy rose 40 today"
without being able to ask whether that was 40 more admissions or 40 fewer
discharges -- clinically and operationally opposite events.

Second finding, and the reason nothing here reports a clean turnover number. The
frozen extract carries no admissions or discharges, so the obvious move is to
infer them from day-over-day occupancy change against new confirmed cases and
cumulative recoveries. This module builds that inference and then MEASURES it
against the days where the packets still carry observed admissions. It recovers a
median 44 percent of observed admissions at r = 0.30. So the inference is not
merely "unobserved, treat with care" -- it is measurably wrong, and this module
reports it as a rejected estimator rather than as a caveated figure. The identity
it rests on (change in occupancy = admissions minus exits) is exact; every term
needed to solve it is either missing or mis-scoped, and the miss is quantified.

Third finding, methodological, and it bears on Block 6. The per-province
breakdown is a coverage-varying sum: provinces drop out of the table and return
between cycles. Comparing only provinces that reported on both days cuts the
day-over-day standard deviation from 62.7 to 35.7 while leaving the mean drift
essentially unchanged. Most of the apparent volatility in the occupancy series is
provinces appearing and disappearing, not patients arriving and leaving. The
Block 6 bootstrap resamples that churn as if it were clinical movement. This
module does not change Block 6 -- it reproduces it exactly as a consistency check
and names the contamination.

What is NOT here, and cannot be. Real headroom. Headroom needs a bed-capacity
denominator and the substrate has exactly one structured observation of it in 101
packets. `headroom` therefore returns a refusal carrying the list of what would
have to be published, not a proxy. A capacity number invented to make a headroom
percentage computable would be the single most dangerous figure this repository
could emit, because it would be quoted as an operational ceiling.

Every record carries a `kind`: OBSERVED (read from a published figure), DERIVED
(computed here from published figures, with the derivation named), FORECAST (a
bootstrap probability) or UNAVAILABLE (a refusal with a reason). Nothing in the
companion doc is typed by hand; `main()` prints every figure the doc quotes.

Deterministic. Stdlib only. No network. No clock -- as-of dates are parameters.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from lovs.forecast import opsforecast as of

REPO = Path(__file__).resolve().parent.parent.parent
DEFAULT_SERIES = of.DEFAULT_SERIES
DEFAULT_LEDGER = REPO / "data" / "operational-calibration-ledger.json"

OCCUPANCY_METRIC = "hospital_isolation_total"
PROVINCE_FIELD = "isolation_by_province"
SPLIT_FIELD = "province_split"

#: The as-of used by Block 6. Passed, never read from a clock.
DEFAULT_AS_OF = dt.date(2026, 9, 1)
DEFAULT_HORIZON_DAYS = 30

#: Trailing window for province pressure. Two weeks: long enough that one
#: missing cycle does not dominate the slope, short enough that a province that
#: only started filling in August is not averaged against July.
DEFAULT_WINDOW_DAYS = 14

#: Below this many active confirmed cases the per-case normalisation is noise:
#: Sud-Kivu carries 3 confirmed and 26 people in isolation, so its "occupancy
#: per active case" is 13 and means nothing about pressure. Provinces under the
#: floor are reported but excluded from the normalised ranking rather than
#: silently ranked.
MIN_ACTIVE_FOR_RATIO = 30

#: Seeds for this module's own threshold ladder. Deliberately far from Block 6's
#: 6001-6012 so a number from here can never be mistaken for a pinned one.
LADDER_SEED = 7100

OBSERVED = "OBSERVED"
DERIVED = "DERIVED"
FORECAST = "FORECAST"
UNAVAILABLE = "UNAVAILABLE"


# --- packet substrate location -----------------------------------------------

def find_packets_dir(start: Path | str | None = None) -> Path | None:
    """Locate the read-only SitRep packet directory, or None if it is not here.

    The packets live in a sibling checkout, not this repo, so every consumer must
    tolerate their absence: the frozen extract is the contract, the packets are a
    bonus that lets the availability census and the turnover validation run. A
    caller that gets None still gets every extract-derived figure.
    """
    if start is not None:
        p = Path(start)
        return p if p.is_dir() else None
    rel = Path("lovs-evidence-mcp") / "data" / "sitrep-packets"
    for base in (REPO.parent, REPO.parent.parent, REPO.parent.parent.parent):
        cand = base / rel
        if cand.is_dir():
            return cand
    return None


# --- 1. availability census --------------------------------------------------

#: The ward-flow families, each with every key the packets have used for it.
#: The alias lists are not tidiness -- the SitRep schema drifted repeatedly
#: (`escaped` -> `escaped_confirmed` -> `escaped_suspect_or_confirmed_24h`), and
#: a census that matched one spelling would report a discontinuation that was
#: really a rename. Matching every spelling is what makes the "last published
#: day" claim trustworthy.
FLOW_ALIASES: dict[str, tuple[str, ...]] = {
    "isolation_escapes": (
        "escaped_suspect_or_confirmed_24h", "escaped", "escaped_confirmed",
        "escaped_suspects_or_confirmed",
    ),
    "isolation_admissions": (
        "admissions_24h", "total_admissions", "admissions_ppl", "admissions_other",
        "new_admissions_ppl_total", "new_admissions_other_total",
        "cumulative_admitted_isolation",
    ),
    "isolation_deaths": (
        "deceased_suspect_or_confirmed_24h", "deceased", "deceased_suspects",
        "deceased_confirmed", "deceased_suspects_or_confirmed",
    ),
    "isolation_exits": ("total_exits_24h", "total_exits"),
    "isolation_non_cases": ("non_case_or_recovered_24h", "non_cases", "non_cases_or_recovered"),
    "isolation_occupancy": (
        "patients_in_isolation_end_of_day", "patients_in_isolation_end_day",
        "patients_in_isolation",
    ),
    "isolation_prior_day_census": (
        "patients_at_bed_j_minus_1", "patients_present_previous_day",
        "patients_present_previous_day_total",
    ),
    "isolation_bed_capacity": ("beds_available",),
}

#: Fields the frozen extract actually carries. Anything else is packets-only.
EXTRACT_FIELDS = {"isolation_occupancy": OCCUPANCY_METRIC}


@dataclass(frozen=True, slots=True)
class MetricAvailability:
    """One LOVS isolation metric and the truth about where it can be read."""

    metric: str
    kind: str
    source: str
    in_frozen_extract: bool
    packets_scanned: int
    #: Distinct DATA days, not packets. Two packets occasionally carry the same
    #: data day; counting packets would overstate coverage by exactly that much.
    packet_days_carrying: int
    extract_days_carrying: int
    first_day: str | None
    last_day: str | None
    days_stale_at_as_of: int | None
    aliases_seen: tuple[str, ...]
    note: str


def _packet_flow_block(packet: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """The ward-flow table, wherever this packet generation put it."""
    figures = packet.get("figures")
    if not isinstance(figures, Mapping):
        return None
    block = figures.get("patient_movement")
    return block if isinstance(block, Mapping) else None


def _packet_capacity_keys(packet: Mapping[str, Any]) -> list[str]:
    """Bed-capacity keys, which live in the reviewed per-province detail, not the
    flow table. Kept separate so a capacity claim is never read off the flow
    table's `patients_at_bed_j_minus_1`, which is a prior-day CENSUS and not a
    capacity at all. Confusing the two would manufacture the exact denominator
    this module refuses to invent."""
    figures = packet.get("figures")
    if not isinstance(figures, Mapping):
        return []
    detail = figures.get("reviewed_operational_detail")
    if not isinstance(detail, Mapping):
        return []
    rows = detail.get("patient_movement_by_province")
    if not isinstance(rows, list):
        return []
    hits = []
    for row in rows:
        if isinstance(row, Mapping):
            hits += [k for k in FLOW_ALIASES["isolation_bed_capacity"] if k in row]
    return hits


def last_data_day(rows: Sequence[Mapping[str, Any]]) -> str | None:
    """The newest DATA day in the frozen extract, as an ISO date string."""
    days = [str(r["data_as_of"])[:10] for r in rows if r.get("data_as_of")]
    return max(days) if days else None


def read_packets(packets_dir: Path | str, through: str | None = None) -> list[dict]:
    """Every SitRep packet, chronologically, read-only.

    Sorted by data day rather than filename because the packet filenames carry a
    WordPress post id that does not order with the data.

    `through` drops packets whose data day is later than it. The sibling checkout
    keeps growing after the extract is frozen, so an uncut read makes every
    figure here drift with each new SitRep; pass the extract's `last_data_day`
    to read exactly the substrate the extract was cut from.
    """
    out: list[dict] = []
    for path in sorted(Path(packets_dir).glob("sitrep-*.json")):
        with open(path, encoding="utf-8") as handle:
            packet = json.load(handle)
        if not (isinstance(packet, dict) and packet.get("data_as_of")):
            continue
        if through is not None and str(packet["data_as_of"])[:10] > through:
            continue
        out.append(packet)
    out.sort(key=lambda p: str(p["data_as_of"])[:10])
    return out


def audit_isolation_metrics(
    rows: Sequence[Mapping[str, Any]],
    packets: Sequence[Mapping[str, Any]] | None = None,
    *,
    as_of: dt.date = DEFAULT_AS_OF,
) -> list[MetricAvailability]:
    """Where each isolation metric can actually be read, and when it stopped.

    `days_stale_at_as_of` is measured against the metric's own last DATA day, not
    its last publication day, and not against a clock. A metric whose staleness
    exceeds the occupancy series' staleness has been discontinued while the
    reports kept coming -- which is the distinction this census exists to make.
    """
    census: list[MetricAvailability] = []
    packet_days: dict[str, list[str]] = {}
    packet_aliases: dict[str, set[str]] = {}
    scanned = len(packets) if packets is not None else 0

    for packet in packets or ():
        day = str(packet.get("data_as_of"))[:10]
        block = _packet_flow_block(packet)
        for metric, aliases in FLOW_ALIASES.items():
            if metric == "isolation_bed_capacity":
                hits = _packet_capacity_keys(packet)
            else:
                hits = [a for a in aliases if isinstance(block, Mapping) and a in block]
            if hits:
                packet_days.setdefault(metric, []).append(day)
                packet_aliases.setdefault(metric, set()).update(hits)

    extract_days = {
        name: sorted(
            str(r["data_as_of"])[:10]
            for r in rows
            if r.get(field_) is not None and r.get("data_as_of")
        )
        for name, field_ in EXTRACT_FIELDS.items()
    }

    for metric in FLOW_ALIASES:
        in_extract = metric in EXTRACT_FIELDS
        ext_days = sorted(set(extract_days.get(metric, [])))
        pkt_days = sorted(set(packet_days.get(metric, [])))
        # Report the span from whichever source the forecast layer would actually
        # read, so "last published day" answers the question a consumer is asking.
        days = ext_days if in_extract else pkt_days
        source = "frozen extract" if in_extract else ("packet substrate" if pkt_days else "nowhere")
        first = days[0] if days else None
        last = days[-1] if days else None
        stale = (as_of - dt.date.fromisoformat(last)).days if last else None
        if not pkt_days and not in_extract:
            kind = UNAVAILABLE
            note = "no packet in the scanned set carries this metric under any known alias"
        elif in_extract:
            kind = OBSERVED
            note = "carried by the frozen extract; the forecast layer can use it directly"
        else:
            kind = OBSERVED
            note = (
                "present in the packet substrate but ABSENT from the frozen extract, "
                "so the forecast layer cannot use it without an extract change"
            )
        census.append(MetricAvailability(
            metric=metric,
            kind=kind,
            source=source,
            in_frozen_extract=in_extract,
            packets_scanned=scanned,
            packet_days_carrying=len(pkt_days),
            extract_days_carrying=len(ext_days),
            first_day=first,
            last_day=last,
            days_stale_at_as_of=stale,
            aliases_seen=tuple(sorted(packet_aliases.get(metric, ()))),
            note=note,
        ))
    census.sort(key=lambda m: (m.days_stale_at_as_of is None, -(m.days_stale_at_as_of or 0)))
    return census


# --- 2. occupancy trajectory --------------------------------------------------

def _ols_slope(points: Sequence[tuple[float, float]]) -> float | None:
    """Least-squares slope. None when x has no spread, rather than a divide."""
    if len(points) < 2:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    den = sum((x - mx) ** 2 for x in xs)
    if den == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den


@dataclass(frozen=True, slots=True)
class Trajectory:
    """How national occupancy has actually moved. Every field OBSERVED or DERIVED."""

    kind: str
    n_observations: int
    first_day: str
    first_value: float
    last_day: str
    last_value: float
    minimum: float
    maximum: float
    at_all_time_high: bool
    rises: int
    falls: int
    flats: int
    monotone_nondecreasing: bool
    mean_change_per_day: float
    median_change_per_day: float
    ols_slope_per_day: float | None
    largest_single_rise: float
    largest_single_rise_day: str
    largest_single_fall: float
    largest_single_fall_day: str
    occupancy_doubling_days: float | None
    days_stale_at_as_of: int


def occupancy_trajectory(
    rows: Sequence[Mapping[str, Any]],
    *,
    as_of: dt.date = DEFAULT_AS_OF,
) -> Trajectory:
    """Characterise the national occupancy series: direction, rate, and shape.

    The direction question is not rhetorical. The Block 6 rationale for
    `iso-above-1000` states that occupancy "has risen monotonically to 896"; this
    function counts the falls so that claim can be checked rather than inherited.

    `occupancy_doubling_days` is a doubling time for BEDS OCCUPIED and must never
    be read as an incidence doubling time. Occupancy is a stock governed by
    admissions minus discharges; a lengthening stay doubles it with no change in
    transmission whatsoever.
    """
    obs = of.series(rows, OCCUPANCY_METRIC)
    if len(obs) < 2:
        raise ValueError(f"{OCCUPANCY_METRIC} has {len(obs)} observations; need at least 2")
    steps = of.diffs(obs)
    raw = [b.value - a.value for a, b in zip(obs, obs[1:])]
    first, last = obs[0], obs[-1]
    values = [o.value for o in obs]

    # Days carried alongside the extremes so a doc can cite one without a
    # reader having to re-derive which day it was.
    moves = [(b.value - a.value, b.date) for a, b in zip(obs, obs[1:])]
    top = max(moves, key=lambda m: m[0])
    bottom = min(moves, key=lambda m: m[0])

    ratio = last.value / first.value if first.value > 0 else None
    span = (last.date - first.date).days
    doubling = None
    if ratio and ratio > 1 and span > 0:
        doubling = span * math.log(2) / math.log(ratio)

    return Trajectory(
        kind=DERIVED,
        n_observations=len(obs),
        first_day=first.date.isoformat(),
        first_value=first.value,
        last_day=last.date.isoformat(),
        last_value=last.value,
        minimum=min(values),
        maximum=max(values),
        at_all_time_high=last.value >= max(values),
        rises=sum(1 for d in raw if d > 0),
        falls=sum(1 for d in raw if d < 0),
        flats=sum(1 for d in raw if d == 0),
        monotone_nondecreasing=all(d >= 0 for d in raw),
        mean_change_per_day=statistics.fmean(steps),
        median_change_per_day=statistics.median(steps),
        ols_slope_per_day=_ols_slope([((o.date - first.date).days, o.value) for o in obs]),
        largest_single_rise=top[0],
        largest_single_rise_day=top[1].isoformat(),
        largest_single_fall=bottom[0],
        largest_single_fall_day=bottom[1].isoformat(),
        occupancy_doubling_days=doubling,
        days_stale_at_as_of=(as_of - last.date).days,
    )


# --- 3. coverage churn --------------------------------------------------------

def province_panel(rows: Sequence[Mapping[str, Any]]) -> list[tuple[dt.date, dict[str, float]]]:
    """Per-day province -> occupancy, first row wins on a duplicated data day.

    The `Total` key is dropped: on the days it appears it is the ONLY key, so
    keeping it would make a one-province panel out of a national figure and the
    constant-panel comparison would silently compare a total against a province.
    """
    seen: set[dt.date] = set()
    out: list[tuple[dt.date, dict[str, float]]] = []
    for row in rows:
        stamp = row.get("data_as_of")
        table = row.get(PROVINCE_FIELD)
        if not stamp or not isinstance(table, Mapping):
            continue
        try:
            day = dt.date.fromisoformat(str(stamp)[:10])
        except ValueError:
            continue
        if day in seen:
            continue
        parts = {k: float(v) for k, v in table.items() if k != "Total" and isinstance(v, (int, float))}
        if not parts:
            continue
        seen.add(day)
        out.append((day, parts))
    out.sort(key=lambda p: p[0])
    return out


@dataclass(frozen=True, slots=True)
class CoverageChurn:
    """How much of the occupancy series' movement is provinces, not patients."""

    kind: str
    panel_days: int
    comparable_pairs: int
    raw_sd: float
    constant_panel_sd: float
    raw_mean: float
    constant_panel_mean: float
    variance_share_from_churn: float
    days_with_membership_change: int
    largest_churn_artifact: tuple[str, float, float, tuple[str, ...]] | None
    note: str


def coverage_churn(rows: Sequence[Mapping[str, Any]]) -> CoverageChurn:
    """Split day-over-day occupancy movement into patient flow and reporting churn.

    Method: for each consecutive pair of panel days, compare the raw change in the
    province-sum against the change restricted to provinces that reported on BOTH
    days. The difference is arithmetic, not modelled -- a province that vanished
    from the table did not discharge its patients.

    The mean is expected to survive and the spread is not. If drift is unchanged
    while the standard deviation collapses, the trend is real and the volatility
    is an artifact, which is exactly the case that misleads a bootstrap: it
    resamples the artifact as if it were a clinical excursion and widens every
    interval built on it.
    """
    panel = province_panel(rows)
    raw: list[float] = []
    held: list[float] = []
    changes = 0
    worst: tuple[str, float, float, tuple[str, ...]] | None = None
    for (d0, p0), (d1, p1) in zip(panel, panel[1:]):
        shared = set(p0) & set(p1)
        if set(p0) != set(p1):
            changes += 1
        if not shared:
            continue
        r = sum(p1.values()) - sum(p0.values())
        h = sum(p1[k] for k in shared) - sum(p0[k] for k in shared)
        raw.append(r)
        held.append(h)
        gap = abs(r - h)
        if worst is None or gap > abs(worst[1] - worst[2]):
            worst = (d1.isoformat(), r, h, tuple(sorted(set(p0) ^ set(p1))))

    if len(raw) < 2:
        raise ValueError("province panel too short to measure coverage churn")
    raw_sd = statistics.pstdev(raw)
    held_sd = statistics.pstdev(held)
    share = 1.0 - (held_sd ** 2 / raw_sd ** 2) if raw_sd > 0 else 0.0
    return CoverageChurn(
        kind=DERIVED,
        panel_days=len(panel),
        comparable_pairs=len(raw),
        raw_sd=raw_sd,
        constant_panel_sd=held_sd,
        raw_mean=statistics.fmean(raw),
        constant_panel_mean=statistics.fmean(held),
        variance_share_from_churn=share,
        days_with_membership_change=changes,
        largest_churn_artifact=worst,
        note=("share of day-over-day VARIANCE removed by holding the province panel "
              "constant; the residual mean is the part that is patient flow"),
    )


# --- 4. pressure --------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ProvincePressure:
    """Where occupancy is climbing fastest relative to that province's caseload."""

    province: str
    kind: str
    occupancy: float
    occupancy_day: str
    active_confirmed: float
    occupancy_per_active_confirmed: float | None
    slope_per_day: float | None
    slope_per_100_active: float | None
    window_days: int
    points_in_window: int
    denominator_stable: bool
    flag: str


def _latest_split(rows: Sequence[Mapping[str, Any]]) -> tuple[str, dict[str, dict]]:
    for row in reversed(list(rows)):
        split = row.get(SPLIT_FIELD)
        if isinstance(split, Mapping) and split:
            return str(row["data_as_of"])[:10], {k: dict(v) for k, v in split.items()}
    raise ValueError(f"no row carries {SPLIT_FIELD}")


def province_pressure(
    rows: Sequence[Mapping[str, Any]],
    *,
    window_days: int = DEFAULT_WINDOW_DAYS,
    min_active: int = MIN_ACTIVE_FOR_RATIO,
) -> list[ProvincePressure]:
    """Rank provinces by occupancy growth per unit of confirmed burden.

    Denominator. `active_confirmed` is confirmed minus confirmed deaths, per
    province. Recoveries are published nationally only, so they cannot be
    subtracted per province -- this denominator is therefore an OVER-estimate of
    live confirmed burden, which makes every ratio here a LOWER bound on pressure.
    Stated rather than corrected: correcting it would need a national recovery
    rate applied province-wise, which assumes exactly the uniformity the ranking
    is trying to detect.

    Numerator. Occupancy holds confirmed AND suspected patients; the packets show
    the ward running roughly half suspects. So a ratio above 1.0 does not mean a
    province has hospitalised more people than it has cases -- it means the ward
    is suspect-dominated. Provinces under `min_active` are computed and returned
    but flagged and excluded from the ranking, because at 2 or 3 active cases the
    ratio measures the denominator's roundoff and nothing else.
    """
    panel = province_panel(rows)
    if not panel:
        raise ValueError("no province occupancy panel available")
    split_day, split = _latest_split(rows)
    last_day, last_table = panel[-1]
    window = [(d, p) for d, p in panel if (last_day - d).days <= window_days]

    out: list[ProvincePressure] = []
    for province in sorted(set(last_table) | set(split)):
        points = [(float((d - window[0][0]).days), p[province]) for d, p in window if province in p]
        occ = last_table.get(province)
        rec = split.get(province) or {}
        confirmed = float(rec.get("confirmed", 0) or 0)
        deaths = float(rec.get("confirmed_deaths", 0) or 0)
        active = confirmed - deaths
        stable = active >= min_active
        slope = _ols_slope(points) if len(points) >= 3 else None
        ratio = (occ / active) if (occ is not None and active > 0) else None
        per100 = (100.0 * slope / active) if (slope is not None and active > 0) else None

        flags = []
        if occ is None:
            flags.append("absent from the latest province table")
        if len(points) < 3:
            flags.append(f"only {len(points)} reporting days in the window")
        if not stable:
            flags.append(f"active confirmed {active:.0f} below the {min_active} floor; ratio not interpretable")
        out.append(ProvincePressure(
            province=province,
            kind=DERIVED,
            occupancy=float(occ) if occ is not None else float("nan"),
            occupancy_day=last_day.isoformat(),
            active_confirmed=active,
            occupancy_per_active_confirmed=ratio,
            slope_per_day=slope,
            slope_per_100_active=per100,
            window_days=window_days,
            points_in_window=len(points),
            denominator_stable=stable and len(points) >= 3 and occ is not None,
            flag="; ".join(flags) or "ok",
        ))
    out.sort(key=lambda p: (not p.denominator_stable, -(p.slope_per_100_active or float("-inf"))))
    return out


# --- 5. headroom: a refusal ---------------------------------------------------

@dataclass(frozen=True, slots=True)
class Headroom:
    """Deliberately not a number."""

    kind: str
    computable: bool
    reason: str
    capacity_observations_found: int
    capacity_days: tuple[str, ...]
    capacity_last_day: str | None
    what_would_be_needed: tuple[str, ...]
    closest_available_reading: str


def headroom(
    rows: Sequence[Mapping[str, Any]],
    packets: Sequence[Mapping[str, Any]] | None = None,
) -> Headroom:
    """Refuse to compute headroom, and say precisely what is missing.

    Headroom is occupancy against beds. The substrate publishes occupancy every
    cycle and beds almost never, so any headroom figure would be a occupancy
    series divided by a constant somebody chose. It would be quoted as an
    operational ceiling and it would be fiction.

    The one thing that CAN be reported is how thin the capacity record is, which
    is why this scans for it rather than asserting its absence.
    """
    days: list[str] = []
    for packet in packets or ():
        if _packet_capacity_keys(packet):
            days.append(str(packet.get("data_as_of"))[:10])
    days = sorted(set(days))
    return Headroom(
        kind=UNAVAILABLE,
        computable=False,
        reason=(
            "no bed-capacity denominator. Occupancy is published every cycle; "
            "capacity is not. Dividing by an assumed capacity would emit a number "
            "that reads as an operational ceiling and is not one."
        ),
        capacity_observations_found=len(days),
        capacity_days=tuple(days),
        capacity_last_day=days[-1] if days else None,
        what_would_be_needed=(
            "per-province beds established in dedicated isolation/treatment units, "
            "republished each cycle rather than once",
            "the confirmed/suspect split of those beds, since the two cannot share a ward",
            "beds STAFFED as distinct from beds installed; an unstaffed bed is not headroom",
            "the same series for the transit and triage points that feed the units",
        ),
        closest_available_reading=(
            "occupancy per active confirmed case (see province_pressure), which is a "
            "burden share and NOT a capacity share; it has no ceiling at 1.0"
        ),
    )


# --- 6. turnover: an inference, and its rejection ------------------------------

@dataclass(frozen=True, slots=True)
class TurnoverDay:
    day: str
    net_flow: float
    new_confirmed: float | None
    delta_recovered: float | None
    delta_confirmed_deaths: float | None
    implied_admissions: float | None


@dataclass(frozen=True, slots=True)
class Turnover:
    """Net ward flow (an identity) and the admissions inference (rejected)."""

    kind: str
    net_flow_days: int
    net_flow_mean: float
    net_flow_sd: float
    implied_admission_days: int
    implied_admissions_mean: float | None
    validated: bool
    validation_days: int | None
    correlation_with_observed: float | None
    median_recovery_ratio: float | None
    mean_absolute_error: float | None
    verdict: str
    days: tuple[TurnoverDay, ...] = field(default=(), repr=False)


def _observed_admissions(packets: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    """National admissions per data day, from whichever alias the packet used."""
    def total(value: Any) -> float | None:
        if isinstance(value, Mapping):
            if "Total" in value and isinstance(value["Total"], (int, float)):
                return float(value["Total"])
            nums = [v for v in value.values() if isinstance(v, (int, float))]
            return float(sum(nums)) if nums else None
        if isinstance(value, list):
            # Column-oriented packets put the national total last.
            return float(value[-1]) if value and isinstance(value[-1], (int, float)) else None
        if isinstance(value, (int, float)):
            return float(value)
        return None

    out: dict[str, float] = {}
    for packet in packets:
        block = _packet_flow_block(packet)
        if not isinstance(block, Mapping):
            continue
        day = str(packet.get("data_as_of"))[:10]
        value = None
        for key in ("admissions_24h", "total_admissions"):
            if key in block:
                value = total(block[key])
                break
        if value is None and "admissions_ppl" in block:
            ppl = total(block["admissions_ppl"]) or 0.0
            other = total(block.get("admissions_other")) or 0.0
            value = ppl + other
        if value is None and "new_admissions_ppl_total" in block:
            ppl = total(block["new_admissions_ppl_total"]) or 0.0
            other = total(block.get("new_admissions_other_total")) or 0.0
            value = ppl + other
        if value is not None:
            out.setdefault(day, value)
    return out


def _correlation(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    if len(xs) < 3:
        return None
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = math.sqrt(sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys))
    return num / den if den else None


def turnover(
    rows: Sequence[Mapping[str, Any]],
    packets: Sequence[Mapping[str, Any]] | None = None,
) -> Turnover:
    """Net flow from the extract, plus the admissions inference and its score.

    The identity is exact: occupancy(t) - occupancy(t-1) = admissions - exits.
    Net flow is therefore as OBSERVED as the occupancy series it comes from
    (which the coverage-churn diagnostic shows is not perfectly observed either).

    The inference is the tempting step: add back the exits we can see -- new
    recoveries and new confirmed deaths -- and read off admissions. This function
    computes it and then, where the packets still carry an observed admissions
    figure, scores it. Publishing the inference without that score would be the
    failure mode this repository exists to avoid: a derived number that looks
    like an observation because nobody measured it.

    Only consecutive calendar days are used. Across a reporting gap the identity
    still holds but the daily terms do not line up, and normalising per elapsed
    day (which the bootstrap does for levels) would invent within-gap admissions.
    """
    by_day: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        stamp = row.get("data_as_of")
        if stamp:
            by_day.setdefault(str(stamp)[:10], row)
    days = sorted(by_day)

    records: list[TurnoverDay] = []
    for prev_day, day in zip(days, days[1:]):
        if (dt.date.fromisoformat(day) - dt.date.fromisoformat(prev_day)).days != 1:
            continue
        a, b = by_day[prev_day], by_day[day]
        occ_a, occ_b = a.get(OCCUPANCY_METRIC), b.get(OCCUPANCY_METRIC)
        if occ_a is None or occ_b is None:
            continue
        net = float(occ_b) - float(occ_a)

        def delta(name: str) -> float | None:
            x, y = a.get(name), b.get(name)
            return None if x is None or y is None else float(y) - float(x)

        d_rec = delta("cumulative_recovered")
        d_dth = delta("confirmed_deaths_total")
        implied = net + d_rec + d_dth if (d_rec is not None and d_dth is not None) else None
        records.append(TurnoverDay(
            day=day,
            net_flow=net,
            new_confirmed=(float(b["new_confirmed_today"]) if b.get("new_confirmed_today") is not None else None),
            delta_recovered=d_rec,
            delta_confirmed_deaths=d_dth,
            implied_admissions=implied,
        ))

    if len(records) < 2:
        raise ValueError("not enough consecutive-day pairs to compute net ward flow")
    nets = [r.net_flow for r in records]
    implied = [r.implied_admissions for r in records if r.implied_admissions is not None]

    corr = ratio = mae = None
    n_val: int | None = None
    validated = False
    verdict = (
        "NOT VALIDATED. The packet substrate was not available, so the admissions "
        "inference is unscored. Treat it as unmeasured, not as an estimate."
    )
    if packets:
        observed = _observed_admissions(packets)
        pairs = [(r.implied_admissions, observed[r.day]) for r in records
                 if r.implied_admissions is not None and r.day in observed]
        if len(pairs) >= 3:
            xs = [p[0] for p in pairs]
            ys = [p[1] for p in pairs]
            n_val = len(pairs)
            corr = _correlation(xs, ys)
            ratios = [x / y for x, y in pairs if y]
            ratio = statistics.median(ratios) if ratios else None
            mae = statistics.fmean(abs(x - y) for x, y in pairs)
            # A usable estimator would need to track (r high) AND be unbiased
            # (ratio near 1). Both thresholds are stated here rather than judged
            # after the fact.
            validated = bool(corr is not None and corr >= 0.8 and ratio is not None and 0.8 <= ratio <= 1.25)
            verdict = (
                f"REJECTED against {n_val} days of observed admissions: the inference "
                f"recovers a median {ratio:.0%} of observed admissions at r={corr:.2f}, "
                f"mean absolute error {mae:.0f} patients/day. It is not a turnover "
                f"estimate and must not be published as one. Cause: suspect admissions "
                f"never enter new_confirmed_today, and the non-case and escape exit "
                f"routes are absent from the extract entirely, while community deaths "
                f"inflate confirmed_deaths_total without any corresponding ward exit."
            ) if not validated else (
                f"VALIDATED against {n_val} days: median ratio {ratio:.0%}, r={corr:.2f}, "
                f"mean absolute error {mae:.0f} patients/day."
            )

    return Turnover(
        kind=DERIVED,
        net_flow_days=len(records),
        net_flow_mean=statistics.fmean(nets),
        net_flow_sd=statistics.pstdev(nets),
        implied_admission_days=len(implied),
        implied_admissions_mean=statistics.fmean(implied) if implied else None,
        validated=validated,
        validation_days=n_val,
        correlation_with_observed=corr,
        median_recovery_ratio=ratio,
        mean_absolute_error=mae,
        verdict=verdict,
        days=tuple(records),
    )


# --- 7. threshold forecasts ---------------------------------------------------

DEFAULT_THRESHOLDS: tuple[float, ...] = (850.0, 900.0, 1000.0, 1100.0, 1200.0)


@dataclass(frozen=True, slots=True)
class ThresholdForecast:
    kind: str
    question: str
    shape: str
    threshold: float
    probability: float
    monte_carlo_stderr: float
    horizon_days: int
    as_of: str
    last_observed_day: str
    last_observed_value: float
    days_between_last_data_and_as_of: int
    seed: int
    n_paths: int
    block: int


def occupancy_threshold_forecasts(
    rows: Sequence[Mapping[str, Any]],
    *,
    as_of: dt.date = DEFAULT_AS_OF,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    thresholds: Sequence[float] = DEFAULT_THRESHOLDS,
    seed: int = LADDER_SEED,
    n_paths: int = of.DEFAULT_PATHS,
    block: int = of.DEFAULT_BLOCK,
) -> list[ThresholdForecast]:
    """A ladder of occupancy thresholds at the 30-day horizon.

    Two shapes per threshold. `ends_above` is the end-of-window level; `ever_above`
    is a touch at any point. The validation doc measured `ends_*` shapes at Brier
    0.174-0.188 and warned that "ever crosses X" on a noisy daily series is
    dominated by single-day noise -- and the coverage-churn diagnostic in this
    module explains WHY that noise exists here: a province leaving the table looks
    like a 100-bed excursion. `ever_*` rows are reported so the gap between the
    two shapes is visible, and should be read as an upper bound, not a forecast.

    The horizon is walked from the last DATA day, not from `as_of`. The two differ
    (occupancy is stale at as-of) and the gap is reported on every row so nobody
    reads a 30-step path as reaching 30 days past the as-of date.
    """
    obs = of.series(rows, OCCUPANCY_METRIC)
    if not obs:
        raise ValueError(f"{OCCUPANCY_METRIC} carries no observations")
    last = obs[-1]
    out: list[ThresholdForecast] = []
    for i, threshold in enumerate(thresholds):
        for j, shape in enumerate(("ends_above", "ever_above")):
            event = of.ever_above(threshold) if shape == "ever_above" else of.ends_above(threshold)
            use_seed = seed + 2 * i + j
            p = of.probability(obs, horizon_days, event, seed=use_seed,
                               n_paths=n_paths, block=block, lo=0.0)
            out.append(ThresholdForecast(
                kind=FORECAST,
                question=(
                    f"Does national isolation occupancy "
                    f"{'reach or exceed' if shape == 'ever_above' else 'end at or above'} "
                    f"{threshold:.0f} within {horizon_days} days of {last.date.isoformat()}?"
                ),
                shape=shape,
                threshold=threshold,
                probability=round(p, 4),
                monte_carlo_stderr=math.sqrt(max(p * (1 - p), 0.0) / n_paths),
                horizon_days=horizon_days,
                as_of=as_of.isoformat(),
                last_observed_day=last.date.isoformat(),
                last_observed_value=last.value,
                days_between_last_data_and_as_of=(as_of - last.date).days,
                seed=use_seed,
                n_paths=n_paths,
                block=block,
            ))
    return out


# --- 8. Block 6 consistency check ---------------------------------------------

@dataclass(frozen=True, slots=True)
class ConsistencyCheck:
    pin_id: str
    shape: str
    threshold: float
    seed: int
    pinned_probability: float
    reproduced_probability: float
    delta: float
    matches: bool


def block6_consistency(
    rows: Sequence[Mapping[str, Any]] | None = None,
    ledger_path: Path | str = DEFAULT_LEDGER,
) -> list[ConsistencyCheck]:
    """Re-derive every pinned isolation probability from the ledger's own settings.

    Nothing here is typed: the shape, threshold, seed, path count and block length
    all come out of the committed ledger, so this asserts that the ledger's
    isolation pins are reproducible from the frozen series and that this module
    sits on the same bootstrap Block 6 was priced with.

    A mismatch is a defect in one of them and must be reported as such. Silently
    re-pricing a pinned probability would break the calibration contract, which is
    the only thing that makes the block scorable.
    """
    rows = list(rows) if rows is not None else of.load_rows()
    with open(ledger_path, encoding="utf-8") as handle:
        ledger = json.load(handle)
    obs = of.series(rows, OCCUPANCY_METRIC)

    out: list[ConsistencyCheck] = []
    for blk in ledger.get("blocks", []):
        for point in blk.get("points", []):
            if point.get("metric") != OCCUPANCY_METRIC:
                continue
            gen = point.get("generator") or {}
            shape = point["shape"]
            threshold = float(point["threshold"])
            event = of.ends_below(threshold) if shape == "ends_below" else of.ends_above(threshold)
            p = of.probability(
                obs,
                int(blk.get("horizon_days", DEFAULT_HORIZON_DAYS)),
                event,
                seed=int(gen["seed"]),
                n_paths=int(gen["paths"]),
                block=int(gen["block"]),
                lo=0.0,
            )
            pinned = float(point["probability"])
            out.append(ConsistencyCheck(
                pin_id=point["pin_id"],
                shape=shape,
                threshold=threshold,
                seed=int(gen["seed"]),
                pinned_probability=pinned,
                reproduced_probability=round(p, 4),
                delta=round(p, 4) - pinned,
                matches=round(p, 4) == pinned,
            ))
    if not out:
        raise ValueError(f"ledger at {ledger_path} carries no {OCCUPANCY_METRIC} pins")
    return out


@dataclass(frozen=True, slots=True)
class SeedAgreement:
    """A pinned probability re-priced from an independent seed."""

    pin_id: str
    threshold: float
    pinned_shape: str
    ladder_shape: str
    pinned_probability: float
    ladder_probability: float
    comparison: str
    residual: float
    combined_stderr: float
    within_two_stderr: bool


def _stderr(p: float, n: int) -> float:
    return math.sqrt(max(p * (1.0 - p), 0.0) / n)


def seed_agreement(
    checks: Sequence[ConsistencyCheck],
    forecasts: Sequence[ThresholdForecast],
    *,
    n_paths: int = of.DEFAULT_PATHS,
) -> list[SeedAgreement]:
    """Re-price each pinned threshold from this module's seed and compare.

    The Block 6 reproduction in `block6_consistency` uses the ledger's OWN seed,
    so an exact match proves the pipeline is intact but says nothing about whether
    the number is a lucky draw. This re-prices the same threshold from a different
    seed. Where the shapes are opposite (`ends_below` against `ends_above` at the
    same threshold) the two are complements and should sum to 1, so the residual
    is measured against 1 rather than against 0.

    A residual inside two combined Monte Carlo standard errors says the pinned
    probability is a property of the series, not of seed 6001. A residual outside
    it would say the path count is too low for the third decimal the ledger
    commits, which would be a defect in the block rather than in this module.
    """
    ladder = {(f.shape, f.threshold): f for f in forecasts}
    out: list[SeedAgreement] = []
    for chk in checks:
        same = ladder.get((chk.shape, chk.threshold))
        flipped = ladder.get(("ends_above" if chk.shape == "ends_below" else "ends_below",
                              chk.threshold))
        pair = same or flipped
        if pair is None:
            continue
        complement = pair is flipped
        residual = ((1.0 - (chk.pinned_probability + pair.probability))
                    if complement else (pair.probability - chk.pinned_probability))
        se = math.hypot(_stderr(chk.pinned_probability, n_paths), pair.monte_carlo_stderr)
        out.append(SeedAgreement(
            pin_id=chk.pin_id,
            threshold=chk.threshold,
            pinned_shape=chk.shape,
            ladder_shape=pair.shape,
            pinned_probability=chk.pinned_probability,
            ladder_probability=pair.probability,
            comparison="complement" if complement else "same shape",
            residual=residual,
            combined_stderr=se,
            within_two_stderr=abs(residual) <= 2.0 * se,
        ))
    return out


# --- report -------------------------------------------------------------------

def report(
    rows: Sequence[Mapping[str, Any]] | None = None,
    packets_dir: Path | str | None = None,
    *,
    as_of: dt.date = DEFAULT_AS_OF,
) -> dict[str, Any]:
    """Everything above in one dict. The doc quotes this and nothing else."""
    rows = list(rows) if rows is not None else of.load_rows()
    resolved = find_packets_dir(packets_dir)
    packets = read_packets(resolved, through=last_data_day(rows)) if resolved else None
    checks = block6_consistency(rows)
    forecasts = occupancy_threshold_forecasts(rows, as_of=as_of)
    return {
        "as_of": as_of.isoformat(),
        "packets_dir": str(resolved) if resolved else None,
        "packets_scanned": len(packets) if packets else 0,
        "availability": audit_isolation_metrics(rows, packets, as_of=as_of),
        "trajectory": occupancy_trajectory(rows, as_of=as_of),
        "coverage_churn": coverage_churn(rows),
        "pressure": province_pressure(rows),
        "headroom": headroom(rows, packets),
        "turnover": turnover(rows, packets),
        "forecasts": forecasts,
        "block6": checks,
        "seed_agreement": seed_agreement(checks, forecasts),
    }


def _fmt(value: Any, spec: str = ".2f") -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return format(value, spec)
    return str(value)


def main() -> None:  # pragma: no cover - presentation only
    r = report()
    print(f"AS OF {r['as_of']}   packets scanned: {r['packets_scanned']}   dir: {r['packets_dir']}")

    print("\n== 1. ISOLATION METRIC AVAILABILITY ==")
    print(f"{'metric':28} {'source':16} {'pkt days':>9} {'ext days':>9} {'first':11} {'last':11} {'stale':>6}")
    for m in r["availability"]:
        print(f"{m.metric:28} {m.source:16} {m.packet_days_carrying:>4}/{m.packets_scanned:<4} "
              f"{m.extract_days_carrying:>9} {m.first_day or '-':11} {m.last_day or '-':11} "
              f"{_fmt(m.days_stale_at_as_of):>5}d")
    for m in r["availability"]:
        if m.aliases_seen:
            print(f"    {m.metric} aliases: {', '.join(m.aliases_seen)}")

    t = r["trajectory"]
    print("\n== 2. NATIONAL OCCUPANCY TRAJECTORY (DERIVED) ==")
    print(f"  {t.n_observations} obs {t.first_day} ({t.first_value:.0f}) -> {t.last_day} ({t.last_value:.0f}); "
          f"min {t.minimum:.0f} max {t.maximum:.0f}; at all-time high: {t.at_all_time_high}")
    print(f"  moves: {t.rises} up / {t.falls} down / {t.flats} flat -> monotone non-decreasing: {t.monotone_nondecreasing}")
    print(f"  mean {t.mean_change_per_day:+.2f}/day, median {t.median_change_per_day:+.2f}/day, "
          f"OLS {_fmt(t.ols_slope_per_day, '+.2f')}/day")
    print(f"  largest single rise {t.largest_single_rise:+.0f} on {t.largest_single_rise_day}, "
          f"largest single fall {t.largest_single_fall:+.0f} on {t.largest_single_fall_day}")
    print(f"  occupancy doubling time {_fmt(t.occupancy_doubling_days, '.1f')} days "
          f"(BEDS, not incidence); stale {t.days_stale_at_as_of}d at as-of")

    c = r["coverage_churn"]
    print("\n== 3. COVERAGE CHURN (DERIVED) ==")
    print(f"  {c.comparable_pairs} comparable pairs over {c.panel_days} panel days; "
          f"{c.days_with_membership_change} days changed province membership")
    print(f"  raw sd {c.raw_sd:.1f} -> constant-panel sd {c.constant_panel_sd:.1f}; "
          f"mean {c.raw_mean:+.1f} -> {c.constant_panel_mean:+.1f}")
    print(f"  variance share attributable to reporting churn: {c.variance_share_from_churn:.1%}")
    if c.largest_churn_artifact:
        d, raw, held, moved = c.largest_churn_artifact
        print(f"  worst artifact {d}: raw {raw:+.0f} vs constant-panel {held:+.0f} (moved: {', '.join(moved)})")

    print("\n== 4. PROVINCE ISOLATION PRESSURE (DERIVED) ==")
    print(f"{'province':11} {'occ':>6} {'active':>7} {'occ/act':>8} {'slope/d':>8} {'per100act':>10} {'n':>3}  flag")
    for p in r["pressure"]:
        print(f"{p.province:11} {p.occupancy:6.0f} {p.active_confirmed:7.0f} "
              f"{_fmt(p.occupancy_per_active_confirmed):>8} {_fmt(p.slope_per_day, '+.2f'):>8} "
              f"{_fmt(p.slope_per_100_active, '+.3f'):>10} {p.points_in_window:>3}  {p.flag}")

    h = r["headroom"]
    print("\n== 5. HEADROOM (UNAVAILABLE) ==")
    print(f"  computable: {h.computable} -- {h.reason}")
    print(f"  structured bed-capacity observations found: {h.capacity_observations_found} "
          f"(days: {', '.join(h.capacity_days) or 'none'})")
    for need in h.what_would_be_needed:
        print(f"    needed: {need}")
    print(f"  closest available: {h.closest_available_reading}")

    tv = r["turnover"]
    print("\n== 6. TURNOVER ==")
    print(f"  net ward flow (identity, OBSERVED): {tv.net_flow_days} consecutive-day pairs, "
          f"mean {tv.net_flow_mean:+.1f}/day, sd {tv.net_flow_sd:.1f}")
    print(f"  implied admissions (DERIVED): {tv.implied_admission_days} days, "
          f"mean {_fmt(tv.implied_admissions_mean, '.1f')}/day")
    print(f"  {tv.verdict}")

    print("\n== 7. OCCUPANCY THRESHOLD FORECASTS (FORECAST) ==")
    print(f"{'shape':11} {'thr':>6} {'p':>7} {'mc_se':>7}  question horizon {DEFAULT_HORIZON_DAYS}d from last data day")
    for f in r["forecasts"]:
        print(f"{f.shape:11} {f.threshold:6.0f} {f.probability:7.4f} {f.monte_carlo_stderr:7.4f}  "
              f"seed {f.seed}")
    f0 = r["forecasts"][0]
    print(f"  paths walk from {f0.last_observed_day} ({f0.last_observed_value:.0f}); "
          f"as-of {f0.as_of} is {f0.days_between_last_data_and_as_of} days later")

    print("\n== 8. BLOCK 6 CONSISTENCY CHECK ==")
    for chk in r["block6"]:
        print(f"  {chk.pin_id.split(':')[-1]:16} {chk.shape:11} {chk.threshold:6.0f} seed {chk.seed} "
              f"pinned {chk.pinned_probability:.4f} reproduced {chk.reproduced_probability:.4f} "
              f"delta {chk.delta:+.4f}  {'MATCH' if chk.matches else 'MISMATCH -- DEFECT'}")
    print("  independent-seed re-price (is the pinned number a seed artifact?):")
    for a in r["seed_agreement"]:
        print(f"    {a.pin_id.split(':')[-1]:16} pinned {a.pinned_probability:.4f} ({a.pinned_shape}) vs "
              f"ladder {a.ladder_probability:.4f} ({a.ladder_shape}, {a.comparison}) -> "
              f"residual {a.residual:+.4f}, 2se {2 * a.combined_stderr:.4f}  "
              f"{'AGREES' if a.within_two_stderr else 'OUTSIDE MC NOISE'}")


if __name__ == "__main__":  # pragma: no cover
    main()
