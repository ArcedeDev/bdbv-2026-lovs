"""LOVS Stage Two: T3 covariate loader and edge-weight modifier.

Stage Two adds four T3 (covariate-tier) features at the geography_id level
to enrich Module E's edge-weight from the Stage One uniform default of 1.0:

 1. population_density (people per km²). Larger source population →
    higher absolute hazard. Larger target population → larger attractor
    (gravity-model attractor).
 2. road_connectivity_index (1-5 ordinal). Higher → easier movement →
    higher cross-zone transmission risk.
 3. healthcare_distance_km (km to nearest reference hospital). Higher →
    longer delays to isolation → higher latent-transmission probability.
 4. conflict_access_score (1-5 ordinal; 5 = no access). Higher → harder
    contact-tracing and surveillance → larger visibility-adjustment.

Edge-weight composition: a multiplicative factor in [0.1, 10.0] derived
from a gravity-style log-population product, road-index product, and
healthcare-distance and conflict-access modifiers. The factor is clamped
to [0.1, 10.0] to bound the propagated hazard and prevent any single
covariate from saturating Module E output. The gravity-model family is
literature-supported, but these exact exponents/normalizers are transparent
engineering heuristics until fitted; see evidence chain
ec:lovs:module-d:corridor-gravity-exponents:2026-05-21.

This is a stdlib-only Stage Two primitive; the covariate values
themselves are loaded from a JSON file shipped under ``data/``. Per-row
derivation rationale is captured in the JSON's ``derivation_notes`` field
for each geography.

Stdlib only. Deterministic.
"""
from __future__ import annotations

import dataclasses
import json
import math
import pathlib


MODEL_VERSION = "lovs_covariates-v0.1.0"

# Edge-weight clamps. The Stage One default is 1.0 (no T3 information); the
# Stage Two factor multiplies this. We bound the multiplier to prevent any
# single covariate from saturating Module E hazard estimates.
EDGE_WEIGHT_MIN = 0.1
EDGE_WEIGHT_MAX = 10.0

# Ordinal range for road_connectivity_index and conflict_access_score.
ORDINAL_MIN = 1
ORDINAL_MAX = 5


@dataclasses.dataclass(frozen=True)
class GeographyCovariates:
    """Per-geography T3 covariate row."""

    geography_id: str
    population_density: float
    road_connectivity_index: int
    healthcare_distance_km: float
    conflict_access_score: int
    derivation_notes: str

    def __post_init__(self) -> None:
        if self.population_density < 0:
            raise ValueError(
                f"GeographyCovariates({self.geography_id}): population_density must be >= 0; "
                f"got {self.population_density}"
            )
        if not (ORDINAL_MIN <= self.road_connectivity_index <= ORDINAL_MAX):
            raise ValueError(
                f"GeographyCovariates({self.geography_id}): road_connectivity_index must be in "
                f"[{ORDINAL_MIN}, {ORDINAL_MAX}]; got {self.road_connectivity_index}"
            )
        if self.healthcare_distance_km < 0:
            raise ValueError(
                f"GeographyCovariates({self.geography_id}): healthcare_distance_km must be >= 0; "
                f"got {self.healthcare_distance_km}"
            )
        if not (ORDINAL_MIN <= self.conflict_access_score <= ORDINAL_MAX):
            raise ValueError(
                f"GeographyCovariates({self.geography_id}): conflict_access_score must be in "
                f"[{ORDINAL_MIN}, {ORDINAL_MAX}]; got {self.conflict_access_score}"
            )


