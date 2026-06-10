"""LOVS Module D: transmission plausibility.

Produces a typed `TransmissionPlausibility` from an OutbreakSnapshot. Output
is a plausibility interval over latent active chains and two separated,
clearly-labeled generation summaries:

  - silent_generations  : generations BEFORE detection (back-calc from the
                          detection-era anchor count; fixed, historical).
  - elapsed_generations : total generations elapsed to date (back-calc from the
                          live confirmed count; grows over time).

Each summary is a median + 50/95 credible interval + an explicit censored
fraction, NOT a hidden-burden point estimate.

Two R's, two roles (this is the core of the v0.3 rebuild):
 - Back-projection R (`back_projection_r_gamma`, uncontrolled early phase,
   truncated to R>1): used to invert the observed count back to a putative
   index. The legacy single-R back-calc reused the effective-R-under-response
   prior here, which is mostly near/below 1 for BDBV Stage Two (mean 1.33);
   dividing a grown outbreak back down by a subcritical R diverges and pins the
   generation count to the cap at ANY cap (the degeneracy this rebuild fixes).
 - Effective R (`r_prior_gamma`, under response): used ONLY by the forward
   latent-chains simulation, which models current hidden chains where
   subcritical fizzle is the correct behavior.

Priors (cited as constants in lovs_priors_bundibugyo):
 - Serial interval, R (effective + back-projection), under-ascertainment,
   incubation. See TransmissionPriors and the Stage One / Stage Two constants.

Method: stochastic branching process Monte Carlo over n_trajectories=1000.
Each trajectory samples a back-projection R and an under-ascertainment, inverts
the branching process back from BOTH the live count and the detection-era anchor
to putative indices (the same realized scenario yields both depths), and
forward-simulates latent chains from the live count under the effective R.

Stdlib only. Deterministic when seeded.
"""
from __future__ import annotations

import dataclasses
import math
import random

from lovs import lovs_priors_bundibugyo
from lovs import lovs_reconciler


MODEL_VERSION = "lovs_transmission-v0.3.0"

# Stage One (Zaire-species) default priors. Preserved at module level for
# backward compatibility; Stage Two can override via the ``priors=`` argument
# to ``transmission_plausibility``.
SERIAL_INTERVAL_GAMMA = (4.0, 0.3)
R_PRIOR_GAMMA = (4.0, 2.0)
UNDER_ASCERTAINMENT_UNIFORM = (0.3, 0.9)
# Uncontrolled early-phase back-projection R (shared across species; see priors module).
BACK_PROJECTION_R_GAMMA = (4.0, 2.0)
BACK_PROJECTION_R_MIN = 1.0

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
        back_projection_r_gamma=BACK_PROJECTION_R_GAMMA,
        back_projection_r_min=BACK_PROJECTION_R_MIN,
    )

# Stage One constants.
N_TRAJECTORIES_DEFAULT = 1000
# Generation-count cap. Raised from the legacy 6 (a binary "3+?" bucket) to give
# headroom for the uncapped elapsed depth. With the back-projection R truncated to
# R>1 the back-calc terminates well below this in the normal range; the censored
# fraction is reported explicitly rather than hidden.
MAX_GENERATIONS = 24
# Forward latent-chains horizon. The forward sim models CURRENT hidden chains, a
# near-term look, NOT the full tree depth back to index. Bounded at the legacy
# cap-6 depth so latent_active_chains keeps its established semantics even though
# the back-calc generation count is now uncapped to 24.
_LATENT_CHAINS_FORWARD_HORIZON = 6


@dataclasses.dataclass(frozen=True)
class IntervalCount:
    lower_50: int
    upper_50: int
    lower_95: int
    upper_95: int


