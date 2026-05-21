"""LOVS Module D: transmission plausibility.

Produces a typed `TransmissionPlausibility` from an OutbreakSnapshot. Output
is a plausibility interval over latent active chains and a probability
distribution over generations-before-detection, NOT a hidden-burden point
estimate.

Priors (cited as constants):
 - Serial interval: gamma(α=4.0, β=0.3), mean ~13.3 days, sd ~6.7 days.
   Faye O, et al. Lancet ID 2015, DOI 10.1016/S1473-3099(14)71075-8 (mean 11.6 d).
   WHO Ebola Response Team. NEJM 2014, DOI 10.1056/NEJMoa1411100 (mean 15.3 d).
   The Stage One prior bridges both estimates.
 - R: gamma(α=4.0, β=2.0), mean ~2.0, sd ~1.0.
   WHO Ebola Response Team 2014 NEJM Table 1 reports early R₀ between
   1.5 and 2.0 across the three West African countries.
 - Under-ascertainment: uniform(0.3, 0.9) matching Module C reporting
   completeness range.

Method: stochastic branching process Monte Carlo over n_trajectories=1000.
Each trajectory samples R per generation from the prior, simulates a tree
back from the observed confirmed-case count to a putative index, and
counts generations and latent active chains.

Stdlib only. Deterministic when seeded.
"""
from __future__ import annotations

import dataclasses
import math
import random

from lovs import lovs_priors_bundibugyo
from lovs import lovs_reconciler


MODEL_VERSION = "lovs_transmission-v0.2.0"

# Stage One (Zaire-species) default priors. Preserved at module level for
# backward compatibility; Stage Two can override via the ``priors=`` argument
# to ``transmission_plausibility``.
SERIAL_INTERVAL_GAMMA = (4.0, 0.3)
R_PRIOR_GAMMA = (4.0, 2.0)
UNDER_ASCERTAINMENT_UNIFORM = (0.3, 0.9)

PRIOR_CITATIONS: tuple[str, ...] = (
    "Faye O, et al. Lancet ID 2015 (10.1016/S1473-3099(14)71075-8): serial interval mean 11.6 d (8.4-15.6)",
    "WHO Ebola Response Team. NEJM 2014 (10.1056/NEJMoa1411100): serial interval mean 15.3 d (13.5-17.1); early R between 1.5 and 2.0",
    "Wamala JF, et al. EID 2010 (10.3201/eid1607.091525): Bundibugyo-species transferability evidence",
)


def _default_priors() -> lovs_priors_bundibugyo.TransmissionPriors:
    """Resolve the Stage One Zaire-species default priors from module constants.

    Distinct from ``lovs_priors_bundibugyo.ZAIRE_PRIORS_STAGE_ONE`` to keep the
    Stage One module-level constants as the source of truth for the default
    code path; the priors module provides named opt-in alternatives.
    """
    return lovs_priors_bundibugyo.TransmissionPriors(
        serial_interval_gamma=SERIAL_INTERVAL_GAMMA,
        r_prior_gamma=R_PRIOR_GAMMA,
        under_ascertainment_uniform=UNDER_ASCERTAINMENT_UNIFORM,
        incubation_gamma=(4.0, 0.4),
        citations=PRIOR_CITATIONS,
        species="EBOV-Z",
        notes=(
            "Stage One baseline; Zaire-species priors transferred to Bundibugyo "
            "per Stage One assumption #3 (Wamala 2010 transferability).",
        ),
        version=MODEL_VERSION,
    )

# Stage One constants.
N_TRAJECTORIES_DEFAULT = 1000
MAX_GENERATIONS = 6  # truncate generation count at 6 (3+ is the qualitative bucket)


@dataclasses.dataclass(frozen=True)
class IntervalCount:
    lower_50: int
    upper_50: int
    lower_95: int
    upper_95: int


