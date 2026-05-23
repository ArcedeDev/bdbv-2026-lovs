"""Validation for staged outbreak observations.

The source manifest archives immutable source bytes. This module validates the
thin staging layer that decides whether a sourced observation is eligible for a
future model run, context-only, or blocked pending official confirmation.
"""
from __future__ import annotations

from typing import Any


OFFICIAL_SOURCE_TIERS: frozenset[str] = frozenset({
    "official_who",
    "official_who_afro",
    "official_cdc",
    "official_africa_cdc",
    "official_continental_body",
    "national_moh",
    "regional_body",
    "laboratory",
    "academic_collab_who",
})

VALID_VALUE_KINDS: frozenset[str] = frozenset({
    "exact_int",
    "approx_int",
    "approx_text",
    "lower_bound",
    "range",
    "qualitative",
})

VALID_ADMISSIBILITY: frozenset[str] = frozenset({
    "model_eligible",
    "cross_check",
    "context_only",
    "blocked_pending_official_confirmation",
})

VALID_MODEL_USE: frozenset[str] = frozenset({
    "eligible_after_release",
    "cross_check_only",
    "context_only",
    "not_model_input",
})

REQUIRED_OBSERVATION_FIELDS: tuple[str, ...] = (
    "observation_id",
    "source_id",
    "source_url",
    "publisher",
    "source_tier",
    "published_at",
    "data_as_of",
    "retrieved_at",
    "metric",
    "case_status",
    "value",
    "value_kind",
    "location_scope",
    "granularity",
    "inclusion_basis",
    "exclusions",
    "claim_status",
    "admissibility",
    "model_use",
    "conflicts_with",
)

REQUIRED_WATCH_FIELDS: tuple[str, ...] = (
    "signal_id",
    "reported_at",
    "publisher",
    "claim",
    "source_chain",
    "source_urls",
    "geography",
    "claim_status",
    "confidence_tier",
    "model_use",
    "credibility_assessment",
    "source_chase",
    "evidence_ref",
    "promotion_criteria",
)


def validate_staged_observations(
    payload: dict[str, Any],
    manifest_source_ids: set[str] | None = None,
) -> list[str]:
    """Return validation gaps for ``payload['staged_observations']``.

    The function is intentionally side-effect free so release tooling can call
    it before any snapshot generation.
    """
    gaps: list[str] = []
    observations = payload.get("staged_observations", [])
    if not isinstance(observations, list):
        return ["staged_observations must be a list"]

    seen_ids: set[str] = set()
    official_keys: set[tuple[str, str]] = set()
    for obs in observations:
        if not isinstance(obs, dict):
            gaps.append("staged_observations entries must be objects")
            continue
        obs_id = str(obs.get("observation_id", ""))
        if obs_id in seen_ids:
            gaps.append(f"duplicate staged observation_id {obs_id!r}")
        seen_ids.add(obs_id)
        for field in REQUIRED_OBSERVATION_FIELDS:
            if field not in obs:
                gaps.append(f"{obs_id or '<missing id>'}: missing {field}")
        metric = str(obs.get("metric", ""))
        data_as_of = str(obs.get("data_as_of", ""))
        if obs.get("source_tier") in OFFICIAL_SOURCE_TIERS:
            official_keys.add((metric, data_as_of))

    for obs in observations:
        if not isinstance(obs, dict):
            continue
        obs_id = str(obs.get("observation_id", "<missing id>"))
        source_id = str(obs.get("source_id", ""))
        if manifest_source_ids is not None and source_id and source_id not in manifest_source_ids:
            gaps.append(f"{obs_id}: source_id {source_id!r} is not in manifest")
        value_kind = obs.get("value_kind")
        if value_kind not in VALID_VALUE_KINDS:
            gaps.append(f"{obs_id}: invalid value_kind {value_kind!r}")
        admissibility = obs.get("admissibility")
        if admissibility not in VALID_ADMISSIBILITY:
            gaps.append(f"{obs_id}: invalid admissibility {admissibility!r}")
        model_use = obs.get("model_use")
        if model_use not in VALID_MODEL_USE:
            gaps.append(f"{obs_id}: invalid model_use {model_use!r}")
        if value_kind == "approx_text" and admissibility == "model_eligible":
            gaps.append(f"{obs_id}: approx_text cannot be model_eligible")
        if obs.get("source_tier") == "aggregator":
            key = (str(obs.get("metric", "")), str(obs.get("data_as_of", "")))
            if key in official_keys and admissibility == "model_eligible":
                gaps.append(f"{obs_id}: aggregator cannot be model_eligible when official same-day metric exists")
        location_scope = obs.get("location_scope")
        if not isinstance(location_scope, dict) or not location_scope.get("scope_type"):
            gaps.append(f"{obs_id}: location_scope must declare scope_type")
        if obs.get("claim_status") == "deconfirmed" and not obs.get("exclusions"):
            gaps.append(f"{obs_id}: deconfirmed observation must list exclusions")
        if location_scope and location_scope.get("scope_type") in {"evacuated_case", "exported_case"}:
            if model_use != "context_only":
                gaps.append(f"{obs_id}: exported/evacuated cases must be context_only")
    return gaps


def validate_watch_signals(payload: dict[str, Any]) -> list[str]:
    """Return validation gaps for a non-model watch-signal artifact."""
    gaps: list[str] = []
    signals = payload.get("watch_signals", [])
    if not isinstance(signals, list):
        return ["watch_signals must be a list"]
    seen_ids: set[str] = set()
    for signal in signals:
        if not isinstance(signal, dict):
            gaps.append("watch_signals entries must be objects")
            continue
        signal_id = str(signal.get("signal_id", ""))
        if signal_id in seen_ids:
            gaps.append(f"duplicate watch signal_id {signal_id!r}")
        seen_ids.add(signal_id)
        for field in REQUIRED_WATCH_FIELDS:
            if field not in signal:
                gaps.append(f"{signal_id or '<missing id>'}: missing {field}")
        if signal.get("model_use") != "not_model_input":
            gaps.append(f"{signal_id or '<missing id>'}: watch signals must not be model inputs")
        if signal.get("claim_status") == "confirmed":
            gaps.append(f"{signal_id or '<missing id>'}: confirmed claims belong in staged_observations, not watch_signals")
        source_urls = signal.get("source_urls")
        if not isinstance(source_urls, list) or not source_urls:
            gaps.append(f"{signal_id or '<missing id>'}: watch signal must carry source_urls")
        if not isinstance(signal.get("credibility_assessment"), dict):
            gaps.append(f"{signal_id or '<missing id>'}: watch signal must carry credibility_assessment")
        if not isinstance(signal.get("source_chase"), dict):
            gaps.append(f"{signal_id or '<missing id>'}: watch signal must carry source_chase")
        evidence_ref = str(signal.get("evidence_ref", ""))
        if not evidence_ref.startswith("ec:lovs:"):
            gaps.append(f"{signal_id or '<missing id>'}: evidence_ref must point to a LOVS evidence chain")
        promotion_criteria = str(signal.get("promotion_criteria", "")).lower()
        if "official" not in promotion_criteria and "public-health" not in promotion_criteria:
            gaps.append(f"{signal_id or '<missing id>'}: promotion_criteria must name an official/public-health promotion gate")
    return gaps
