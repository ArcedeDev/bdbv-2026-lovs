"""Schedule policy helpers for recurring BDBV source-prep checks.

The schedule is a read-only freshness/prep layer. It chooses which registered
sources to check in each UTC slot; it does not decide whether any detected
source can enter a released snapshot.
"""
from __future__ import annotations

from typing import Any


class SourceScheduleError(ValueError):
    """Raised when the source-prep schedule policy is not internally coherent."""


COUNT_FEEDS = {"counts", "case_counts", "deaths", "geography", "corridors"}
OFFICIAL_TIERS = {
    "national_moh",
    "official_who",
    "official_who_afro",
    "official_continental_body",
}
REGIONAL_TIERS = {"regional_body", "official_cdc"}
COVARIATE_TARGETS = {
    "poe_restricted_json",
    "external_covariate_metadata",
    "geospatial_context_metadata",
}


def schedule_group(source: dict[str, Any]) -> str:
    """Infer the operational schedule group from the registered source role."""
    if isinstance(source.get("schedule_group"), str) and source["schedule_group"].strip():
        return source["schedule_group"]

    feeds = set(source.get("feeds") or [])
    tier = source.get("source_tier")
    archive_target = source.get("archive_target")
    cadence_type = (source.get("cadence") or {}).get("type")

    if archive_target in COVARIATE_TARGETS or cadence_type == "monthly":
        return "covariate_context"
    if tier == "aggregator" or archive_target == "watch_list":
        return "watch_review"
    if source.get("extractor_backend") == "air_preferred":
        return "official_social" if tier in OFFICIAL_TIERS else "watch_review"
    if tier == "academic_collab_who":
        return "academic_reference"
    if tier in OFFICIAL_TIERS and feeds.intersection(COUNT_FEEDS):
        return "primary_official"
    if tier in REGIONAL_TIERS and feeds.intersection(COUNT_FEEDS):
        return "official_crosscheck"
    if tier in OFFICIAL_TIERS or "guidance" in feeds or "travel_monitoring" in feeds:
        return "context_official"
    return "context_review"


def schedule_policy(registry: dict[str, Any]) -> dict[str, Any]:
    policy = (registry.get("_meta") or {}).get("scheduled_prep_policy")
    if not isinstance(policy, dict):
        raise SourceScheduleError("_meta.scheduled_prep_policy must be an object")
    slots = policy.get("slots")
    if not isinstance(slots, list) or not slots:
        raise SourceScheduleError("scheduled_prep_policy.slots must be a non-empty list")
    return policy


def validate_schedule_policy(registry: dict[str, Any]) -> dict[str, int]:
    """Validate slots and prove every registered source maps to at least one slot."""
    sources = registry.get("sources")
    if not isinstance(sources, list) or not sources:
        raise SourceScheduleError("source registry must include non-empty sources")

    policy = schedule_policy(registry)
    seen_slots: set[str] = set()
    known_groups: set[str] = set()
    for index, slot in enumerate(policy["slots"]):
        if not isinstance(slot, dict):
            raise SourceScheduleError(f"scheduled_prep_policy.slots[{index}] must be an object")
        slot_id = _string(slot.get("slot_id"), f"slots[{index}].slot_id")
        if slot_id in seen_slots:
            raise SourceScheduleError(f"duplicate schedule slot_id {slot_id!r}")
        seen_slots.add(slot_id)
        _validate_cron_utc(_string(slot.get("cron_utc"), f"{slot_id}.cron_utc"), slot_id)
        _string(slot.get("local_rationale"), f"{slot_id}.local_rationale")
        _string(slot.get("publication_boundary"), f"{slot_id}.publication_boundary")
        groups = slot.get("groups")
        if not isinstance(groups, list) or not groups or not all(isinstance(g, str) and g for g in groups):
            raise SourceScheduleError(f"{slot_id}.groups must be non-empty strings")
        known_groups.update(groups)

    missing: list[str] = []
    assignments = 0
    for source in sources:
        group = schedule_group(source)
        if group not in known_groups:
            missing.append(f"{source.get('registry_id', '<missing>')}:{group}")
        else:
            assignments += 1
    if missing:
        raise SourceScheduleError(
            "schedule policy has no slot for source group(s): " + ", ".join(sorted(missing))
        )
    return {"slots": len(seen_slots), "assigned_sources": assignments}


def build_schedule(registry: dict[str, Any]) -> dict[str, Any]:
    """Return a cron-ready schedule projection with per-slot source IDs."""
    validate_schedule_policy(registry)
    policy = schedule_policy(registry)
    sources = registry["sources"]
    source_rows = []
    for source in sources:
        group = schedule_group(source)
        slots = [
            slot["slot_id"]
            for slot in policy["slots"]
            if group in set(slot["groups"])
        ]
        source_rows.append({
            "registry_id": source["registry_id"],
            "schedule_group": group,
            "source_tier": source.get("source_tier"),
            "archive_target": source.get("archive_target"),
            "feeds": source.get("feeds", []),
            "review_only": group in {"watch_review", "context_review", "covariate_context"},
            "slots": slots,
        })

    slot_rows = []
    for slot in policy["slots"]:
        groups = set(slot["groups"])
        slot_source_ids = [
            source["registry_id"]
            for source in sources
            if schedule_group(source) in groups
        ]
        slot_rows.append({
            "slot_id": slot["slot_id"],
            "cron_utc": slot["cron_utc"],
            "local_rationale": slot["local_rationale"],
            "publication_boundary": slot["publication_boundary"],
            "groups": slot["groups"],
            "source_count": len(slot_source_ids),
            "source_ids": slot_source_ids,
            "command": (
                "./tools/bdbv_daily_prep_cron.sh "
                f"--slot {slot['slot_id']} --as-of $(date -u +\\%F) "
                "--earth-awake --auto-pull --build-review-snapshot --website-gates"
            ),
        })

    return {
        "schema_version": 1,
        "outbreak_id": (registry.get("_meta") or {}).get("outbreak_id"),
        "policy": {
            "timezone_basis": policy.get("timezone_basis"),
            "publication_boundary": policy.get("publication_boundary"),
            "promotion_boundary": policy.get("promotion_boundary"),
        },
        "slots": slot_rows,
        "sources": source_rows,
    }


def source_ids_for_slot(registry: dict[str, Any], slot_id: str) -> list[str]:
    schedule = build_schedule(registry)
    for slot in schedule["slots"]:
        if slot["slot_id"] == slot_id:
            return list(slot["source_ids"])
    raise SourceScheduleError(f"unknown schedule slot {slot_id!r}")


def _string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SourceScheduleError(f"{path} must be a non-empty string")
    return value


def _validate_cron_utc(value: str, slot_id: str) -> None:
    parts = value.split()
    if len(parts) != 5:
        raise SourceScheduleError(f"{slot_id}.cron_utc must have five cron fields")
    minute, hour, *_ = parts
    if not (minute.isdigit() and 0 <= int(minute) <= 59):
        raise SourceScheduleError(f"{slot_id}.cron_utc minute must be 0-59")
    if not (hour.isdigit() and 0 <= int(hour) <= 23):
        raise SourceScheduleError(f"{slot_id}.cron_utc hour must be 0-23 UTC")
