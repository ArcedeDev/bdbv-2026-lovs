# SPDX-License-Identifier: Apache-2.0
"""Scores of spec v1 (DESIGN.md 3): WIS on the natural and log(x + 1) scales, coverage,
randomised PIT, pin Brier and clipped log scores, ladder-Brier, relative WIS.

A target whose realised value is negative (a cumulative fall) is scored on the natural
scale only, since log(x + 1) is undefined below -1.
"""
from __future__ import annotations

import math
import statistics
from typing import Iterable, Mapping, Sequence

from lovs import forecast_scoring as fs
from lovs.forecast.weekly import spec

SCORER_VERSION = f"{spec.SPEC_VERSION}.score.1"
LOG_CLIP = 0.01


def _log_quantiles(quantiles) -> list[tuple[float, float]]:
    return [(level, math.log(max(value, 0.0) + 1.0)) for level, value in quantiles]


def target_scores(quantiles, y: float, *, u: float) -> dict:
    out = {
        "wis": round(fs.wis(quantiles, y), 6),
        "wis_log": None,
        "coverage": {f"{int(round(level * 100))}": fs.interval_coverage(quantiles, y, level)
                     for level in spec.COVERAGE_LEVELS},
        "pit": round(fs.randomized_pit(quantiles, y, u), 6),
        "abs_error": round(abs(dict((round(a, 10), b) for a, b in quantiles)[0.5] - y), 6),
    }
    if y >= 0:
        out["wis_log"] = round(fs.wis(_log_quantiles(quantiles), math.log(y + 1.0)), 6)
    return out


def pin_scores(probability: float, outcome: int) -> dict:
    p = min(1 - LOG_CLIP, max(LOG_CLIP, probability))
    return {"brier": round(fs.brier_score(probability, outcome), 6),
            "log": round(math.log(p if outcome else 1 - p), 6)}


def ladder_brier(rows: Iterable[Mapping], method: str) -> float | None:
    """Mean pin Brier of one method over a series and horizon's resolved pins."""
    col = spec.score_column("brier", method)
    vals = [float(r[col]) for r in rows if r.get(col) not in (None, "")]
    return statistics.fmean(vals) if vals else None


def relative_wis(by_method: Mapping[str, Sequence[float]], baseline: str = spec.M_PERSIST) -> dict:
    return fs.relative_wis(dict(by_method), baseline=baseline)
