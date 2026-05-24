#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Calibration backtest for the LOVS reporting-completeness nowcast.

ADDITIVE to the public release pipeline. It does NOT modify the live snapshot,
the visibility module, or any committed deliverable's existing fields. It answers
the question the live single-snapshot nowcast cannot answer on its own: is the
reporting-completeness estimate well-calibrated, and is merging the two candidate
onset-to-notification delay priors superior to picking one?

Why a simulation rather than a live backtest: the BDBV 2026 snapshot carries a
single as-of observation, not a reporting triangle (count by event-date x
observation-date), so the live counts cannot separate epidemic growth from
reporting lag. We therefore use the settled WA-2014 epicurve
(data/west-africa-prefecture-weekly.json) for realistic epidemic SHAPES only,
define ground truth by convolving each shape with a known delay, and score the
nowcast's completeness interval against that known truth.

Three reads (all scored with the existing stdlib rules in lovs_validation.py):

  A. Simulation-based calibration (SBC). Data-generating delay drawn from the
     model's own prior; correct interval machinery yields ~nominal coverage. A
     pure software/soundness check.
  B. Misspecification sweep. Ground-truth delay set to a grid of fixed means
     (fast EBOV-like 4.5d, species-matched Rosello 8.83d, slow 14d, field-like
     21d). Each candidate model (Rosello / Camacho / equal-weight Pooled) is
     scored by interval score + coverage. The model with the lowest worst-case
     interval score is the most robust; this is the merge verdict.
  C. Real-data anchor. Each candidate is scored (CRPS, log-density) against the
     three real BDBV 2026 field delays. Sparse (n<=3); a plausibility anchor, not
     a fit.

Boundary: SBC validates the inference machinery; the sweep validates robustness
to delay misspecification on real epicurve shapes; only the anchor touches real
BDBV delays, and it is too small to fit. Real-world delay validation still awaits
2026 line-list field delays (the documented next-lever).

Stdlib only. Deterministic (fixed seed).

  python3 -m lovs.lovs_visibility_backtest
  python3 -m lovs.lovs_visibility_backtest --json-out deliverables/robustness/visibility-calibration.json
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import random
import sys

from lovs import lovs_validation as val
from lovs import lovs_visibility as vis

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
WA_SUBSTRATE_PATH = REPO_ROOT / "data" / "west-africa-prefecture-weekly.json"
OBSERVED_PATH = REPO_ROOT / "data" / "external_sources" / "bdbv-2026.observed.json"
DEFAULT_OUT = REPO_ROOT / "deliverables" / "robustness" / "visibility-calibration.json"

SCHEMA_VERSION = 1
SEED = 20260523
N_MODEL_SAMPLES = 240
N_ANCHOR_SAMPLES = 4000
WEEK_DAYS = 7.0
ONSET_OFFSET_DAYS = 3.5  # mid-week onset relative to week-end observation
MIN_CUMULATIVE_CASES = 10  # only score weeks with enough onsets for a stable ratio
MAX_LAG_WEEKS = 73  # WA-2014 substrate is 74 weekly bins; max lag index is 73

# The two candidate onset-to-notification gammas (shape, rate), reused verbatim.
ROSELLO = vis.ROSELLO_BDBV_DELAY_GAMMA
CAMACHO = vis.CAMACHO_EBOV_ZAIRE_DELAY_GAMMA

# Ground-truth delays for the misspecification sweep: fixed shape (CV ~= 0.95,
# matching the real candidates' dispersion) across a span of plausible means.
TRUTH_SHAPE = 1.1
TRUTH_MEANS_DAYS = (4.5, 8.83, 14.0, 21.0)

# Coverage tolerances for the SBC soundness gate.
NOMINAL_50, TOL_50 = 0.50, (0.40, 0.60)
NOMINAL_95, TOL_95 = 0.95, (0.90, 0.98)


def _gamma_mean(shape: float, rate: float) -> float:
    return shape / rate


def _shape_sigma(shape: float) -> float:
    """The live nowcast's shape uncertainty (lovs_visibility.py:349)."""
    return max(0.10, shape * 0.12)


def _cdf_table(shape: float, rate: float, max_lag: int) -> list[float]:
    """Pre-tabulate the delay CDF at each weekly lag's mid-week elapsed days."""
    return [
        vis._gamma_cdf(lag * WEEK_DAYS + ONSET_OFFSET_DAYS, shape, rate)
        for lag in range(max_lag + 1)
    ]


