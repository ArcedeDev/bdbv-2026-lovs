# SPDX-License-Identifier: Apache-2.0
"""Calibration Blocks 14 and 15, generated from the frozen 2026-11-15 extract.

Block 14 (response system) is a second prospective test of Block 9's comparison. Every
question is priced twice, at identical thresholds, by Block 9's two methods in Block 9's
roles: the level bootstrap (day-over-day changes resampled from the whole history) is
the incumbent and the 21-day levels method is the challenger. Block 9 kept the
incumbent; its registered rule asks for no repeat, and this block is one by the
founder's choice. Thresholds, steps, quantile levels, path counts and the 31-day
horizon are Block 9's. It carries seven of Block 9's nine metrics: national hospital
isolation is withheld by the source from SitRep 179, so a question on it would not
resolve, and samples analysed is left out because its national count moves mostly with
what it includes. Several series changed basis inside the extract. Every question resolves on
the value this repository's reviewed record of the SitRep carries. The questions on lab
positivity, alerts and Nord-Kivu isolation name that basis in their text, and the
publication-lag question names the date it counts to.

Block 15 (low counts and feed) carries Block 10's seven questions forward unchanged,
priced by the forecaster of record alone: the low-count ladder on daily new
confirmations from the termination bootstrap, and the two feed-continuity questions
from the gap bootstrap. Its questions read only data days from `WINDOW_OPENS`.

Nothing here is authored. A test regenerates every pin and fails on any difference.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Sequence

from lovs.forecast import backtest as bt
from lovs.forecast import cadenceintegrity as ci
from lovs.forecast import opsforecast as of
from lovs.forecast import pins_block8 as p8

REPO = Path(__file__).resolve().parents[2]
LEDGER = REPO / "data" / "operational-calibration-ledger.json"
SUBSTRATE = REPO / "data" / "operational-series-2026-11-15.json"
SUBSTRATE_RELATIVE = "data/operational-series-2026-11-15.json"
SUBSTRATE_SHA256 = "e74fbc5231ee0e52b9410ec011c2d41614adc3832c27ae202b91bd9e5051fb53"
SOURCE_CUTOFF = {"sitrep": "185", "data_as_of": "2026-11-15", "published_at": "2026-11-17"}
# The Block 13 extract; every one of its rows is unchanged in this block's substrate.
PREVIOUS_SUBSTRATE = REPO / "data" / "operational-series-2026-11-07.json"

OUTBREAK = p8.OUTBREAK
PINNED_AT = "2026-11-18"
REGISTRATION_DEADLINE_UTC = "2026-11-18T23:59:59Z"
WINDOW_OPENS = "2026-11-19"
RESOLVES_AT = "2026-12-16T23:59:59Z"
WINDOW_END = dt.date(2026, 12, 16)
HORIZON_DAYS = p8.HORIZON_DAYS
LEVELS = p8.LEVELS
PATHS = p8.PATHS
BLOCK = p8.BLOCK
LEVELS_DAYS = p8.LEVELS_DAYS
FEED_REFERENCE_DAYS = p8.FEED_REFERENCE_DAYS
LOW_COUNT_RUNGS = p8.LOW_COUNT_RUNGS

BLOCK14_ID = f"operational-block:{OUTBREAK}:{PINNED_AT}:response"
BLOCK15_ID = f"operational-block:{OUTBREAK}:{PINNED_AT}:lowcount-feed"
BLOCK_IDS = (BLOCK14_ID, BLOCK15_ID)

INCUMBENT = p8.INCUMBENT
RECENT_LEVELS = p8.RECENT_LEVELS
METHOD_TAG = {INCUMBENT: "inc", RECENT_LEVELS: "levels21"}

# Block 9's rule: the challenger must be better on at least 6 of its 9 metrics, scaled to
# ceil(6 x k / 9) for k scoreable metrics. Applied unchanged to this block's metrics.
BLOCK9_NEED, BLOCK9_OF_METRICS = 6, 9


class RegistrationError(RuntimeError):
    """The inputs or timing do not support a registration; generate nothing."""


_LAST = f"On the last SitRep data day on or before {WINDOW_END.isoformat()} that reports it"
_B9 = {m.key: m for m in p8.BLOCK9_METRICS}

# Block 9 metrics left out, and why. Nothing else of Block 9 is dropped.
DROPPED_METRICS = {
    "isolation": (
        "hospital_isolation_total, the national care census, is withheld as a source conflict "
        "in every edition from SitRep 179 to SitRep 185 (the cover cannot be reconciled with "
        "the provincial censuses), so its last observation is 2026-11-08, a week before the "
        "cutoff. A question on it resolves only if the census is printed again, and the "
        "resolver scores it unscoreable otherwise. No province replaces it: Nord-Kivu is "
        "already a metric, and the Ituri census is missing at SitReps 181, 183 and 184."),
    "samples": (
        "samples_analyzed, the national count of samples analysed, moves mostly with what it "
        "includes rather than with laboratory work. It adds follow-up re-samples at SitReps 179 "
        "to 182 (at least 137, then 62, 44 and 103), adds samples awaiting analysis at SitReps "
        "183 and 184 (18 and 51; at SitRep 184 with Nord-Kivu's received count in place of its "
        "analysed count) and leaves out the 78 samples of Haut-Uele, Bas-Uele and Nord-Ubangi at "
        "SitRep 185. That swing, from about -78 to +137, is wider than the 125 separating its "
        "three central thresholds (450, 475 and 575). Lab positivity is computed on the same "
        "count, so carrying both would score one denominator twice. Its Block 9 difference was "
        "+0.0115, close to zero, so leaving it out favours neither method."),
}

# Questions whose resolving value changed basis inside the extract, or whose resolving value a
# reader of the PDF could take differently, name the value the resolver scores.
_RECORD = (
    "The value is the one in this repository's reviewed record of that SitRep "
    "(data/sitrep_promotions). A value the review withholds as a source conflict does not "
    "count; the question then reads the last edition with a data day from "
    f"{WINDOW_OPENS} to {WINDOW_END.isoformat()} whose record carries one.")

_QUESTION = {
    "positivity": (
        f"{_LAST}, is national lab positivity at or below {{t}} percent? {_RECORD} From "
        "SitRep 179 the national sample count the rate divides by includes follow-up re-samples."),
    "alerts": (
        f"{_LAST}, are alerts reported in the preceding 24 hours, as the printed national "
        "total of the provinces that report that day, at or below {t}?"),
    "nkisolation": (
        f"{_LAST}, is the total patient census the reviewed record carries for Nord-Kivu at or "
        "below {t}? When the Nord-Kivu table prints a total and a CTE/CT subtotal, the total "
        "counts, and a count printed separately is not added. Some editions count patients "
        f"cared for outside CTE/CT in the total, others leave them out or do not say. {_RECORD}"),
    "lag": (
        f"Is the publication lag of the last SitRep, by data day, on or before "
        f"{WINDOW_END.isoformat()} at or below {{t}} day(s), counted from its data day to the "
        "UTC date it was first posted on insp.cd (published_at in the reviewed record)?"),
}

# What each pin's rationale discloses about the series it is priced on.
BASIS_NOTES = {
    "followup": (
        "The national rate is over the provinces that report contacts that day, five or six "
        "at SitReps 176 to 185, and the reviewed records note that the cohort changes between "
        "editions."),
    "positivity": (
        "National denominators are withheld at SitReps 176 to 178. From SitRep 179 the cover's "
        "sample count includes follow-up re-samples (at least 137, 62, 44 and 103 at SitReps "
        "179 to 182, against 11 at SitRep 171), so the printed rate runs low against earlier "
        "editions; from SitRep 183 the printed denominator can also include samples awaiting "
        "analysis or leave provinces out. The question resolves on the rate the reviewed record "
        "carries, and both methods are priced on that series."),
    "alerts": (
        "The national total is the sum over the provinces that report that day. Haut-Uele is "
        "absent from the alert table at SitReps 181, 182 and 185, and the national totals "
        "leave it out."),
    "nkisolation": (
        "The printed Nord-Kivu census changes basis. The reviewed records show it including "
        "patients cared for outside CTE/CT at SitReps 165 to 173 and 176. From SitRep 178 the "
        "page reports those patients separately: SitRep 179 calls its figure a CTE/CT census, "
        "SitReps 183 and 184 state that they are not included, and SitRep 185 includes them "
        "again (343 in CTE/CT and 171 outside). The question resolves on the total the reviewed "
        "record carries, whatever its basis that day: 514 at SitRep 185, not the 343 in CTE/CT."),
    "lag": (
        "The lag counts from the data day to the UTC date of the first insp.cd post "
        "(published_at in the reviewed record), not to the publication date printed in the PDF. "
        "At SitReps 150 to 185 the two give different lags in 26 of 35 editions: by post, 1 day "
        "in 8, 2 days in 26 and 3 days in 1; by PDF date, 1 day in 34."),
}


def _metric(m: p8.Metric) -> p8.Metric:
    question = _QUESTION.get(m.key) or m.question.replace("2026-11-01", WINDOW_END.isoformat())
    return p8.Metric(m.key, m.series, question, m.step, m.kind, m.derived)


BLOCK14_METRICS: tuple[p8.Metric, ...] = tuple(
    _metric(m) for m in p8.BLOCK9_METRICS if m.key not in DROPPED_METRICS)


def _ledger_seeds(path: Path = LEDGER) -> frozenset[int]:
    """Every seed an existing pin of the operational ledger records, these blocks' aside."""
    ledger = json.loads(path.read_text(encoding="utf-8"))
    return frozenset(
        int(pin["generator"]["seed"]) for block in ledger["blocks"] if block["block_id"] not in BLOCK_IDS
        for pin in block["points"] if isinstance((pin.get("generator") or {}).get("seed"), int))


