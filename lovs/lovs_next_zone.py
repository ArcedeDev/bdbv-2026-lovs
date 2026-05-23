"""LOVS Module E: next-zone and corridor risk.

Produces a tuple of `CorridorRiskEstimate` from an OutbreakSnapshot plus an
adjacency graph plus a Module C VisibilityPosterior. Output separates
biological-plausibility risk from surveillance-likelihood risk via the
visibility-adjustment factor.

Method: gravity-model-derived hazard.
 raw_hazard(source → target) = source_cases × edge_weight × λ
 adjusted_hazard = raw_hazard × visibility_adj
 visibility_adj = 1 / max(reporting_completeness_lower_50, 0.05)

The hazard is converted to a per-horizon appearance probability via:
 P(appear within horizon) = 1 - exp(-hazard × horizon_days / 30)

Stage One: edge weights default to 1.0 unless supplied by an adjacency
graph fixture. Source load uses per-zone confirmed counts when the snapshot
carries a zone-attributed count table, and falls back to the headline aggregate
only when no per-zone count exists. Visibility-adjusted risk uses the
lower-bound 50% reporting-completeness from Module C as the divisor; this is
conservative (under-reporting inflates apparent risk).

Live forecasts: a Forecast write path requires a registered
`hypothesis_id`. Provisional forecasts carry `hypothesis_id=None` and
`status="provisional"`.

Stdlib only. Deterministic when seeded.
"""
from __future__ import annotations

import dataclasses
import math
import random

from lovs import lovs_covariates
from lovs import lovs_reconciler
from lovs import lovs_visibility


MODEL_VERSION = "lovs_next_zone-v0.3.0"

# Conversion constant: hazard units to per-30-day appearance probability.
HAZARD_NORMALIZER = 30.0

# Per-case per-day cross-prefecture hazard coefficient.
# Household secondary attack rate ~15% is reported by Glynn JR, et al.
# "Variability in intra-household transmission of Ebola virus, and estimation
# of the household secondary attack rate." J Infect Dis 2018
# (DOI 10.1093/infdis/jix579). The ~100x down-scaling from household to
# cross-prefecture spread, and the resulting 0.003 value, are ENGINEERING
# HEURISTICS, not fitted to any cited source; Faye 2015 Lancet ID and
# Camacho 2015 PLOS Currents are directionally consistent but do not fit this
# coefficient (see data/evidence-chains.json,
# ec:lovs:module-d:corridor-gravity-exponents). The ~0.001 to ~0.005 range
# motivates 0.003 as the Stage One prior, with broad uncertainty propagated by
# the gamma rate sample.
PER_CASE_HAZARD_COEFFICIENT = 0.003

# Gamma prior on per-corridor base hazard rate multiplier (dimensionless).
HAZARD_PRIOR_GAMMA = (2.0, 1.0)  # mean 2.0

VALID_HORIZONS: frozenset[int] = frozenset({7, 14, 30})


@dataclasses.dataclass(frozen=True)
class IntervalProportion:
    lower_50: float
    upper_50: float
    lower_95: float
    upper_95: float


@dataclasses.dataclass(frozen=True)
class CorridorRiskEstimate:
    outbreak_id: str
    source_geography_id: str
    target_geography_id: str
    horizon_days: int
    risk_raw: IntervalProportion
    risk_visibility_adjusted: IntervalProportion
    drivers: tuple[str, ...]
    caveats: tuple[str, ...]
    model_version: str
    hypothesis_id: str | None
    status: str


@dataclasses.dataclass(frozen=True)
class Forecast:
    hypothesis_id: str
    model_version: str
    horizon_days: int
    direction: str
    magnitude_value: float
    magnitude_interval_lower: float
    magnitude_interval_upper: float
    confidence: float | None
    invalidation_condition: str
    registered_at: str
    provenance_ids: tuple[str, ...]


class CorridorRiskError(ValueError):
    """Raised when corridor risk cannot be computed."""


def _sample_gamma(rng: random.Random, alpha: float, beta: float) -> float:
    return rng.gammavariate(alpha, 1.0 / beta)


def _quantile(samples: list[float], q: float) -> float:
    if not samples:
        return float("nan")
    s = sorted(samples)
    idx = q * (len(s) - 1)
    lo = int(math.floor(idx))
    hi = int(math.ceil(idx))
    if lo == hi:
        return s[lo]
    return s[lo] + (s[hi] - s[lo]) * (idx - lo)


def _interval_proportion(samples: list[float]) -> IntervalProportion:
    return IntervalProportion(
        lower_50=_quantile(samples, 0.25),
        upper_50=_quantile(samples, 0.75),
        lower_95=_quantile(samples, 0.025),
        upper_95=_quantile(samples, 0.975),
    )


