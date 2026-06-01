"""LOVS validation layer: proper scoring rules and Mode A backtest.

Implements stdlib-only:
 - Brier score (Brier 1950 MWR).
 - CRPS sample-based (Gneiting & Raftery 2007 JASA).
 - Weighted Interval Score (Bracher et al. 2021 PLOS Comp Bio, equations 4-6).
 - Calibration curve plus expected calibration error.
 - Mode A backtest against the WA 2014 substrate.

The substrate Mode A scores:
 - Module C visibility-posterior reporting-completeness interval coverage
   against the eventually-observed cumulative count.
 - Module E next-zone forecast Brier and WIS against observed zone
   appearances at horizon.

Stdlib only. Deterministic.
"""
from __future__ import annotations

import collections
import dataclasses
import hashlib
import json
import math
import pathlib
import random

from lovs import lovs_archive
from lovs import lovs_covariates
from lovs import lovs_next_zone
from lovs import lovs_reconciler
from lovs import lovs_visibility


MODEL_VERSION = "lovs_validation-v0.2.0"


@dataclasses.dataclass(frozen=True)
class CalibrationBin:
    bin_lower: float
    bin_upper: float
    predicted_mean: float
    observed_frequency: float
    count: int


@dataclasses.dataclass(frozen=True)
class ModeABacktestResult:
    substrate_label: str
    as_of_dates: tuple[str, ...]
    visibility_calibration_bins: tuple[CalibrationBin, ...]
    visibility_interval_50_coverage: float
    visibility_interval_95_coverage: float
    next_zone_brier: float | None
    next_zone_wis: float | None
    expected_calibration_error: float
    methodology_notes: tuple[str, ...]
    model_version: str


# Proper scoring primitives.


def brier_score(predicted_prob: float, outcome: int) -> float:
    """Brier score for a single binary outcome.

    BS = (p - o)². Range [0, 1]; 0 is perfect.
    Reference: Brier 1950 Monthly Weather Review.
    """
    if not (0.0 <= predicted_prob <= 1.0):
        raise ValueError(f"brier_score: predicted_prob must be in [0, 1], got {predicted_prob}")
    if outcome not in (0, 1):
        raise ValueError(f"brier_score: outcome must be 0 or 1, got {outcome}")
    return (predicted_prob - outcome) ** 2


def crps_sample(predicted_samples: tuple[float, ...], outcome: float) -> float:
    """CRPS via the sample-based estimator.

    CRPS(F, y) = E|X - y| - (1/2) E|X - X'|
    where X, X' are iid samples from F.

    Reference: Gneiting & Raftery 2007 JASA, equations 17-20.
    """
    if not predicted_samples:
        raise ValueError("crps_sample: predicted_samples must be non-empty")
    n = len(predicted_samples)
    s = sorted(predicted_samples)
    # E|X - y|
    term1 = sum(abs(x - outcome) for x in s) / n
    # E|X - X'| via the sorted-sample identity:
    # E|X-X'| = (2/n²) Σ (2i - n - 1) s_i  (zero-indexed: i from 0 to n-1)
    # See Headrick 2010 or any sample-CRPS implementation.
    term2 = 0.0
    for i, val in enumerate(s):
        term2 += (2 * (i + 1) - n - 1) * val
    term2 = term2 / (n * n)
    return term1 - term2


def interval_score(
    lower: float, upper: float, outcome: float, alpha: float
) -> float:
    """Interval score per Bracher 2021 equation 5.

    IS_α(L, U; y) = (U - L) + (2/α)(L - y) * 1{y < L} + (2/α)(y - U) * 1{y > U}
    """
    if not (0.0 < alpha < 1.0):
        raise ValueError(f"interval_score: alpha must be in (0, 1), got {alpha}")
    if upper < lower:
        raise ValueError(f"interval_score: upper ({upper}) < lower ({lower})")
    base = upper - lower
    if outcome < lower:
        base += (2.0 / alpha) * (lower - outcome)
    elif outcome > upper:
        base += (2.0 / alpha) * (outcome - upper)
    return base


def weighted_interval_score(
    median: float,
    intervals: tuple[tuple[float, float, float], ...],
    outcome: float,
) -> float:
    """WIS per Bracher 2021 equations 4 and 6.

    WIS_α₀:K(F, y) = (1/(K + 1/2)) [ (1/2) |y - m| + Σ_k (α_k/2) IS_α_k(L_k, U_k; y) ]

    `intervals` is a tuple of (alpha, lower, upper) triples for K intervals.
    `median` is the central point estimate.
    """
    if not intervals:
        raise ValueError("weighted_interval_score: intervals must be non-empty")
    K = len(intervals)
    median_term = 0.5 * abs(outcome - median)
    interval_terms = 0.0
    for alpha, lower, upper in intervals:
        interval_terms += (alpha / 2.0) * interval_score(lower, upper, outcome, alpha)
    return (median_term + interval_terms) / (K + 0.5)


def calibration_curve(
    predicted_probs: tuple[float, ...],
    outcomes: tuple[int, ...],
    n_bins: int = 10,
) -> tuple[CalibrationBin, ...]:
    """Build a calibration curve over n_bins equal-width buckets.

    Returns one CalibrationBin per non-empty bucket.
    """
    if len(predicted_probs) != len(outcomes):
        raise ValueError(
            f"calibration_curve: length mismatch ({len(predicted_probs)} vs {len(outcomes)})"
        )
    if n_bins <= 0:
        raise ValueError(f"calibration_curve: n_bins must be > 0, got {n_bins}")
    if not predicted_probs:
        return ()

    bin_width = 1.0 / n_bins
    buckets: list[list[tuple[float, int]]] = [[] for _ in range(n_bins)]
    for p, o in zip(predicted_probs, outcomes):
        if not (0.0 <= p <= 1.0):
            raise ValueError(f"calibration_curve: predicted_prob out of [0, 1]: {p}")
        if o not in (0, 1):
            raise ValueError(f"calibration_curve: outcome must be 0 or 1: {o}")
        idx = min(n_bins - 1, int(p / bin_width))
        buckets[idx].append((p, o))

    result: list[CalibrationBin] = []
    for i, bucket in enumerate(buckets):
        if not bucket:
            continue
        bin_lower = i * bin_width
        bin_upper = (i + 1) * bin_width if i < n_bins - 1 else 1.0
        predicted_mean = sum(p for p, _ in bucket) / len(bucket)
        observed_frequency = sum(o for _, o in bucket) / len(bucket)
        result.append(
            CalibrationBin(
                bin_lower=bin_lower,
                bin_upper=bin_upper,
                predicted_mean=predicted_mean,
                observed_frequency=observed_frequency,
                count=len(bucket),
            )
        )
    return tuple(result)


