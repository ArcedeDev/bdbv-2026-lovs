"""LOVS Stage Two: Bundibugyo-species-specific transmission priors.

Replaces the Stage One Zaire-derived priors for Module D when the user opts
in via the ``priors=`` argument to ``transmission_plausibility``. Stage One
default behavior is preserved (Zaire-derived priors carried as a named
constant ``ZAIRE_PRIORS_STAGE_ONE`` for backward-compatibility tests).

Anchors:
 - Wamala JF, et al. EID 2010 (10.3201/eid1607.091525). Discovery outbreak,
   Uganda 2007-2008. 116 confirmed-or-probable cases, 39 deaths (CFR 34%).
   Median incubation 7 d (range 2-20); transmission cycle 6 weeks with
   inter-case interval 3-11 d.
 - MacNeil A, et al. EID 2010 (10.3201/eid1612.100627). Clinical features.
   Mean incubation 6.3 d (n=24); survivors 5.7 d, fatal cases 7.4 d.
   Bleeding manifestation 54% prevalence among confirmed cases.
 - Albariño CG, et al. Genetic study of the 2012 DRC Isiro BDBV cluster
   (consistency check; same species).

Method-of-derivation notes for the R0 prior gamma(α=4.0, β=3.0), mean ~1.33:
 - Wamala 2010 cluster: 116 cases over ~6 weeks of active transmission with
   serial interval ~7 d implies ~6 transmission generations. An empirical
   R0 from cluster-size-and-generations alone would imply
   116^(1/6) ≈ 2.21, but this overstates effective R because superspreading,
   isolation, and within-household saturation truncate the chain.
 - Direct BDBV R0 estimates were not located in the 2026-05-20 grounding
   audit. Van Kerkhove et al. 2015 explicitly states that R0 had not been
   estimated for Ebola Bundibugyo. This prior is therefore an interim modeling
   prior, not a primary-source-grounded BDBV R0 estimate.
 - gamma(α=4.0, β=3.0) gives mean = 4/3 ≈ 1.33 and sd ≈ 0.67. The prior
   places ~14% mass below 1.0 (epidemic threshold; modest under-criticality
   probability), ~50% mass in [1.0, 1.5] (epidemic but slow), and ~16% mass
   above 2.0 (Zaire-species crossover plausibility).

Stage Two opt-in pattern:
 - Pass ``priors=BUNDIBUGYO_PRIORS_STAGE_TWO`` to ``transmission_plausibility``.
 - Default (no priors argument) preserves Stage One Zaire-derived behavior.

Stdlib only. No clock, no network, no randomness in module load.
"""
from __future__ import annotations

import dataclasses


MODEL_VERSION = "lovs_priors_bundibugyo-v0.1.0"


@dataclasses.dataclass(frozen=True)
class TransmissionPriors:
    """Frozen container for the four priors that Module D consumes.

    All gamma priors are parameterized as (alpha, beta) in shape-rate form,
    consistent with ``random.Random.gammavariate`` after the 1/beta scale
    conversion in module helpers.
    """

    serial_interval_gamma: tuple[float, float]
    r_prior_gamma: tuple[float, float]
    under_ascertainment_uniform: tuple[float, float]
    incubation_gamma: tuple[float, float]
    citations: tuple[str, ...]
    species: str
    notes: tuple[str, ...]
    version: str
    evidence_chain_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name, val in (
            ("serial_interval_gamma", self.serial_interval_gamma),
            ("r_prior_gamma", self.r_prior_gamma),
            ("incubation_gamma", self.incubation_gamma),
        ):
            if len(val) != 2 or val[0] <= 0 or val[1] <= 0:
                raise ValueError(
                    f"TransmissionPriors.{name}: gamma (alpha, beta) requires both positive; got {val}"
                )
        lo, hi = self.under_ascertainment_uniform
        if not (0.0 <= lo < hi <= 1.0):
            raise ValueError(
                f"TransmissionPriors.under_ascertainment_uniform: requires 0 <= lo < hi <= 1; got {(lo, hi)}"
            )
        if not self.citations:
            raise ValueError("TransmissionPriors.citations: must be non-empty")
        if not self.species:
            raise ValueError("TransmissionPriors.species: must be non-empty")
        for chain_id in self.evidence_chain_ids:
            if not chain_id.startswith("ec:"):
                raise ValueError(
                    f"TransmissionPriors.evidence_chain_ids: expected 'ec:' prefix, got {chain_id!r}"
                )


