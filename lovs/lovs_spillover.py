"""LOVS Module F: spillover and forest/mining interface plausibility.

Qualitative classification of suspected origin geography into interface
categories (forest, mining, hunting, health-facility-amplification, burial,
cross-border-care-seeking). No quantitative claim about the index event;
"unknown index" state is the safe default.

Method: keyword pattern matching on T1 source narrative text (extracted
from ArchivedSnapshot.normalized_content) plus historical-filovirus
spillover priors from Leroy 2005 (DOI 10.1038/nature04134) and Pigott 2014
(DOI 10.7554/eLife.04395). The classification is deliberately permissive:
multiple categories can co-apply.

Stdlib only. Deterministic.
"""
from __future__ import annotations

import dataclasses

from lovs import lovs_archive


MODEL_VERSION = "lovs_spillover-v0.1.0"

PRIOR_CITATIONS: tuple[str, ...] = (
    "Leroy EM, et al. Nature 2005 (10.1038/nature04134): fruit bats as reservoir hosts of Ebola",
    "Pigott DM, et al. eLife 2014 (10.7554/eLife.04395): mapping the zoonotic niche of Ebola in Africa",
    "Wamala JF, et al. EID 2010 (10.3201/eid1607.091525): Bundibugyo-species spillover at the forest-village interface",
)

INTERFACE_CATEGORIES: tuple[str, ...] = (
    "forest",
    "mining",
    "hunting",
    "health_facility_amplification",
    "burial",
    "cross_border_care_seeking",
)

# Keyword patterns per category. Sourced from typical WHO DON, MoH, and
# Africa CDC narrative vocabulary.
_PATTERNS: dict[str, tuple[str, ...]] = {
    "forest": (
        "forest", "rainforest", "deforestation", "bushmeat",
        "fruit bat", "primates", "wildlife",
    ),
    "mining": (
        "mining", "miner", "artisanal mining", "cave", "gold extraction",
    ),
    "hunting": (
        "hunting", "hunter", "bushmeat", "wildlife harvest",
    ),
    "health_facility_amplification": (
        "nosocomial", "healthcare worker", "health-care worker", "hcw",
        "facility outbreak", "ward transmission",
    ),
    "burial": (
        "burial", "funeral", "safe and dignified burial", "sdb",
        "mourning", "wake",
    ),
    "cross_border_care_seeking": (
        "cross-border", "border crossing", "imported case", "imported cases",
        "travel history",
    ),
}


@dataclasses.dataclass(frozen=True)
class SpilloverNarrative:
    outbreak_id: str
    as_of: str
    possible_interface_categories: tuple[str, ...]
    origin_uncertainty: str
    narrative_text: str
    source_excerpts: tuple[str, ...]
    provenance_ids: tuple[str, ...]
    model_version: str


def _extract_narrative_text(
    snapshots: tuple[lovs_archive.ArchivedSnapshot, ...],
) -> tuple[str, tuple[str, ...]]:
    """Collect narrative text from T1 sources.

    Returns (concatenated text, source-id list). Pulls from any string-valued
    fields in normalized_content; the fixture seeder includes narrative-bearing
    fields like 'declaration_text' and 'summary'.
    """
    excerpts: list[str] = []
    source_ids: list[str] = []
    for snap in snapshots:
        text_fragments: list[str] = []
        for key, value in snap.normalized_content.items():
            if isinstance(value, str):
                text_fragments.append(value)
        if text_fragments:
            joined = " ".join(text_fragments)
            excerpts.append(joined)
            source_ids.append(snap.provenance.source_id)
    return (" ".join(excerpts).lower(), tuple(source_ids))


def _classify_categories(text: str) -> tuple[str, ...]:
    matched: list[str] = []
    for category in INTERFACE_CATEGORIES:
        for pattern in _PATTERNS[category]:
            if pattern.lower() in text:
                matched.append(category)
                break
    return tuple(matched)


def spillover_narrative(
    archive: lovs_archive.Archive,
    outbreak_id: str,
    as_of: str,
) -> SpilloverNarrative:
    """Produce a qualitative spillover narrative.

    No quantitative claim about the index event. Empty categories are the
    safe default ("unknown index").
    """
    snapshots = lovs_archive.query_as_of(archive, outbreak_id, as_of)
    text, source_ids = _extract_narrative_text(snapshots)
    categories = _classify_categories(text)
    if categories:
        narrative = (
            f"Source narrative consistent with possible interface categor"
            f"{'ies' if len(categories) > 1 else 'y'}: {', '.join(categories)}. "
            f"No quantitative claim about the index event; this is a permissive "
            f"classification grounded in WHO/MoH/Africa CDC source text patterns."
        )
        origin_uncertainty = (
            "Possible interface candidates identified by keyword pattern; "
            "no authoritative attribution. Awaiting field investigation."
        )
    else:
        narrative = (
            "No interface category matched the available T1 narrative text. "
            "Origin remains in the 'unknown index' state."
        )
        origin_uncertainty = "Unknown index. No authoritative attribution available in current sources."

    excerpts: list[str] = []
    for snap in snapshots:
        for key, value in snap.normalized_content.items():
            if isinstance(value, str) and any(
                pat in value.lower()
                for cat_patterns in _PATTERNS.values()
                for pat in cat_patterns
            ):
                excerpts.append(f"{snap.provenance.source_id}: {value[:200]}")
                break

    return SpilloverNarrative(
        outbreak_id=outbreak_id,
        as_of=as_of,
        possible_interface_categories=categories,
        origin_uncertainty=origin_uncertainty,
        narrative_text=narrative,
        source_excerpts=tuple(excerpts),
        provenance_ids=source_ids,
        model_version=MODEL_VERSION,
    )
