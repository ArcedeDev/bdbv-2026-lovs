# SPDX-License-Identifier: Apache-2.0
"""Shared deterministic scoring primitives for prospective outbreak forecasts."""
from __future__ import annotations

import math


def _check_lengths(scores: tuple[float, ...], outcomes: tuple[int, ...], name: str) -> None:
    if len(scores) != len(outcomes):
        raise ValueError(f"{name}: length mismatch ({len(scores)} vs {len(outcomes)})")


def _check_binary_outcomes(outcomes: tuple[int, ...], name: str) -> None:
    for outcome in outcomes:
        if outcome not in (0, 1):
            raise ValueError(f"{name}: outcome must be 0 or 1: {outcome!r}")


def _check_probabilities(probabilities: tuple[float, ...], name: str) -> None:
    for probability in probabilities:
        if not (0.0 <= probability <= 1.0):
            raise ValueError(f"{name}: probability out of [0, 1]: {probability!r}")


def brier_score(probability: float, outcome: int) -> float:
    """Binary Brier score for one probability forecast."""
    _check_probabilities((probability,), "brier_score")
    if outcome not in (0, 1):
        raise ValueError(f"brier_score: outcome must be 0 or 1: {outcome!r}")
    return (probability - outcome) ** 2


def mean_brier_score(predicted_probs: tuple[float, ...], outcomes: tuple[int, ...]) -> float:
    """Mean binary Brier score; NaN when there are no scored rows."""
    _check_lengths(predicted_probs, outcomes, "mean_brier_score")
    _check_probabilities(predicted_probs, "mean_brier_score")
    _check_binary_outcomes(outcomes, "mean_brier_score")
    if not predicted_probs:
        return float("nan")
    return sum((p - o) ** 2 for p, o in zip(predicted_probs, outcomes)) / len(outcomes)


def brier_skill_score(predicted_probs: tuple[float, ...], outcomes: tuple[int, ...]) -> float:
    """Brier skill score versus sample climatology."""
    _check_lengths(predicted_probs, outcomes, "brier_skill_score")
    _check_binary_outcomes(outcomes, "brier_skill_score")
    n = len(outcomes)
    if n == 0:
        return float("nan")
    pbar = sum(outcomes) / n
    bs_ref = pbar * (1.0 - pbar)
    if bs_ref == 0.0:
        return float("nan")
    return 1.0 - mean_brier_score(predicted_probs, outcomes) / bs_ref


def roc_auc(scores: tuple[float, ...], outcomes: tuple[int, ...]) -> float:
    """Tie-corrected ROC AUC via the rank-sum identity."""
    _check_lengths(scores, outcomes, "roc_auc")
    _check_binary_outcomes(outcomes, "roc_auc")
    pairs = sorted(zip(scores, outcomes), key=lambda t: t[0])
    n = len(pairs)
    if n == 0:
        return float("nan")
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j < n and pairs[j][0] == pairs[i][0]:
            j += 1
        avg_rank = (i + 1 + j) / 2.0
        for k in range(i, j):
            ranks[k] = avg_rank
        i = j
    n_pos = sum(o for _, o in pairs)
    n_neg = n - n_pos
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    rank_sum_pos = sum(rank for rank, (_, outcome) in zip(ranks, pairs) if outcome == 1)
    return (rank_sum_pos - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)


def calibration_bins(
    predicted_probs: tuple[float, ...],
    outcomes: tuple[int, ...],
    n_bins: int = 10,
) -> tuple[dict[str, float | int], ...]:
    """Equal-width calibration bins for binary probability forecasts."""
    _check_lengths(predicted_probs, outcomes, "calibration_bins")
    _check_probabilities(predicted_probs, "calibration_bins")
    _check_binary_outcomes(outcomes, "calibration_bins")
    if n_bins <= 0:
        raise ValueError(f"calibration_bins: n_bins must be > 0, got {n_bins!r}")
    if not predicted_probs:
        return ()

    bin_width = 1.0 / n_bins
    buckets: list[list[tuple[float, int]]] = [[] for _ in range(n_bins)]
    for probability, outcome in zip(predicted_probs, outcomes):
        idx = min(n_bins - 1, int(probability / bin_width))
        buckets[idx].append((probability, outcome))

    result: list[dict[str, float | int]] = []
    for i, bucket in enumerate(buckets):
        if not bucket:
            continue
        result.append({
            "bin_lower": i * bin_width,
            "bin_upper": (i + 1) * bin_width if i < n_bins - 1 else 1.0,
            "predicted_mean": sum(p for p, _ in bucket) / len(bucket),
            "observed_frequency": sum(o for _, o in bucket) / len(bucket),
            "count": len(bucket),
        })
    return tuple(result)


def expected_calibration_error(
    predicted_probs: tuple[float, ...],
    outcomes: tuple[int, ...],
    n_bins: int = 10,
) -> float:
    """Weighted average bin gap |predicted_mean - observed_frequency|."""
    bins = calibration_bins(predicted_probs, outcomes, n_bins)
    if not bins:
        return float("nan")
    total = sum(int(row["count"]) for row in bins)
    if total == 0:
        return float("nan")
    weighted_gap = sum(
        int(row["count"]) * abs(float(row["predicted_mean"]) - float(row["observed_frequency"]))
        for row in bins
    )
    return weighted_gap / total


def finite_or_none(value: float) -> float | None:
    """Map non-finite metrics to JSON-safe null."""
    return value if math.isfinite(value) else None
