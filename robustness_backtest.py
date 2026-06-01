#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Rolling-origin robustness backtest for the LOVS WA-2014 calibration.

ADDITIVE to the public release pipeline. This does NOT touch the immutable,
pre-committed headline scorecard (mode_a_backtest_* in lovs/lovs_validation.py)
or any 20 May deliverable. It answers the questions the headline cannot:

  - Skill: does the method beat a trivial base-rate forecast? (Brier skill score)
  - Discrimination: does it RANK deployment targets above chance, and above the
    obvious field heuristics "go to the nearest active area" (distance-only) and
    "go where the most cases are" (source-load-only)? (ROC AUC)
  - Uncertainty: how wide are the intervals once autocorrelated rows are handled?
    (target-prefecture clustered bootstrap)
  - Stability: does any finding survive a pre-registered grid of as-of windows,
    or is it an artifact of one flattering window?

Stdlib only. Deterministic (fixed bootstrap seed). No network calls.

  python3 robustness_backtest.py
  python3 robustness_backtest.py --json-out deliverables/robustness/wa-2014.json

See README.md for the methodology and grounding.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

from lovs import lovs_validation


REPO_ROOT = pathlib.Path(__file__).parent.resolve()
DATA_DIR = REPO_ROOT / "data"
WA_SUBSTRATE_PATH = DATA_DIR / "west-africa-prefecture-weekly.json"
COVARIATES_WA_PATH = DATA_DIR / "covariates-wa-2014.json"
COVARIATES_WA_V3_PATH = DATA_DIR / "covariates-wa-2014-v3.json"

CONFIGS: tuple[tuple[str, pathlib.Path | None], ...] = (
    ("no-context", None),
    ("country", COVARIATES_WA_PATH),
    ("district", COVARIATES_WA_V3_PATH),
)


def _fmt(x: float | None, places: int = 4) -> str:
    if x is None or x != x:  # None or NaN
        return "n/a"
    return f"{x:.{places}f}"


def _ci(lo: float, hi: float) -> str:
    return f"[{_fmt(lo, 3)}, {_fmt(hi, 3)}]"


def print_report(report: lovs_validation.RobustnessReport) -> None:
    bar = "=" * 100
    print(bar)
    print(f"LOVS rolling-origin robustness  |  {report.substrate_label}")
    print(
        f"horizon {report.horizon_weeks}w  |  proximity {report.proximity_threshold_km:.0f}km  |  "
        f"MC samples {report.n_samples}  |  bootstrap {report.bootstrap_iters} (seed {report.bootstrap_seed})"
    )
    print(bar)
    header = (
        f"{'config':<11} {'window':<20} {'N':>5} {'base':>6} "
        f"{'BSS':>8} {'BSS 95% CI':>18} {'AUC':>7} {'AUC 95% CI':>18} "
        f"{'AUCdist':>8} {'AUCload':>8} {'ECE':>7}"
    )
    print(header)
    print("-" * len(header))
    last_config = None
    for c in report.cells:
        if last_config is not None and c.config_label != last_config:
            print("")
        last_config = c.config_label
        print(
            f"{c.config_label:<11} {c.window_label:<20} {c.n:>5} {_fmt(c.base_rate, 3):>6} "
            f"{_fmt(c.brier_skill_score, 3):>8} "
            f"{_ci(*c.brier_skill_score_ci):>18} "
            f"{_fmt(c.auc_model, 3):>7} {_ci(*c.auc_model_ci):>18} "
            f"{_fmt(c.auc_distance_only, 3):>8} {_fmt(c.auc_source_load_only, 3):>8} "
            f"{_fmt(c.ece, 3):>7}"
        )
    print(bar)
    print("How to read this (the honest finding):")
    print(
        "  1. Discrimination is above chance but it is only spatial proximity:\n"
        "     AUC point estimates (~0.72 at the early windows) beat 0.5, yet\n"
        "     distance-only (AUCdist) and source-load-only (AUCload) match the model\n"
        "     at every window. The covariate / gravity machinery adds no ranking\n"
        "     value; the signal is the epidemic's spatial autocorrelation."
    )
    print(
        "  2. No positive calibration skill at any window: no Brier-skill-score CI\n"
        "     clears zero. The no-context CIs fall far below zero at longer windows\n"
        "     (the early-detection to saturated-epidemic regime change); covariate\n"
        "     configs hug zero. Skill, discrimination, and calibration are distinct."
    )
    print(
        "  3. Conclusions rest on the model-vs-baseline comparison and the BSS sign,\n"
        "     stable across all 15 cells, not on whether one AUC CI clears 0.5.\n"
        "     Target-prefecture clustering corrects the dominant repeated-event\n"
        "     dependence; residual same-week dependence means CIs may be wider still."
    )
    print(
        "  4. Not portable to BDBV: the substrate is a Zaire-species (EBOV) outbreak,\n"
        "     so these are spatial-proximity results on WA-2014, not skill claims for\n"
        "     a Bundibugyo-species outbreak. All pre-registered windows are shown."
    )
    print(bar)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--json-out",
        type=pathlib.Path,
        default=None,
        help="Optional path to also write the full report as deterministic JSON.",
    )
    parser.add_argument(
        "--bootstrap-iters",
        type=int,
        default=lovs_validation.ROBUSTNESS_BOOTSTRAP_ITERS,
        help="Bootstrap iterations (default keeps the published value; lower only for quick checks).",
    )
    args = parser.parse_args(argv)

    if not WA_SUBSTRATE_PATH.exists():
        sys.stderr.write(f"substrate not found: {WA_SUBSTRATE_PATH}\n")
        return 2

    report = lovs_validation.rolling_origin_robustness(
        WA_SUBSTRATE_PATH, CONFIGS, bootstrap_iters=args.bootstrap_iters
    )
    print_report(report)

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            lovs_validation.robustness_to_json(report), indent=2
        ) + "\n"
        args.json_out.write_text(payload, encoding="utf-8")
        print(f"wrote {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
