# SPDX-License-Identifier: Apache-2.0
"""Readiness health report for autonomous BDBV snapshot prep."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import pathlib
import subprocess
import urllib.error
import urllib.request
from typing import Any, Callable


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
FRESHNESS_DIR = REPO_ROOT / "data" / "external_sources" / "freshness"
PREP_DIR = REPO_ROOT / "data" / "external_sources" / "prep"
HEALTH_DIR = REPO_ROOT / "data" / "external_sources" / "health"
PUBLIC_DATASET_DIR = REPO_ROOT / "deliverables" / "public-health-dataset"
DEFAULT_LIVE_BASE_URL = "https://arcede.com/bdbv-2026"
PUBLIC_DATASET_ARTIFACTS = (
    "lovs-public-health-dataset.xlsx",
    "lovs-public-health-dataset.manifest.json",
    "lovs-public-health-dataset.schema.json",
)
COUNT_FEEDS = {"counts", "case_counts", "deaths", "geography", "corridors"}


class HealthError(RuntimeError):
    """Raised when health-report inputs cannot be read."""


FetchFn = Callable[[str], bytes]


def freshness_path(as_of: str, slot_id: str | None = None) -> pathlib.Path:
    suffix = f"-{slot_id}" if slot_id else ""
    return FRESHNESS_DIR / f"bdbv-2026-{as_of}{suffix}.json"


def prep_path(as_of: str, slot_id: str | None = None) -> pathlib.Path:
    suffix = f"-{slot_id}" if slot_id else "-full"
    return PREP_DIR / f"bdbv-2026-{as_of}{suffix}-prep.json"


def health_path(as_of: str, slot_id: str | None = None) -> pathlib.Path:
    suffix = f"-{slot_id}" if slot_id else "-full"
    return HEALTH_DIR / f"bdbv-2026-{as_of}{suffix}-health.json"


def load_json(path: pathlib.Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise HealthError(f"missing {path}") from exc


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def file_age_hours(path: pathlib.Path, now: dt.datetime | None = None) -> float | None:
    if not path.exists():
        return None
    now = now or utc_now()
    modified = dt.datetime.fromtimestamp(path.stat().st_mtime, tz=dt.timezone.utc)
    return round((now - modified).total_seconds() / 3600, 3)


def classify_review_row(row: dict[str, Any]) -> str:
    """Classify a freshness row for daily-prep triage.

    This is an operations label only. It never promotes a source into the scored
    manifest; release gates and evidence-chain review remain authoritative.
    """
    if row.get("status") == "fetch_failed":
        return "fetch_blocked"
    if not row.get("needs_review"):
        return "no_review_required"

    reasons = set(row.get("review_reasons") or [])
    archive_target = row.get("archive_target")
    source_tier = row.get("source_tier")
    extracted_counts = row.get("extracted_counts") or {}
    feeds = set(row.get("feeds") or [])

    if {
        "drc_moh_table_semantics_source_review",
        "latest_report_pdf_missing",
        "display_only_pending_table_semantics",
    }.intersection(reasons):
        return "source_review_blocked"
    if archive_target == "watch_list" or source_tier == "aggregator":
        return "watch_only"
    if (
        {"context_update_date_newer_than_archive", "context_update_as_of_date"}.intersection(reasons)
        or (feeds and not feeds.intersection(COUNT_FEEDS) and not extracted_counts)
    ):
        return "context_update_review"
    if extracted_counts and (
        "detected_date_newer_than_archive" in reasons
        or "count_tuple_differs_from_latest_archive" in reasons
    ):
        return "model_eligible_after_review"
    return "source_review_required"


def classified_review_queue(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in rows:
        if not row.get("needs_review") and row.get("status") != "fetch_failed":
            continue
        output.append({
            "registry_id": row.get("registry_id"),
            "publisher": row.get("publisher"),
            "status": row.get("status"),
            "latest_detected_date": row.get("latest_detected_date"),
            "extracted_counts": row.get("extracted_counts") or {},
            "feeds": row.get("feeds") or [],
            "review_reasons": row.get("review_reasons") or [],
            "classification": classify_review_row(row),
        })
    return output


def summarize_classes(queue: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in queue:
        label = str(row.get("classification") or "unclassified")
        counts[label] = counts.get(label, 0) + 1
    return dict(sorted(counts.items()))


def fetch_url_bytes(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "bdbv-lovs-health/1"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.read()
    except (urllib.error.URLError, TimeoutError):
        curl = pathlib.Path("/usr/bin/curl")
        if not curl.exists():
            raise
        result = subprocess.run(
            [
                str(curl),
                "-L",
                "--fail",
                "--silent",
                "--show-error",
                "--max-time",
                "30",
                "-H",
                "Cache-Control: no-cache",
                url,
            ],
            check=True,
            capture_output=True,
        )
        return result.stdout


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def live_public_parity(
    live_base_url: str = DEFAULT_LIVE_BASE_URL,
    fetch_fn: FetchFn = fetch_url_bytes,
) -> dict[str, Any]:
    artifacts = []
    ok = True
    for name in PUBLIC_DATASET_ARTIFACTS:
        local_path = PUBLIC_DATASET_DIR / name
        local_bytes = local_path.read_bytes()
        local_sha = sha256_bytes(local_bytes)
        url = f"{live_base_url.rstrip('/')}/{name}"
        try:
            remote_bytes = fetch_fn(url)
            remote_sha = sha256_bytes(remote_bytes)
            remote_len = len(remote_bytes)
            artifact_ok = local_sha == remote_sha
            error = ""
        except Exception as exc:  # noqa: BLE001 - health reports transport failures.
            remote_sha = ""
            remote_len = None
            artifact_ok = False
            error = str(exc)
        ok = ok and artifact_ok
        artifacts.append({
            "path": name,
            "url": url,
            "local_sha256": local_sha,
            "remote_sha256": remote_sha,
            "local_bytes": len(local_bytes),
            "remote_bytes": remote_len,
            "ok": artifact_ok,
            "error": error,
        })
    return {"status": "ok" if ok else "failed", "artifacts": artifacts}


def _status_value(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("status") or "")
    return ""


def live_public_mismatch_is_expected(prep_payload: dict[str, Any]) -> bool:
    website_sync = prep_payload.get("website_sync")
    if not isinstance(website_sync, dict) or website_sync.get("status") != "skipped":
        return False
    reason = str(website_sync.get("reason") or "").lower()
    basis = str(website_sync.get("basis") or "").lower()
    return (
        "no new completed publication-state snapshot" in reason
        or basis == "analytic_as_of_no_new_completed_source_publication"
    )


def build_health_report(
    as_of: str,
    slot_id: str | None = None,
    *,
    check_live_public: bool = False,
    max_age_hours: float = 30.0,
    live_base_url: str = DEFAULT_LIVE_BASE_URL,
    fetch_fn: FetchFn = fetch_url_bytes,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    now = now or utc_now()
    generated_at = now.isoformat().replace("+00:00", "Z")
    freshness_file = freshness_path(as_of, slot_id)
    prep_file = prep_path(as_of, slot_id)

    issues: list[dict[str, str]] = []
    freshness: dict[str, Any] = {"path": str(freshness_file.relative_to(REPO_ROOT))}
    prep: dict[str, Any] = {"path": str(prep_file.relative_to(REPO_ROOT))}

    if freshness_file.exists():
        freshness_payload = load_json(freshness_file)
        rows = freshness_payload.get("sources") or []
        queue = classified_review_queue(rows)
        freshness.update({
            "status": "ok",
            "age_hours": file_age_hours(freshness_file, now),
            "summary": freshness_payload.get("summary") or {},
            "classified_review_queue": queue,
            "classification_counts": summarize_classes(queue),
        })
        if freshness["age_hours"] is not None and freshness["age_hours"] > max_age_hours:
            issues.append({"severity": "hard", "code": "freshness_stale", "message": freshness["path"]})
        if queue:
            issues.append({
                "severity": "review",
                "code": "review_queue_nonempty",
                "message": ", ".join(row["registry_id"] for row in queue if row.get("registry_id")),
            })
    else:
        freshness.update({"status": "missing", "age_hours": None, "summary": {}})
        issues.append({"severity": "hard", "code": "freshness_missing", "message": freshness["path"]})

    prep_payload: dict[str, Any] = {}
    if prep_file.exists():
        prep_payload = load_json(prep_file)
        release_check = prep_payload.get("release_check")
        website_sync = prep_payload.get("website_sync")
        website_gates = prep_payload.get("website_gates")
        earth_journal = prep_payload.get("earth_journal")
        prep.update({
            "status": "ok",
            "age_hours": file_age_hours(prep_file, now),
            "release_check_returncode": (
                release_check.get("returncode") if isinstance(release_check, dict) else None
            ),
            "website_sync_status": _status_value(website_sync) or ("skipped" if website_sync is None else ""),
            "website_gates_status": _status_value(website_gates) or ("skipped" if website_gates is None else ""),
            "earth_journal_status": _status_value(earth_journal) or ("skipped" if earth_journal is None else ""),
            "auto_pulled_count": len(prep_payload.get("auto_pulled") or []),
        })
        if prep["age_hours"] is not None and prep["age_hours"] > max_age_hours:
            issues.append({"severity": "hard", "code": "prep_stale", "message": prep["path"]})
        if prep["release_check_returncode"] not in (None, 0):
            issues.append({"severity": "hard", "code": "release_check_failed", "message": prep["path"]})
        if prep["website_sync_status"] == "failed":
            issues.append({"severity": "hard", "code": "website_sync_failed", "message": prep["path"]})
        if prep["website_gates_status"] == "failed":
            issues.append({"severity": "hard", "code": "website_gates_failed", "message": prep["path"]})
    else:
        prep.update({"status": "missing", "age_hours": None})
        issues.append({"severity": "hard", "code": "prep_missing", "message": prep["path"]})

    live_public = {"status": "skipped", "reason": "run with --live-public-check"}
    if check_live_public:
        live_public = live_public_parity(live_base_url=live_base_url, fetch_fn=fetch_fn)
        if live_public["status"] != "ok":
            if live_public_mismatch_is_expected(prep_payload):
                live_public["expected_mismatch"] = True
                issues.append({
                    "severity": "review",
                    "code": "live_public_candidate_not_synced",
                    "message": "website sync skipped to preserve publication-state route",
                })
            else:
                issues.append({
                    "severity": "hard",
                    "code": "live_public_parity_failed",
                    "message": live_base_url,
                })

    hard_issues = [issue for issue in issues if issue["severity"] == "hard"]
    review_issues = [issue for issue in issues if issue["severity"] == "review"]
    traffic_light = "red" if hard_issues else "yellow" if review_issues else "green"

    return {
        "schema_version": 1,
        "outbreak_id": "bdbv-uga-cod-2026",
        "as_of": as_of,
        "slot_id": slot_id,
        "generated_at": generated_at,
        "traffic_light": traffic_light,
        "freshness": freshness,
        "prep": prep,
        "live_public_parity": live_public,
        "issues": issues,
        "ready_for_public_release": traffic_light == "green" and live_public.get("status") == "ok",
    }


def write_health_report(report: dict[str, Any]) -> pathlib.Path:
    as_of = str(report["as_of"])
    slot_id = report.get("slot_id")
    path = health_path(as_of, str(slot_id) if slot_id else None)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path
