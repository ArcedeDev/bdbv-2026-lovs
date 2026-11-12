# SPDX-License-Identifier: Apache-2.0
"""Calibration Block 13 (trajectory), generated from the frozen 2026-11-07 extract.

Block 8 tested a 28-day drift method against the level bootstrap on the four cumulative
series. Its registered rule adopted the drift method as the provisional forecaster of
record for cumulative series in the next block, and asked for a second prospective
block that agrees before the adoption becomes permanent. This is that block.

Every question is priced twice, at identical thresholds, by the same two methods Block 8
used, with their roles exchanged: the 28-day drift method is the forecaster of record
and the level bootstrap (day-over-day changes resampled from the whole history) is
priced beside it as the comparison. Thresholds, steps, quantile levels, path counts and
the horizon are Block 8's, so the two blocks ask the same kind of question.

Block 9 kept its incumbent and Block 10 tested a hypothesis that was not falsified;
neither registered rule asks for a repeat, so this block carries only the cumulative
series.

Nothing here is authored. A test regenerates every pin and fails on any difference.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Sequence

from lovs.forecast import backtest as bt
from lovs.forecast import opsforecast as of
from lovs.forecast import pins_block8 as p8

REPO = Path(__file__).resolve().parents[2]
LEDGER = REPO / "data" / "operational-calibration-ledger.json"
SUBSTRATE = REPO / "data" / "operational-series-2026-11-07.json"
SUBSTRATE_RELATIVE = "data/operational-series-2026-11-07.json"
SUBSTRATE_SHA256 = "b9a4be20c2d15df673cdacc5f0c214a8b4ea2d52a25e231d60462b89a8e66748"
SOURCE_CUTOFF = {"sitrep": "177", "data_as_of": "2026-11-07", "published_at": "2026-11-09"}

OUTBREAK = p8.OUTBREAK
PINNED_AT = "2026-11-12"
REGISTRATION_DEADLINE_UTC = "2026-11-12T23:59:59Z"
WINDOW_OPENS = "2026-11-13"
RESOLVES_AT = "2026-12-08T23:59:59Z"
WINDOW_END = dt.date(2026, 12, 8)
HORIZON_DAYS = p8.HORIZON_DAYS
LEVELS = p8.LEVELS
PATHS = p8.PATHS
BLOCK = p8.BLOCK
DRIFT_DAYS = p8.DRIFT_DAYS

BLOCK13_ID = f"operational-block:{OUTBREAK}:{PINNED_AT}:trajectory"
BLOCK_KEY = "block13"

DRIFT = p8.DRIFT
LEVEL = p8.INCUMBENT
METHOD_TAG = {DRIFT: "drift28", LEVEL: "level"}
ROLE = {DRIFT: "incumbent", LEVEL: "challenger"}


class RegistrationError(RuntimeError):
    """The inputs or timing do not support a registration; generate nothing."""


_LAST = f"On the last SitRep data day on or before {WINDOW_END.isoformat()} that reports it"
# Block 8's four cumulative series, steps and kinds, with this block's window end.
BLOCK13_METRICS: tuple[p8.Metric, ...] = tuple(
    p8.Metric(m.key, m.series, m.question.replace(p8._LAST, _LAST), m.step, m.kind, m.derived)
    for m in p8.BLOCK8_METRICS
)


def _ledger_seeds(path: Path = LEDGER) -> frozenset[int]:
    """Every seed an existing pin of the operational ledger records."""
    ledger = json.loads(path.read_text(encoding="utf-8"))
    return frozenset(
        int(pin["generator"]["seed"]) for block in ledger["blocks"] if block["block_id"] != BLOCK13_ID
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
    """The window opens after the deadline, which follows the cutoff; the horizon is Block 8's."""
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


def _end_values(metric: p8.Metric, method: str, obs, seed: int) -> list[float]:
    lo, hi = metric.bounds(obs)
    if method == LEVEL:
        return of.end_values(obs, HORIZON_DAYS, seed=seed, n_paths=PATHS, block=BLOCK, lo=lo, hi=hi)
    if method == DRIFT:
        return of.end_values(obs, HORIZON_DAYS, seed=seed, n_paths=PATHS, block=BLOCK, lo=lo, hi=hi,
                             recent_days=DRIFT_DAYS)
    raise ValueError(method)