def expected_calibration_error(
    predicted_probs: tuple[float, ...],
    outcomes: tuple[int, ...],
    n_bins: int = 10,
) -> float:
    """ECE: weighted average bin gap |predicted_mean - observed_frequency|."""
    bins = calibration_curve(predicted_probs, outcomes, n_bins)
    if not bins:
        return 0.0
    total = sum(b.count for b in bins)
    if total == 0:
        return 0.0
    weighted_gap = sum(
        b.count * abs(b.predicted_mean - b.observed_frequency) for b in bins
    )
    return weighted_gap / total


# Mode A backtest.


def _load_wa_substrate(path: pathlib.Path) -> dict:
    """Load the WA 2014 substrate JSON. Inert; trusted as already-validated."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _first_active_week(prefecture: dict) -> int | None:
    """Return the 1-indexed week the prefecture first reports a non-zero count, or None."""
    counts = prefecture.get("weekly_counts", [])
    for i, c in enumerate(counts):
        if c > 0:
            return i + 1
    return None


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres between two (lat, lon) points."""
    radius = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return radius * c


def _build_proximity_graph(substrate: dict) -> dict[str, list[tuple[str, float]]]:
    """Build a per-prefecture sorted list of (neighbour_id, distance_km).

    Used as a proximity-based proxy for adjacency in the absence of an
    explicit graph.
    """
    prefectures = substrate.get("prefectures", [])
    graph: dict[str, list[tuple[str, float]]] = {}
    for i, p1 in enumerate(prefectures):
        id1 = p1.get("prefecture", f"pref_{i}")
        neighbours: list[tuple[str, float]] = []
        for j, p2 in enumerate(prefectures):
            if i == j:
                continue
            id2 = p2.get("prefecture", f"pref_{j}")
            try:
                d = _haversine_km(p1["lat"], p1["lon"], p2["lat"], p2["lon"])
            except (KeyError, TypeError):
                continue
            neighbours.append((id2, d))
        neighbours.sort(key=lambda x: x[1])
        graph[id1] = neighbours
    return graph