def _pooled_cdf_table(shape_r: float, shape_c: float, max_lag: int) -> list[float]:
    """Equal-weight mixture CDF table for a sampled pooled draw is built per
    component at sample time; this helper builds the deterministic mixture table
    used only for reference, not for sampling."""
    tr = _cdf_table(shape_r, ROSELLO[1], max_lag)
    tc = _cdf_table(shape_c, CAMACHO[1], max_lag)
    return [0.5 * a + 0.5 * b for a, b in zip(tr, tc)]


def _completeness(counts: list[int], week: int, table: list[float]) -> float | None:
    """Completeness at as-of `week`: reported-by-week / onset-by-week, where a
    week-t onset is reported by week w with probability table[w - t]."""
    num = 0.0
    den = 0.0
    for t in range(week + 1):
        c = counts[t]
        if c <= 0:
            continue
        num += c * table[week - t]
        den += c
    if den < MIN_CUMULATIVE_CASES:
        return None
    return num / den


def _model_sample_tables(model: str, rng: random.Random, max_lag: int) -> list[list[float]]:
    """N pre-tabulated CDFs representing the model's predictive over the delay,
    propagating the live shape uncertainty. Pooled draws a component per sample."""
    tables: list[list[float]] = []
    if model == "rosello":
        shape0, rate0 = ROSELLO
        sigma = _shape_sigma(shape0)
        for _ in range(N_MODEL_SAMPLES):
            a = max(0.1, rng.gauss(shape0, sigma))
            tables.append(_cdf_table(a, rate0, max_lag))
    elif model == "camacho":
        shape0, rate0 = CAMACHO
        sigma = _shape_sigma(shape0)
        for _ in range(N_MODEL_SAMPLES):
            a = max(0.1, rng.gauss(shape0, sigma))
            tables.append(_cdf_table(a, rate0, max_lag))
    elif model == "pooled":
        for _ in range(N_MODEL_SAMPLES):
            if rng.random() < 0.5:
                shape0, rate0 = ROSELLO
            else:
                shape0, rate0 = CAMACHO
            a = max(0.1, rng.gauss(shape0, _shape_sigma(shape0)))
            tables.append(_cdf_table(a, rate0, max_lag))
    else:
        raise ValueError(f"unknown model {model!r}")
    return tables


def _interval(samples: list[float], lo_q: float, hi_q: float) -> tuple[float, float]:
    return vis._quantile(samples, lo_q), vis._quantile(samples, hi_q)


def _active_weeks(counts: list[int]) -> range:
    """Weeks from the first that reaches MIN_CUMULATIVE_CASES to the last onset."""
    cum = 0
    start = None
    for w, c in enumerate(counts):
        cum += c
        if start is None and cum >= MIN_CUMULATIVE_CASES:
            start = w
    last = max((w for w, c in enumerate(counts) if c > 0), default=-1)
    if start is None or last < start:
        return range(0)
    return range(start, last + 1)


def _score_cells(
    prefectures: list[dict],
    truth_table_fn,
    model_tables: list[list[float]],
    rng: random.Random | None,
) -> dict:
    """Score model intervals against a per-cell truth completeness.

    truth_table_fn(max_lag, rng) -> a CDF table for the data-generating delay.
    When rng is None the truth is deterministic (sweep); when rng is provided the
    truth is redrawn per cell from the model prior (SBC)."""
    cov50 = cov95 = n = 0
    is50_sum = is95_sum = 0.0
    for pref in prefectures:
        counts = pref["weekly_counts"]
        weeks = _active_weeks(counts)
        if not len(weeks):
            continue
        max_lag = len(counts) - 1
        det_truth = None if rng is not None else truth_table_fn(max_lag, None)
        for w in weeks:
            truth_table = det_truth if det_truth is not None else truth_table_fn(max_lag, rng)
            truth = _completeness(counts, w, truth_table)
            if truth is None:
                continue
            samples = [
                comp
                for tbl in model_tables
                if (comp := _completeness(counts, w, tbl)) is not None
            ]
            if len(samples) < 10:
                continue
            l50, u50 = _interval(samples, 0.25, 0.75)
            l95, u95 = _interval(samples, 0.025, 0.975)
            is50_sum += val.interval_score(l50, u50, truth, 0.5)
            is95_sum += val.interval_score(l95, u95, truth, 0.05)
            cov50 += 1 if l50 <= truth <= u50 else 0
            cov95 += 1 if l95 <= truth <= u95 else 0
            n += 1
    if n == 0:
        return {"n_cells": 0}
    return {
        "n_cells": n,
        "coverage_50": round(cov50 / n, 4),
        "coverage_95": round(cov95 / n, 4),
        "mean_interval_score_50": round(is50_sum / n, 5),
        "mean_interval_score_95": round(is95_sum / n, 5),
    }


