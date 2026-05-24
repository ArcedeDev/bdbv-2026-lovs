"""Source-registry release gates for recurring BDBV inputs.

This validates the monitoring registry as a contract: source rows must state
their role, license, archive target, and redistribution boundary. Open
covariate/context sources are allowed, but they must not masquerade as outbreak
case-count sources.
"""
from __future__ import annotations

import json
import pathlib
import sys
from typing import Any

from lovs import source_schedule


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_REGISTRY_PATH = REPO_ROOT / "data" / "external_sources" / "source_registry.json"
DEFAULT_OPEN_COVARIATE_PATH = REPO_ROOT / "data" / "external_sources" / "bdbv-2026.open-covariate-sources.json"

VALID_CADENCE_TYPES = {"weekly", "monthly", "ad_hoc", "continuous"}
VALID_ARCHIVE_TARGETS = {
    "outbreak_manifest",
    "poe_restricted_json",
    "watch_list",
    "external_covariate_metadata",
    "geospatial_context_metadata",
}
VALID_REDISTRIBUTION = {"public", "restricted", "derived_only"}
VALID_API_RESPONSE_KINDS = {"drc_moh_epidemie_dashboard"}
VALID_EXTRACTOR_BACKENDS = {"air_preferred"}
COUNT_FEEDS = {"counts", "case_counts", "deaths", "geography"}
NON_COUNT_ARCHIVE_TARGETS = {
    "poe_restricted_json",
    "watch_list",
    "external_covariate_metadata",
    "geospatial_context_metadata",
}


class SourceRegistryGateError(ValueError):
    """Raised when source-registry release gates fail."""