def _hazard_to_probability(hazard: float, horizon_days: int) -> float:
    if hazard <= 0:
        return 0.0
    return 1.0 - math.exp(-hazard * horizon_days / HAZARD_NORMALIZER)


def _visibility_adjustment(visibility: lovs_visibility.VisibilityPosterior) -> float:
    floor = max(visibility.reporting_completeness.lower_50, 0.05)
    return 1.0 / floor


def _drivers(
    source_cases: int,
    edge_weight: float,
    visibility_adj: float,
    source_case_basis: str,
) -> tuple[str, ...]:
    drivers: list[str] = []
    if source_case_basis == "zone_attributed":
        drivers.append(f"zone-attributed confirmed count {source_cases} for this source zone")
    elif source_cases >= 10:
        drivers.append(
            f"aggregate confirmed count {source_cases} applied to this source zone "
            f"(upper-envelope assumption)"
        )
    elif source_cases >= 1:
        drivers.append(
            f"aggregate confirmed count {source_cases} applied to this source zone "
            f"(low-baseline upper-envelope assumption)"
        )
    if edge_weight >= 1.5:
        drivers.append("elevated corridor connectivity (road / river / care-seeking route)")
    if visibility_adj >= 2.0:
        drivers.append(
            f"visibility-adjustment factor {visibility_adj:.2f}× reflects under-reporting "
            f"in source zone; raw risk likely understates true hazard"
        )
    if not drivers:
        drivers.append("no specific elevation; baseline gravity-model hazard only")
    return tuple(drivers)


def _caveats(
    edge_weight_provided: bool,
    visibility: lovs_visibility.VisibilityPosterior,
    source_case_basis: str,
) -> tuple[str, ...]:
    caveats: list[str] = []
    if not edge_weight_provided:
        caveats.append("edge weight defaulted to 1.0 (no adjacency graph supplied)")
    if visibility.visibility_grade in ("low", "very_low"):
        caveats.append(
            "source-zone visibility grade is low; biological hazard is harder to separate from surveillance gap"
        )
    if source_case_basis != "zone_attributed":
        caveats.append(
            "confirmed cases are aggregate, not source-zone-attributed; corridor risk is an upper-envelope read"
        )
    caveats.append(
        "Stage One model: live forecasts require pre-registered hypotheses via an external hypothesis store"
    )
    return tuple(caveats)


def _source_confirmed_cases(
    snapshot: lovs_reconciler.OutbreakSnapshot,
    source_zone: str,
    aggregate_source_cases: int,
) -> tuple[int, str]:
    zone_counts = snapshot.zone_attributed_counts.get(source_zone, {})
    confirmed = zone_counts.get("confirmed")
    if isinstance(confirmed, int):
        return max(0, confirmed), "zone_attributed"
    return aggregate_source_cases, "aggregate"