def _run_sbc(prefectures: list[dict], rng: random.Random) -> dict:
    out: dict = {
        "nominal_50": NOMINAL_50,
        "nominal_95": NOMINAL_95,
        "tolerance_50": list(TOL_50),
        "tolerance_95": list(TOL_95),
        "by_model": {},
    }
    passed = True
    for model in ("rosello", "camacho", "pooled"):
        model_tables = _model_sample_tables(model, rng, max_lag=MAX_LAG_WEEKS)

        def truth_fn(max_lag: int, r: random.Random | None, _m=model):
            # Truth drawn from the same prior the model represents.
            if _m == "pooled":
                shape0, rate0 = ROSELLO if r.random() < 0.5 else CAMACHO
            else:
                shape0, rate0 = ROSELLO if _m == "rosello" else CAMACHO
            a = max(0.1, r.gauss(shape0, _shape_sigma(shape0)))
            return _cdf_table(a, rate0, max_lag)

        res = _score_cells(prefectures, truth_fn, model_tables, rng)
        out["by_model"][model] = res
        if res.get("n_cells", 0):
            if not (TOL_50[0] <= res["coverage_50"] <= TOL_50[1]):
                passed = False
            if not (TOL_95[0] <= res["coverage_95"] <= TOL_95[1]):
                passed = False
    out["pass"] = passed
    return out


def _run_sweep(prefectures: list[dict], rng: random.Random) -> dict:
    model_tables = {m: _model_sample_tables(m, rng, max_lag=MAX_LAG_WEEKS) for m in ("rosello", "camacho", "pooled")}
    by_model: dict = {m: {"per_truth": {}} for m in model_tables}
    for mean_days in TRUTH_MEANS_DAYS:
        rate = TRUTH_SHAPE / mean_days
        truth_table = _cdf_table(TRUTH_SHAPE, rate, MAX_LAG_WEEKS)

        def truth_fn(max_lag: int, r, _t=truth_table):
            return _t

        for model, tables in model_tables.items():
            res = _score_cells(prefectures, truth_fn, tables, None)
            by_model[model]["per_truth"][f"{mean_days:.2f}"] = res

    for model, block in by_model.items():
        scores = [v["mean_interval_score_50"] for v in block["per_truth"].values() if "mean_interval_score_50" in v]
        block["worst_case_interval_score_50"] = round(max(scores), 5) if scores else None
        block["mean_interval_score_50"] = round(sum(scores) / len(scores), 5) if scores else None

    ranked = sorted(
        (m for m in by_model if by_model[m]["worst_case_interval_score_50"] is not None),
        key=lambda m: by_model[m]["worst_case_interval_score_50"],
    )
    best = ranked[0] if ranked else None
    single_worst = [
        by_model[m]["worst_case_interval_score_50"]
        for m in ("rosello", "camacho")
        if by_model[m]["worst_case_interval_score_50"] is not None
    ]
    pooled_wc = by_model["pooled"]["worst_case_interval_score_50"]
    pooled_beats = (
        pooled_wc is not None and single_worst and pooled_wc <= min(single_worst)
    )
    return {
        "truth_means_days": list(TRUTH_MEANS_DAYS),
        "truth_shape": TRUTH_SHAPE,
        "by_model": by_model,
        "verdict": {
            "best_worst_case_model": best,
            "ranking_by_worst_case_interval_score_50": ranked,
            "pooled_beats_worst_single": bool(pooled_beats),
        },
    }


def _weighted_pooled_tables(weight_rosello: float, rng: random.Random, max_lag: int) -> list[list[float]]:
    """Pooled predictive that puts `weight_rosello` mass on the Rosello component."""
    tables: list[list[float]] = []
    for _ in range(N_MODEL_SAMPLES):
        shape0, rate0 = ROSELLO if rng.random() < weight_rosello else CAMACHO
        a = max(0.1, rng.gauss(shape0, _shape_sigma(shape0)))
        tables.append(_cdf_table(a, rate0, max_lag))
    return tables