def load_json(path: pathlib.Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SourceRegistryGateError(f"{path}: file not found") from exc
    except json.JSONDecodeError as exc:
        raise SourceRegistryGateError(f"{path}: invalid JSON: {exc}") from exc


def validate_source_registry(path: pathlib.Path = DEFAULT_REGISTRY_PATH) -> dict[str, int]:
    payload = load_json(path)
    sources = payload.get("sources")
    if not isinstance(sources, list) or not sources:
        raise SourceRegistryGateError("source_registry.sources must be a non-empty list")

    seen: set[str] = set()
    counts = {"sources": 0, "public": 0, "restricted": 0, "derived_only": 0}
    required = (
        "registry_id",
        "title",
        "publisher",
        "source_tier",
        "cadence",
        "feeds",
        "redistribution",
        "license",
        "landing_url",
        "archive_target",
        "latest_known",
        "next_expected",
        "notes",
    )
    for idx, source in enumerate(sources):
        if not isinstance(source, dict):
            raise SourceRegistryGateError(f"sources[{idx}] must be an object")
        for field in required:
            if field not in source:
                raise SourceRegistryGateError(f"sources[{idx}] missing {field!r}")
        registry_id = _string(source["registry_id"], f"sources[{idx}].registry_id")
        if registry_id in seen:
            raise SourceRegistryGateError(f"duplicate registry_id {registry_id!r}")
        seen.add(registry_id)
        counts["sources"] += 1

        cadence = source["cadence"]
        if not isinstance(cadence, dict) or cadence.get("type") not in VALID_CADENCE_TYPES:
            raise SourceRegistryGateError(f"{registry_id}: invalid cadence.type")
        feeds = source["feeds"]
        if not isinstance(feeds, list) or not all(isinstance(feed, str) and feed for feed in feeds):
            raise SourceRegistryGateError(f"{registry_id}: feeds must be non-empty strings")
        archive_target = _string(source["archive_target"], f"{registry_id}.archive_target")
        if archive_target not in VALID_ARCHIVE_TARGETS:
            raise SourceRegistryGateError(f"{registry_id}: unknown archive_target {archive_target!r}")
        redistribution = _string(source["redistribution"], f"{registry_id}.redistribution")
        if redistribution not in VALID_REDISTRIBUTION:
            raise SourceRegistryGateError(f"{registry_id}: invalid redistribution {redistribution!r}")
        counts[redistribution] += 1
        _string(source["license"], f"{registry_id}.license")
        _string(source["landing_url"], f"{registry_id}.landing_url")
        _string(source["notes"], f"{registry_id}.notes")
        extractor_backend = source.get("extractor_backend")
        if extractor_backend is not None:
            extractor_backend = _string(extractor_backend, f"{registry_id}.extractor_backend")
            if extractor_backend not in VALID_EXTRACTOR_BACKENDS:
                raise SourceRegistryGateError(
                    f"{registry_id}: invalid extractor_backend {extractor_backend!r}"
                )
        if source.get("api_request") is not None:
            api_request = source["api_request"]
            if not isinstance(api_request, dict):
                raise SourceRegistryGateError(f"{registry_id}.api_request must be an object")
            if api_request.get("type") != "graphql":
                raise SourceRegistryGateError(f"{registry_id}.api_request.type must be 'graphql'")
            api_url = _string(api_request.get("url"), f"{registry_id}.api_request.url")
            if not api_url.startswith("https://"):
                raise SourceRegistryGateError(f"{registry_id}.api_request.url must be https")
            _string(api_request.get("query"), f"{registry_id}.api_request.query")
            response_kind = _string(
                api_request.get("response_kind"),
                f"{registry_id}.api_request.response_kind",
            )
            if response_kind not in VALID_API_RESPONSE_KINDS:
                raise SourceRegistryGateError(
                    f"{registry_id}.api_request.response_kind unknown: {response_kind!r}"
                )
        if archive_target in NON_COUNT_ARCHIVE_TARGETS and COUNT_FEEDS.intersection(feeds):
            raise SourceRegistryGateError(
                f"{registry_id}: non-count archive target cannot feed {sorted(COUNT_FEEDS.intersection(feeds))}"
            )
        if archive_target in {"external_covariate_metadata", "geospatial_context_metadata"}:
            notes = source["notes"].lower()
            if "not" not in notes or "case" not in notes:
                raise SourceRegistryGateError(
                    f"{registry_id}: covariate/context source notes must state not a case-count source"
                )
            if "humdata.org" in source["landing_url"] and not source.get("hdx_package_id"):
                raise SourceRegistryGateError(f"{registry_id}: HDX sources require hdx_package_id")
            latest_known = source["latest_known"]
            if not isinstance(latest_known, dict) or not latest_known.get("data_as_of"):
                raise SourceRegistryGateError(f"{registry_id}: latest_known.data_as_of required")
            if redistribution == "public" and archive_target == "geospatial_context_metadata":
                raise SourceRegistryGateError(
                    f"{registry_id}: geospatial ODbL/context sources should be derived_only or restricted"
                )
    try:
        source_schedule.validate_schedule_policy(payload)
    except source_schedule.SourceScheduleError as exc:
        raise SourceRegistryGateError(str(exc)) from exc
    return counts


def validate_open_covariate_sources(
    path: pathlib.Path = DEFAULT_OPEN_COVARIATE_PATH,
    registry_path: pathlib.Path = DEFAULT_REGISTRY_PATH,
) -> dict[str, int]:
    if not path.exists():
        return {"packages": 0, "resources": 0}
    payload = load_json(path)
    registry = load_json(registry_path)
    registry_ids = {source["registry_id"] for source in registry.get("sources", [])}
    packages = payload.get("packages")
    if not isinstance(packages, list) or not packages:
        raise SourceRegistryGateError(f"{path}: packages must be a non-empty list")
    resource_count = 0
    for idx, package in enumerate(packages):
        if not isinstance(package, dict):
            raise SourceRegistryGateError(f"packages[{idx}] must be an object")
        registry_id = _string(package.get("registry_id"), f"packages[{idx}].registry_id")
        if registry_id not in registry_ids:
            raise SourceRegistryGateError(f"packages[{idx}].registry_id not in source registry")
        for field in ("package_id", "title", "license_id", "license_title", "data_vintage", "use_role", "license_handling"):
            _string(package.get(field), f"packages[{idx}].{field}")
        if "case" in package["use_role"].lower() and "not" not in package["use_role"].lower():
            raise SourceRegistryGateError(f"packages[{idx}]: covariate package cannot be case-count input")
        resources = package.get("resources")
        if not isinstance(resources, list) or not resources:
            raise SourceRegistryGateError(f"packages[{idx}].resources must be non-empty")
        for resource in resources:
            if not isinstance(resource, dict):
                raise SourceRegistryGateError(f"packages[{idx}].resources[] must be objects")
            _string(resource.get("resource_id"), f"packages[{idx}].resources[].resource_id")
            _string(resource.get("url"), f"packages[{idx}].resources[].url")
            if not resource["url"].startswith("https://"):
                raise SourceRegistryGateError(f"packages[{idx}].resources[].url must be https")
            resource_count += 1
    return {"packages": len(packages), "resources": resource_count}


def validate_all() -> dict[str, int]:
    registry_counts = validate_source_registry()
    covariate_counts = validate_open_covariate_sources()
    return {
        "registry_sources": registry_counts["sources"],
        "covariate_packages": covariate_counts["packages"],
        "covariate_resources": covariate_counts["resources"],
    }


def _string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SourceRegistryGateError(f"{path} must be a non-empty string")
    return value


def main() -> int:
    try:
        counts = validate_all()
    except SourceRegistryGateError as exc:
        print(f"source-registry gate failed: {exc}", file=sys.stderr)
        return 1
    print(
        "source-registry gate ok "
        f"({counts['registry_sources']} registry sources; "
        f"{counts['covariate_packages']} open-covariate package(s); "
        f"{counts['covariate_resources']} resource(s))"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