def derived_seed(*parts: str, taken: frozenset[int] = frozenset()) -> int:
    seed = int(hashlib.sha256("|".join((OUTBREAK, *parts)).encode()).hexdigest()[:8], 16)
    if seed in p8.RESERVED_SEEDS or seed in taken:
        raise RegistrationError(f"derived seed {seed} for {parts} collides with an earlier block")
    return seed


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
    """The window opens after the deadline, which follows the cutoff; the horizon is Block 9's."""
    deadline = dt.datetime.fromisoformat(REGISTRATION_DEADLINE_UTC.replace("Z", "+00:00"))
    opens = dt.date.fromisoformat(WINDOW_OPENS)
    if opens != deadline.date() + dt.timedelta(days=1):
        raise RegistrationError(f"window_opens {opens} is not the UTC day after {REGISTRATION_DEADLINE_UTC}")
    if dt.date.fromisoformat(PINNED_AT) > deadline.date():
        raise RegistrationError(f"pinned_at {PINNED_AT} is after the deadline {REGISTRATION_DEADLINE_UTC}")
    if dt.date.fromisoformat(SOURCE_CUTOFF["published_at"]) >= dt.date.fromisoformat(PINNED_AT):
        raise RegistrationError("the substrate's last publication is not before the pin date")
    if (WINDOW_END - dt.date.fromisoformat(SOURCE_CUTOFF["data_as_of"])).days != HORIZON_DAYS:
        raise RegistrationError("the window end is not HORIZON_DAYS after the substrate's last data day")
    if RESOLVES_AT[:10] != WINDOW_END.isoformat():
        raise RegistrationError("resolves_at and WINDOW_END disagree")