def _run_stacking(prefectures: list[dict], rng: random.Random) -> dict:
    """Sweep the Rosello/Camacho mixture weight to locate the score-optimal merge.

    weight = mass on Rosello; 0.0 is pure Camacho, 1.0 is pure Rosello, 0.5 is the
    equal-weight pool reported in the sweep."""
    weights = (0.0, 0.25, 0.5, 0.75, 1.0)
    truth_tables = {m: _cdf_table(TRUTH_SHAPE, TRUTH_SHAPE / m, MAX_LAG_WEEKS) for m in TRUTH_MEANS_DAYS}
    by_weight: dict = {}
    for wt in weights:
        tables = _weighted_pooled_tables(wt, rng, MAX_LAG_WEEKS)
        scores: list[float] = []
        per_truth: dict = {}
        for mean_days, tt in truth_tables.items():
            res = _score_cells(prefectures, (lambda ml, r, _t=tt: _t), tables, None)
            score = res.get("mean_interval_score_50")
            per_truth[f"{mean_days:.2f}"] = score
            if score is not None:
                scores.append(score)
        by_weight[f"{wt:.2f}"] = {
            "per_truth": per_truth,
            "worst_case_interval_score_50": round(max(scores), 5) if scores else None,
            "mean_interval_score_50": round(sum(scores) / len(scores), 5) if scores else None,
        }
    best_wc = min(by_weight, key=lambda k: by_weight[k]["worst_case_interval_score_50"])
    best_mean = min(by_weight, key=lambda k: by_weight[k]["mean_interval_score_50"])
    return {
        "weight_is_mass_on_rosello": True,
        "by_weight": by_weight,
        "optimal_weight_worst_case_is50": float(best_wc),
        "optimal_weight_mean_is50": float(best_mean),
        "note": (
            "Optimal mixture weight on Rosello. A value near 1.0 means merging in "
            "Camacho does not improve robustness; a value near 0.5 would support a pool."
        ),
    }


def _candidate_delay_samples(candidate: str, rng: random.Random, n: int) -> tuple[float, ...]:
    out: list[float] = []
    for _ in range(n):
        if candidate == "rosello":
            shape, rate = ROSELLO
        elif candidate == "camacho":
            shape, rate = CAMACHO
        elif candidate == "pooled":
            shape, rate = ROSELLO if rng.random() < 0.5 else CAMACHO
        else:
            raise ValueError(candidate)
        out.append(vis._sample_gamma(rng, shape, rate))
    return tuple(out)


def _load_field_delays() -> dict:
    data = json.loads(OBSERVED_PATH.read_text(encoding="utf-8"))
    points = data.get("confirmation_latency", {}).get("datapoints_days", [])
    by_what = {p.get("what", ""): float(p.get("days")) for p in points if "days" in p}
    onset = next((v for k, v in by_what.items() if "onset" in k), None)
    alert = next((v for k, v in by_what.items() if "alert" in k), None)
    return {"onset_to_confirmation": onset, "alert_to_confirmation": alert, "all": by_what}


def _run_anchor(rng: random.Random) -> dict:
    field = _load_field_delays()
    primary = field["onset_to_confirmation"]
    points = [p for p in (primary, field["alert_to_confirmation"]) if p is not None]
    by_candidate: dict = {}
    for candidate in ("rosello", "camacho", "pooled"):
        samples = _candidate_delay_samples(candidate, rng, N_ANCHOR_SAMPLES)
        crps = sum(val.crps_sample(samples, pt) for pt in points) / len(points) if points else None
        if candidate == "pooled":
            logdens = sum(
                math.log(max(1e-12, 0.5 * vis._gamma_pdf(pt, *ROSELLO) + 0.5 * vis._gamma_pdf(pt, *CAMACHO)))
                for pt in points
            )
        else:
            shape, rate = ROSELLO if candidate == "rosello" else CAMACHO
            logdens = sum(math.log(max(1e-12, vis._gamma_pdf(pt, shape, rate))) for pt in points)
        by_candidate[candidate] = {
            "crps_days": round(crps, 4) if crps is not None else None,
            "log_density": round(logdens, 4),
            "mean_days": round(_gamma_mean(*(ROSELLO if candidate == "rosello" else CAMACHO)), 2)
            if candidate != "pooled"
            else round(0.5 * _gamma_mean(*ROSELLO) + 0.5 * _gamma_mean(*CAMACHO), 2),
        }
    best = min(
        (c for c in by_candidate if by_candidate[c]["crps_days"] is not None),
        key=lambda c: by_candidate[c]["crps_days"],
        default=None,
    )
    return {
        "field_delays_days": {
            "onset_to_confirmation": field["onset_to_confirmation"],
            "alert_to_confirmation": field["alert_to_confirmation"],
        },
        "scored_points_days": points,
        "by_candidate": by_candidate,
        "best_fit_candidate": best,
        "caveat": (
            "n<=2 onset/alert-to-confirmation proxies for onset-to-notification; "
            "a plausibility anchor, not a fit. Sample-to-result lab turnaround is "
            "excluded (different delay segment). Real validation awaits 2026 "
            "line-list field delays."
        ),
    }