@dataclasses.dataclass(frozen=True)
class GenerationSummary:
    """Decision-useful summary of a generation-count posterior.

    median + 50/95 credible intervals, plus the explicit censored fraction (mass
    at the terminal MAX_GENERATIONS bin) so a degenerate distribution can never
    masquerade as a clean point estimate. ``anchor_confirmed`` records the
    observed count this depth was back-projected from (the detection-era anchor
    for the silent metric; the live count for the elapsed metric).
    """

    median: float
    ci_50: tuple[int, int]
    ci_95: tuple[int, int]
    censored_fraction: float
    anchor_confirmed: int


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
    # v0.3 separated, decision-useful summaries. ``elapsed_generations`` is the
    # accurately-named version of the legacy ``generations_before_detection``
    # histogram (which is retained as the elapsed per-bin distribution for
    # backward compatibility). ``silent_generations`` is populated only when a
    # detection-era anchor is supplied.
    silent_generations: "GenerationSummary | None" = None
    elapsed_generations: "GenerationSummary | None" = None


def _sample_gamma(rng: random.Random, alpha: float, beta: float) -> float:
    """Sample gamma with shape-rate parameterization."""
    return rng.gammavariate(alpha, 1.0 / beta)


def _sample_truncated_gamma(
    rng: random.Random, alpha: float, beta: float, r_min: float
) -> float:
    """Sample gamma(alpha, beta) (shape-rate) truncated to x > r_min via rejection.

    Bounded retry: the back-projection prior places ample mass above r_min=1.0,
    so rejection terminates quickly; the fallback returns the (clamped) prior mean
    on the (practically unreachable) exhaustion path. Deterministic given the rng.
    """
    for _ in range(64):
        x = rng.gammavariate(alpha, 1.0 / beta)
        if x > r_min:
            return x
    return max(r_min + 1e-6, alpha / beta)


def _generations_to_index(true_observed: float, r_back: float) -> int:
    """Invert the branching process: divide back by R until the expected count <= 1.

    With ``r_back > 1`` (the prior is truncated to the supercritical region) the
    expected count strictly decreases, so this terminates; MAX_GENERATIONS bounds
    it regardless. This is the step the legacy single-R back-calc got wrong:
    feeding a subcritical R (R<=1) made ``current`` grow, pinning every such
    trajectory to the cap.
    """
    current = true_observed
    gens = 0
    while current > 1.0 and gens < MAX_GENERATIONS:
        current = current / r_back
        gens += 1
    return gens


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


def _generation_summary(samples: list[int], anchor_confirmed: int) -> GenerationSummary:
    censored = sum(1 for g in samples if g >= MAX_GENERATIONS) / len(samples) if samples else 0.0
    return GenerationSummary(
        median=_quantile(samples, 0.5),
        ci_50=(int(round(_quantile(samples, 0.25))), int(round(_quantile(samples, 0.75)))),
        ci_95=(int(round(_quantile(samples, 0.025))), int(round(_quantile(samples, 0.975)))),
        censored_fraction=censored,
        anchor_confirmed=anchor_confirmed,
    )


