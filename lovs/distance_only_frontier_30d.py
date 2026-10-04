# SPDX-License-Identifier: Apache-2.0
"""Distance-only frontier rank for the BDBV model tournament.

`rank.distance_only_frontier_30d` asks one question of each target: how far is it
from where the outbreak was last moving? It carries no case counts, no mobility and
no covariates, so it is the geographic floor a richer spatial model has to beat, the
way `benchmark.base_rate_30d` is the floor for any model at all.

Every rule below was fixed before any outcome in the round's window existed.

FRONTIER. The frontier is the set of zones whose reviewed cumulative confirmed
count ROSE during the 30 days before the cutoff: the latest reviewed table before
the cutoff, compared with the latest reviewed table dated before the 30-day window
opened. A zone with no earlier table row counts from zero. If no zone rose (a
quiescent outbreak), the frontier falls back to every zone in the latest table, and
the output says which basis it used.

SCORE. ``rank_score = -(great-circle km from the target's point to the nearest
frontier zone's point)``, rounded to the metre. Higher is more likely, so a nearer
target ranks higher. Points are the generator-owned population peaks in
``data/health-zone-centroids.json``.

TIES. Two targets at exactly the same distance keep the same score. The tournament
scores rank models by ROC AUC, which gives a tied pair half credit, so leaving the
tie is the honest choice; breaking it by name would inject an arbitrary order.
When two frontier zones are equally near a target, the one reported as nearest is
the first by zone_id, which affects the diagnostic only, never the score.

LOOK-AHEAD. Every observation must be dated strictly before ``cutoff``. A later one
raises rather than being filtered, the same guard the base-rate benchmark uses.

Stdlib only.
"""
from __future__ import annotations

import datetime as dt
import math
from collections.abc import Iterable, Mapping
from typing import Any

from lovs.health_zone_centroids import haversine_km

MODEL_ID = "rank.distance_only_frontier_30d"
MODEL_VERSION = "v1"
FRONTIER_DAYS = 30


class DistanceRankError(ValueError):
    """Raised when the distance rank cannot be computed honestly."""


def _day(value: Any) -> dt.date:
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError as exc:
        raise DistanceRankError(f"observation date {value!r} is not an ISO date") from exc


def _tables_by_day(observations: Iterable[Mapping[str, Any]], cutoff: dt.date) -> list[tuple[dt.date, dict[str, int]]]:
    tables: dict[dt.date, dict[str, int]] = {}
    for record in observations:
        day = _day(record.get("date"))
        if day >= cutoff:
            raise DistanceRankError(
                f"look-ahead: observation dated {day.isoformat()} is on or after the "
                f"{cutoff.isoformat()} cutoff; the model may only read pre-cutoff evidence"
            )
        if day in tables:
            raise DistanceRankError(f"two observations share {day.isoformat()}; pass one table per day")
        counts = record.get("counts")
        if not isinstance(counts, Mapping):
            raise DistanceRankError(f"observation {day.isoformat()} has no counts mapping")
        clean: dict[str, int] = {}
        for zone, value in counts.items():
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise DistanceRankError(f"{day.isoformat()}:{zone}: cumulative count must be a non-negative integer")
            clean[str(zone)] = value
        tables[day] = clean
    return sorted(tables.items())


def frontier(observations: Iterable[Mapping[str, Any]], *, cutoff: str) -> dict[str, Any]:
    """Return the frontier zones and the basis used to pick them."""
    cutoff_day = _day(cutoff)
    tables = _tables_by_day(observations, cutoff_day)
    if not tables:
        raise DistanceRankError("no reviewed observations before the cutoff; cannot form a frontier")
    window_start = cutoff_day - dt.timedelta(days=FRONTIER_DAYS)
    latest_day, latest = tables[-1]
    earlier = [counts for day, counts in tables if day < window_start]
    baseline = earlier[-1] if earlier else {}
    rose = sorted(zone for zone, value in latest.items() if value > baseline.get(zone, 0))
    basis = "rose_in_30_days_before_cutoff"
    if not rose:
        rose = sorted(zone for zone, value in latest.items() if value > 0)
        basis = "cumulative_footprint_fallback"
    if not rose:
        raise DistanceRankError("the latest reviewed table holds no confirmed zone")
    return {
        "zones": rose,
        "basis": basis,
        "window_start": window_start.isoformat(),
        "latest_observation": latest_day.isoformat(),
        "baseline_observation": (
            max(day for day, _ in tables if day < window_start).isoformat() if earlier else None
        ),
    }


def predict(
    targets: Iterable[str],
    observations: Iterable[Mapping[str, Any]],
    *,
    cutoff: str,
    points: Mapping[str, tuple[float, float]],
) -> dict[str, Any]:
    """Score every target by negative km to the nearest frontier zone."""
    target_list = sorted({str(t) for t in targets})
    if not target_list:
        raise DistanceRankError("no targets supplied; the round universe must be frozen first")
    front = frontier(observations, cutoff=cutoff)
    missing = sorted(z for z in (*target_list, *front["zones"]) if z not in points)
    if missing:
        raise DistanceRankError(f"no generator-owned point for {len(missing)} zone(s): {missing[:5]}")

    scores: dict[str, float] = {}
    nearest: dict[str, str] = {}
    distances: dict[str, float] = {}
    for target in target_list:
        t_lat, t_lon = points[target]
        best_zone, best_km = "", math.inf
        for zone in front["zones"]:  # sorted, so a strict < keeps the first zone_id on ties
            z_lat, z_lon = points[zone]
            km = haversine_km(t_lat, t_lon, z_lat, z_lon)
            if km < best_km:
                best_zone, best_km = zone, km
        km = round(best_km, 3)
        distances[target] = km
        nearest[target] = best_zone
        scores[target] = -km if km else 0.0
    return {
        "model_id": MODEL_ID,
        "version": MODEL_VERSION,
        "cutoff": _day(cutoff).isoformat(),
        "frontier_days": FRONTIER_DAYS,
        "frontier_basis": front["basis"],
        "frontier_window_start": front["window_start"],
        "frontier_latest_observation": front["latest_observation"],
        "frontier_baseline_observation": front["baseline_observation"],
        "frontier_zones": front["zones"],
        "distance_km": distances,
        "nearest_frontier_zone": nearest,
        "predictions": scores,
        "is_observed": False,
    }