def run_backtest() -> dict:
    substrate = val._load_wa_substrate(WA_SUBSTRATE_PATH)
    prefectures = [p for p in substrate.get("prefectures", []) if any(p.get("weekly_counts", []))]
    max_weeks = max((len(p.get("weekly_counts", [])) for p in prefectures), default=0)
    if max_weeks - 1 > MAX_LAG_WEEKS:
        raise ValueError(f"substrate has {max_weeks} weekly bins; raise MAX_LAG_WEEKS (currently {MAX_LAG_WEEKS})")
    rng = random.Random(SEED)

    sbc = _run_sbc(prefectures, rng)
    sweep = _run_sweep(prefectures, rng)
    stacking = _run_stacking(prefectures, rng)
    anchor = _run_anchor(rng)

    pooled_mean = 0.5 * _gamma_mean(*ROSELLO) + 0.5 * _gamma_mean(*CAMACHO)
    result = {
        "schema_version": SCHEMA_VERSION,
        "generated_by": "lovs/lovs_visibility_backtest.py",
        "seed": SEED,
        "n_model_samples": N_MODEL_SAMPLES,
        "substrate": {
            "path": str(WA_SUBSTRATE_PATH.relative_to(REPO_ROOT)),
            "citation": substrate.get("metadata", {}).get("citation", ""),
            "n_prefectures_used": len(prefectures),
            "note": "WA-2014 (Zaire-species) used for epidemic SHAPES only; never for delay values.",
        },
        "candidates": {
            "rosello": {"gamma_shape_rate": list(ROSELLO), "mean_days": round(_gamma_mean(*ROSELLO), 2)},
            "camacho": {"gamma_shape_rate": list(CAMACHO), "mean_days": round(_gamma_mean(*CAMACHO), 2)},
            "pooled": {"definition": "equal-weight mixture of rosello and camacho", "mean_days": round(pooled_mean, 2)},
        },
        "sbc": sbc,
        "misspecification_sweep": sweep,
        "stacking": stacking,
        "real_data_anchor": anchor,
        "interpretation": _interpret(sbc, sweep, stacking, anchor),
    }
    return result


def _interpret(sbc: dict, sweep: dict, stacking: dict, anchor: dict) -> str:
    parts: list[str] = []
    parts.append(
        "SBC machinery {}.".format("calibrated within tolerance" if sbc.get("pass") else "OUT OF tolerance")
    )
    v = sweep.get("verdict", {})
    parts.append(
        "Most robust to delay misspecification (lowest worst-case interval score): "
        f"{v.get('best_worst_case_model')}; equal-weight pooled beats both singles' worst case: "
        f"{v.get('pooled_beats_worst_single')}."
    )
    parts.append(
        f"Score-optimal mixture weight on Rosello: {stacking.get('optimal_weight_worst_case_is50')} "
        f"(worst-case), {stacking.get('optimal_weight_mean_is50')} (mean)."
    )
    parts.append(
        f"Against real BDBV field delays the best-fitting candidate is "
        f"{anchor.get('best_fit_candidate')} (sparse anchor; not a fit)."
    )
    return " ".join(parts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json-out", type=pathlib.Path, default=None, help="Write the result JSON to this path.")
    parser.add_argument("--quiet", action="store_true", help="Suppress the stdout summary.")
    args = parser.parse_args(argv)

    result = run_backtest()

    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        if not args.quiet:
            print(f"visibility_calibration={args.json_out}")

    if not args.quiet:
        print(result["interpretation"])
        sweep = result["misspecification_sweep"]["by_model"]
        for model in ("rosello", "camacho", "pooled"):
            b = sweep[model]
            print(f"  {model:8s} worst-case IS50={b['worst_case_interval_score_50']}  mean IS50={b['mean_interval_score_50']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
