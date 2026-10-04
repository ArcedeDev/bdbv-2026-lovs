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


def sign_flip_p_value(differences: tuple[float, ...], *, alternative: str = "two-sided") -> dict:
    """Exact sign-flip permutation p-value for a mean of paired differences.

    Each difference is one unit of inference (for the operational head-to-head, one
    metric's mean threshold-Brier difference, challenger minus incumbent). Under the
    null that the units' signs are exchangeable, every one of the 2**m sign patterns is
    equally likely, so the p-value is the share of patterns whose mean is at least as
    extreme as the observed one. "less" tests whether the mean is below zero.

    The smallest attainable p-value is reported beside the p-value because with few
    units it is the binding limit: four units can never reach 0.05 two-sided. The test
    is calibrated only if the units' signs are jointly exchangeable under the null.
    National series that move together do not justify that, so without such a
    justification the number is descriptive, not calibrated significance evidence.
    """
    if alternative not in ("two-sided", "less"):
        raise ValueError(f"sign_flip_p_value: unknown alternative {alternative!r}")
    values = tuple(float(d) for d in differences)
    m = len(values)
    if m == 0:
        raise ValueError("sign_flip_p_value: no differences")
    if m > 20:
        raise ValueError(f"sign_flip_p_value: {m} units is too many to enumerate exactly")
    if any(not math.isfinite(v) for v in values):
        raise ValueError("sign_flip_p_value: non-finite difference")
    observed = sum(values) / m
    # Small tolerance so ties produced by float rounding count as "as extreme".
    tol = 1e-12 * max(1.0, max(abs(v) for v in values))
    extreme = 0
    for pattern in range(2 ** m):
        mean = sum(-v if (pattern >> i) & 1 else v for i, v in enumerate(values)) / m
        if alternative == "two-sided":
            extreme += abs(mean) >= abs(observed) - tol
        else:
            extreme += mean <= observed + tol
    nonzero = sum(1 for v in values if v != 0.0)
    floor = (2 if alternative == "two-sided" else 1) / 2 ** nonzero if nonzero else 1.0
    return {
        "units": m,
        "mean_difference": observed,
        "alternative": alternative,
        "p_value": extreme / 2 ** m,
        "min_attainable_p": min(1.0, floor),
        "units_favouring_negative": sum(1 for v in values if v < 0.0),
    }


def paired_metric_differences(questions) -> dict:
    """Per-metric mean threshold-Brier difference, challenger minus incumbent.

    `questions` is an iterable of (metric, incumbent_probability, challenger_probability,
    outcome) for every scoreable, non-void paired question. A metric with no such
    question is absent from the result; the caller reports it as unscoreable.
    """
    sums: dict[str, list[float]] = {}
    for metric, p_inc, p_chal, outcome in questions:
        diff = brier_score(float(p_chal), int(outcome)) - brier_score(float(p_inc), int(outcome))
        sums.setdefault(str(metric), []).append(diff)
    return {metric: sum(d) / len(d) for metric, d in sorted(sums.items())}


def paired_block_decision(differences: dict, *, need: int, of_metrics: int) -> dict:
    """A paired block's precommitted decision rule, applied to per-metric differences.

    The rule is registered as "the mean difference is below zero AND the challenger is
    better on at least `need` of `of_metrics` metrics". With k scoreable metrics the
    win requirement scales to ceil(need * k / of_metrics); with fewer than `need`
    scoreable metrics the block is inconclusive and the incumbent stays. A difference
    of exactly zero is not a win. The sign-flip p-value rides along as a descriptive
    number only.
    """
    k = len(differences)
    if k > of_metrics:
        raise ValueError(f"paired_block_decision: {k} metrics exceed the registered {of_metrics}")
    if k < need:
        return {"scoreable_metrics": k, "outcome": "inconclusive", "adopt_challenger": False,
                "reason": f"only {k} scoreable metrics; the rule needs at least {need}"}
    required = -(-need * k // of_metrics)
    wins = sum(1 for d in differences.values() if d < 0.0)
    mean = sum(differences.values()) / k
    adopt = mean < 0.0 and wins >= required
    return {
        "scoreable_metrics": k,
        "mean_difference": mean,
        "challenger_better_on": wins,
        "required_wins": required,
        "adopt_challenger": adopt,
        "outcome": "adopt_challenger_provisionally" if adopt else "keep_incumbent",
        "sign_flip_descriptive": sign_flip_p_value(tuple(differences.values())),
    }