@dataclasses.dataclass(frozen=True)
class TransmissionPlausibility:
    outbreak_id: str
    geography_id: str
    as_of: str
    latent_active_chains: IntervalCount
    generations_before_detection: dict[int, float]
    priors_cited: tuple[str, ...]
    assumptions: tuple[str, ...]
    model_version: str
    provenance_ids: tuple[str, ...]
    status: str


def _sample_gamma(rng: random.Random, alpha: float, beta: float) -> float:
    """Sample gamma with shape-rate parameterization."""
    return rng.gammavariate(alpha, 1.0 / beta)


def _quantile(samples: list[int], q: float) -> float:
    if not samples:
        return float("nan")
    s = sorted(samples)
    idx = q * (len(s) - 1)
    lo = int(math.floor(idx))
    hi = int(math.ceil(idx))
    if lo == hi:
        return float(s[lo])
    return s[lo] + (s[hi] - s[lo]) * (idx - lo)


def _interval_count(samples: list[int]) -> IntervalCount:
    return IntervalCount(
        lower_50=int(round(_quantile(samples, 0.25))),
        upper_50=int(round(_quantile(samples, 0.75))),
        lower_95=int(round(_quantile(samples, 0.025))),
        upper_95=int(round(_quantile(samples, 0.975))),
    )


def _simulate_back_to_index(
    rng: random.Random,
    observed: int,
    priors: lovs_priors_bundibugyo.TransmissionPriors,
) -> tuple[int, int]:
    """Simulate one trajectory back to a putative index event.

    Returns (generations_to_index, latent_active_lineages).

    Strategy: starting from `observed` confirmed cases (the visible tip),
    invert the branching process to estimate generations-to-index. Then
    forward-simulate multiple INDEPENDENT lineages from a multi-seed index
    (the index is rarely truly singular; multiple introductions or parallel
    transmission chains are common in filovirus outbreaks per Faye 2015
    contact-tracing evidence in early Conakry).

    A lineage is counted as "latent active" if it stays alive through all
    `gens` generations AND its final case load is below a fraction of the
    true observed tip (it exists behind the visible counts without
    dominating them).

    Stage Two: ``priors`` carries the species-appropriate R prior and
    under-ascertainment range. Stage One callers receive the default
    Zaire-species priors via ``_default_priors()``.
    """
    R = _sample_gamma(rng, *priors.r_prior_gamma)
    R = max(0.5, R)  # avoid degenerate sub-critical scenarios
    under_ascertainment = rng.uniform(*priors.under_ascertainment_uniform)
    true_observed = observed / under_ascertainment

    # Generations-to-index: divide back by R until expected count <= 1.
    current = true_observed
    gens = 0
    while current > 1.0 and gens < MAX_GENERATIONS:
        current = current / R
        gens += 1

    # Multi-seed index: typically 1-3 introductions or parallel chains.
    # Small Poisson around 1.5; Stage One default.
    n_initial_seeds = max(1, _poisson(rng, 1.5))

    # Forward-simulate each lineage independently. Each generation the
    # lineage's case load is Poisson(R * previous_case_load); offspring=0
    # kills the lineage. A surviving lineage with final case load below the
    # latent threshold counts as one latent active lineage.
    threshold = max(1.0, true_observed * 0.1)
    active_lineages = 0
    for _ in range(n_initial_seeds):
        case = 1
        alive = True
        for _ in range(gens):
            offspring = _poisson(rng, R * case)
            if offspring == 0:
                alive = False
                break
            case = offspring
        if alive and case < threshold:
            active_lineages += 1
    return (gens, active_lineages)


def _poisson(rng: random.Random, lam: float) -> int:
    """Knuth Poisson sampler. OK for small lambda."""
    if lam <= 0:
        return 0
    if lam < 30:
        L = math.exp(-lam)
        k = 0
        p = 1.0
        while True:
            k += 1
            p *= rng.random()
            if p <= L:
                return k - 1
    # Normal approximation for large lambda.
    return max(0, int(round(rng.gauss(lam, math.sqrt(lam)))))