@dataclasses.dataclass(frozen=True)
class CovariateTable:
    """Loaded T3 covariate table; immutable."""

    geographies: dict[str, GeographyCovariates]
    source: str
    version: str

    def get(self, geography_id: str) -> GeographyCovariates | None:
        return self.geographies.get(geography_id)

    def edge_weight(
        self,
        source_geography_id: str,
        target_geography_id: str,
    ) -> float:
        """Return the multiplicative edge-weight modifier for a (source, target) pair.

        Decomposition:
         - log_pop_product = log(1 + pop_source) * log(1 + pop_target) / 100.0
           normalizes the gravity-model attractor term to roughly [0.1, 10.0]
           for realistic population scales.
         - road_factor = (road_index_source * road_index_target) / 25.0
           bounded to [0.04, 1.0]; ordinal 1-5 ranges.
         - healthcare_factor = max(0.5, log(1 + dist_source_km) / 5.0)
           shorter healthcare distance → less latent transmission → lower edge.
         - conflict_factor = (conflict_source / 5.0) * (1 + conflict_target / 10.0)
           higher conflict access score (less access) → higher hazard.

        Final factor: product of the four, clamped to [EDGE_WEIGHT_MIN, EDGE_WEIGHT_MAX].
        """
        source = self.geographies.get(source_geography_id)
        target = self.geographies.get(target_geography_id)
        if source is None or target is None:
            # Conservative fallback: no T3 information → no modulation.
            return 1.0

        log_pop_product = (
            math.log(1.0 + source.population_density)
            * math.log(1.0 + target.population_density)
            / 100.0
        )
        road_factor = (
            float(source.road_connectivity_index) * float(target.road_connectivity_index)
        ) / 25.0
        healthcare_factor = max(
            0.5, math.log(1.0 + source.healthcare_distance_km) / 5.0
        )
        conflict_factor = (
            (float(source.conflict_access_score) / 5.0)
            * (1.0 + float(target.conflict_access_score) / 10.0)
        )

        factor = log_pop_product * road_factor * healthcare_factor * conflict_factor
        return max(EDGE_WEIGHT_MIN, min(EDGE_WEIGHT_MAX, factor))


class CovariateLoadError(ValueError):
    """Raised when a covariate table cannot be loaded or validated."""


def _validate_payload_shape(payload: object, path: pathlib.Path) -> dict:
    if not isinstance(payload, dict):
        raise CovariateLoadError(f"load_covariates({path}): root must be JSON object, got {type(payload).__name__}")
    required = ("source", "version", "geographies")
    missing = [k for k in required if k not in payload]
    if missing:
        raise CovariateLoadError(
            f"load_covariates({path}): missing required keys: {missing}"
        )
    if not isinstance(payload["geographies"], list):
        raise CovariateLoadError(
            f"load_covariates({path}): 'geographies' must be a list, got {type(payload['geographies']).__name__}"
        )
    return payload


def load_covariates(path: pathlib.Path) -> CovariateTable:
    """Load a covariate table from a canonical JSON file.

    JSON schema:
    {
      "source": "<provenance for derivation>",
      "version": "<semver>",
      "geographies": [
        {
          "geography_id": "<id>",
          "population_density": <float>,
          "road_connectivity_index": <int 1-5>,
          "healthcare_distance_km": <float>,
          "conflict_access_score": <int 1-5>,
          "derivation_notes": "<cited source per row>"
        },
        ...
      ]
    }

    Raises CovariateLoadError on shape or value violations.
    """
    if not path.exists():
        raise CovariateLoadError(f"load_covariates: file not found at {path}")
    with open(path, "r", encoding="utf-8") as f:
        try:
            payload = json.load(f)
        except json.JSONDecodeError as e:
            raise CovariateLoadError(f"load_covariates({path}): JSON decode error: {e}") from e

    validated = _validate_payload_shape(payload, path)

    geographies: dict[str, GeographyCovariates] = {}
    for i, row in enumerate(validated["geographies"]):
        if not isinstance(row, dict):
            raise CovariateLoadError(
                f"load_covariates({path}): geographies[{i}] must be an object, got {type(row).__name__}"
            )
        try:
            geog = GeographyCovariates(
                geography_id=row["geography_id"],
                population_density=float(row["population_density"]),
                road_connectivity_index=int(row["road_connectivity_index"]),
                healthcare_distance_km=float(row["healthcare_distance_km"]),
                conflict_access_score=int(row["conflict_access_score"]),
                derivation_notes=str(row.get("derivation_notes", "")),
            )
        except (KeyError, TypeError, ValueError) as e:
            raise CovariateLoadError(
                f"load_covariates({path}): geographies[{i}] invalid: {e}"
            ) from e
        if geog.geography_id in geographies:
            raise CovariateLoadError(
                f"load_covariates({path}): duplicate geography_id: {geog.geography_id}"
            )
        geographies[geog.geography_id] = geog

    return CovariateTable(
        geographies=geographies,
        source=str(validated["source"]),
        version=str(validated["version"]),
    )
