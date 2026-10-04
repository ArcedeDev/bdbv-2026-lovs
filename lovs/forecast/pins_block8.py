# SPDX-License-Identifier: Apache-2.0
"""Calibration Blocks 8, 9 and 10, generated from the frozen 2026-10-01 extract.

Block 8 (trajectory) and Block 9 (response system) are a paired test of the
operational forecaster. Every question is priced twice, at identical thresholds:
once by the incumbent level bootstrap (day-over-day changes resampled from the whole
history) and once by a challenger. Cumulative series get a drift challenger that
resamples only the last 28 days of changes; fluctuating series get a levels challenger
that assumes the spread of levels seen in the last 21 days persists. The windows were
chosen on the walk-forward backtest in lovs/forecast/backtest.py before anything here
was generated, one window per class, not per metric.

Thresholds sit at the 0.1/0.3/0.5/0.7/0.9 quantiles of a 50/50 mixture of the two
methods' end values, so neither method sets the questions, then snap to a readable
per-metric step. Every probability for one metric and method is read off one seeded
set of 20,000 end values, so a method's prices rise with the threshold by construction.

Block 10 (low counts and feed) carries Block 7's other two methods forward, priced by
the forecaster of record alone: a low-count ladder on daily new confirmations from
the termination bootstrap, and two feed-continuity questions from the gap bootstrap.
Its questions read only data days from `WINDOW_OPENS`, the UTC day after the
registration deadline, so no day before the registration was public counts.

Nothing here is authored. A test regenerates every pin and fails on any difference.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from lovs.forecast import backtest as bt
from lovs.forecast import cadenceintegrity as ci
from lovs.forecast import opsforecast as of
from lovs.forecast import pins_block7 as p7

REPO = Path(__file__).resolve().parents[2]
LEDGER = REPO / "data" / "operational-calibration-ledger.json"
SUBSTRATE = REPO / "data" / "operational-series-2026-10-01.json"
SUBSTRATE_RELATIVE = "data/operational-series-2026-10-01.json"
SUBSTRATE_SHA256 = "3874d21a899007e6b6ef5f694514eb630f1ad57156977ab98290c85145903448"
SOURCE_CUTOFF = {"sitrep": "140", "data_as_of": "2026-10-01", "published_at": "2026-10-02"}

OUTBREAK = "bdbv-uga-cod-2026"
PINNED_AT = "2026-10-04"
REGISTRATION_DEADLINE_UTC = "2026-10-05T23:59:59Z"
WINDOW_OPENS = "2026-10-06"
RESOLVES_AT = "2026-11-01T23:59:59Z"
WINDOW_END = dt.date(2026, 11, 1)
HORIZON_DAYS = 31
LEVELS = (0.1, 0.3, 0.5, 0.7, 0.9)
PATHS = of.DEFAULT_PATHS
BLOCK = of.DEFAULT_BLOCK
DRIFT_DAYS = 28
LEVELS_DAYS = 21
FEED_REFERENCE_DAYS = 60
LOW_COUNT_RUNGS = (0, 15, 30, 45, 60)

# Seeds earlier blocks use. A derived seed landing on one of these is refused rather
# than silently shared, so no two pinned blocks can draw the same random stream.
RESERVED_SEEDS = frozenset(
    set(range(6001, 6013)) | {6042} | set(range(7001, 7015)) | set(range(7100, 7110)) | {20260901}
)

BLOCK8_ID = f"operational-block:{OUTBREAK}:{PINNED_AT}:trajectory"
BLOCK9_ID = f"operational-block:{OUTBREAK}:{PINNED_AT}:response"
BLOCK10_ID = f"operational-block:{OUTBREAK}:{PINNED_AT}:lowcount-feed"

INCUMBENT = "level_bootstrap"
DRIFT = "drift_bootstrap_28d"
RECENT_LEVELS = "recent_levels_21d"
METHOD_TAG = {INCUMBENT: "inc", DRIFT: "drift28", RECENT_LEVELS: "levels21"}


class RegistrationError(RuntimeError):
    """The inputs or timing do not support a registration; generate nothing."""


@dataclass(frozen=True, slots=True)
class Metric:
    key: str
    series: str
    question: str          # "{t}" is replaced by the threshold
    step: int
    kind: str              # cumulative | percent | count
    derived: bool = False

    def observations(self, rows) -> list[of.Observation]:
        if self.series == "publication_lag":
            return p7.publication_lag_series(rows)
        if self.series == "nordkivu_isolation":
            return p7.province_isolation_series(rows, "Nord-Kivu")
        return of.series(rows, self.series)

    def resolution_rule(self) -> dict:
        if self.derived:
            return {"rule": "derived_series", "builder": self.series}
        return {"rule": "extract_column", "column": self.series}

    def bounds(self, obs: Sequence[of.Observation]) -> tuple[float | None, float | None]:
        if self.kind == "cumulative":
            # A running total never falls; the floor is its last reported value.
            return obs[-1].value, None
        if self.kind == "percent":
            return 0.0, 100.0
        return 0.0, None


_LAST = "On the last SitRep data day on or before 2026-11-01"
BLOCK8_METRICS: tuple[Metric, ...] = (
    Metric("confirmed", "confirmed_total",
           f"{_LAST}, is the cumulative count of confirmed cases at or below {{t}}?", 50, "cumulative"),
    Metric("deaths", "confirmed_deaths_total",
           f"{_LAST}, is the cumulative count of confirmed deaths at or below {{t}}?", 25, "cumulative"),
    Metric("recovered", "cumulative_recovered",
           f"{_LAST}, is cumulative recovered at or below {{t}}?", 50, "cumulative"),
    Metric("zones", "health_zones_touched",
           f"{_LAST}, is the cumulative count of affected health zones at or below {{t}}?", 1, "cumulative"),
)
BLOCK9_METRICS: tuple[Metric, ...] = (
    Metric("isolation", "hospital_isolation_total",
           f"{_LAST}, is national hospital isolation occupancy at or below {{t}}?", 25, "count"),
    Metric("followup", "contact_followup_percent",
           f"{_LAST}, is the national contact follow-up rate at or below {{t}} percent?", 1, "percent"),
    Metric("positivity", "lab_positivity_percent",
           f"{_LAST}, is national lab positivity at or below {{t}} percent?", 1, "percent"),
    Metric("samples", "samples_analyzed",
           f"{_LAST}, were {{t}} or fewer samples analysed that day?", 25, "count"),
    Metric("alerts", "alerts_reported",
           f"{_LAST}, are alerts reported in the preceding 24 hours at or below {{t}}?", 100, "count"),
    Metric("newcases", "new_confirmed_today",
           f"{_LAST}, are new confirmed cases for that day at or below {{t}}?", 5, "count"),
    Metric("newdeaths", "new_confirmed_deaths_today",
           f"{_LAST}, are new confirmed deaths for that day at or below {{t}}?", 5, "count"),
    Metric("lag", "publication_lag",
           "Is the publication lag of the last SitRep on or before 2026-11-01 at or below {t} days?",
           1, "count", derived=True),
    Metric("nkisolation", "nordkivu_isolation",
           f"{_LAST}, is the Nord-Kivu isolation census at or below {{t}}?", 10, "count", derived=True),
)


def derived_seed(*parts: str) -> int:
    seed = int(hashlib.sha256("|".join((OUTBREAK, *parts)).encode()).hexdigest()[:8], 16)
    if seed in RESERVED_SEEDS:
        raise RegistrationError(f"derived seed {seed} for {parts} collides with an earlier block")
    return seed


def snap(value: float, step: int) -> int:
    """Nearest multiple of `step`, halves rounded up, so a threshold reads cleanly."""
    return int(math.floor(value / step + 0.5) * step)


def share_at_or_below(values: Sequence[float], threshold: float) -> float:
    return round(sum(1 for v in values if v <= threshold) / len(values), 4)


def load_substrate(path: Path = SUBSTRATE) -> list[dict]:
    """The frozen extract, refused unless it is byte-for-byte the registered one."""
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != SUBSTRATE_SHA256:
        raise RegistrationError(f"{path} sha256 {digest} is not the registered {SUBSTRATE_SHA256}")
    rows = json.loads(raw)["rows"]
    check_cutoff(rows)
    return rows


def check_cutoff(rows: Sequence[dict]) -> None:
    """Refuse any input row newer than the registered source cutoff."""
    newest_data = max(str(r.get("data_as_of", ""))[:10] for r in rows)
    newest_published = max(str(r.get("published_at", ""))[:10] for r in rows)
    if newest_data > SOURCE_CUTOFF["data_as_of"] or newest_published > SOURCE_CUTOFF["published_at"]:
        raise RegistrationError(
            f"substrate carries data {newest_data} / publication {newest_published}, past the "
            f"registered cutoff {SOURCE_CUTOFF}")


def check_timing() -> None:
    """The window must open after the registration deadline, which follows the cutoff."""
    deadline = dt.datetime.fromisoformat(REGISTRATION_DEADLINE_UTC.replace("Z", "+00:00"))
    opens = dt.date.fromisoformat(WINDOW_OPENS)
    if opens != deadline.date() + dt.timedelta(days=1):
        raise RegistrationError(f"window_opens {opens} is not the UTC day after {REGISTRATION_DEADLINE_UTC}")
    if dt.date.fromisoformat(PINNED_AT) > deadline.date():
        raise RegistrationError(f"pinned_at {PINNED_AT} is after the deadline {REGISTRATION_DEADLINE_UTC}")
    if dt.date.fromisoformat(SOURCE_CUTOFF["published_at"]) >= dt.date.fromisoformat(PINNED_AT):
        raise RegistrationError("the substrate's last publication is not before the pin date")


# --- Blocks 8 and 9: paired questions ----------------------------------------

def _end_values(metric: Metric, method: str, obs, seed: int) -> list[float]:
    lo, hi = metric.bounds(obs)
    if method == INCUMBENT:
        return of.end_values(obs, HORIZON_DAYS, seed=seed, n_paths=PATHS, block=BLOCK, lo=lo, hi=hi)
    if method == DRIFT:
        return of.end_values(obs, HORIZON_DAYS, seed=seed, n_paths=PATHS, block=BLOCK, lo=lo, hi=hi,
                             recent_days=DRIFT_DAYS)
    if method == RECENT_LEVELS:
        return of.recent_level_ends(obs, recent_days=LEVELS_DAYS, seed=seed, n_paths=PATHS)
    raise ValueError(method)


def _method_note(method: str, metric: Metric, obs) -> dict:
    lo, hi = metric.bounds(obs)
    if method == RECENT_LEVELS:
        return {"method": f"end values drawn uniformly from the levels in the last {LEVELS_DAYS} days, no drift",
                "window_days": LEVELS_DAYS}
    window = DRIFT_DAYS if method == DRIFT else None
    return {"method": ("stationary block bootstrap over day-over-day changes"
                       + (f" from the last {window} days" if window else " from the whole history")),
            "window_days": window, "lo": lo, "hi": hi}


def paired_metric_pins(metric: Metric, challenger: str, block_key: str, rows) -> tuple[list[dict], list[dict]]:
    """Both methods' pins for one metric, and any questions dropped as degenerate."""
    obs = metric.observations(rows)
    methods = (INCUMBENT, challenger)
    seeds = {m: derived_seed(block_key, metric.key, m) for m in methods}
    ends = {m: _end_values(metric, m, obs, seeds[m]) for m in methods}
    mix = sorted(ends[INCUMBENT] + ends[challenger])
    thresholds: dict[int, list[float]] = {}
    for q in LEVELS:
        thresholds.setdefault(snap(bt.quantile(mix, q), metric.step), []).append(q)

    pins, dropped = [], []
    for t, levels in thresholds.items():
        prices = {m: share_at_or_below(ends[m], t) for m in methods}
        if len(set(prices.values())) == 1 and prices[INCUMBENT] in (0.0, 1.0):
            # Both methods certain the same way: not a forecast, so not a question.
            dropped.append({"metric": metric.series, "threshold": t, "quantile_levels": levels,
                            "both_methods_priced": prices[INCUMBENT]})
            continue
        qkey = f"{metric.key}-le-{t}"
        for m in methods:
            pins.append({
                "pin_id": f"operational-pin:{OUTBREAK}:31d:{qkey}-{METHOD_TAG[m]}",
                "question_id": f"{block_key}:{qkey}",
                "method": m,
                "role": "incumbent" if m == INCUMBENT else "challenger",
                "metric": metric.series,
                "shape": "ends_below",
                "threshold": t,
                "quantile_levels": levels,
                "public_question": metric.question.format(t=t),
                "rationale": (
                    f"Paired question {qkey}: priced by the incumbent and by the "
                    f"{'28-day drift' if challenger == DRIFT else '21-day levels'} challenger at "
                    f"the same threshold, set at mixture quantile(s) {', '.join(f'{x:g}' for x in levels)} "
                    f"and snapped to a step of {metric.step}."),
                "bias_test": False,
                "probability": prices[m],
                "horizon_days": HORIZON_DAYS,
                "observations_at_pin": len(obs),
                "last_observed_value": obs[-1].value,
                "last_observed_date": obs[-1].date.isoformat(),
                "generator": {"seed": seeds[m], "paths": PATHS, "block": BLOCK, **_method_note(m, metric, obs)},
                "resolution_rule": metric.resolution_rule(),
            })
    return pins, dropped