def _simulate_trajectory(
    rng: random.Random,
    observed: int,
    priors: lovs_priors_bundibugyo.TransmissionPriors,
    anchor: int | None = None,
) -> tuple[int, int | None, int]:
    """Simulate one Monte Carlo scenario.

    Returns (generations_elapsed, generations_silent_or_None, latent_active_lineages).

    One realized scenario (a single back-projection R and under-ascertainment draw)
    yields BOTH depths: the elapsed depth back-projected from the live ``observed``
    count and, when ``anchor`` is given, the silent depth back-projected from the
    detection-era anchor count. Coupling them on the same R + ascertainment keeps the
    two metrics coherent (one scenario, two depths) rather than independent.

    The latent-chains forward simulation uses the effective-R prior (current
    under-response dynamics) over a bounded near-term horizon; a surviving lineage
    whose final case load stays below a fraction of the visible tip counts as one
    latent active lineage (it exists behind the visible counts without dominating).
    """
    r_back = _sample_truncated_gamma(
        rng, priors.back_projection_r_gamma[0], priors.back_projection_r_gamma[1], priors.back_projection_r_min
    )
    under_ascertainment = rng.uniform(*priors.under_ascertainment_uniform)
    true_observed = observed / under_ascertainment

    gens_elapsed = _generations_to_index(true_observed, r_back)
    gens_silent: int | None = None
    if anchor is not None and anchor > 0:
        gens_silent = _generations_to_index(anchor / under_ascertainment, r_back)

    # Forward latent-chains: effective-R under response, bounded near-term horizon.
    r_fwd = max(0.5, _sample_gamma(rng, *priors.r_prior_gamma))
    n_initial_seeds = max(1, _poisson(rng, 1.5))
    threshold = max(1.0, true_observed * 0.1)
    horizon = min(gens_elapsed, _LATENT_CHAINS_FORWARD_HORIZON)
    active_lineages = 0
    for _ in range(n_initial_seeds):
        case = 1
        alive = True
        for _ in range(horizon):
            offspring = _poisson(rng, r_fwd * case)
            if offspring == 0:
                alive = False
                break
            case = offspring
        if alive and case < threshold:
            active_lineages += 1
    return (gens_elapsed, gens_silent, active_lineages)


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
    detection_anchor_confirmed: int | None = None,
) -> TransmissionPlausibility:
    """Compute the transmission plausibility for a reconciled outbreak snapshot.

    Stage Two: pass ``priors=lovs_priors_bundibugyo.BUNDIBUGYO_PRIORS_STAGE_TWO``
    to use Bundibugyo-species-specific priors. The default (no priors argument)
    preserves Stage One Zaire-species behavior for backward compatibility.

    ``detection_anchor_confirmed``: the detection-era confirmed count (e.g. 10 as
    of 16 May 2026). When provided, the silent-generations-before-detection metric
    is computed from it (a fixed, historical quantity), separated from the
    total-generations-elapsed metric computed from the live count. When None, only
    the elapsed metric is produced (backward-compatible behavior).
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
    method_assumption = (
        "Generations-to-index uses an UNCONTROLLED early-phase back-projection R "
        "truncated to R>1 (not the effective-R-under-response, which is reserved for "
        "the forward latent-chains sim); silent (anchor) and elapsed (live) depths are "
        f"reported as median + 50/95 CI + censored_fraction over 1..{MAX_GENERATIONS} bins."
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
            silent_generations=None,
            elapsed_generations=None,
        )

    observed = confirmed.primary_value
    elapsed_counts: list[int] = []
    silent_counts: list[int] = []
    chain_counts: list[int] = []
    for _ in range(n_trajectories):
        gens_elapsed, gens_silent, chains = _simulate_trajectory(
            rng, observed, effective_priors, anchor=detection_anchor_confirmed
        )
        elapsed_counts.append(gens_elapsed)
        if gens_silent is not None:
            silent_counts.append(gens_silent)
        chain_counts.append(chains)

    # Legacy per-bin histogram (the ELAPSED distribution). Bins span 1..MAX_GENERATIONS;
    # the terminal bin is censored ("MAX or more"). Retained for backward compatibility;
    # the decision-useful summaries are elapsed_generations / silent_generations below.
    gen_dist: dict[int, float] = {i: 0.0 for i in range(1, MAX_GENERATIONS + 1)}
    for g in elapsed_counts:
        bucket = max(1, min(g, MAX_GENERATIONS))
        gen_dist[bucket] += 1
    for k in gen_dist:
        gen_dist[k] = gen_dist[k] / n_trajectories

    latent_chains = _interval_count(chain_counts)
    elapsed_summary = _generation_summary(elapsed_counts, observed)
    silent_summary = (
        _generation_summary(silent_counts, detection_anchor_confirmed)
        if silent_counts and detection_anchor_confirmed is not None
        else None
    )

    return TransmissionPlausibility(
        outbreak_id=snapshot.outbreak_id,
        geography_id=snapshot.affected_zones[0] if snapshot.affected_zones else "unknown",
        as_of=snapshot.as_of,
        latent_active_chains=latent_chains,
        generations_before_detection=gen_dist,
        priors_cited=effective_priors.citations,
        assumptions=(
            species_assumption,
            method_assumption,
            "Branching process is a Stage One simplification; constant per-trajectory R and "
            "full sequential Monte Carlo (time-varying R) is a Stage Two extension.",
            f"Generation bins span 1..{MAX_GENERATIONS}; the {MAX_GENERATIONS} bin is censored "
            f"({MAX_GENERATIONS} or more). censored_fraction reports the residual tail mass.",
        ),
        model_version=MODEL_VERSION,
        provenance_ids=snapshot.sources,
        status="provisional",
        silent_generations=silent_summary,
        elapsed_generations=elapsed_summary,
    )