# Stage Two Bundibugyo-species prior.
#
# Serial interval gamma(4.0, 0.55) gives mean ~7.27 d, sd ~3.64 d.
# Bracket-matches Wamala 2010 inter-case interval 3-11 d.
#
# R prior gamma(4.0, 3.0) gives mean ~1.33, sd ~0.67. Per derivation above.
#
# Under-ascertainment uniform(0.3, 0.9) preserved from Stage One; Wamala 2010
# offers no strong evidence for a different range, and the WA 2014 sensitivity
# of Module C suggests this range is appropriate for early-outbreak surveillance
# in low-resource settings.
#
# Incubation gamma(4.0, 0.6) gives mean ~6.67 d, sd ~3.33 d. MacNeil 2010 mean
# 6.3 d (n=24); Wamala 2010 median 7 d (range 2-20). Stage Two adopts the
# MacNeil-anchored mean with broad sd consistent with the Wamala range.
BUNDIBUGYO_PRIORS_STAGE_TWO = TransmissionPriors(
    serial_interval_gamma=(4.0, 0.55),
    r_prior_gamma=(4.0, 3.0),
    under_ascertainment_uniform=(0.3, 0.9),
    incubation_gamma=(4.0, 0.6),
    citations=(
        "Wamala JF, et al. EID 2010 (10.3201/eid1607.091525): "
        "Bundibugyo virus discovery outbreak, Uganda 2007-2008; "
        "116 cases, CFR 34%, median incubation 7d (2-20), "
        "transmission cycle 6 weeks, serial interval 3-11d.",
        "MacNeil A, et al. EID 2010 (10.3201/eid1612.100627): "
        "BDBV clinical features; mean incubation 6.3d (n=24); "
        "survivors 5.7d, fatal cases 7.4d; bleeding prevalence 54%.",
        "Albariño CG, et al. Virology 2013 (10.1016/j.virol.2013.05.001): "
        "2012 DRC Isiro BDBV cluster genetic characterization; "
        "consistency check for species-stable transmission dynamics.",
        "Van Kerkhove MD, et al. Scientific Data 2015 (10.1038/sdata.2015.19): "
        "BDBV R0 evidence gap; review states BDBV R0 had not been estimated.",
    ),
    species="BDBV",
    notes=(
        "R0 prior derivation: Wamala 2010 cluster size 116 over ~6 generations "
        "would imply empirical R0 ~2.2 if multiplicative, but cluster truncation "
        "by isolation and saturation makes effective R0 lower. No direct BDBV "
        "R0 source was located in the 2026-05-20 grounding audit; gamma(4.0, 3.0) "
        "is retained as an interim modeling prior and tracked by evidence chain.",
        "Serial interval prior derivation: Wamala 2010 reports 3-11 day inter-case "
        "intervals with 6-week transmission cycles; gamma(4.0, 0.55) gives mean "
        "7.27d sd 3.64d, bracket-matching the reported range.",
        "Incubation prior derivation: MacNeil 2010 mean 6.3d (n=24) anchors the "
        "mean; gamma(4.0, 0.6) gives mean 6.67d sd 3.33d, consistent with "
        "Wamala 2010 range 2-20d.",
        "Stage One Zaire-species priors preserved as ZAIRE_PRIORS_STAGE_ONE for "
        "backward-compatibility tests and side-by-side comparison.",
    ),
    version=MODEL_VERSION,
    evidence_chain_ids=(
        "ec:lovs:module-d:bdbv-r-prior-gamma:2026-05-20",
        "ec:lovs:module-b:detection-depth-priors:2026-05-21",
    ),
)


# Stage One baseline: Zaire-species priors. Preserved for backward
# compatibility. When passed to ``transmission_plausibility(priors=...)``,
# produces identical output to the Stage One default behavior.
ZAIRE_PRIORS_STAGE_ONE = TransmissionPriors(
    serial_interval_gamma=(4.0, 0.3),
    r_prior_gamma=(4.0, 2.0),
    under_ascertainment_uniform=(0.3, 0.9),
    incubation_gamma=(4.0, 0.4),  # mean 10 d, sd 5 d; broadly consistent with Zaire 8-12 d
    citations=(
        "Faye O, et al. Lancet ID 2015 (10.1016/S1473-3099(14)71075-8): "
        "Zaire-species serial interval mean 11.6d (8.4-15.6).",
        "WHO Ebola Response Team. NEJM 2014 (10.1056/NEJMoa1411100): "
        "Zaire-species serial interval mean 15.3d (13.5-17.1); early R "
        "between 1.5 and 2.0.",
        "Wamala JF, et al. EID 2010 (10.3201/eid1607.091525): "
        "Bundibugyo-species transferability evidence (Stage One assumption).",
    ),
    species="EBOV-Z",
    notes=(
        "Stage One baseline; transferred to Bundibugyo per Stage One assumption #3.",
        "Use ``BUNDIBUGYO_PRIORS_STAGE_TWO`` for species-specific Stage Two work.",
    ),
    version=MODEL_VERSION,
)