def transmission_plausibility(
    snapshot: lovs_reconciler.OutbreakSnapshot,
    n_trajectories: int = N_TRAJECTORIES_DEFAULT,
    seed: int | None = None,
    priors: lovs_priors_bundibugyo.TransmissionPriors | None = None,
) -> TransmissionPlausibility:
    """Compute the transmission plausibility for a reconciled outbreak snapshot.

    Stage Two: pass ``priors=lovs_priors_bundibugyo.BUNDIBUGYO_PRIORS_STAGE_TWO``
    to use Bundibugyo-species-specific priors. The default (no priors argument)
    preserves Stage One Zaire-species behavior for backward compatibility.
    """
    if seed is None:
        seed = lovs_reconciler.snapshot_content_seed(snapshot)
    rng = random.Random(seed)

    effective_priors = priors if priors is not None else _default_priors()
    is_default_priors = priors is None
    species_assumption = (
        "Bundibugyo-species priors transferred from Zaire-species literature; "
        "see Wamala 2010 for transferability evidence."
        if is_default_priors
        else f"Stage Two: {effective_priors.species}-species-specific priors "
        f"applied per ``priors=`` override; see priors_cited for derivation."
    )

    confirmed = snapshot.reported_counts.get("confirmed")
    if confirmed is None or confirmed.primary_value <= 0:
        # Not enough signal; return a degenerate plausibility.
        empty_chains = IntervalCount(0, 0, 0, 0)
        return TransmissionPlausibility(
            outbreak_id=snapshot.outbreak_id,
            geography_id=snapshot.affected_zones[0] if snapshot.affected_zones else "unknown",
            as_of=snapshot.as_of,
            latent_active_chains=empty_chains,
            generations_before_detection={i: 0.0 for i in range(1, MAX_GENERATIONS + 1)},
            priors_cited=effective_priors.citations,
            assumptions=(
                species_assumption,
                "Branching process is a Stage One simplification; full sequential Monte Carlo "
                "is a Stage Two extension.",
                "Insufficient confirmed-case signal: degenerate plausibility.",
            ),
            model_version=MODEL_VERSION,
            provenance_ids=snapshot.sources,
            status="provisional",
        )

    observed = confirmed.primary_value
    generation_counts: list[int] = []
    chain_counts: list[int] = []
    for _ in range(n_trajectories):
        gens, chains = _simulate_back_to_index(rng, observed, effective_priors)
        generation_counts.append(gens)
        chain_counts.append(chains)

    # Distribution over generations-before-detection. Bins span 1..MAX_GENERATIONS
    # so that the visual can render the full censored posterior. The terminal bin
    # (key == MAX_GENERATIONS) is interpreted as "MAX_GENERATIONS or more"; the
    # branching-back-to-index simulator caps gens at MAX_GENERATIONS by
    # construction, so this is the censored upper bin.
    gen_dist: dict[int, float] = {i: 0.0 for i in range(1, MAX_GENERATIONS + 1)}
    for g in generation_counts:
        bucket = max(1, min(g, MAX_GENERATIONS))
        gen_dist[bucket] += 1
    for k in gen_dist:
        gen_dist[k] = gen_dist[k] / n_trajectories

    latent_chains = _interval_count(chain_counts)

    return TransmissionPlausibility(
        outbreak_id=snapshot.outbreak_id,
        geography_id=snapshot.affected_zones[0] if snapshot.affected_zones else "unknown",
        as_of=snapshot.as_of,
        latent_active_chains=latent_chains,
        generations_before_detection=gen_dist,
        priors_cited=effective_priors.citations,
        assumptions=(
            species_assumption,
            "Branching process is a Stage One simplification; full sequential Monte Carlo "
            "is a Stage Two extension.",
            f"Generations-before-detection bins span 1..{MAX_GENERATIONS}; the {MAX_GENERATIONS} "
            f"bin is censored ({MAX_GENERATIONS} or more), since the back-to-index simulator caps "
            f"at {MAX_GENERATIONS} generations.",
        ),
        model_version=MODEL_VERSION,
        provenance_ids=snapshot.sources,
        status="provisional",
    )