# --- Block 10: low counts and feed --------------------------------------------

def _in_window_start(last: dt.date) -> int:
    """Path index of WINDOW_OPENS; index 0 is the day after the last observation."""
    return (dt.date.fromisoformat(WINDOW_OPENS) - last).days - 1


def low_count_prices(rows) -> tuple[dict[int, float], dict]:
    obs = of.series(rows, "new_confirmed_today")
    seed = derived_seed("lowcount", "new_confirmed_today", "termination_bootstrap")
    # The termination bootstrap's reflecting variant: the incumbent walk floored at zero
    # (lovs/forecast/termination.py forecast_horizon), read for its lowest in-window day.
    paths = of._paths(obs, HORIZON_DAYS, PATHS, BLOCK, seed, 0.0, None)
    start = _in_window_start(obs[-1].date)
    lows = sorted(min(path[start:]) for path in paths)
    prices = {k: share_at_or_below(lows, k) for k in LOW_COUNT_RUNGS}
    meta = {"seed": seed, "paths": PATHS, "block": BLOCK, "obs": obs,
            "median_low": lows[len(lows) // 2]}
    return prices, meta


def feed_prices(rows) -> tuple[dict[str, float], dict]:
    delivered, _ = ci.packets(rows)
    days = sorted({p.data_day for p in delivered})
    last = days[-1]
    reference = [d for d in days if (last - d).days < FEED_REFERENCE_DAYS]
    gaps = ci._gap_series(reference)
    seed = derived_seed("feed", "sitrep_arrivals", "gap_bootstrap")
    opens = dt.date.fromisoformat(WINDOW_OPENS)
    width = (WINDOW_END - opens).days + 1
    no_gap = enough = 0
    for offsets, _ in ci._arrival_offsets(gaps, HORIZON_DAYS, PATHS, BLOCK, seed):
        inside = [last + dt.timedelta(days=o) for o in offsets]
        inside = [d for d in inside if opens <= d <= WINDOW_END]
        steps = [(b - a).days for a, b in zip(inside, inside[1:])]
        no_gap += bool(steps) and max(steps) <= 1
        enough += len(inside) >= width - 1
    meta = {"seed": seed, "paths": PATHS, "block": BLOCK, "gaps": gaps, "width": width,
            "recent_arrivals": len([d for d in days if (last - d).days < 30]), "max_gap": max(gaps)}
    return {"no_gap": round(no_gap / PATHS, 4), "arrivals": round(enough / PATHS, 4)}, meta


def block10_pins(rows) -> list[dict]:
    prices, low = low_count_prices(rows)
    obs = low["obs"]
    span = f"from {WINDOW_OPENS} to 2026-11-01"
    pins = []
    for k in LOW_COUNT_RUNGS:
        count = "zero new confirmed cases" if k == 0 else f"{k} or fewer new confirmed cases"
        pins.append({
            "pin_id": f"operational-pin:{OUTBREAK}:31d:newcases-any-le-{k}",
            "question_id": f"block10:newcases-any-le-{k}",
            "method": "termination_bootstrap",
            "role": "record",
            "metric": "new_confirmed_today",
            "shape": "derived",
            "threshold": k,
            "public_question": f"Does any SitRep data day {span} report {count}?",
            "rationale": (
                "Low-count ladder, one rung of five on the lowest in-window daily count. A low "
                "or zero report is a reporting outcome (a laboratory or reporting interruption "
                "can produce one), not evidence the outbreak is ending."),
            "bias_test": False,
            "probability": prices[k],
            "horizon_days": HORIZON_DAYS,
            "observations_at_pin": len(obs),
            "last_observed_value": obs[-1].value,
            "last_observed_date": obs[-1].date.isoformat(),
            "generator": {"seed": low["seed"], "paths": PATHS, "block": BLOCK,
                          "method": ("termination bootstrap, reflecting floor at zero: whole-history "
                                     "day-over-day changes, lowest value over in-window days")},
            "resolution_rule": {"rule": "any_day_at_or_below", "metric": "new_confirmed_today", "value": k},
        })

    feed, f = feed_prices(rows)
    method = (f"stationary block bootstrap over {len(f['gaps'])} inter-arrival gaps from the last "
              f"{FEED_REFERENCE_DAYS} days; it cannot draw a silence longer than {f['max_gap']} days")
    for key, question, rule, p in (
        ("feed-no-gap-over-1d",
         f"{span[0].upper()}{span[1:]}, does every pair of consecutive SitRep data days sit at most one day apart?",
         {"rule": "no_silence_longer_than", "days": 1}, feed["no_gap"]),
        (f"feed-arrivals-ge-{f['width'] - 1}",
         f"Do at least {f['width'] - 1} SitRep data days fall {span}?",
         {"rule": "arrivals_at_least", "count": f["width"] - 1}, feed["arrivals"]),
    ):
        pins.append({
            "pin_id": f"operational-pin:{OUTBREAK}:31d:{key}",
            "question_id": f"block10:{key}",
            "method": "gap_bootstrap",
            "role": "record",
            "metric": "sitrep_arrivals",
            "shape": "derived",
            "threshold": None,
            "public_question": question,
            "rationale": (
                "Feed continuity. The gap bootstrap's support is the gaps of the last "
                f"{FEED_REFERENCE_DAYS} days, none longer than {f['max_gap']} days, so these are the "
                "only two feed questions it does not price at certainty. It cannot price the feed "
                "stopping, which is the failure that matters most."),
            "bias_test": False,
            "probability": p,
            "horizon_days": HORIZON_DAYS,
            "observations_at_pin": len(f["gaps"]),
            "last_observed_value": float(f["recent_arrivals"]),
            "last_observed_date": obs[-1].date.isoformat(),
            "generator": {"seed": f["seed"], "paths": PATHS, "block": BLOCK, "method": method},
            "resolution_rule": rule,
        })
    return pins


# --- blocks ---------------------------------------------------------------------

def _registration(block_key: str) -> dict:
    return {
        "source_cutoff": dict(SOURCE_CUTOFF),
        "inputs": [{"path": SUBSTRATE_RELATIVE, "sha256": SUBSTRATE_SHA256}],
        "registration_deadline_utc": REGISTRATION_DEADLINE_UTC,
        "window_opens": WINDOW_OPENS,
        "public_registration": (
            "Public when the pinning commit first reaches the public repository. That UTC time "
            "and commit are appended afterwards as registration_receipt; a receipt later than "
            "registration_deadline_utc voids the block, which would be regenerated with a new "
            "deadline before publication rather than edited."),
        "prospective_horizon": (
            "Priced from data through 2026-10-01. End-of-window questions read the last data day "
            "on or before 2026-11-01, so their real prospective horizon runs from the registration "
            "receipt to 2026-11-01. Path, event and feed questions read only data days from "
            f"{WINDOW_OPENS}. No day between the substrate cutoff and registration is claimed."),
        "designer_exposure": [
            "Primary design session: newest figures seen were SitRep 140 (data 2026-10-01). It saw "
            "the SitRep 141 file name and upload time in the publisher's media listing at "
            "2026-10-04T09:58Z and never opened it.",
            "Code inventory session: read code and data at the base commit, newest SitRep 140; set no "
            "design parameter.",
            "Founder: chose which question families run; set no threshold, probability, window "
            "length, rung or method parameter.",
        ],
    }


def _paired_pre_registration(challenger: str, n_metrics: int, need: int) -> dict:
    floor = 2 / 2 ** n_metrics
    return {
        "comparison": {
            "incumbent": "level_bootstrap: whole-history day-over-day changes (Blocks 6 and 7)",
            "challenger": challenger,
            "pairing": "every question is priced by both methods at the identical threshold",
        },
        "unit_of_inference": (
            "the metric. A metric's threshold questions are a ladder on one realised value, not "
            "independent samples."),
        "primary_endpoint": (
            "mean over metrics of each metric's mean threshold-Brier difference, challenger minus "
            "incumbent, over every question this block pins for that metric"),
        "decision_rule": (
            f"adopt the challenger as the provisional forecaster of record for this series class in "
            f"the next block if the primary endpoint is below zero AND the challenger's difference is "
            f"below zero on at least {need} of the {n_metrics} metrics; otherwise keep the incumbent. "
            "Adoption is provisional: the displaced method keeps being priced beside it, and "
            "permanence needs a second prospective block that agrees. Pins registered here keep "
            "their roles permanently."),
        "descriptive_statistics": [
            (f"exact sign-flip p-value over metrics (lovs/forecast_scoring.sign_flip_p_value), "
             f"reported with its smallest attainable two-sided value ({floor:.4g} for {n_metrics} "
             "metrics) and no significance claim: national series move together, so their signs "
             "are not exchangeable"),
            "PIT position of each realised value among a metric's thresholds, per method",
            "hit rate of pins priced at or below 0.20, per method",
            ("the pooled-record recalibration map applied to incumbent prices, paired against raw "
             "(never a published price)"),
            "Brier against the pooled base rate",
        ],
        "unscoreable_policy": (
            "a metric with no covering observation is reported unscoreable and leaves the "
            "endpoint; nothing is imputed"),
        "backtest": (
            "docs/operational-forecaster-validation.md, Blocks 8 and 9 section: retrospective, "
            "publication-day truncated, on the corrected extract"),
    }


def build_blocks(rows=None) -> list[dict]:
    """The three blocks, deterministically. Raises before generating anything doubtful."""
    check_timing()
    if rows is None:
        rows = load_substrate()
    else:
        check_cutoff(rows)

    b8_pins, b8_dropped = [], []
    for metric in BLOCK8_METRICS:
        pins, dropped = paired_metric_pins(metric, DRIFT, "block8", rows)
        b8_pins += pins
        b8_dropped += dropped
    b9_pins, b9_dropped = [], []
    for metric in BLOCK9_METRICS:
        pins, dropped = paired_metric_pins(metric, RECENT_LEVELS, "block9", rows)
        b9_pins += pins
        b9_dropped += dropped
    b10_pins = block10_pins(rows)
    _, low = low_count_prices(rows)

    common = {"pinned_at": PINNED_AT, "window_opens": WINDOW_OPENS, "resolves_at": RESOLVES_AT,
              "horizon_days": HORIZON_DAYS, "status": "active"}
    return [
        {
            "block_id": BLOCK8_ID, "label": "Block 8", **common,
            "rationale": (
                "Block 8, trajectory. Blocks 6 and 7 showed the level bootstrap extrapolating the "
                "spring's growth into a plateau (affected zones held at 63 against a 0.73 price for "
                "above 70) and lagging a steadily growing total (recovered reached 2,182 against a "
                "0.15 price for above 1,900). The cause is that it resamples day-over-day changes "
                "from the whole history. This block prices every cumulative-series question twice, "
                "with the incumbent and with the same bootstrap restricted to the last 28 days, so "
                "the forward window decides whether recent drift should replace whole-history drift. "
                "In the retrospective backtest the challenger beat the incumbent on all four on both "
                "CRPS and threshold Brier. "
                "Discriminating prediction: affected health zones, where the incumbent extrapolates "
                "growth and the challenger reads the plateau."),
            "registration_note": (
                "Generated by lovs/forecast/pins_block8.build_blocks() from "
                f"{SUBSTRATE_RELATIVE} (sha256 {SUBSTRATE_SHA256}); a test re-runs it and fails on "
                "any difference. Cumulative series are floored at their last value because a running "
                "total never falls."),
            "registration": _registration("block8"),
            "pre_registration": {**_paired_pre_registration(DRIFT, len(BLOCK8_METRICS), 3),
                                 "dropped_degenerate_questions": b8_dropped},
            "points": b8_pins,
        },
        {
            "block_id": BLOCK9_ID, "label": "Block 9", **common,
            "rationale": (
                "Block 9, response system. Nine fluctuating series: the six Block 6 tracked plus "
                "the three Block 7 structural series no other block carries (daily confirmed "
                "deaths, publication lag, Nord-Kivu isolation). A random walk with drift is the "
                "wrong family for a series that rises and falls around a level, so each question is "
                "priced by the incumbent and by a challenger that assumes the spread of the last 21 "
                "days persists. The retrospective backtest (publication-day cut) is split: the "
                "challenger's whole-distribution score (CRPS) beat the incumbent on six of the nine, "
                "but its threshold Brier, this block's primary endpoint, beat it on only two, "
                "because a levels model prices any threshold outside its 21-day range at certainty. "
                "This is an open test that leans to the incumbent, not a confirmation."),
            "registration_note": (
                "Generated by lovs/forecast/pins_block8.build_blocks(). Publication lag and Nord-Kivu "
                "isolation are derived series built by lovs/forecast/pins_block7, which the resolver "
                "imports, so generator and resolver cannot drift apart. The national isolation series "
                "carries provincial panel churn (2026-09-01 erratum); both methods face it."),
            "registration": _registration("block9"),
            "pre_registration": {**_paired_pre_registration(RECENT_LEVELS, len(BLOCK9_METRICS), 6),
                                 "dropped_degenerate_questions": b9_dropped},
            "points": b9_pins,
        },
        {
            "block_id": BLOCK10_ID, "label": "Block 10", **common,
            "rationale": (
                "Block 10, low counts and feed. It carries Block 7's other two methods forward, "
                "priced by the forecaster of record alone. The low-count ladder asks whether any "
                f"data day from {WINDOW_OPENS} reports at most 0, 15, 30, 45 or 60 new confirmed "
                "cases. The two feed questions ask whether the SitRep series keeps its daily rhythm. "
                "A low or zero report is a reporting outcome, not evidence the outbreak is ending."),
            "registration_note": (
                "Generated by lovs/forecast/pins_block8.build_blocks(). The termination bootstrap is "
                "the incumbent walk floored at zero; the gap bootstrap is the cadence module's arrival "
                "walk, read for in-window data days exactly as the resolver reads them."),
            "registration": _registration("block10"),
            "pre_registration": {
                "hypothesis": (
                    "The termination bootstrap is a random walk on a daily count that has stayed "
                    "between 40 and 99 for a month, so it overprices low days. Prediction: fewer "
                    "low-count rungs come true than their prices imply, and the lowest in-window "
                    f"daily count stays above the bootstrap's median lowest day ({low['median_low']:g})."),
                "falsified_if": (
                    f"the lowest new-confirmed count on any data day from {WINDOW_OPENS} to "
                    f"2026-11-01 is at or below {low['median_low']:g}"),
                "unit_of_inference": (
                    "one realised value (the lowest in-window daily count) for the five rungs, and one "
                    "realised series of arrival days for the two feed questions"),
                "structural_limit": (
                    "the gap bootstrap cannot draw a silence longer than any in its reference window, "
                    "so it cannot price the feed stopping"),
            },
            "points": b10_pins,
        },
    ]


def append_to_ledger(path: Path = LEDGER) -> list[str]:
    """Append Blocks 8 to 10. Refuses to touch a ledger that already holds any of them."""
    ledger = json.loads(path.read_text(encoding="utf-8"))
    blocks = build_blocks()
    existing = {b["block_id"] for b in ledger["blocks"]}
    clash = [b["block_id"] for b in blocks if b["block_id"] in existing]
    if clash:
        raise RegistrationError(f"ledger already holds {clash}; pinned blocks are never rewritten")
    ids = {p["pin_id"] for b in ledger["blocks"] for p in b["points"]}
    for block in blocks:
        for pin in block["points"]:
            if pin["pin_id"] in ids:
                raise RegistrationError(f"pin id {pin['pin_id']} already in the ledger")
            ids.add(pin["pin_id"])
    ledger["blocks"].extend(blocks)
    ledger["_meta"]["generators"].update({b["block_id"]: "lovs/forecast/pins_block8.py" for b in blocks})
    path.write_text(json.dumps(ledger, indent=1, ensure_ascii=True) + "\n", encoding="utf-8")
    return [b["block_id"] for b in blocks]


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--append", action="store_true", help="append the blocks to the ledger")
    args = parser.parse_args()
    if args.append:
        print("\n".join(append_to_ledger()))
    else:
        print(json.dumps(build_blocks(), indent=1))
