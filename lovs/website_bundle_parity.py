"""Canonical LOVS-to-website release-bundle parity gate.

This gate checks the latest registered website snapshot against the canonical
LOVS checkout that is running the release/prep command. It complements
``cross_surface_parity``: byte parity catches stale public assets, while this
module catches stale or non-canonical website snapshot JSON.
"""

from __future__ import annotations

import json
import pathlib
import re
from typing import Any

from lovs import cross_surface_parity

# Post-INRB-SitRep-#015/#016 (2026-05-29) schema split:
# - case counts surface as 'confirmed', 'suspected_cumulative', 'suspected_active'
#   (legacy 'suspected' alias retained for pre-split snapshot back-compat)
# - deaths split into 'confirmed' and 'suspected' under reported_deaths
#   (legacy single 'deaths' bucket retired; the carried-forward 246 figure that
#   used to populate it was the load-bearing audit defect at audit.md:14)
COUNT_METRICS = ("confirmed", "suspected_active", "suspected_cumulative")
DEATH_METRICS = ("confirmed", "suspected")
LEGACY_COUNT_METRICS = ("confirmed", "suspected", "deaths")
SNAPSHOT_DATES_RE = re.compile(
    r"SNAPSHOT_DATES_BEGIN.*?(?P<body>.*?)SNAPSHOT_DATES_END",
    re.DOTALL,
)
DATE_RE = re.compile(r"['\"](?P<date>\d{4}-\d{2}-\d{2})['\"]")


def canonical_source_id(source_id: str) -> str:
    """Return the public source id used by the website surface."""
    return source_id[: -len("-live")] if source_id.endswith("-live") else source_id