def _check_ends_on_cutoff(name: str, obs: Sequence[of.Observation]) -> None:
    # The horizon is counted from the cutoff; a series ending earlier would be priced for
    # a different window than its question reads.
    if not obs or obs[-1].date.isoformat() != SOURCE_CUTOFF["data_as_of"]:
        raise RegistrationError(f"{name} ends {obs[-1].date if obs else 'nowhere'}, not on the cutoff day")


# --- Block 14: paired questions -------------------------------------------------

def paired_metric_pins(metric: p8.Metric, rows, taken: frozenset[int]) -> tuple[list[dict], list[dict]]:
    """Both methods' pins for one metric, and any questions dropped as degenerate."""
    obs = metric.observations(rows)
    _check_ends_on_cutoff(metric.series, obs)
    methods = (INCUMBENT, RECENT_LEVELS)
    seeds = {m: derived_seed("block14", metric.key, m, taken=taken) for m in methods}
    ends = {m: p8._end_values(metric, m, obs, seeds[m]) for m in methods}
    mix = sorted(ends[INCUMBENT] + ends[RECENT_LEVELS])
    thresholds: dict[int, list[float]] = {}
    for q in LEVELS:
        thresholds.setdefault(p8.snap(bt.quantile(mix, q), metric.step), []).append(q)

    pins, dropped = [], []
    for t, levels in thresholds.items():
        prices = {m: p8.share_at_or_below(ends[m], t) for m in methods}
        if len(set(prices.values())) == 1 and prices[INCUMBENT] in (0.0, 1.0):
            # Both methods certain the same way: not a forecast, so not a question.
            dropped.append({"metric": metric.series, "threshold": t, "quantile_levels": levels,
                            "both_methods_priced": prices[INCUMBENT]})
            continue
        qkey = f"{metric.key}-le-{t}"
        note = BASIS_NOTES.get(metric.key)
        for m in methods:
            pins.append({
                "pin_id": f"operational-pin:{OUTBREAK}:{PINNED_AT}:31d:{qkey}-{METHOD_TAG[m]}",
                "question_id": f"block14:{qkey}",
                "method": m,
                "role": "incumbent" if m == INCUMBENT else "challenger",
                "metric": metric.series,
                "shape": "ends_below",
                "threshold": t,
                "quantile_levels": levels,
                "public_question": metric.question.format(t=t),
                "rationale": (
                    f"Paired question {qkey}: priced by the incumbent and by the 21-day levels "
                    f"challenger at the same threshold, set at mixture quantile(s) "
                    f"{', '.join(f'{x:g}' for x in levels)} and snapped to a step of {metric.step}, "
                    "as in Block 9." + (f" {note}" if note else "")),
                "bias_test": False,
                "probability": prices[m],
                "horizon_days": HORIZON_DAYS,
                "observations_at_pin": len(obs),
                "last_observed_value": obs[-1].value,
                "last_observed_date": obs[-1].date.isoformat(),
                "generator": {"seed": seeds[m], "paths": PATHS, "block": BLOCK, **p8._method_note(m, metric, obs)},
                "resolution_rule": metric.resolution_rule(),
            })
    return pins, dropped