def paired_metric_pins(metric: p8.Metric, rows, taken: frozenset[int]) -> tuple[list[dict], list[dict]]:
    """Both methods' pins for one metric, and any questions dropped as degenerate."""
    obs = metric.observations(rows)
    if obs[-1].date.isoformat() != SOURCE_CUTOFF["data_as_of"]:
        # The horizon is counted from the cutoff; a series ending earlier would be priced
        # for a different window than its question reads.
        raise RegistrationError(f"{metric.series} ends {obs[-1].date}, not on the cutoff day")
    methods = (DRIFT, LEVEL)
    seeds = {m: derived_seed(BLOCK_KEY, metric.key, m, taken=taken) for m in methods}
    ends = {m: _end_values(metric, m, obs, seeds[m]) for m in methods}
    mix = sorted(ends[DRIFT] + ends[LEVEL])
    thresholds: dict[int, list[float]] = {}
    for q in LEVELS:
        thresholds.setdefault(p8.snap(bt.quantile(mix, q), metric.step), []).append(q)

    pins, dropped = [], []
    for t, levels in thresholds.items():
        prices = {m: p8.share_at_or_below(ends[m], t) for m in methods}
        if len(set(prices.values())) == 1 and prices[DRIFT] in (0.0, 1.0):
            dropped.append({"metric": metric.series, "threshold": t, "quantile_levels": levels,
                            "both_methods_priced": prices[DRIFT]})
            continue
        qkey = f"{metric.key}-le-{t}"
        for m in methods:
            pins.append({
                "pin_id": f"operational-pin:{OUTBREAK}:{PINNED_AT}:31d:{qkey}-{METHOD_TAG[m]}",
                "question_id": f"{BLOCK_KEY}:{qkey}",
                "method": m,
                "role": ROLE[m],
                "metric": metric.series,
                "shape": "ends_below",
                "threshold": t,
                "quantile_levels": levels,
                "public_question": metric.question.format(t=t),
                "rationale": (
                    f"Paired question {qkey}: priced by the 28-day drift method, the provisional "
                    f"forecaster of record, and by the level bootstrap at the same threshold, set at "
                    f"mixture quantile(s) {', '.join(f'{x:g}' for x in levels)} and snapped to a step "
                    f"of {metric.step}."),
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


def _registration() -> dict:
    return {
        "source_cutoff": dict(SOURCE_CUTOFF),
        "inputs": [{"path": SUBSTRATE_RELATIVE, "sha256": SUBSTRATE_SHA256}],
        "registration_deadline_utc": REGISTRATION_DEADLINE_UTC,
        "window_opens": WINDOW_OPENS,
        "public_registration": (
            "Public when the pinning commit first reaches the public repository. That UTC time "
            "and commit are appended afterwards as registration_receipt. If the commit is not "
            "public by registration_deadline_utc, the block is regenerated with a new deadline "
            "before anything is published. A recorded receipt later than the deadline marks the "
            "block void: reported, never scored."),
        "prospective_horizon": (
            f"Priced from data through {SOURCE_CUTOFF['data_as_of']}. End-of-window questions read "
            f"the last data day on or before {WINDOW_END.isoformat()}, so their real prospective "
            f"horizon runs from the registration receipt to {WINDOW_END.isoformat()}. SitRep 178 "
            "(data 2026-11-08) was published before the pin date but is not in the frozen extract; "
            "a question it already decides is void under the void rule. No day between the "
            "substrate cutoff and registration is claimed."),
        "designer_exposure": [
            "Design session: had read every reviewed SitRep promotion through SitRep 178 (data "
            "2026-11-08), the Blocks 8 to 10 resolution and its per-metric results, before "
            "generating. It set no threshold, probability, window length, step or method "
            "parameter: the metrics, steps, quantile levels, path count, block length, horizon and "
            "both methods are Block 8's, and the roles follow Block 8's registered rule.",
            "Founder: asked on 2026-11-11 for the outstanding registrations to be completed; set no "
            "question, threshold, probability, window or method parameter.",
        ],
    }


def _pre_registration(dropped: list[dict]) -> dict:
    n_metrics, need = len(BLOCK13_METRICS), 3
    floor = 2 / 2 ** n_metrics
    return {
        "comparison": {
            "incumbent": (f"{DRIFT}: the provisional forecaster of record for cumulative series, "
                          "adopted under Block 8's registered rule"),
            "challenger": f"{LEVEL}: whole-history day-over-day changes, priced beside it as the comparison",
            "pairing": "every question is priced by both methods at the identical threshold",
        },
        "unit_of_inference": (
            "the metric. A metric's threshold questions are a ladder on one realised value, not "
            "independent samples."),
        "primary_endpoint": (
            "mean over metrics of each metric's mean threshold-Brier difference, drift method minus "
            "level bootstrap (the sign Block 8 used), over every question this block pins for that "
            "metric"),
        "decision_rule": (
            "this block is the second prospective test Block 8's rule requires. The drift method's "
            "adoption becomes permanent for cumulative series if the primary endpoint is below zero "
            f"AND the drift method's difference is below zero on at least {need} of the {n_metrics} "
            "metrics, the rule Block 8 met. Otherwise the level bootstrap returns as the forecaster "
            "of record for cumulative series in the next block, with the drift method priced beside "
            "it. A difference of exactly zero is not a win. If only k metrics are scoreable, the "
            f"requirement becomes ceil({need} x k / {n_metrics}) wins; with fewer than {need} "
            "scoreable metrics the block is inconclusive and the level bootstrap returns. Evaluated "
            "by lovs/forecast_scoring.paired_block_decision(need=3, of_metrics=4) on the differences "
            "from paired_metric_differences, passed the level bootstrap's probability first and the "
            "drift method's second. Pins registered here keep their roles permanently."),
        "void_rule": (
            "a question that a SitRep data day published on or before the registration receipt "
            "date already decides is void for both methods and leaves the endpoint (a running total "
            "that has passed a threshold settles 'at or below' as NO). Identified by "
            "lovs/forecast/pins_block8.questions_decided_before."),
        "descriptive_statistics": [
            (f"exact sign-flip p-value over metrics (lovs/forecast_scoring.sign_flip_p_value), "
             f"reported with its smallest attainable two-sided value ({floor:.4g} for {n_metrics} "
             "metrics). Without justified joint sign-exchangeability, which national series that "
             "move together do not provide, the number is descriptive, not calibrated significance "
             "evidence"),
            "PIT position of each realised value among a metric's thresholds, per method",
            "the share of pins priced at or below 0.20 that resolve YES, per method",
            "Brier against the pooled base rate",
            "the Block 8 and Block 13 differences side by side, per metric",
        ],
        "unscoreable_policy": (
            "a metric with no covering observation is reported unscoreable and leaves the "
            "endpoint; nothing is imputed"),
        "prior_result": (
            "Block 8 (resolved 2026-11-11): drift method minus level bootstrap -0.1140 for confirmed "
            "cases, -0.0490 for confirmed deaths, -0.0982 for affected health zones and +0.1325 for "
            "recovered; mean -0.0322, better on 3 of 4. The deaths result turned on SitRep 169's "
            "retrospective harmonisation, which SitReps 170 and 171 reverted; had it held, the "
            "level bootstrap would have stayed."),
        "dropped_degenerate_questions": dropped,
    }


def build_block(rows=None) -> dict:
    """Block 13, deterministically. Raises before generating anything doubtful."""
    check_timing()
    if rows is None:
        rows = load_substrate()
    else:
        check_cutoff(rows)
    taken = _ledger_seeds()
    pins, dropped = [], []
    for metric in BLOCK13_METRICS:
        metric_pins, metric_dropped = paired_metric_pins(metric, rows, taken)
        pins += metric_pins
        dropped += metric_dropped
    return {
        "block_id": BLOCK13_ID,
        "label": "Block 13",
        "pinned_at": PINNED_AT,
        "window_opens": WINDOW_OPENS,
        "resolves_at": RESOLVES_AT,
        "horizon_days": HORIZON_DAYS,
        "status": "active",
        "rationale": (
            "Block 13, trajectory: the second prospective test of the 28-day drift method on the "
            "four cumulative series. In Block 8 it was better than the level bootstrap on 3 of 4 "
            "metrics with a mean threshold-Brier difference of -0.0322, which met the registered "
            "rule, so it is this block's forecaster of record and the level bootstrap is priced "
            "beside it. The adoption is provisional until a second block agrees. The confirmed "
            "deaths series carries SitRep 169's retrospective harmonisation (5,026 to 5,372) and "
            "its reversal in SitRep 170 (5,103) inside the drift method's 28-day window; both "
            "methods read the series as published. "
            "Block 9's incumbent stays the forecaster of record for fluctuating series and Block 10's "
            "hypothesis was not falsified; neither registered rule asks for a repeat, so neither is "
            "carried here."),
        "registration_note": (
            "Generated by lovs/forecast/pins_block13.build_block() from "
            f"{SUBSTRATE_RELATIVE} (sha256 {SUBSTRATE_SHA256}); a test re-runs it and fails on any "
            "difference. Cumulative series are floored at their last value because a running total "
            "never falls. Every row of the Block 8 substrate is unchanged in this extract, and its four "
            "cumulative series for SitReps 141 to 177 were checked against the reviewed promotions "
            "before generation."),
        "registration": _registration(),
        "pre_registration": _pre_registration(dropped),
        "points": pins,
    }


def append_to_ledger(path: Path = LEDGER) -> str:
    """Append Block 13. Refuses to touch a ledger that already holds it or any of its pins."""
    ledger = json.loads(path.read_text(encoding="utf-8"))
    block = build_block()
    if any(b["block_id"] == block["block_id"] for b in ledger["blocks"]):
        raise RegistrationError(f"ledger already holds {block['block_id']}; pinned blocks are never rewritten")
    ids = {p["pin_id"] for b in ledger["blocks"] for p in b["points"]}
    for pin in block["points"]:
        if pin["pin_id"] in ids:
            raise RegistrationError(f"pin id {pin['pin_id']} already in the ledger")
    ledger["blocks"].append(block)
    ledger["_meta"]["generators"][block["block_id"]] = "lovs/forecast/pins_block13.py"
    path.write_text(json.dumps(ledger, indent=1, ensure_ascii=True) + "\n", encoding="utf-8")
    return block["block_id"]


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--append", action="store_true", help="append the block to the ledger")
    args = parser.parse_args()
    if args.append:
        print(append_to_ledger())
    else:
        print(json.dumps(build_block(), indent=1))