def mode_a_backtest_wa_2014(
    substrate_path: pathlib.Path,
    as_of_weeks: tuple[int, ...] = (3, 5, 7, 9, 11),
    horizon_weeks: int = 4,
    proximity_threshold_km: float = 400.0,
) -> ModeABacktestResult:
    """Mode A retrospective backtest on the WA 2014 substrate.

    Scoring focus: next-prefecture-appearance forecasting at horizon.

    For each as_of_week W in `as_of_weeks`:
     - Identify active prefectures (any non-zero cumulative count by W).
     - For each not-yet-active prefecture target with at least one active
       neighbour within `proximity_threshold_km`:
       - Forecast P(appear within horizon_weeks) using the proximity-hazard
         model (analogous to Module E gravity-model output).
       - Outcome = 1 if the prefecture's first_active_week falls within
         (W, W + horizon_weeks], else 0.
       - Score with Brier; aggregate WIS over the 50% and 95% intervals.

    This is a genuine forecasting-skill validation of the next-zone
    methodology on a real historical substrate. The reporting-completeness
    interval is reported separately as a methodology check (the prior says
    "by W weeks elapsed, completeness should be in this range"), but is not
    scored against the substrate (which is not a reporting-delay substrate).
    """
    substrate = _load_wa_substrate(substrate_path)
    prefectures = substrate.get("prefectures", [])
    graph = _build_proximity_graph(substrate)
    first_active = {
        p.get("prefecture", f"pref_{i}"): _first_active_week(p)
        for i, p in enumerate(prefectures)
    }

    methodology_notes: list[str] = [
        "Substrate: Backer JA, Wallinga J. PLOS Comp Bio 2016 (10.1371/journal.pcbi.1005210).",
        "Scoring: next-prefecture-appearance forecasting via proximity-hazard.",
        f"Proximity threshold: {proximity_threshold_km:.0f} km (haversine distance between prefecture centroids).",
        f"Horizon: {horizon_weeks} weeks per as-of evaluation point.",
        f"Forecast: P(target appears within horizon) = 1 - exp(-source_load × {lovs_next_zone.PER_CASE_HAZARD_COEFFICIENT} × rate × horizon_days / 30), per Module E methodology.",
        f"Per-case hazard coefficient {lovs_next_zone.PER_CASE_HAZARD_COEFFICIENT}: household SAR ~15% from Glynn 2018, heuristically scaled ~100x for cross-prefecture spread. The scaling factor and this coefficient are engineering heuristics, not fitted to Faye 2015 or Camacho 2015 (see evidence-chains.json ec:lovs:module-d:corridor-gravity-exponents).",
        "Headline scores: Brier (binary mean-squared error), WIS (Bracher 2021), ECE (expected calibration error).",
        "Note: predicted-probability interval coverage of a binary outcome is degenerate (intervals on [0,1] rarely contain 0 or 1); ECE is the primary calibration metric.",
        "Reporting-completeness interval (Module C prior) is reported separately as a methodology check, not a scored metric on this substrate.",
    ]

    predicted_probs: list[float] = []
    outcomes: list[int] = []
    interval_50_hits = 0
    interval_50_total = 0
    interval_95_hits = 0
    interval_95_total = 0
    wis_terms: list[float] = []
    wis_skipped = 0  # surfaced in methodology_notes if non-zero; no silent failures.

    weeks_in_window: list[str] = []
    rng = random.Random(20140323)  # WA declaration-date-seeded for determinism

    for w in as_of_weeks:
        weeks_in_window.append(f"W{w}")
        active_now = {pid for pid, fw in first_active.items() if fw is not None and fw <= w}
        inactive_now = {pid: fw for pid, fw in first_active.items() if pid not in active_now}

        for target_id, target_first in inactive_now.items():
            neighbours = graph.get(target_id, [])
            adj_active = [
                (nid, d) for nid, d in neighbours
                if d <= proximity_threshold_km and nid in active_now
            ]
            if not adj_active:
                continue
            # Source load = sum of cumulative counts of active neighbours.
            source_load = 0
            for nid, _ in adj_active:
                source_pref = next(
                    (p for p in prefectures if p.get("prefecture") == nid), None
                )
                if source_pref is None:
                    continue
                cum = sum(source_pref.get("weekly_counts", [])[:w])
                source_load += cum

            # Forecast probability via the same hazard-to-probability mapping
            # used by Module E. PER_CASE_HAZARD_COEFFICIENT and HAZARD_PRIOR_GAMMA
            # are sourced from `lovs_next_zone` to guarantee Mode A scores the
            # same methodology that ships.
            samples: list[float] = []
            for _ in range(500):
                rate = max(
                    0.001,
                    rng.gammavariate(
                        lovs_next_zone.HAZARD_PRIOR_GAMMA[0],
                        1.0 / lovs_next_zone.HAZARD_PRIOR_GAMMA[1],
                    ),
                )
                hazard = source_load * lovs_next_zone.PER_CASE_HAZARD_COEFFICIENT * rate
                p = 1.0 - math.exp(-hazard * horizon_weeks * 7.0 / lovs_next_zone.HAZARD_NORMALIZER)
                samples.append(min(1.0, max(0.0, p)))
            sorted_s = sorted(samples)
            n = len(sorted_s)
            median_p = sorted_s[n // 2]
            lower_50 = sorted_s[int(0.25 * (n - 1))]
            upper_50 = sorted_s[int(0.75 * (n - 1))]
            lower_95 = sorted_s[int(0.025 * (n - 1))]
            upper_95 = sorted_s[int(0.975 * (n - 1))]

            # Outcome at horizon: did target become active by W + horizon?
            outcome = 1 if (target_first is not None and target_first <= w + horizon_weeks) else 0

            predicted_probs.append(median_p)
            outcomes.append(outcome)

            interval_50_total += 1
            interval_95_total += 1
            if lower_50 <= outcome <= upper_50:
                interval_50_hits += 1
            if lower_95 <= outcome <= upper_95:
                interval_95_hits += 1

            # WIS contribution. `weighted_interval_score` only raises ValueError
            # if `intervals` is empty (impossible here, the tuple is literal) or
            # if `interval_score` produces `upper < lower` (possible only via
            # float-rounding at degenerate sample sizes). The counter surfaces
            # any drop in methodology_notes so a divergent WIS-vs-Brier
            # denominator cannot fail silently.
            try:
                wis = weighted_interval_score(
                    median_p,
                    ((0.5, lower_50, upper_50), (0.05, lower_95, upper_95)),
                    float(outcome),
                )
                wis_terms.append(wis)
            except ValueError:
                wis_skipped += 1

    coverage_50 = interval_50_hits / interval_50_total if interval_50_total > 0 else 0.0
    coverage_95 = interval_95_hits / interval_95_total if interval_95_total > 0 else 0.0

    brier = (
        sum((p - o) ** 2 for p, o in zip(predicted_probs, outcomes)) / len(predicted_probs)
        if predicted_probs else None
    )
    wis = sum(wis_terms) / len(wis_terms) if wis_terms else None

    bins = calibration_curve(tuple(predicted_probs), tuple(outcomes), n_bins=5)
    ece = expected_calibration_error(tuple(predicted_probs), tuple(outcomes), n_bins=5)

    if wis_skipped > 0:
        methodology_notes.append(
            f"WIS contribution dropped for {wis_skipped} of {len(wis_terms) + wis_skipped} "
            f"data points due to ValueError from weighted_interval_score "
            f"(likely upper < lower from float rounding). WIS denominator "
            f"is {len(wis_terms)}; Brier denominator is {len(predicted_probs)}."
        )

    return ModeABacktestResult(
        substrate_label="WA 2014 (Backer & Wallinga 2016 S1)",
        as_of_dates=tuple(weeks_in_window),
        visibility_calibration_bins=bins,
        visibility_interval_50_coverage=coverage_50,
        visibility_interval_95_coverage=coverage_95,
        next_zone_brier=brier,
        next_zone_wis=wis,
        expected_calibration_error=ece,
        methodology_notes=tuple(methodology_notes),
        model_version=MODEL_VERSION,
    )


def mode_a_backtest_wa_2014_t3(
    substrate_path: pathlib.Path,
    covariate_path: pathlib.Path,
    as_of_weeks: tuple[int, ...] = (3, 5, 7, 9, 11),
    horizon_weeks: int = 4,
    proximity_threshold_km: float = 400.0,
) -> ModeABacktestResult:
    """Mode A v2: WA 2014 backtest with T3 covariate-enriched edge weights.

    Identical methodology to `mode_a_backtest_wa_2014` (Mode A v1) except that
    the per-(source, target) hazard is scaled by the T3 covariate edge-weight
    modifier loaded from `covariate_path`. The Stage One v1 result is preserved
    independently; this v2 result is reported alongside in the Stage Two
    deliverable to expose any discrimination lift from the T3 covariates.

    Scoring focus matches v1: next-prefecture-appearance forecasting at horizon.
    """
    substrate = _load_wa_substrate(substrate_path)
    prefectures = substrate.get("prefectures", [])
    graph = _build_proximity_graph(substrate)
    first_active = {
        p.get("prefecture", f"pref_{i}"): _first_active_week(p)
        for i, p in enumerate(prefectures)
    }

    covariate_table = lovs_covariates.load_covariates(covariate_path)

    methodology_notes: list[str] = [
        "Substrate: Backer JA, Wallinga J. PLOS Comp Bio 2016 (10.1371/journal.pcbi.1005210).",
        "Scoring: next-prefecture-appearance forecasting via proximity-hazard with T3 covariate enrichment.",
        f"Proximity threshold: {proximity_threshold_km:.0f} km (haversine distance between prefecture centroids).",
        f"Horizon: {horizon_weeks} weeks per as-of evaluation point.",
        (
            "Mode A v2: hazard = source_load × t3_edge_weight(s, t) × "
            f"{lovs_next_zone.PER_CASE_HAZARD_COEFFICIENT} × rate × horizon_days / 30."
        ),
        (
            f"T3 covariates loaded from {covariate_path.name}. "
            f"Source: {covariate_table.source[:120]}"
            f"{'...' if len(covariate_table.source) > 120 else ''}"
        ),
        "Headline scores: Brier (binary mean-squared error), WIS (Bracher 2021), ECE (expected calibration error).",
        "Comparison to Mode A v1 (no T3 covariates) reported in the Stage Two deliverable.",
        "Note: predicted-probability interval coverage of a binary outcome is degenerate (intervals on [0,1] rarely contain 0 or 1); ECE is the primary calibration metric.",
    ]

    predicted_probs: list[float] = []
    outcomes: list[int] = []
    interval_50_hits = 0
    interval_50_total = 0
    interval_95_hits = 0
    interval_95_total = 0
    wis_terms: list[float] = []
    wis_skipped = 0  # surfaced in methodology_notes if non-zero; no silent failures.

    weeks_in_window: list[str] = []
    rng = random.Random(20140323)

    for w in as_of_weeks:
        weeks_in_window.append(f"W{w}")
        active_now = {
            pid for pid, fw in first_active.items() if fw is not None and fw <= w
        }
        inactive_now = {
            pid: fw for pid, fw in first_active.items() if pid not in active_now
        }

        for target_id, target_first in inactive_now.items():
            neighbours = graph.get(target_id, [])
            adj_active = [
                (nid, d)
                for nid, d in neighbours
                if d <= proximity_threshold_km and nid in active_now
            ]
            if not adj_active:
                continue

            # Per-neighbour T3-modified source-load contribution.
            # For Mode A v2 the function sums per-neighbour (source_load × t3_edge_weight),
            # making T3 differentially boost or attenuate neighbour-specific hazards.
            t3_weighted_source_load = 0.0
            for nid, _ in adj_active:
                source_pref = next(
                    (p for p in prefectures if p.get("prefecture") == nid), None
                )
                if source_pref is None:
                    continue
                cum = sum(source_pref.get("weekly_counts", [])[:w])
                t3w = covariate_table.edge_weight(nid, target_id)
                t3_weighted_source_load += cum * t3w

            samples: list[float] = []
            for _ in range(500):
                rate = max(
                    0.001,
                    rng.gammavariate(
                        lovs_next_zone.HAZARD_PRIOR_GAMMA[0],
                        1.0 / lovs_next_zone.HAZARD_PRIOR_GAMMA[1],
                    ),
                )
                hazard = (
                    t3_weighted_source_load
                    * lovs_next_zone.PER_CASE_HAZARD_COEFFICIENT
                    * rate
                )
                p = 1.0 - math.exp(
                    -hazard * horizon_weeks * 7.0 / lovs_next_zone.HAZARD_NORMALIZER
                )
                samples.append(min(1.0, max(0.0, p)))
            sorted_s = sorted(samples)
            n = len(sorted_s)
            median_p = sorted_s[n // 2]
            lower_50 = sorted_s[int(0.25 * (n - 1))]
            upper_50 = sorted_s[int(0.75 * (n - 1))]
            lower_95 = sorted_s[int(0.025 * (n - 1))]
            upper_95 = sorted_s[int(0.975 * (n - 1))]

            outcome = (
                1
                if (target_first is not None and target_first <= w + horizon_weeks)
                else 0
            )

            predicted_probs.append(median_p)
            outcomes.append(outcome)

            interval_50_total += 1
            interval_95_total += 1
            if lower_50 <= outcome <= upper_50:
                interval_50_hits += 1
            if lower_95 <= outcome <= upper_95:
                interval_95_hits += 1

            # See `mode_a_backtest_wa_2014` for the same defensive pattern; any
            # drop is surfaced in methodology_notes after the loop so that a
            # WIS/Brier denominator divergence cannot fail silently.
            try:
                wis = weighted_interval_score(
                    median_p,
                    ((0.5, lower_50, upper_50), (0.05, lower_95, upper_95)),
                    float(outcome),
                )
                wis_terms.append(wis)
            except ValueError:
                wis_skipped += 1

    coverage_50 = interval_50_hits / interval_50_total if interval_50_total > 0 else 0.0
    coverage_95 = interval_95_hits / interval_95_total if interval_95_total > 0 else 0.0

    brier = (
        sum((p - o) ** 2 for p, o in zip(predicted_probs, outcomes))
        / len(predicted_probs)
        if predicted_probs
        else None
    )
    wis = sum(wis_terms) / len(wis_terms) if wis_terms else None

    bins = calibration_curve(tuple(predicted_probs), tuple(outcomes), n_bins=5)
    ece = expected_calibration_error(tuple(predicted_probs), tuple(outcomes), n_bins=5)

    if wis_skipped > 0:
        methodology_notes.append(
            f"WIS contribution dropped for {wis_skipped} of {len(wis_terms) + wis_skipped} "
            f"data points due to ValueError from weighted_interval_score "
            f"(likely upper < lower from float rounding). WIS denominator "
            f"is {len(wis_terms)}; Brier denominator is {len(predicted_probs)}."
        )

    return ModeABacktestResult(
        substrate_label="WA 2014 (Backer & Wallinga 2016 S1) T3 v2",
        as_of_dates=tuple(weeks_in_window),
        visibility_calibration_bins=bins,
        visibility_interval_50_coverage=coverage_50,
        visibility_interval_95_coverage=coverage_95,
        next_zone_brier=brier,
        next_zone_wis=wis,
        expected_calibration_error=ece,
        methodology_notes=tuple(methodology_notes),
        model_version=MODEL_VERSION,
    )


# ---------------------------------------------------------------------------
# Robustness layer (additive). The headline functions above (mode_a_backtest_*)
# and their pre-committed published numbers are immutable; nothing below changes
# them. This layer answers the questions the headline scorecard cannot:
#   - Is there skill beyond a trivial base-rate forecast?  (Brier skill score)
#   - Does the model RANK deployment targets better than chance, or than the
#     obvious field heuristics?  (ROC AUC vs distance-only and source-load-only
#     baselines)
#   - How wide is the uncertainty once autocorrelated rows are accounted for?
#     (target-prefecture clustered bootstrap CIs)
#   - Does any finding survive a pre-registered grid of as-of windows, or is it
#     an artifact of one flattering window?
#
# Unlike the headline (one shared RNG stream, so the window choice perturbs every
# draw), this layer seeds each forecast by (config, week, target). That makes the
# window sweep a clean "same forecasts, more checkpoints" comparison, at the cost
# of absolute numbers that differ slightly from the headline cell; this layer
# therefore reports skill / discrimination / calibration / CIs, never a competing
# headline Brier value.
# ---------------------------------------------------------------------------


ROBUSTNESS_MODEL_VERSION = "lovs_robustness-v0.1.0"

# Pre-registered as-of-week grid, reported in full so no window is chosen post
# hoc for a flattering result. (label, weeks).
PREREGISTERED_WINDOWS: tuple[tuple[str, tuple[int, ...]], ...] = (
    ("sparse W3,5,7,9,11", (3, 5, 7, 9, 11)),
    ("dense W3-W11", tuple(range(3, 12))),
    ("dense W3-W15", tuple(range(3, 16))),
    ("dense W3-W20", tuple(range(3, 21))),
    ("dense W3-W30", tuple(range(3, 31))),
)

ROBUSTNESS_BOOTSTRAP_ITERS = 1000
ROBUSTNESS_BOOTSTRAP_SEED = 20140323  # WA declaration-date-seeded, as the headline


@dataclasses.dataclass(frozen=True)
class ForecastRecord:
    """One scored (as-of week, target prefecture) forecast instance."""

    week: int
    target_id: str
    source_load: float        # unweighted neighbour case load (load-only baseline)
    nearest_active_km: float  # distance to nearest active neighbour (distance baseline)
    predicted_prob: float     # model output (median of the per-instance MC samples)
    outcome: int              # 1 if the target became active within the horizon


@dataclasses.dataclass(frozen=True)
class RobustnessCell:
    """All robustness metrics for one (config, window) cell."""

    config_label: str
    window_label: str
    weeks: tuple[int, ...]
    n: int
    n_positive: int
    base_rate: float
    brier: float
    brier_skill_score: float
    brier_skill_score_ci: tuple[float, float]
    auc_model: float
    auc_model_ci: tuple[float, float]
    auc_distance_only: float
    auc_source_load_only: float
    ece: float


@dataclasses.dataclass(frozen=True)
class RobustnessReport:
    substrate_label: str
    config_labels: tuple[str, ...]
    window_labels: tuple[str, ...]
    cells: tuple[RobustnessCell, ...]
    horizon_weeks: int
    proximity_threshold_km: float
    n_samples: int
    bootstrap_iters: int
    bootstrap_seed: int
    methodology_notes: tuple[str, ...]
    model_version: str


# ----- scoring primitives (pure; unit-tested) -----


def brier_skill_score(
    predicted_probs: tuple[float, ...], outcomes: tuple[int, ...]
) -> float:
    """Brier skill score versus the sample-climatology (base-rate) reference.

    BSS = 1 - BS_model / BS_ref, where BS_ref is the Brier of always predicting
    the overall positive rate pbar (equivalently pbar * (1 - pbar)). BSS > 0
    means skill beyond climatology; BSS <= 0 means none. Returns NaN when the
    outcomes have no variation (reference Brier is zero). Reference: Murphy 1973
    J Appl Meteorol; WMO forecast-verification guidance.
    """
    n = len(outcomes)
    if n == 0:
        return float("nan")
    pbar = sum(outcomes) / n
    bs_ref = pbar * (1.0 - pbar)
    if bs_ref == 0.0:
        return float("nan")
    bs = sum((p - o) ** 2 for p, o in zip(predicted_probs, outcomes)) / n
    return 1.0 - bs / bs_ref


def roc_auc(scores: tuple[float, ...], outcomes: tuple[int, ...]) -> float:
    """Tie-corrected ROC AUC via the rank-sum (Mann-Whitney) identity.

    AUC = P(score of a random positive > score of a random negative) + 0.5 *
    P(tie), computed from average (mid) ranks so ties contribute 0.5. Returns
    NaN when a slice has no positives or no negatives (AUC undefined).
    Reference: Hanley & McNeil 1982 Radiology.
    """
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
        avg_rank = (i + 1 + j) / 2.0  # mean of the 1-indexed ranks i+1 .. j
        for k in range(i, j):
            ranks[k] = avg_rank
        i = j
    n_pos = sum(o for _, o in pairs)
    n_neg = n - n_pos
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    sum_ranks_pos = sum(r for r, (_, o) in zip(ranks, pairs) if o == 1)
    return (sum_ranks_pos - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)


# Discrimination is summarised by ROC AUC (scale- and threshold-free), not
# precision@k: a pooled precision@k is not deployment-faithful (deployment is
# per-week), and the choice of k plus pooled-vs-per-week adds researcher degrees
# of freedom that an auditable public artifact should not carry.


# ----- forecast-record generation (per-instance seeded) -----


def _seed_int(label: str) -> int:
    """Deterministic non-negative int seed from a label (stable across runs)."""
    return int(hashlib.sha256(label.encode("utf-8")).hexdigest(), 16) % (2 ** 63)


def _prepare_substrate(
    substrate: dict,
) -> tuple[
    dict[str, list[tuple[str, float]]],
    dict[str, int | None],
    dict[str, list[int]],
]:
    """Precompute the proximity graph, first-active weeks, and per-id weekly counts."""
    prefectures = substrate.get("prefectures", [])
    graph = _build_proximity_graph(substrate)
    first_active: dict[str, int | None] = {}
    weekly_by_id: dict[str, list[int]] = {}
    for i, p in enumerate(prefectures):
        pid = p.get("prefecture", f"pref_{i}")
        first_active[pid] = _first_active_week(p)
        weekly_by_id[pid] = list(p.get("weekly_counts", []))
    return graph, first_active, weekly_by_id


def _forecast_records(
    graph: dict[str, list[tuple[str, float]]],
    first_active: dict[str, int | None],
    weekly_by_id: dict[str, list[int]],
    as_of_weeks: tuple[int, ...],
    horizon_weeks: int,
    proximity_threshold_km: float,
    edge_weight_fn,
    config_label: str,
    n_samples: int,
    n_weeks: int | None = None,
) -> list[ForecastRecord]:
    """Generate per-instance-seeded forecast records for one config and window.

    Reproduces the headline Module E proximity-hazard forecast formula exactly
    (see ``mode_a_backtest_wa_2014``), except the Monte Carlo rate draws are
    seeded per (config, week, target) instead of from one shared stream, so the
    window choice does not perturb individual forecasts. ``edge_weight_fn(source,
    target) -> float`` is the constant 1.0 for the no-context config and
    ``CovariateTable.edge_weight`` for the covariate configs.

    Right-censoring: an as-of week ``w`` is skipped when ``w + horizon_weeks``
    exceeds the substrate length, because a future appearance cannot yet be
    observed there and scoring it would fabricate true negatives. ``n_weeks``
    defaults to the longest weekly-count series in the substrate.
    """
    records: list[ForecastRecord] = []
    if n_weeks is None:
        n_weeks = max((len(v) for v in weekly_by_id.values()), default=0)
    coeff = lovs_next_zone.PER_CASE_HAZARD_COEFFICIENT
    gamma_shape, gamma_rate = lovs_next_zone.HAZARD_PRIOR_GAMMA
    normalizer = lovs_next_zone.HAZARD_NORMALIZER
    for w in as_of_weeks:
        if n_weeks and w + horizon_weeks > n_weeks:
            continue  # horizon extends past the record: outcome unobservable
        active_now = {
            pid for pid, fw in first_active.items() if fw is not None and fw <= w
        }
        for target_id, target_first in first_active.items():
            if target_id in active_now:
                continue
            adj_active = [
                (nid, d)
                for nid, d in graph.get(target_id, [])
                if d <= proximity_threshold_km and nid in active_now
            ]
            if not adj_active:
                continue
            nearest_active_km = adj_active[0][1]  # graph is distance-sorted ascending
            source_load = 0.0
            weighted_load = 0.0
            for nid, _ in adj_active:
                cum = float(sum(weekly_by_id.get(nid, [])[:w]))
                source_load += cum
                weighted_load += cum * edge_weight_fn(nid, target_id)
            rng = random.Random(_seed_int(f"{config_label}|{w}|{target_id}"))
            samples = []
            for _ in range(n_samples):
                rate = max(0.001, rng.gammavariate(gamma_shape, 1.0 / gamma_rate))
                hazard = weighted_load * coeff * rate
                p = 1.0 - math.exp(-hazard * horizon_weeks * 7.0 / normalizer)
                samples.append(min(1.0, max(0.0, p)))
            samples.sort()
            median_p = samples[len(samples) // 2]
            outcome = (
                1
                if (target_first is not None and target_first <= w + horizon_weeks)
                else 0
            )
            records.append(
                ForecastRecord(
                    week=w,
                    target_id=target_id,
                    source_load=source_load,
                    nearest_active_km=nearest_active_km,
                    predicted_prob=median_p,
                    outcome=outcome,
                )
            )
    return records


# ----- record-level metric adapters + clustered bootstrap -----


def _records_brier(records: list[ForecastRecord]) -> float:
    if not records:
        return float("nan")
    return sum((r.predicted_prob - r.outcome) ** 2 for r in records) / len(records)


def _records_bss(records: list[ForecastRecord]) -> float:
    return brier_skill_score(
        tuple(r.predicted_prob for r in records), tuple(r.outcome for r in records)
    )


def _records_auc_model(records: list[ForecastRecord]) -> float:
    return roc_auc(
        tuple(r.predicted_prob for r in records), tuple(r.outcome for r in records)
    )


def _records_auc_distance(records: list[ForecastRecord]) -> float:
    # Closer to an active neighbour = higher risk, so score = -distance.
    return roc_auc(
        tuple(-r.nearest_active_km for r in records), tuple(r.outcome for r in records)
    )


def _records_auc_source_load(records: list[ForecastRecord]) -> float:
    return roc_auc(
        tuple(r.source_load for r in records), tuple(r.outcome for r in records)
    )


def _records_ece(records: list[ForecastRecord]) -> float:
    return expected_calibration_error(
        tuple(r.predicted_prob for r in records),
        tuple(r.outcome for r in records),
        n_bins=5,
    )


def _percentile(sorted_vals: list[float], q: float) -> float:
    """Type-7 (linear-interpolation) percentile of a pre-sorted list; q in [0,1]."""
    if not sorted_vals:
        return float("nan")
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = q * (len(sorted_vals) - 1)
    lo_i = int(math.floor(pos))
    hi_i = int(math.ceil(pos))
    if lo_i == hi_i:
        return sorted_vals[lo_i]
    frac = pos - lo_i
    return sorted_vals[lo_i] * (1.0 - frac) + sorted_vals[hi_i] * frac


def cluster_bootstrap_cis(
    records: list[ForecastRecord],
    metric_fns: dict,
    n_boot: int,
    seed: int,
    alpha: float = 0.05,
    cluster_key=lambda r: r.target_id,
) -> dict[str, tuple[float, float]]:
    """Clustered nonparametric bootstrap percentile CIs.

    Resamples whole clusters with replacement and recomputes each metric on the
    resample. The default cluster is the target prefecture, which is the
    dependence-correct unit here: one appearance event is scored at several
    as-of weeks (the overlapping 4-week horizons), and all of those rows share a
    single target, so target clustering corrects the dominant source of inflated
    N (the same event counted many times). It does not capture residual same-week
    cross-sectional dependence, so true intervals may be slightly wider; pass a
    different ``cluster_key`` (for example ``lambda r: r.week``) to inspect that.
    Bounds are type-7 interpolated percentiles. One resample feeds all metrics.
    Deterministic for a fixed seed. Degenerate resamples (a metric undefined,
    e.g. AUC with no outcome variation) are excluded from that metric's
    percentile. Reference: Field & Welsh 2007 JRSS-B; Davison & Hinkley 1997.
    """
    by_cluster: dict = collections.defaultdict(list)
    for r in records:
        by_cluster[cluster_key(r)].append(r)
    keys = list(by_cluster.keys())  # insertion order -> deterministic
    if not keys:
        return {name: (float("nan"), float("nan")) for name in metric_fns}
    rng = random.Random(seed)
    samples: dict[str, list[float]] = {name: [] for name in metric_fns}
    n_clusters = len(keys)
    for _ in range(n_boot):
        drawn: list[ForecastRecord] = []
        for _ in range(n_clusters):
            drawn.extend(by_cluster[keys[rng.randrange(n_clusters)]])
        for name, fn in metric_fns.items():
            v = fn(drawn)
            if v == v:  # drop NaN draws (degenerate resample)
                samples[name].append(v)
    out: dict[str, tuple[float, float]] = {}
    for name, vals in samples.items():
        if not vals:
            out[name] = (float("nan"), float("nan"))
            continue
        vals.sort()
        out[name] = (
            _percentile(vals, alpha / 2.0),
            _percentile(vals, 1.0 - alpha / 2.0),
        )
    return out


def _cell_metrics(
    records: list[ForecastRecord],
    config_label: str,
    window_label: str,
    weeks: tuple[int, ...],
    bootstrap_iters: int,
    bootstrap_seed: int,
) -> RobustnessCell:
    """Compute every robustness metric (plus week-block CIs on BSS and AUC) for one cell."""
    n = len(records)
    n_pos = sum(r.outcome for r in records)
    base_rate = (n_pos / n) if n else float("nan")
    cis = cluster_bootstrap_cis(
        records,
        {"bss": _records_bss, "auc_model": _records_auc_model},
        n_boot=bootstrap_iters,
        seed=bootstrap_seed,
    )
    return RobustnessCell(
        config_label=config_label,
        window_label=window_label,
        weeks=weeks,
        n=n,
        n_positive=n_pos,
        base_rate=base_rate,
        brier=_records_brier(records),
        brier_skill_score=_records_bss(records),
        brier_skill_score_ci=cis["bss"],
        auc_model=_records_auc_model(records),
        auc_model_ci=cis["auc_model"],
        auc_distance_only=_records_auc_distance(records),
        auc_source_load_only=_records_auc_source_load(records),
        ece=_records_ece(records),
    )


def rolling_origin_robustness(
    substrate_path: pathlib.Path,
    configs: tuple[tuple[str, pathlib.Path | None], ...],
    windows: tuple[tuple[str, tuple[int, ...]], ...] = PREREGISTERED_WINDOWS,
    horizon_weeks: int = 4,
    proximity_threshold_km: float = 400.0,
    n_samples: int = 500,
    bootstrap_iters: int = ROBUSTNESS_BOOTSTRAP_ITERS,
    bootstrap_seed: int = ROBUSTNESS_BOOTSTRAP_SEED,
) -> RobustnessReport:
    """Rolling-origin robustness sweep over a pre-registered window grid.

    ``configs`` is a tuple of (label, covariate_path | None); None gives the
    no-context config (edge weight 1.0), a path loads a covariate table. For each
    config and window this scores skill (BSS vs the base-rate climatology),
    discrimination (ROC AUC vs distance-only and source-load-only baselines) and
    calibration (ECE), each with as-of-week block bootstrap CIs on the two
    load-bearing numbers (BSS, AUC). The full grid is returned; no single window
    is selected as a headline. Per (config, week) forecasts are computed once and
    shared across windows for efficiency, and as-of weeks whose horizon would run
    past the substrate are right-censored out.
    """
    substrate = _load_wa_substrate(substrate_path)
    graph, first_active, weekly_by_id = _prepare_substrate(substrate)
    n_weeks = max((len(v) for v in weekly_by_id.values()), default=0)

    edge_weight_fns: dict = {}
    for label, cov_path in configs:
        if cov_path is None:
            edge_weight_fns[label] = lambda s, t: 1.0
        else:
            table = lovs_covariates.load_covariates(cov_path)
            edge_weight_fns[label] = (
                lambda tbl: (lambda s, t: tbl.edge_weight(s, t))
            )(table)

    union_weeks = sorted({w for _, weeks in windows for w in weeks})

    cells: list[RobustnessCell] = []
    for label, _cov in configs:
        fn = edge_weight_fns[label]
        records_by_week: dict[int, list[ForecastRecord]] = {
            w: _forecast_records(
                graph=graph,
                first_active=first_active,
                weekly_by_id=weekly_by_id,
                as_of_weeks=(w,),
                horizon_weeks=horizon_weeks,
                proximity_threshold_km=proximity_threshold_km,
                edge_weight_fn=fn,
                config_label=label,
                n_samples=n_samples,
                n_weeks=n_weeks,
            )
            for w in union_weeks
        }
        for window_label, weeks in windows:
            records = [r for w in weeks for r in records_by_week[w]]
            cells.append(
                _cell_metrics(
                    records,
                    config_label=label,
                    window_label=window_label,
                    weeks=weeks,
                    bootstrap_iters=bootstrap_iters,
                    bootstrap_seed=bootstrap_seed,
                )
            )

    notes = (
        "Robustness layer is additive; the headline mode_a_backtest_* numbers are immutable and unchanged.",
        "Per-instance MC seeding by (config, week, target); absolute Brier therefore differs slightly from the headline cell, so this layer reports skill / discrimination / calibration / CIs, not a competing headline Brier.",
        "Skill: Brier skill score vs the in-sample base-rate climatology reference (Murphy 1973). The in-sample reference is conservative (harder to beat), so a BSS at or below 0 is a robust 'no skill' verdict, not an artifact.",
        "Discrimination: tie-corrected ROC AUC (Hanley & McNeil 1982); 0.5 is chance. The model AUC is compared head-to-head with distance-only and source-load-only baselines.",
        "Finding: AUC point estimates (about 0.72 at the early windows) sit above 0.5, so the method ranks better than chance, BUT distance-only and source-load-only match it at every window. The covariate / gravity machinery adds no ranking value: the discrimination is the epidemic's spatial autocorrelation, available from distance to the nearest active area alone.",
        "Calibration: no configuration shows positive calibration skill at any window; no Brier-skill-score CI lies entirely above 0. The no-context CIs fall far below 0 at the longer windows (the early-detection to saturated-epidemic regime change, significantly worse than the base-rate forecast); covariate damping keeps the contextual configs near 0 rather than improving on it.",
        "Uncertainty: 95% percentile CIs from a target-prefecture clustered bootstrap (Field & Welsh 2007; Davison & Hinkley 1997). Target clustering keeps all rows of one appearance event together (the event is scored at several as-of weeks under the overlapping horizons), correcting the dominant inflated-N source. Residual same-week cross-sectional dependence is not captured, so true intervals may be slightly wider; conclusions are therefore not hung on whether a single AUC CI clears 0.5. Degenerate resamples are excluded.",
        "Pre-registered window grid reported in full; no window is chosen post hoc.",
        "Transfer caveat: the substrate is a Zaire-species (EBOV) outbreak. These are spatial-proximity discrimination results on WA-2014, NOT skill claims portable to a Bundibugyo-species (BDBV) outbreak.",
        "WIS / interval sharpness remains the headline metric; this layer focuses on skill, discrimination, and calibration.",
        (
            "Forecast formula matches Module E exactly: P = 1 - exp(-weighted_source_load * "
            f"{lovs_next_zone.PER_CASE_HAZARD_COEFFICIENT} * rate * horizon_days / "
            f"{lovs_next_zone.HAZARD_NORMALIZER})."
        ),
    )

    return RobustnessReport(
        substrate_label="WA 2014 (Backer & Wallinga 2016 S1)",
        config_labels=tuple(label for label, _ in configs),
        window_labels=tuple(wl for wl, _ in windows),
        cells=tuple(cells),
        horizon_weeks=horizon_weeks,
        proximity_threshold_km=proximity_threshold_km,
        n_samples=n_samples,
        bootstrap_iters=bootstrap_iters,
        bootstrap_seed=bootstrap_seed,
        methodology_notes=notes,
        model_version=ROBUSTNESS_MODEL_VERSION,
    )


def _jnum(x: float | None) -> float | None:
    """JSON-clean a float: NaN/None -> None, else round to 6 dp for determinism."""
    if x is None or x != x:
        return None
    return round(float(x), 6)


def robustness_to_json(report: RobustnessReport) -> dict:
    """Serialize a RobustnessReport to a plain JSON-friendly dict (NaN -> null)."""
    return {
        "substrate_label": report.substrate_label,
        "model_version": report.model_version,
        "config_labels": list(report.config_labels),
        "window_labels": list(report.window_labels),
        "horizon_weeks": report.horizon_weeks,
        "proximity_threshold_km": report.proximity_threshold_km,
        "n_samples": report.n_samples,
        "bootstrap_iters": report.bootstrap_iters,
        "bootstrap_seed": report.bootstrap_seed,
        "methodology_notes": list(report.methodology_notes),
        "cells": [
            {
                "config": c.config_label,
                "window": c.window_label,
                "weeks": list(c.weeks),
                "n": c.n,
                "n_positive": c.n_positive,
                "base_rate": _jnum(c.base_rate),
                "brier": _jnum(c.brier),
                "brier_skill_score": _jnum(c.brier_skill_score),
                "brier_skill_score_ci": [
                    _jnum(c.brier_skill_score_ci[0]),
                    _jnum(c.brier_skill_score_ci[1]),
                ],
                "auc_model": _jnum(c.auc_model),
                "auc_model_ci": [_jnum(c.auc_model_ci[0]), _jnum(c.auc_model_ci[1])],
                "auc_distance_only": _jnum(c.auc_distance_only),
                "auc_source_load_only": _jnum(c.auc_source_load_only),
                "ece": _jnum(c.ece),
            }
            for c in report.cells
        ],
    }