# --- Block 15: low counts and feed ----------------------------------------------

def _in_window_start(last: dt.date) -> int:
    """Path index of WINDOW_OPENS; index 0 is the day after the last observation."""
    return (dt.date.fromisoformat(WINDOW_OPENS) - last).days - 1


def low_count_prices(rows, taken: frozenset[int]) -> tuple[dict[int, float], dict]:
    obs = of.series(rows, "new_confirmed_today")
    _check_ends_on_cutoff("new_confirmed_today", obs)
    seed = derived_seed("block15", "lowcount", "new_confirmed_today", "termination_bootstrap", taken=taken)
    # The termination bootstrap's reflecting variant, as in Block 10: the incumbent walk
    # floored at zero, read for its lowest in-window day.
    paths = of._paths(obs, HORIZON_DAYS, PATHS, BLOCK, seed, 0.0, None)
    start = _in_window_start(obs[-1].date)
    lows = sorted(min(path[start:]) for path in paths)
    prices = {k: p8.share_at_or_below(lows, k) for k in LOW_COUNT_RUNGS}
    month = [o.value for o in obs if (obs[-1].date - o.date).days < 30]
    meta = {"seed": seed, "obs": obs, "median_low": lows[len(lows) // 2],
            "month_low": min(month), "month_high": max(month)}
    return prices, meta


def feed_prices(rows, taken: frozenset[int]) -> tuple[dict[str, float], dict]:
    delivered, _ = ci.packets(rows)
    days = sorted({p.data_day for p in delivered})
    last = days[-1]
    reference = [d for d in days if (last - d).days < FEED_REFERENCE_DAYS]
    gaps = ci._gap_series(reference)
    seed = derived_seed("block15", "feed", "sitrep_arrivals", "gap_bootstrap", taken=taken)
    opens = dt.date.fromisoformat(WINDOW_OPENS)
    width = (WINDOW_END - opens).days + 1
    no_gap = enough = 0
    for offsets, _ in ci._arrival_offsets(gaps, HORIZON_DAYS, PATHS, BLOCK, seed):
        inside = [last + dt.timedelta(days=o) for o in offsets]
        inside = [d for d in inside if opens <= d <= WINDOW_END]
        steps = [(b - a).days for a, b in zip(inside, inside[1:])]
        no_gap += bool(steps) and max(steps) <= 1
        enough += len(inside) >= width - 1
    meta = {"seed": seed, "gaps": gaps, "width": width,
            "recent_arrivals": len([d for d in days if (last - d).days < 30]), "max_gap": max(gaps)}
    return {"no_gap": round(no_gap / PATHS, 4), "arrivals": round(enough / PATHS, 4)}, meta


def block15_pins(rows, taken: frozenset[int]) -> tuple[list[dict], dict]:
    prices, low = low_count_prices(rows, taken)
    obs = low["obs"]
    span = f"from {WINDOW_OPENS} to {WINDOW_END.isoformat()}"
    pins = []
    for k in LOW_COUNT_RUNGS:
        count = "zero new confirmed cases" if k == 0 else f"{k} or fewer new confirmed cases"
        pins.append({
            "pin_id": f"operational-pin:{OUTBREAK}:{PINNED_AT}:31d:newcases-any-le-{k}",
            "question_id": f"block15:newcases-any-le-{k}",
            "method": "termination_bootstrap",
            "role": "record",
            "metric": "new_confirmed_today",
            "shape": "derived",
            "threshold": k,
            "public_question": f"Does any SitRep data day {span} report {count}?",
            "rationale": (
                "Low-count ladder, one rung of five on the lowest in-window daily count, Block 10's "
                "rungs and method unchanged. A low or zero report is a reporting outcome (a "
                "laboratory or reporting interruption can produce one), not evidence the outbreak "
                "is ending."),
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

    feed, f = feed_prices(rows, taken)
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
            "pin_id": f"operational-pin:{OUTBREAK}:{PINNED_AT}:31d:{key}",
            "question_id": f"block15:{key}",
            "method": "gap_bootstrap",
            "role": "record",
            "metric": "sitrep_arrivals",
            "shape": "derived",
            "threshold": None,
            "public_question": question,
            "rationale": (
                "Feed continuity, Block 10's two questions unchanged. The gap bootstrap's support "
                f"is the gaps of the last {FEED_REFERENCE_DAYS} days, none longer than "
                f"{f['max_gap']} days; SitRep 172 (data 2026-11-02) is absent from the extract. "
                "It cannot price the feed stopping, which is the failure that matters most."),
            "bias_test": False,
            "probability": p,
            "horizon_days": HORIZON_DAYS,
            "observations_at_pin": len(f["gaps"]),
            "last_observed_value": float(f["recent_arrivals"]),
            "last_observed_date": obs[-1].date.isoformat(),
            "generator": {"seed": f["seed"], "paths": PATHS, "block": BLOCK, "method": method},
            "resolution_rule": rule,
        })
    return pins, low


# --- blocks ---------------------------------------------------------------------

def _registration() -> dict:
    return {
        "source_cutoff": dict(SOURCE_CUTOFF),
        "inputs": [{"path": SUBSTRATE_RELATIVE, "sha256": SUBSTRATE_SHA256}],
        "registration_deadline_utc": REGISTRATION_DEADLINE_UTC,
        "window_opens": WINDOW_OPENS,
        "public_registration": (
            "Public when the pinning commit first reaches the public repository. That UTC time "
            "and commit are appended afterwards as registration_receipt. If the commit is not "
            "public by registration_deadline_utc, the blocks are regenerated with a new deadline "
            "before anything is published. A recorded receipt later than the deadline marks the "
            "blocks void: reported, never scored."),
        "prospective_horizon": (
            f"Priced from data through {SOURCE_CUTOFF['data_as_of']}. End-of-window questions read "
            f"the last data day on or before {WINDOW_END.isoformat()} among data days from "
            f"{WINDOW_OPENS}, and path and feed questions read only data days from {WINDOW_OPENS}, "
            "so a SitRep for a data day before the window decides nothing: SitReps 186 to 188 "
            "(data 2026-11-16 to 2026-11-18), whether or not they are published before the "
            "receipt, decide no question. No day between the substrate cutoff and registration "
            "is claimed."),
        "designer_exposure": [
            "Design session: had read every reviewed SitRep promotion through SitRep 185 (data "
            "2026-11-15), the Blocks 8 to 10 resolution and its per-metric results, and the "
            "evidence-store packet for SitRep 185 from the release worktree, before generating. "
            "It saw no figure for a data day after 2026-11-15. It chose which Block 9 metric to "
            "drop and how each question names its basis; it set no threshold, probability, window "
            "length, step, rung or method parameter: those are Block 9's and Block 10's.",
            "Pre-registration review: an independent review on 2026-11-17 had read the same "
            "reviewed promotions through SitRep 185 and the Block 9 per-metric results. It "
            "recommended leaving out samples analysed for the composition of its count, which this "
            "registration does, and the wording that names the reviewed record and the lag date. "
            "It set no threshold, probability, window length, step or method parameter.",
            "Founder: on 2026-11-17 asked for Block 9's comparison and Block 10's low-count "
            "questions to be re-priced on an extract through SitRep 185, fixed the pin date, "
            "deadline, window and horizon, and named the basis breaks to check; set no threshold, "
            "probability, window length, step or method parameter. The founder's own exposure "
            "declaration is recorded with the registration receipt.",
        ],
    }


def _block14_pre_registration(dropped: list[dict]) -> dict:
    n_metrics = len(BLOCK14_METRICS)
    need_all = -(-BLOCK9_NEED * n_metrics // BLOCK9_OF_METRICS)
    floor = 2 / 2 ** n_metrics
    return {
        "comparison": {
            "incumbent": ("level_bootstrap: whole-history day-over-day changes, the forecaster of "
                          "record for fluctuating series, kept under Block 9's rule"),
            "challenger": RECENT_LEVELS,
            "pairing": "every question is priced by both methods at the identical threshold",
        },
        "unit_of_inference": (
            "the metric. A metric's threshold questions are a ladder on one realised value, not "
            "independent samples."),
        "primary_endpoint": (
            "mean over metrics of each metric's mean threshold-Brier difference, challenger minus "
            "incumbent, over every question this block pins for that metric"),
        "decision_rule": (
            f"this block is a second prospective test of Block 9's comparison, and Block 9's rule "
            f"is applied unchanged to the {n_metrics} metrics registered here. Adopt the challenger "
            "as the provisional forecaster of record for fluctuating series in the next block if "
            "the primary endpoint is below zero AND the challenger's difference is below zero on at "
            f"least ceil({BLOCK9_NEED} x k / {BLOCK9_OF_METRICS}) of the k scoreable metrics; "
            "otherwise keep the incumbent. Block 9 registered 9 metrics and required 6; this "
            "block registers 7, because national hospital isolation is withheld by the source "
            "and samples analysed is left out (see dropped_metrics), "
            f"so with all {n_metrics} scoreable the requirement is {need_all} of {n_metrics}, by "
            f"Block 9's own scaling (ceil({BLOCK9_NEED} x {n_metrics} / {BLOCK9_OF_METRICS}) = "
            f"{need_all}). A difference of exactly zero is not a win. With fewer than "
            f"{BLOCK9_NEED} scoreable metrics the block is inconclusive and the incumbent stays. "
            "Evaluated by lovs/forecast_scoring.paired_block_decision(need=6, of_metrics=9) on the "
            "differences from paired_metric_differences, passed the incumbent's probability first "
            "and the challenger's second. Adoption is provisional: the displaced method keeps "
            "being priced beside it, and permanence needs a further prospective block that "
            "agrees. Block 9 kept the incumbent, so a challenger win here would leave the two "
            "blocks split. Pins registered here keep their roles permanently."),
        "void_rule": (
            "no question here can be decided before its window: each reads the last in-window "
            "data day of a fluctuating series, which no earlier publication settles, so "
            "lovs/forecast/pins_block8.questions_decided_before finds none. A recorded "
            "registration receipt later than the deadline marks the block void."),
        "descriptive_statistics": [
            (f"exact sign-flip p-value over metrics (lovs/forecast_scoring.sign_flip_p_value), "
             f"reported with its smallest attainable two-sided value ({floor:.4g} for {n_metrics} "
             "metrics). Without justified joint sign-exchangeability, which national series that "
             "move together do not provide, the number is descriptive, not calibrated significance "
             "evidence"),
            "PIT position of each realised value among a metric's thresholds, per method",
            "the share of pins priced at or below 0.20 that resolve YES, per method",
            "Brier against the pooled base rate",
            "the Block 9 and Block 14 differences side by side, per metric",
        ],
        "unscoreable_policy": (
            "a metric with no covering observation is reported unscoreable and leaves the "
            "endpoint; nothing is imputed"),
        "prior_result": (
            "Block 9 (resolved 2026-11-11): challenger minus incumbent +0.0522 for alerts, -0.0475 "
            "for contact follow-up, +0.2442 for national isolation, -0.1078 for lab positivity, "
            "+0.0285 for new confirmed deaths, -0.0779 for new confirmed cases, +0.4855 for "
            "Nord-Kivu isolation, -0.0832 for publication lag and +0.0115 for samples analysed; "
            "mean +0.0561, better on 4 of 9 against the 6 required, so the incumbent stayed."),
        "dropped_metrics": [
            {"metric": _B9[key].series, "block9_key": key, "reason": reason}
            for key, reason in DROPPED_METRICS.items()],
        "basis_notes": {_B9[key].series: note for key, note in BASIS_NOTES.items()},
        "dropped_degenerate_questions": dropped,
    }


def build_blocks(rows=None) -> list[dict]:
    """Blocks 14 and 15, deterministically. Raises before generating anything doubtful."""
    check_timing()
    if rows is None:
        rows = load_substrate()
    else:
        check_cutoff(rows)
    taken = _ledger_seeds()
    b14_pins, b14_dropped = [], []
    for metric in BLOCK14_METRICS:
        pins, dropped = paired_metric_pins(metric, rows, taken)
        b14_pins += pins
        b14_dropped += dropped
    b15_pins, low = block15_pins(rows, taken)
    seeds = {p["generator"]["seed"] for p in b14_pins + b15_pins}
    if len(seeds) != len(BLOCK14_METRICS) * 2 + 2:
        raise RegistrationError("two of this registration's random streams share a seed")

    common = {"pinned_at": PINNED_AT, "window_opens": WINDOW_OPENS, "resolves_at": RESOLVES_AT,
              "horizon_days": HORIZON_DAYS, "status": "active"}
    span = f"from {WINDOW_OPENS} to {WINDOW_END.isoformat()}"
    return [
        {
            "block_id": BLOCK14_ID, "label": "Block 14", **common,
            "rationale": (
                "Block 14, response system: a second prospective test of Block 9's comparison on "
                "the fluctuating series. Each question is priced by the incumbent level bootstrap "
                "and by the 21-day levels challenger at the same threshold, with Block 9's "
                "threshold rule, steps, paths and horizon. In Block 9 the challenger was better on "
                "4 of 9 metrics with a mean threshold-Brier difference of +0.0561, so the "
                "incumbent stayed; its two largest losses were the isolation series, where the "
                "realised value fell above its 21-day range. Block 9's rule asks for no repeat; "
                "this block repeats the comparison on a later window to add a second observation "
                "of each metric. National hospital isolation is not carried: the source withholds "
                "it from SitRep 179. Samples analysed is not carried either: its national count "
                "moves mostly with what it includes, and positivity is computed on the same count. "
                "Four of its series changed basis inside the extract: the contact "
                "follow-up cohort, the laboratory denominator behind positivity, the "
                "provinces in the alert total and the Nord-Kivu census. Every question resolves on "
                "the value the reviewed record of the SitRep carries; a value the review withholds "
                "does not count. The questions on positivity, alerts and Nord-Kivu "
                "isolation name that basis in their text, the publication-lag question names the "
                "date it counts to (the UTC date of the first insp.cd post), and each pin's "
                "rationale states the break."),
            "registration_note": (
                "Generated by lovs/forecast/pins_block14.build_blocks() from "
                f"{SUBSTRATE_RELATIVE} (sha256 {SUBSTRATE_SHA256}); a test re-runs it and fails on "
                "any difference. Publication lag and Nord-Kivu isolation are derived series built by "
                "lovs/forecast/pins_block7, which the resolver imports. Every row of the Block 13 "
                "substrate is unchanged in this extract; SitRep 172 (data 2026-11-02) is absent from "
                "both and is not filled. The SitRep 178 to 185 values of every series used here were "
                "checked against the reviewed promotions before generation."),
            "registration": _registration(),
            "pre_registration": _block14_pre_registration(b14_dropped),
            "points": b14_pins,
        },
        {
            "block_id": BLOCK15_ID, "label": "Block 15", **common,
            "rationale": (
                "Block 15, low counts and feed. It carries Block 10's seven questions forward "
                "unchanged, priced by the forecaster of record alone: the same rungs, methods and "
                "reference window. The low-count ladder asks whether any data day "
                f"{span} reports at most 0, 15, 30, 45 or 60 new confirmed cases. Block 10 was "
                "registered when the daily count had stayed between 40 and 99 for a month; in the "
                f"month to {SOURCE_CUTOFF['data_as_of']} it stayed between {low['month_low']:g} and "
                f"{low['month_high']:g}, so the rungs sit where they sat then and the ladder still "
                "asks the question Block 10 asked. The two feed questions ask whether consecutive "
                "in-window SitRep data days stay at most one day apart (a silence before the first "
                "or after the last in-window day is not counted) and how many data days arrive. "
                "A low or zero report is a reporting outcome, not evidence the outbreak is ending."),
            "registration_note": (
                "Generated by lovs/forecast/pins_block14.build_blocks(). The termination bootstrap is "
                "the incumbent walk floored at zero; the gap bootstrap is the cadence module's arrival "
                "walk, read for in-window data days exactly as the resolver reads them. Both are "
                "Block 10's, with this block's seeds and window."),
            "registration": _registration(),
            "pre_registration": {
                "hypothesis": (
                    "Block 10's hypothesis, tested a second time. The termination bootstrap is a "
                    "random walk on a daily count that has stayed between "
                    f"{low['month_low']:g} and {low['month_high']:g} for a month, so it overprices "
                    "low days. Prediction: fewer low-count rungs come true than their prices imply, "
                    "and the lowest in-window daily count stays above the bootstrap's median lowest "
                    f"day ({low['median_low']:g})."),
                "falsified_if": (
                    f"the lowest new-confirmed count on any data day from {WINDOW_OPENS} to "
                    f"{WINDOW_END.isoformat()} is at or below {low['median_low']:g}"),
                "unit_of_inference": (
                    "one realised value (the lowest in-window daily count) for the five rungs, and one "
                    "realised series of arrival days for the two feed questions"),
                "structural_limit": (
                    "the gap bootstrap cannot draw a silence longer than any in its reference window, "
                    "so it cannot price the feed stopping"),
                "prior_result": (
                    "Block 10 (resolved 2026-11-11): the lowest in-window count was 40 against a "
                    "median lowest day of 27, so the prediction was not falsified; 2 of the 5 rungs "
                    "resolved YES against prices summing to 2.675, and both feed questions resolved "
                    "YES."),
            },
            "points": b15_pins,
        },
    ]


def append_to_ledger(path: Path = LEDGER) -> list[str]:
    """Append Blocks 14 and 15. Refuses to touch a ledger that already holds either."""
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
    ledger["_meta"]["generators"].update({b["block_id"]: "lovs/forecast/pins_block14.py" for b in blocks})
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