def next_zone_risk(
    snapshot: lovs_reconciler.OutbreakSnapshot,
    visibility: lovs_visibility.VisibilityPosterior,
    candidate_targets: tuple[str, ...],
    horizon_days: int = 14,
    edge_weights: dict[tuple[str, str], float] | None = None,
    n_samples: int = 500,
    seed: int | None = None,
    hypothesis_ids: dict[tuple[str, str, int], str] | None = None,
    t3_covariates: lovs_covariates.CovariateTable | None = None,
) -> tuple[CorridorRiskEstimate, ...]:
    """Compute corridor risk estimates from a snapshot to each candidate target.

    `hypothesis_ids` is an optional map from (source_geo, target_geo, horizon)
    to an external hypothesis-store ID. Estimates whose key is present in the
    map are tagged status="registered" with the supplied hypothesis_id;
    otherwise status="provisional" with hypothesis_id=None.

    Stage Two: `t3_covariates` is an optional CovariateTable. When supplied,
    each (source, target) pair's edge weight is multiplied by
    `t3_covariates.edge_weight(source, target)`, integrating
    population-density, road-connectivity, healthcare-access, and
    conflict-access covariates into the hazard. The Stage One default
    behavior (no T3 enrichment) is preserved when `t3_covariates is None`.
    """
    if horizon_days not in VALID_HORIZONS:
        raise CorridorRiskError(
            f"next_zone_risk: horizon_days must be one of {sorted(VALID_HORIZONS)}, got {horizon_days}"
        )
    if seed is None:
        seed = lovs_reconciler.snapshot_content_seed(snapshot)
    rng = random.Random(seed)

    confirmed = snapshot.reported_counts.get("confirmed")
    aggregate_source_cases = confirmed.primary_value if confirmed else 0
    visibility_adj = _visibility_adjustment(visibility)
    source_zones = snapshot.affected_zones or ("unknown",)
    edge_weights = edge_weights or {}
    hypothesis_ids = hypothesis_ids or {}

    estimates: list[CorridorRiskEstimate] = []
    for source_zone in source_zones:
        source_cases, source_case_basis = _source_confirmed_cases(
            snapshot, source_zone, aggregate_source_cases
        )
        for target_zone in candidate_targets:
            if target_zone == source_zone:
                continue
            edge_key = (source_zone, target_zone)
            edge_weight_supplied = edge_key in edge_weights
            edge_weight = edge_weights.get(edge_key, 1.0)

            # Stage Two: T3 covariate edge-weight modifier.
            t3_factor = 1.0
            if t3_covariates is not None:
                t3_factor = t3_covariates.edge_weight(source_zone, target_zone)
            effective_edge_weight = edge_weight * t3_factor

            raw_samples: list[float] = []
            adj_samples: list[float] = []
            for _ in range(n_samples):
                rate = _sample_gamma(rng, *HAZARD_PRIOR_GAMMA)
                raw_hazard = (
                    max(0.0, source_cases)
                    * effective_edge_weight
                    * rate
                    * PER_CASE_HAZARD_COEFFICIENT
                )
                raw_p = _hazard_to_probability(raw_hazard, horizon_days)
                raw_samples.append(min(1.0, raw_p))
                adj_hazard = raw_hazard * visibility_adj
                adj_p = _hazard_to_probability(adj_hazard, horizon_days)
                adj_samples.append(min(1.0, adj_p))

            risk_raw = _interval_proportion(raw_samples)
            risk_adj = _interval_proportion(adj_samples)

            hypothesis_id = hypothesis_ids.get((source_zone, target_zone, horizon_days))
            status = "registered" if hypothesis_id is not None else "provisional"

            drivers = list(
                _drivers(
                    source_cases,
                    effective_edge_weight,
                    visibility_adj,
                    source_case_basis,
                )
            )
            if t3_covariates is not None and t3_factor != 1.0:
                drivers.append(
                    f"T3 covariate edge-weight modifier {t3_factor:.2f}× "
                    f"applied (population, roads, healthcare, conflict)"
                )
            caveats = list(_caveats(edge_weight_supplied, visibility, source_case_basis))
            if t3_covariates is None:
                caveats.append(
                    "T3 covariates not supplied (Stage One baseline); pass t3_covariates= "
                    "to enrich edge-weight with population, roads, healthcare, conflict."
                )

            estimates.append(
                CorridorRiskEstimate(
                    outbreak_id=snapshot.outbreak_id,
                    source_geography_id=source_zone,
                    target_geography_id=target_zone,
                    horizon_days=horizon_days,
                    risk_raw=risk_raw,
                    risk_visibility_adjusted=risk_adj,
                    drivers=tuple(drivers),
                    caveats=tuple(caveats),
                    model_version=MODEL_VERSION,
                    hypothesis_id=hypothesis_id,
                    status=status,
                )
            )

    # Rank corridors by visibility-adjusted upper_50 descending, then by
    # source_zone and target_zone for deterministic ordering.
    estimates.sort(
        key=lambda e: (
            -e.risk_visibility_adjusted.upper_50,
            e.source_geography_id,
            e.target_geography_id,
            e.horizon_days,
        )
    )
    return tuple(estimates)


def build_forecast(
    estimate: CorridorRiskEstimate,
    registered_at: str,
    confidence: float | None = None,
    invalidation_condition: str | None = None,
) -> Forecast:
    """Construct a Forecast from a registered CorridorRiskEstimate.

    Raises ValueError if the estimate is provisional (no hypothesis_id).
    """
    if estimate.hypothesis_id is None or estimate.status != "registered":
        raise ValueError(
            "build_forecast: estimate is provisional; register via an external hypothesis store first"
        )
    if confidence is not None and not (0.0 <= confidence <= 1.0):
        raise ValueError(f"build_forecast: confidence must be in [0, 1], got {confidence}")
    invalidation = invalidation_condition or (
        f"target zone {estimate.target_geography_id!r} does not appear in the public picture "
        f"within {estimate.horizon_days} days from {registered_at}"
    )
    magnitude = (
        estimate.risk_visibility_adjusted.lower_50
        + estimate.risk_visibility_adjusted.upper_50
    ) / 2.0
    return Forecast(
        hypothesis_id=estimate.hypothesis_id,
        model_version=estimate.model_version,
        horizon_days=estimate.horizon_days,
        direction="appear_in_new_zone",
        magnitude_value=magnitude,
        magnitude_interval_lower=estimate.risk_visibility_adjusted.lower_50,
        magnitude_interval_upper=estimate.risk_visibility_adjusted.upper_50,
        confidence=confidence,
        invalidation_condition=invalidation,
        registered_at=registered_at,
        provenance_ids=(),
    )