def _load_json(path: pathlib.Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _latest_registered_snapshot_date(snapshots_dir: pathlib.Path) -> str:
    index_path = snapshots_dir / "index.ts"
    if index_path.is_file():
        text = index_path.read_text(encoding="utf-8")
        match = SNAPSHOT_DATES_RE.search(text)
        if match:
            dates = [m.group("date") for m in DATE_RE.finditer(match.group("body"))]
            if dates:
                return dates[0]
    dates = sorted(path.stem for path in snapshots_dir.glob("*.json"))
    return dates[-1] if dates else ""


def _manifest_source_ids(manifest: dict[str, Any]) -> set[str]:
    return {
        canonical_source_id(str(entry.get("source_id") or ""))
        for entry in manifest.get("entries", [])
        if entry.get("source_id")
    }


def _snapshot_source_ids(snapshot: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for source in snapshot.get("sources") or []:
        if isinstance(source, dict) and source.get("id"):
            ids.add(canonical_source_id(str(source["id"])))
    return ids


def _iter_source_refs(value: Any, path: str = "snapshot") -> list[tuple[str, str]]:
    refs: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            child_path = f"{path}.{key}"
            if key in {"sourceId", "primarySourceId"}:
                if item:
                    refs.append((child_path, canonical_source_id(str(item))))
                continue
            if key == "sourceIds":
                for idx, source_id in enumerate(item or []):
                    if source_id:
                        refs.append((f"{child_path}[{idx}]", canonical_source_id(str(source_id))))
                continue
            if key == "metricSourceIds":
                for metric, source_ids in (item or {}).items():
                    for idx, source_id in enumerate(source_ids or []):
                        if source_id:
                            refs.append(
                                (
                                    f"{child_path}.{metric}[{idx}]",
                                    canonical_source_id(str(source_id)),
                                )
                            )
                continue
            refs.extend(_iter_source_refs(item, child_path))
    elif isinstance(value, list):
        for idx, item in enumerate(value):
            refs.extend(_iter_source_refs(item, f"{path}[{idx}]"))
    return refs


def _canonical_audit_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        out.append({
            "surface": row.get("surface"),
            "status": row.get("status"),
            "inputs": row.get("inputs"),
            "clockBasis": row.get("clockBasis") or row.get("clock_basis"),
        })
    return out


def _compare_single_count(
    surface: str,
    metric: str,
    live_metric: dict[str, Any],
    web_metric: dict[str, Any],
) -> list[str]:
    """Compare one count field; skip silently when both sides absent."""
    findings: list[str] = []
    if not live_metric and not web_metric:
        return findings
    for field in ("min", "max", "primary"):
        if live_metric.get(field) != web_metric.get(field):
            findings.append(
                f"{surface}.{metric}.{field}: website={web_metric.get(field)!r} "
                f"canonical={live_metric.get(field)!r}"
            )
    live_primary = canonical_source_id(str(live_metric.get("primary_source_id") or ""))
    web_primary = canonical_source_id(str(web_metric.get("primarySourceId") or ""))
    if live_primary != web_primary and (live_primary or web_primary):
        findings.append(
            f"{surface}.{metric}.primarySourceId: website={web_primary!r} "
            f"canonical={live_primary!r}"
        )
    return findings


def _compare_counts(live: dict[str, Any], website: dict[str, Any]) -> list[str]:
    findings: list[str] = []
    live_counts = live.get("reported_counts") or {}
    web_counts = website.get("reportedCounts") or {}

    # Post-split case counts (canonical for SitRep #015+).
    # Canonical side uses snake_case (suspected_active); website uses camelCase
    # (suspectedActive). Map each canonical key to its website peer.
    for metric in COUNT_METRICS:
        if "_" in metric:
            parts = metric.split("_")
            web_key = parts[0] + "".join(p.capitalize() for p in parts[1:])
        else:
            web_key = metric
        findings.extend(_compare_single_count(
            "reportedCounts", metric,
            live_counts.get(metric) or {},
            web_counts.get(web_key) or {},
        ))

    # Legacy back-compat: pre-split snapshots only carry 'suspected' and 'deaths'.
    # Compare them when BOTH sides have the field; this keeps pre-split parity
    # green without forcing post-split snapshots to retain retired keys.
    for legacy_key in ("suspected", "deaths"):
        if legacy_key in live_counts or legacy_key in web_counts:
            findings.extend(_compare_single_count(
                "reportedCounts", legacy_key,
                live_counts.get(legacy_key) or {},
                web_counts.get(legacy_key) or {},
            ))

    # Post-split deaths split: reported_deaths.{confirmed, suspected} on the
    # canonical side maps to reportedCounts.deathsConfirmed / deathsSuspected
    # on the website. Compare class-by-class.
    live_deaths = live.get("reported_deaths") or {}
    for death_class in DEATH_METRICS:
        web_key = f"deaths{death_class.capitalize()}"
        findings.extend(_compare_single_count(
            "reportedDeaths", death_class,
            live_deaths.get(death_class) or {},
            web_counts.get(web_key) or {},
        ))

    return findings


def check_website_bundle_parity(
    lovs_root: pathlib.Path,
    website_root: pathlib.Path,
    *,
    allow_historical_source_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Return structured parity status for the latest website review snapshot.

    ``lovs_root`` is authoritative. ``website_root`` is the website
    ``apps/site`` checkout. The function never writes files.
    """
    lovs_root = pathlib.Path(lovs_root)
    website_root = pathlib.Path(website_root)
    allow_historical_source_ids = {
        canonical_source_id(source_id)
        for source_id in (allow_historical_source_ids or set())
    }
    snapshots_dir = website_root / "app" / "bdbv-2026" / "_data" / "snapshots"
    public_root = website_root / "public" / "bdbv-2026"
    result: dict[str, Any] = {
        "status": "ok",
        "latest_snapshot_date": "",
        "latest_snapshot_path": "",
        "checked": {
            "counts": 0,
            "source_refs": 0,
            "asset_pairs": 0,
        },
        "findings": [],
        "asset_parity": {"checked": 0, "mismatches": [], "missing": []},
    }

    live_path = lovs_root / "data" / "live-bdbv-2026-output.json"
    manifest_path = lovs_root / "data" / "bundibugyo-2026" / "manifest.json"
    if not live_path.is_file():
        result["status"] = "skipped"
        result["reason"] = f"missing canonical LOVS output: {live_path}"
        return result
    if not manifest_path.is_file():
        result["status"] = "skipped"
        result["reason"] = f"missing canonical source manifest: {manifest_path}"
        return result
    if not snapshots_dir.is_dir():
        result["status"] = "skipped"
        result["reason"] = f"missing website snapshot directory: {snapshots_dir}"
        return result

    latest_date = _latest_registered_snapshot_date(snapshots_dir)
    result["latest_snapshot_date"] = latest_date
    if not latest_date:
        result["status"] = "skipped"
        result["reason"] = f"no website snapshots found under {snapshots_dir}"
        return result

    latest_path = snapshots_dir / f"{latest_date}.json"
    result["latest_snapshot_path"] = str(latest_path)
    if not latest_path.is_file():
        result["status"] = "failed"
        result["findings"].append(
            f"latest registered website snapshot {latest_date} is missing ({latest_path})"
        )
        return result

    live = _load_json(live_path)
    manifest = _load_json(manifest_path)
    website = _load_json(latest_path)
    findings: list[str] = []

    live_as_of = str(live.get("as_of") or "")
    website_date = str(website.get("date") or "")
    website_as_of = str(website.get("asOf") or "")
    if website_date and website_date != latest_date:
        findings.append(
            f"snapshot.date: website={website_date!r} registered={latest_date!r}"
        )
    if website_as_of != live_as_of:
        findings.append(
            f"snapshot.asOf: website={website_as_of!r} canonical={live_as_of!r}"
        )

    date_semantics = website.get("dateSemantics") or {}
    if date_semantics.get("snapshotDate") != latest_date:
        findings.append(
            f"dateSemantics.snapshotDate: website={date_semantics.get('snapshotDate')!r} "
            f"registered={latest_date!r}"
        )
    if date_semantics.get("analyticAsOf") != live_as_of:
        findings.append(
            f"dateSemantics.analyticAsOf: website={date_semantics.get('analyticAsOf')!r} "
            f"canonical={live_as_of!r}"
        )

    count_findings = _compare_counts(live, website)
    # Post-split: case counts + 2 legacy back-compat keys + 2 death classes.
    result["checked"]["counts"] = len(COUNT_METRICS) + 2 + len(DEATH_METRICS)
    findings.extend(count_findings)

    canonical_manifest_ids = _manifest_source_ids(manifest) | allow_historical_source_ids
    website_source_ids = _snapshot_source_ids(website)
    for source_id in sorted(website_source_ids - canonical_manifest_ids):
        findings.append(f"sources[] id not in canonical source manifest: {source_id}")

    refs = _iter_source_refs(website)
    result["checked"]["source_refs"] = len(refs)
    for path, source_id in refs:
        if source_id not in website_source_ids:
            findings.append(f"{path}: source ref not listed in website sources[]: {source_id}")
        if source_id not in canonical_manifest_ids:
            findings.append(f"{path}: source ref not in canonical source manifest: {source_id}")

    live_audit = _canonical_audit_rows(live.get("analysis_dependency_audit") or [])
    web_audit = _canonical_audit_rows(website.get("analysisDependencyAudit") or [])
    if live_audit != web_audit:
        findings.append("analysisDependencyAudit differs from canonical LOVS analysis_dependency_audit")

    if public_root.is_dir():
        asset_parity = cross_surface_parity.check_cross_surface_parity(lovs_root, public_root)
        result["asset_parity"] = asset_parity
        result["checked"]["asset_pairs"] = asset_parity["checked"]
        findings.extend(f"asset parity: {line}" for line in asset_parity["mismatches"])
        findings.extend(f"asset parity: {line}" for line in asset_parity["missing"])
    else:
        result["asset_parity"] = {
            "checked": 0,
            "mismatches": [],
            "missing": [f"website public dir missing ({public_root})"],
        }
        findings.append(f"asset parity: website public dir missing ({public_root})")

    result["findings"] = findings
    result["status"] = "failed" if findings else "ok"
    return result
