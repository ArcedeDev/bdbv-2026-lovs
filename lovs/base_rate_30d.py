# SPDX-License-Identifier: Apache-2.0
"""Base-rate probability benchmark for the BDBV model tournament.

`benchmark.base_rate_30d` is the bar every other competitor has to clear. It is a
memoryless climatology: it assigns EVERY target in the round the same probability,
the historical per-zone rate at which a not-yet-affected health zone records its
first public-authority lab-confirmed BDBV case within a 30-day window. A model that
cannot beat this is not carrying spatial information, whatever its Brier looks like
in isolation.

Two policies are pre-registered here rather than chosen at scoring time, because the
tournament forbids metric shopping (`scoring_policy.no_metric_shopping`).

DENOMINATOR. The exposure denominator is the ROUND'S FROZEN TARGET UNIVERSE, never a
national health-zone count. Two reasons. The reviewed national footprint is not a
structured field anywhere in the corpus, it appears only in SitRep prose, and it is
not even constant: SitRep #072 reads "48 touched of 140" and SitRep #093 reads "55 of
151", because the official footprint expands as provinces are added. And the
tournament already requires every model to be scored on one identical frozen target
set (`scoring_policy.common_target_policy`), so the round's universe is the only
denominator that is both structured and shared. This matters more than any other
choice in the module: across the observed corpus the estimate is stable against the
lookback window (0.112 to 0.137 for windows of 30, 45, 60 and full history) but
swings from 0.12 to 0.40 as the assumed universe moves from 151 to 80. The
denominator IS the model.

LOOKBACK. All pre-cutoff history, with no window parameter. Because the estimate is
window-insensitive over the observed range, a window would buy no accuracy while
adding a tunable knob, and a tunable knob on a benchmark is how a benchmark quietly
becomes unbeatable-by-construction.

SMOOTHING. Jeffreys, the Beta(0.5, 0.5) posterior mean. A raw ratio can return
exactly 0 when no zone has yet converted, and a zero probability is unbounded under
log loss and unrecoverable under any proper score. Jeffreys is the standard
non-informative choice for a Bernoulli rate and is declared, not fitted.

Stdlib only.
"""
from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Mapping
from typing import Any

WINDOW_DAYS = 30
JEFFREYS_PRIOR_SUCCESSES = 0.5
JEFFREYS_PRIOR_TRIALS = 1.0


class BaseRateError(ValueError):
    """Raised when the base rate cannot be computed honestly."""


def _day(value: str) -> dt.date:
    return dt.date.fromisoformat(str(value)[:10])


def first_detection_dates(observations: Iterable[Mapping[str, Any]]) -> dict[str, str]:
    """Map zone -> the earliest observation date the zone appears in.

    ``observations`` is the reviewed per-zone table series: an iterable of
    ``{"date": "YYYY-MM-DD", "zones": [...]}``. A zone's first appearance in a
    reviewed table is its detection date; the series is cumulative, so a zone never
    leaves once it enters.
    """
    first: dict[str, str] = {}
    for record in sorted(observations, key=lambda r: str(r.get("date"))):
        date = record.get("date")
        if not date:
            continue
        for zone in record.get("zones") or ():
            if zone:
                first.setdefault(str(zone), str(date)[:10])
    return first


def estimate_base_rate(
    observations: Iterable[Mapping[str, Any]],
    *,
    target_universe_size: int,
    cutoff: str,
) -> dict[str, Any]:
    """Return the Jeffreys-smoothed per-target 30-day first-detection probability.

    ``cutoff`` is the freeze date. Every observation must be STRICTLY before it: the
    benchmark may only use what was knowable at freeze time. An observation dated on
    or after the cutoff is a look-ahead leak, which the IDB validation work names the
    single largest backtest failure mode, so it raises rather than being filtered
    away silently.
    """
    records = list(observations)
    cutoff_day = _day(cutoff)
    leaked = sorted(
        str(r.get("date"))[:10] for r in records if r.get("date") and _day(r["date"]) >= cutoff_day
    )
    if leaked:
        raise BaseRateError(
            f"look-ahead: {len(leaked)} observation(s) dated on or after the {cutoff} cutoff "
            f"(first {leaked[0]}); the benchmark may only read pre-cutoff evidence"
        )
    if target_universe_size <= 0:
        raise BaseRateError("target_universe_size must be positive")

    first = first_detection_dates(records)
    if not first:
        raise BaseRateError("no reviewed observations before the cutoff; cannot form a base rate")

    detections = sorted(_day(d) for d in first.values())
    if len(first) > target_universe_size:
        raise BaseRateError(
            f"{len(first)} detected zones exceeds the frozen target universe of "
            f"{target_universe_size}; the universe does not contain its own history"
        )

    # LEFT CENSORING. The reviewed per-zone series does not begin at the start of the
    # outbreak, so every zone already affected when the series opens appears, spuriously,
    # to have been detected on the opening day. In this corpus that is 26 of 59 zones on
    # 2026-06-02. Counting them as events nearly doubles the numerator (58 against a true
    # 33) and roughly doubles the base rate, which would hand every competitor an
    # inflated bar to clear. They are PREVALENT, not incident: they reduce the at-risk
    # pool and contribute no event.
    baseline_day = detections[0]
    prevalent = sum(1 for det in detections if det == baseline_day)
    incident = [det for det in detections if det > baseline_day]
    events = len(incident)

    # Integrate at-risk exposure in zone-days from the day after the baseline, then
    # convert to 30-day zone-windows. At-risk on day d = universe, less the prevalent
    # pool, less the zones that have converted by d.
    zone_days = 0
    day = baseline_day + dt.timedelta(days=1)
    while day < cutoff_day:
        converted = sum(1 for det in incident if det <= day)
        zone_days += max(target_universe_size - prevalent - converted, 0)
        day += dt.timedelta(days=1)
    exposure_windows = zone_days / WINDOW_DAYS
    probability = (events + JEFFREYS_PRIOR_SUCCESSES) / (exposure_windows + JEFFREYS_PRIOR_TRIALS)
    probability = min(max(probability, 0.0), 1.0)

    return {
        "model_id": "benchmark.base_rate_30d",
        "probability": round(probability, 6),
        "window_days": WINDOW_DAYS,
        "cutoff": cutoff,
        "observation_start": baseline_day.isoformat(),
        "prevalent_at_baseline": prevalent,
        "events": events,
        "exposure_zone_windows": round(exposure_windows, 3),
        "target_universe_size": target_universe_size,
        "detected_before_cutoff": len(first),
        "smoothing": "jeffreys_beta_0.5_0.5",
        "lookback": "all_pre_cutoff_history",
        "denominator_basis": "round_frozen_target_universe",
        "is_observed": False,
    }


def predict(
    targets: Iterable[str],
    observations: Iterable[Mapping[str, Any]],
    *,
    target_universe_size: int | None = None,
    cutoff: str,
) -> dict[str, Any]:
    """Assign every target the same base-rate probability.

    Uniformity is the point, not a limitation: the benchmark deliberately carries no
    spatial information, so any competitor that beats it is demonstrating that its
    geography actually predicts something.
    """
    target_list = [str(t) for t in targets]
    if not target_list:
        raise BaseRateError("no targets supplied; the round universe must be frozen first")
    universe = target_universe_size if target_universe_size is not None else len(target_list)
    estimate = estimate_base_rate(
        observations, target_universe_size=universe, cutoff=cutoff
    )
    return {
        **estimate,
        "predictions": {target: estimate["probability"] for target in target_list},
    }
