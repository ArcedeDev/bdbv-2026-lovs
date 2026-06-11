#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Recurring-source ingest + cadence gate for the BDBV 2026 LOVS pipeline.

Reads data/external_sources/source_registry.json (the recurring publications we
monitor) and answers two questions:

  1. Which recurring sources are DUE or OVERDUE for a fresh pull?
     (cadence gate: compares each source's newest archived edition in the
     manifest against its declared cadence.)
  2. Which files dropped into the gitignored dropbox
     (data/bundibugyo-2026/private/sources/) are NOT yet byte-archived, and
     which registry source each matches?

It also ingests a dropped edition into the archive:

  python3 source_ingest.py                         # cadence report (default)
  python3 source_ingest.py --schedule              # UTC cron plan for autonomous prep
  python3 source_ingest.py --live-check --as-of 2026-05-21
  python3 source_ingest.py --live-check --slot africa_morning_primary
  python3 source_ingest.py --ingest '<path-to-file-in-dropbox>'

Ingest is byte + provenance automated; the EXTRACTED FIGURES must be supplied
in a sidecar JSON next to the file: '<file>.meta.json'. Public sources are
archived under raw/<sha256> (redistributed); restricted publisher material is
hash-recorded with bytes kept under the gitignored private/raw/<sha256>.

Read-only in --report mode. --live-check fetches registered landing URLs and
writes a freshness JSON report; it does not mutate released snapshots. --ingest
writes only the manifest + a private/raw copy. Sources marked
extractor_backend=air_preferred should be captured with AIR when ordinary HTTP
text extraction is incomplete; AIR output still enters this same review/archive
path. Stdlib only.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import html.parser
import io
import json
import pathlib
import re
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request

from lovs import lovs_archive
from lovs import lovs_live_ingest
from lovs import source_schedule

REPO_ROOT = pathlib.Path(__file__).parent.resolve()
DATA = REPO_ROOT / "data"
REGISTRY = DATA / "external_sources" / "source_registry.json"
MANIFEST_DIR = DATA / "bundibugyo-2026"
MANIFEST = MANIFEST_DIR / "manifest.json"
DROPBOX = MANIFEST_DIR / "private" / "sources"
FRESHNESS_DIR = DATA / "external_sources" / "freshness"

_BAR = "=" * 74


def _now_utc_iso_z() -> str:
    return (
        dt.datetime.now(dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _load(path: pathlib.Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256_file(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _display_path(path: pathlib.Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _manifest_hashes(manifest: dict) -> set[str]:
    return {e["content_hash"] for e in manifest.get("entries", []) if e.get("content_hash")}


def newest_edition(manifest: dict, prefix: str | None) -> str | None:
    """Newest published_at (YYYY-MM-DD) among manifest entries whose source_id starts with prefix."""
    if not prefix:
        return None
    dates = [
        e["published_at"][:10]
        for e in manifest.get("entries", [])
        if e.get("source_id", "").startswith(prefix) and e.get("published_at")
    ]
    return max(dates) if dates else None


def newest_entry(manifest: dict, prefix: str | None) -> dict | None:
    """Newest manifest entry whose source_id starts with prefix."""
    if not prefix:
        return None
    entries = [
        e for e in manifest.get("entries", [])
        if e.get("source_id", "").startswith(prefix) and e.get("published_at")
    ]
    if not entries:
        return None
    return max(entries, key=lambda e: e["published_at"])


def cadence_status(source: dict, manifest: dict, as_of: str) -> dict:
    """Classify one recurring source as CURRENT / DUE / OVERDUE / AD-HOC / UNARCHIVED."""
    cadence = source.get("cadence", {})
    ctype = cadence.get("type", "ad_hoc")
    days = cadence.get("days")
    newest = newest_edition(manifest, source.get("manifest_source_prefix"))
    if source.get("archive_target") != "outbreak_manifest":
        newest = (source.get("latest_known") or {}).get("data_as_of")

    status = "CURRENT"
    gap_days = None
    if ctype in ("ad_hoc", "continuous"):
        status = "AD-HOC" if ctype == "ad_hoc" else "CONTINUOUS"
    elif newest is None:
        status = "UNARCHIVED"
    elif days:
        d_new = dt.date.fromisoformat(newest)
        d_as_of = dt.date.fromisoformat(as_of)
        gap_days = (d_as_of - d_new).days
        if gap_days > int(days * 1.5):
            status = "OVERDUE"
        elif gap_days > days:
            status = "DUE"
    return {
        "registry_id": source["registry_id"],
        "cadence": ctype,
        "newest_archived": newest,
        "gap_days": gap_days,
        "status": status,
    }


def scan_dropbox(registry: dict, manifest: dict) -> list[dict]:
    """Match dropbox files to registry sources and flag which are not yet archived."""
    if not DROPBOX.exists():
        return []
    archived = _manifest_hashes(manifest)
    rows: list[dict] = []
    for path in sorted(DROPBOX.iterdir()):
        if not path.is_file() or path.name.endswith(".meta.json"):
            continue  # skip sidecars and non-files
        h = _sha256_file(path)
        match = None
        sidecar = path.with_name(path.name + ".meta.json")
        if sidecar.exists():
            try:
                meta = _load(sidecar)
                rid = meta.get("registry_id")
                if rid:
                    match = next(
                        (source for source in registry["sources"] if source["registry_id"] == rid),
                        None,
                    )
            except (OSError, json.JSONDecodeError):
                match = None
        if match is None:
            match = match_registry(registry, path.name)
        rows.append({
            "file": path.name,
            "sha256": h,
            "archived": h in archived,
            "registry_id": match["registry_id"] if match else None,
            "archive_target": match.get("archive_target") if match else None,
        })
    return rows


def match_registry(registry: dict, filename: str) -> dict | None:
    """Find the registry source whose filename_hints match this filename (case-insensitive)."""
    low = filename.lower()
    for source in registry["sources"]:
        for hint in source.get("filename_hints", []):
            if hint.lower() in low:
                return source
    return None


class _HTMLTextExtractor(html.parser.HTMLParser):
    """Small visible-text extractor for source freshness checks."""

    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self._chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript"}:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            stripped = data.strip()
            if stripped:
                self._chunks.append(stripped)

    @property
    def text(self) -> str:
        return " ".join(self._chunks)


_MONTHS: dict[str, int] = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}

_DATE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b"),
    re.compile(r"(?<!\d)(\d{1,2})[./_-](\d{1,2})[./_-](20\d{2})(?!\d)"),
    re.compile(
        r"\b("
        + "|".join(_MONTHS.keys())
        + r")\s+(\d{1,2}),\s*(20\d{2})\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(\d{1,2})\s+("
        + "|".join(_MONTHS.keys())
        + r")\s+(20\d{2})\b",
        re.IGNORECASE,
    ),
)


def _iso_date(year: int, month: int, day: int) -> str | None:
    try:
        return dt.date(year, month, day).isoformat()
    except ValueError:
        return None


def extract_dates(text: str) -> list[str]:
    """Extract ISO dates from the most common publisher date formats."""
    dates: set[str] = set()
    for match in _DATE_PATTERNS[0].finditer(text):
        iso = _iso_date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        if iso:
            dates.add(iso)
    for match in _DATE_PATTERNS[1].finditer(text):
        iso = _iso_date(int(match.group(3)), int(match.group(2)), int(match.group(1)))
        if iso:
            dates.add(iso)
    for match in _DATE_PATTERNS[2].finditer(text):
        iso = _iso_date(
            int(match.group(3)),
            _MONTHS[match.group(1).lower()],
            int(match.group(2)),
        )
        if iso:
            dates.add(iso)
    for match in _DATE_PATTERNS[3].finditer(text):
        iso = _iso_date(
            int(match.group(3)),
            _MONTHS[match.group(2).lower()],
            int(match.group(1)),
        )
        if iso:
            dates.add(iso)
    return sorted(dates)


def _visible_text(raw: bytes, content_type: str = "") -> str:
    if "html" not in content_type.lower() and b"<html" not in raw[:500].lower():
        return raw.decode("utf-8", errors="replace")
    parser = _HTMLTextExtractor()
    parser.feed(raw.decode("utf-8", errors="replace"))
    return parser.text


def extract_count_tuple(text: str) -> dict:
    """Extract outbreak headline counts when a page exposes them in prose."""
    normalized = " ".join(text.split())
    cdc_counts = lovs_live_ingest.extract_cdc_current_situation_counts(normalized)
    if (
        cdc_counts
        and (
            "DRC and Uganda Ministries of Health" in normalized
            or "Uganda: A total of" in normalized
        )
    ):
        keys = (
            "cases_suspected",
            "cases_confirmed",
            "cases_confirmed_total",
            "cases_confirmed_drc",
            "cases_confirmed_uganda",
            "deaths_suspected",
            "deaths_suspected_drc",
            "deaths_confirmed_drc",
            "deaths_uganda",
        )
        return {
            key: cdc_counts[key]
            for key in keys
            if isinstance(cdc_counts.get(key), int)
        }
    counts: dict[str, int] = {}

    def add_count(key: str, token: str) -> bool:
        value = int(token)
        # Avoid reading a nearby publication year as a case/death count in
        # label-first layouts such as "First reported 15 May 2026 Confirmed cases 51".
        if 1900 <= value <= 2100:
            return False
        counts[key] = value
        return True

    tuple_match = re.search(
        r"(\d{1,6})\s+suspected\s+cases,\s+"
        r"(?:(\d{1,6})\s+probable\s+cases,\s+)?"
        r"(\d{1,6})\s+confirmed\s+cases,\s+and\s+"
        r"(\d{1,6})\s+suspected\s+deaths",
        normalized,
        re.IGNORECASE,
    )
    if tuple_match:
        add_count("cases_suspected", tuple_match.group(1))
        if tuple_match.group(2):
            add_count("cases_probable", tuple_match.group(2))
        add_count("cases_confirmed", tuple_match.group(3))
        add_count("deaths_suspected", tuple_match.group(4))
        return counts

    patterns = {
        "cases_suspected": (
            r"\bsuspected\s+cases\s+(\d{1,6})\b",
            r"(?<!cases\s)(?<!deaths\s)\b(\d{1,6})\s+suspected\s+cases\b",
        ),
        "cases_probable": (
            r"\bprobable\s+cases\s+(\d{1,6})\b",
            r"(?<!cases\s)(?<!deaths\s)\b(\d{1,6})\s+probable\s+cases\b",
        ),
        "cases_confirmed": (
            r"\bconfirmed\s+cases\s+(\d{1,6})\b",
            r"(?<!cases\s)(?<!deaths\s)\b(\d{1,6})\s+(?:laboratory\s+)?confirmed\s+cases\b",
        ),
        "deaths_suspected": (
            r"\bsuspected\s+deaths\s+(\d{1,6})\b",
            r"(?<!cases\s)(?<!deaths\s)\b(\d{1,6})\s+suspected\s+deaths\b",
        ),
        "deaths": (
            r"\bdeaths\s+(\d{1,6})\b",
            r"(?<!cases\s)(?<!deaths\s)\b(\d{1,6})\s+(?:associated\s+)?deaths\b",
        ),
    }
    for key, key_patterns in patterns.items():
        for pattern in key_patterns:
            for match in re.finditer(pattern, normalized, re.IGNORECASE):
                if add_count(key, match.group(1)):
                    break
            if key in counts:
                break
    return counts


def _has_outbreak_context(text: str) -> bool:
    return bool(re.search(r"\b(?:Bundibugyo|BDBV|Ituri\s+Province)\b", text, re.IGNORECASE))


def _has_count_feed(source: dict) -> bool:
    return bool(set(source.get("feeds") or []).intersection(source_schedule.COUNT_FEEDS))


def _context_only_date_reason(source: dict, extracted_counts: dict) -> bool:
    return not _has_count_feed(source) and not extracted_counts


def _latest_archived_counts(entry: dict | None) -> dict:
    if not entry:
        return {}
    content = entry.get("normalized_content") or {}
    keys = (
        "cases_suspected",
        "cases_probable",
        "cases_confirmed",
        "deaths_suspected",
        "deaths",
        "deaths_approx",
        "cases_suspected_drc_approx",
    )
    return {k: content[k] for k in keys if k in content}


def _fetch_url(
    url: str,
    timeout: int = 30,
    *,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[bytes, int | None, str]:
    request_headers = {
        "User-Agent": (
            "bdbv-2026-lovs/0.1.0 "
            "(public-health surveillance validation; source freshness check)"
        ),
        "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.8",
    }
    if data is not None:
        request_headers["Content-Type"] = "application/json"
        request_headers["Accept"] = "application/json,application/pdf;q=0.9,*/*;q=0.8"
    if headers:
        request_headers.update(headers)
    request = urllib.request.Request(
        _ascii_request_url(url),
        data=data,
        headers=request_headers,
    )
    context = lovs_live_ingest._resolve_ssl_context()
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
                return (
                    response.read(),
                    getattr(response, "status", None),
                    response.headers.get("Content-Type", ""),
                )
        except urllib.error.HTTPError as exc:
            if exc.code < 500 or attempt == 2:
                raise
        except (TimeoutError, urllib.error.URLError) as exc:
            if attempt == 2:
                raise
        time.sleep(0.5 * (attempt + 1))
    raise RuntimeError("unreachable fetch retry state")


def _hdx_package_url(package_id: str) -> str:
    return f"https://data.humdata.org/api/3/action/package_show?id={package_id}"


def _hdx_resource_summary(package_doc: dict) -> list[dict]:
    resources = []
    for resource in package_doc.get("resources", []):
        resources.append({
            "resource_id": resource.get("id"),
            "name": resource.get("name"),
            "format": resource.get("format"),
            "last_modified": resource.get("last_modified"),
            "url": resource.get("url"),
        })
    return resources


def _fetch_hdx_package(package_id: str, fetch_fn=_fetch_url) -> tuple[dict, bytes, int | None, str]:
    raw, http_status, content_type = fetch_fn(_hdx_package_url(package_id))
    payload = json.loads(raw.decode("utf-8"))
    if not payload.get("success"):
        raise ValueError(f"HDX package_show failed for {package_id!r}")
    return payload["result"], raw, http_status, content_type


def _github_release_api_url(source: dict) -> str:
    config = source.get("github_release") or {}
    api_url = config.get("api_url")
    if isinstance(api_url, str) and api_url.startswith("https://"):
        return api_url
    repo = config.get("repo")
    if isinstance(repo, str) and repo.count("/") == 1:
        return f"https://api.github.com/repos/{repo}/releases/latest"
    raise ValueError(f"{source['registry_id']}: github_release.repo or api_url required")


def _github_asset_digest(asset: dict) -> str | None:
    digest = asset.get("digest")
    if isinstance(digest, str) and digest.startswith("sha256:"):
        value = digest.split(":", 1)[1].strip().lower()
        if re.fullmatch(r"[0-9a-f]{64}", value):
            return value
    return None


def _github_release_asset(source: dict, release: dict) -> dict:
    config = source.get("github_release") or {}
    pattern = config.get("asset_name_regex") or r".*\.tar\.gz$"
    assets = release.get("assets")
    if not isinstance(assets, list) or not assets:
        raise ValueError(f"{source['registry_id']}: latest GitHub release has no assets")
    for asset in assets:
        name = asset.get("name")
        if isinstance(name, str) and re.fullmatch(pattern, name):
            url = asset.get("browser_download_url")
            if not isinstance(url, str) or not url.startswith("https://"):
                raise ValueError(f"{source['registry_id']}: release asset missing download URL")
            return asset
    raise ValueError(f"{source['registry_id']}: no release asset matches {pattern!r}")


def _fetch_github_latest_release(source: dict, fetch_fn=_fetch_url) -> tuple[dict, bytes, int | None, str]:
    raw, http_status, content_type = fetch_fn(
        _github_release_api_url(source),
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    release = json.loads(raw.decode("utf-8"))
    if not isinstance(release, dict) or not release.get("tag_name"):
        raise ValueError(f"{source['registry_id']}: invalid GitHub release payload")
    return release, raw, http_status, content_type


def _normalize_date_token(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    iso = _day(value)
    if iso:
        return iso
    match = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(20\d{2})", value)
    if match:
        return _iso_date(int(match.group(3)), int(match.group(2)), int(match.group(1)))
    return None


def _tar_text(tf: tarfile.TarFile, member_name: str) -> str | None:
    try:
        member = tf.getmember(member_name)
    except KeyError:
        return None
    extracted = tf.extractfile(member)
    if extracted is None:
        return None
    return extracted.read().decode("utf-8-sig", errors="replace")


def _latest_metric_from_csv_text(text: str, *, path: str) -> dict:
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        return {"path": path, "status": "empty"}
    value_fields = [field for field in (rows[0].keys() or []) if field not in {"nom", "date"}]
    if len(value_fields) != 1:
        return {"path": path, "status": "unsupported_columns", "columns": list(rows[0].keys())}
    metric = value_fields[0]
    dated_rows: list[tuple[str, dict]] = []
    for row in rows:
        date = _normalize_date_token(row.get("date"))
        if date:
            dated_rows.append((date, row))
    if not dated_rows:
        return {"path": path, "metric": metric, "status": "missing_dates", "row_count": len(rows)}
    latest = max(date for date, _ in dated_rows)
    latest_rows = [row for date, row in dated_rows if date == latest]
    values = sorted({str(row.get(metric, "")).strip() for row in latest_rows})
    numeric_values = sorted({int(v) for v in values if re.fullmatch(r"-?\d+", v)})
    result = {
        "path": path,
        "metric": metric,
        "date": latest,
        "row_count": len(rows),
        "latest_row_count": len(latest_rows),
        "distinct_latest_values": numeric_values if numeric_values else values,
    }
    if len(numeric_values) == 1:
        result["value"] = numeric_values[0]
    return result


def _inrb_release_dataset_summary(raw: bytes) -> dict:
    """Extract the small DRC SitRep summary we need from the released build tarball."""
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tf:
        manifest_text = _tar_text(tf, "build/manifest.json")
        build_manifest = json.loads(manifest_text) if manifest_text else {}
        metric_paths = {
            "national_cumulative_confirmed_cases": "build/long/insp_sitrep__national_cumulative_confirmed_cases.csv",
            "national_cumulative_confirmed_deaths": "build/long/insp_sitrep__national_cumulative_confirmed_deaths.csv",
            "national_cumulative_suspected_cases": "build/long/insp_sitrep__national_cumulative_suspected_cases.csv",
            "national_cumulative_suspected_deaths": "build/long/insp_sitrep__national_cumulative_suspected_deaths.csv",
            "health_zone_cumulative_confirmed_cases": "build/long/insp_sitrep__cumulative_confirmed_cases.csv",
            "health_zone_cumulative_confirmed_deaths": "build/long/insp_sitrep__cumulative_confirmed_deaths.csv",
        }
        metrics = {}
        for key, path in metric_paths.items():
            text = _tar_text(tf, path)
            metrics[key] = (
                _latest_metric_from_csv_text(text, path=path)
                if text is not None
                else {"path": path, "status": "missing"}
            )
        members = tf.getnames()
    national_dates = [
        metric.get("date")
        for key, metric in metrics.items()
        if key.startswith("national_") and metric.get("date")
    ]
    zone_dates = [
        metric.get("date")
        for key, metric in metrics.items()
        if key.startswith("health_zone_") and metric.get("date")
    ]
    return {
        "build_manifest": {
            "built_at": build_manifest.get("built_at"),
            "commit": build_manifest.get("commit"),
            "n_features": build_manifest.get("n_features"),
        },
        "member_count": len(members),
        "contains_geojson": "build/drc_health_zones.geojson" in members,
        "contains_build_manifest": "build/manifest.json" in members,
        "metrics": metrics,
        "latest_national_data_date": max(national_dates) if national_dates else None,
        "latest_health_zone_data_date": max(zone_dates) if zone_dates else None,
    }


def _inrb_release_sidecar(
    source: dict,
    release: dict,
    asset: dict,
    dataset_summary: dict,
    *,
    source_id: str,
    retrieved_at: str,
) -> dict:
    published_day = _day(release.get("published_at"))
    metrics = dataset_summary.get("metrics") or {}
    national_confirmed = metrics.get("national_cumulative_confirmed_cases") or {}
    national_confirmed_deaths = metrics.get("national_cumulative_confirmed_deaths") or {}
    national_suspected = metrics.get("national_cumulative_suspected_cases") or {}
    national_suspected_deaths = metrics.get("national_cumulative_suspected_deaths") or {}
    health_zone_confirmed = metrics.get("health_zone_cumulative_confirmed_cases") or {}
    health_zone_deaths = metrics.get("health_zone_cumulative_confirmed_deaths") or {}
    return {
        "registry_id": source["registry_id"],
        "source_id": source_id,
        "url": asset.get("browser_download_url") or release.get("html_url") or source["landing_url"],
        "retrieved_at": retrieved_at,
        "published_at": release.get("published_at"),
        "outbreak_id": "bdbv-uga-cod-2026",
        "pathogen": "BDBV",
        "country_scope": ["COD"],
        "geography_id": "COD:national-health-zones",
        "extraction_status": "partial",
        "normalized_content": {
            "capture_type": "github_release_build_asset",
            "repository": (source.get("github_release") or {}).get("repo"),
            "release_tag": release.get("tag_name"),
            "release_name": release.get("name"),
            "release_url": release.get("html_url"),
            "release_body": release.get("body"),
            "asset_name": asset.get("name"),
            "asset_digest": asset.get("digest"),
            "asset_size": asset.get("size"),
            "asset_download_url": asset.get("browser_download_url"),
            "publication_date": published_day,
            "data_as_of": dataset_summary.get("latest_national_data_date"),
            "health_zone_data_as_of": dataset_summary.get("latest_health_zone_data_date"),
            "build_manifest": dataset_summary.get("build_manifest"),
            "cases_confirmed_drc": national_confirmed.get("value"),
            "cases_suspected_drc": national_suspected.get("value"),
            "deaths_confirmed_drc": national_confirmed_deaths.get("value"),
            "deaths_suspected_drc": national_suspected_deaths.get("value"),
            "national_metrics": {
                "confirmed_cases": national_confirmed,
                "suspected_cases": national_suspected,
                "confirmed_deaths": national_confirmed_deaths,
                "suspected_deaths": national_suspected_deaths,
            },
            "health_zone_metrics": {
                "confirmed_cases": health_zone_confirmed,
                "confirmed_deaths": health_zone_deaths,
            },
            "scope_caveat": (
                "This INRB/INSP/UMIE release is DRC-only. Do not collapse it into "
                "the COD+UGA headline without an explicit composition step that "
                "adds or reconciles Uganda on the same metric concept."
            ),
            "table_semantics_status": "source_review",
            "model_use": (
                "primary DRC authority/partner data release for source review and "
                "publication-clock routing; DRC-only national fields are preserved "
                "as country-specific values and are not generic headline counts."
            ),
            "snapshot_trigger": True,
            "release_gate_note": (
                "May advance the publication-state review route because it is a "
                "dated authority/partner release, but it must not replace corridor "
                "source-load or COD+UGA headline counts until source composition "
                "and table semantics are reviewed."
            ),
        },
    }


def _live_github_release_check(
    source: dict,
    manifest: dict,
    as_of: str,
    row: dict,
    fetch_fn,
) -> dict:
    release, raw, http_status, content_type = _fetch_github_latest_release(source, fetch_fn)
    asset = _github_release_asset(source, release)
    asset_digest = _github_asset_digest(asset)
    published_day = _day(release.get("published_at"))
    detected_dates = sorted(set(extract_dates(json.dumps(release)) + ([published_day] if published_day else [])))
    latest_detected = published_day or (max(detected_dates) if detected_dates else None)
    row.update({
        "status": "fetched",
        "url": release.get("html_url") or source.get("landing_url"),
        "api_url": _github_release_api_url(source),
        "http_status": http_status,
        "content_type": content_type,
        "content_length": len(raw),
        "content_hash": asset_digest or _sha256_bytes(raw),
        "detected_dates": detected_dates[-12:],
        "latest_detected_date": latest_detected,
        "outbreak_context_found": True,
        "extracted_counts": {},
        "github_release": {
            "repository": (source.get("github_release") or {}).get("repo"),
            "tag_name": release.get("tag_name"),
            "name": release.get("name"),
            "published_at": release.get("published_at"),
            "html_url": release.get("html_url"),
            "body": release.get("body"),
            "asset": {
                "name": asset.get("name"),
                "size": asset.get("size"),
                "digest": asset.get("digest"),
                "browser_download_url": asset.get("browser_download_url"),
            },
        },
    })
    newest_archived = row["newest_archived"]
    if latest_detected and newest_archived and latest_detected > newest_archived:
        row["needs_review"] = True
        row["review_reasons"].append("detected_date_newer_than_archive")
    if (
        latest_detected
        and latest_detected == as_of
        and source.get("archive_target") == "outbreak_manifest"
        and (newest_archived is None or newest_archived < as_of)
    ):
        row["needs_review"] = True
        row["review_reasons"].append("detected_as_of_date")
    if row["content_hash"] not in _manifest_hashes(manifest):
        row["needs_review"] = True
        row["review_reasons"].append("bytes_not_in_manifest")
    return row


def _day(value: str | None) -> str | None:
    if not value:
        return None
    match = re.match(r"^(20\d{2}-\d{2}-\d{2})", value)
    return match.group(1) if match else None


def _as_int(value: object) -> int:
    if value in (None, "", "ND"):
        return 0
    return int(value)


def _safe_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "source"


def _drc_moh_graphql_body(source: dict) -> bytes:
    request = source.get("api_request") or {}
    if request.get("type") != "graphql":
        raise ValueError(f"{source['registry_id']}: unsupported api_request.type")
    payload = {"query": request["query"]}
    if request.get("variables"):
        payload["variables"] = request["variables"]
    if request.get("operation_name"):
        payload["operationName"] = request["operation_name"]
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _drc_moh_pdf_url(report_fields: dict) -> str | None:
    pdf = report_fields.get("pdfOfficiel")
    if not isinstance(pdf, dict):
        return None
    node = pdf.get("node")
    if not isinstance(node, dict):
        return None
    url = node.get("mediaItemUrl")
    return url if isinstance(url, str) and url.startswith("https://") else None


def _drc_moh_reports(payload: dict) -> tuple[dict, list[dict]]:
    epidemie = ((payload.get("data") or {}).get("epidemie") or {})
    fields = epidemie.get("epidemiesFields") or {}
    meta = {
        "name": epidemie.get("name"),
        "code_oms": fields.get("codeOms"),
        "date_debut": fields.get("dateDebut"),
        "statut": fields.get("statut"),
        "souche": fields.get("souche"),
    }
    reports: list[dict] = []
    edges = (((epidemie.get("rapportsHebdomandaires") or {}).get("edges")) or [])
    for edge in edges:
        node = (edge or {}).get("node") or {}
        report_fields = node.get("reportsFields") or {}
        rows: list[dict] = []
        for province_block in report_fields.get("situationProvince") or []:
            province = province_block.get("province") or {}
            province_names = province.get("nom") or []
            province_name = province_names[0] if province_names else None
            for zone in province.get("zoneSante") or []:
                rows.append({
                    "province": province_name,
                    "zone_sante": str(zone.get("nom") or "").strip(),
                    "cas_confirmes": _as_int(zone.get("casConfirmes")),
                    "cas_suspects": _as_int(zone.get("casSuspects")),
                    "deces": _as_int(zone.get("deces")),
                })
        reports.append({
            "slug": node.get("slug"),
            "title": node.get("title"),
            "date_rapportage": report_fields.get("dateRapportage"),
            "date_rapportage_day": _day(report_fields.get("dateRapportage")),
            "date_publication": report_fields.get("datePublication"),
            "date_publication_day": _day(report_fields.get("datePublication")),
            "pdf_url": _drc_moh_pdf_url(report_fields),
            "reported_rows": rows,
            "row_totals": {
                "cas_confirmes": sum(row["cas_confirmes"] for row in rows),
                "cas_suspects": sum(row["cas_suspects"] for row in rows),
                "deces": sum(row["deces"] for row in rows),
            },
        })
    reports.sort(
        key=lambda r: (
            r.get("date_publication_day") or "",
            r.get("date_rapportage_day") or "",
            r.get("slug") or "",
        ),
        reverse=True,
    )
    return meta, reports


def _drc_moh_latest_report(reports: list[dict], as_of: str | None = None) -> dict | None:
    if not reports:
        return None
    if as_of:
        candidates = [
            r for r in reports
            if (r.get("date_publication_day") or r.get("date_rapportage_day") or "") <= as_of
        ]
        if candidates:
            return candidates[0]
    return reports[0]


def _drc_moh_dashboard_aggregate(reports: list[dict]) -> dict:
    """Aggregate the dashboard's all-published-bulletins card values.

    The page labels per-bulletin rows as cases reported for that bulletin only.
    Summing those rows across all returned published bulletins reproduces the
    dashboard's "All published bulletins" cards; keep that basis explicit so it
    cannot be mistaken for a verified cumulative health-zone table.
    """
    by_province: dict[str, dict[str, int]] = {}
    totals = {"cas_confirmes": 0, "cas_suspects": 0, "deces": 0}
    report_clocks: list[dict] = []
    for report in reports:
        report_clocks.append({
            "report_slug": report.get("slug"),
            "report_title": report.get("title"),
            "date_rapportage": report.get("date_rapportage"),
            "date_publication": report.get("date_publication"),
        })
        for row in report.get("reported_rows") or []:
            province = str(row.get("province") or "Unspecified")
            bucket = by_province.setdefault(
                province,
                {"reported_cases": 0, "confirmed": 0, "deaths": 0},
            )
            suspected = int(row.get("cas_suspects") or 0)
            confirmed = int(row.get("cas_confirmes") or 0)
            deaths = int(row.get("deces") or 0)
            bucket["reported_cases"] += suspected
            bucket["confirmed"] += confirmed
            bucket["deaths"] += deaths
            totals["cas_suspects"] += suspected
            totals["cas_confirmes"] += confirmed
            totals["deces"] += deaths

    reported_cases = totals["cas_suspects"]
    cfr_pct = round((totals["deces"] / reported_cases) * 100, 2) if reported_cases else None
    return {
        "scope": "all_published_bulletins",
        "basis": (
            "Sum of dashboard per-bulletin health-zone rows across all reports "
            "returned by the GraphQL payload. The dashboard UI states the chart "
            "uses cases reported for that bulletin only, not cumulative rows per "
            "bulletin."
        ),
        "reported_cases": reported_cases,
        "confirmed": totals["cas_confirmes"],
        "deaths": totals["deces"],
        "cfr_pct": cfr_pct,
        "by_province": [
            {"province": province, **values}
            for province, values in sorted(by_province.items())
        ],
        "report_clocks": report_clocks,
        "latest_publication_date": max(
            (str(r.get("date_publication_day") or "") for r in reports),
            default="",
        ) or None,
        "latest_report_date": max(
            (str(r.get("date_rapportage_day") or "") for r in reports),
            default="",
        ) or None,
    }


def _drc_moh_normalized_content(
    source: dict,
    report: dict,
    reports: list[dict],
    *,
    capture_type: str,
) -> dict:
    request = source.get("api_request") or {}
    linked_pdfs = [
        {
            "report_slug": r.get("slug"),
            "report_title": r.get("title"),
            "date_rapportage": r.get("date_rapportage"),
            "date_publication": r.get("date_publication"),
            "url": r.get("pdf_url"),
        }
        for r in reports
        if r.get("pdf_url")
    ]
    return {
        "capture_type": capture_type,
        "landing_url": source.get("landing_url"),
        "api_url": request.get("url"),
        "query": request.get("query"),
        "report_slug": report.get("slug"),
        "report_title": report.get("title"),
        "date_rapportage": report.get("date_rapportage"),
        "date_publication": report.get("date_publication"),
        "date_field_caveat": (
            "date_rapportage and date_publication are the dashboard API fields. "
            "For linked PDFs, verify the printed report/publication dates in the "
            "PDF before final manifest ingest; the API and PDF label can differ."
        ),
        "pdf_officiel": report.get("pdf_url"),
        "linked_pdf_assets": linked_pdfs,
        "table_semantics_status": "source_review",
        "row_total_caveat": (
            "These are summed health-zone rows from the dashboard payload. "
            "They may exclude non-zone rows in the PDF, such as samples without "
            "forms, and must not be treated as headline cumulative totals "
            "without report-level review."
        ),
        "interpretation_caveat": (
            "DRC MoH dashboard fields are official, but the GraphQL zone rows "
            "must be tied back to the report/PDF table before using them as "
            "headline cumulative counts. SitRep 007 exposes cumulative rows; "
            "the latest dashboard rows may represent daily/new values until "
            "the matching PDF or table label is verified."
        ),
        "reported_zone_row_totals": report.get("row_totals", {}),
        "dashboard_aggregate": _drc_moh_dashboard_aggregate(reports),
        "reported_rows": report.get("reported_rows", []),
    }


def _live_drc_moh_dashboard_check(
    source: dict,
    manifest: dict,
    as_of: str,
    row: dict,
    fetch_fn,
) -> dict:
    request = source.get("api_request") or {}
    raw, http_status, content_type = fetch_fn(
        request["url"],
        data=_drc_moh_graphql_body(source),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    payload = json.loads(raw.decode("utf-8"))
    if payload.get("errors"):
        raise ValueError(f"{source['registry_id']}: GraphQL errors: {payload['errors']}")
    epidemic_meta, reports = _drc_moh_reports(payload)
    latest = _drc_moh_latest_report(reports, as_of)
    dashboard_aggregate = _drc_moh_dashboard_aggregate(reports)
    detected_dates = sorted({
        date
        for report in reports
        for date in (report.get("date_rapportage_day"), report.get("date_publication_day"))
        if date
    })
    latest_detected = (
        latest.get("date_publication_day") or latest.get("date_rapportage_day")
        if latest else (max(detected_dates) if detected_dates else None)
    )
    row.update({
        "status": "fetched",
        "url": source.get("landing_url"),
        "api_url": request["url"],
        "http_status": http_status,
        "content_type": content_type,
        "content_length": len(raw),
        "content_hash": _sha256_bytes(raw),
        "detected_dates": detected_dates[-12:],
        "latest_detected_date": latest_detected,
        "outbreak_context_found": True,
        "extracted_counts": {},
        "drc_moh_dashboard": {
            "epidemic": epidemic_meta,
            "report_count": len(reports),
            "latest_report": latest,
            "dashboard_aggregate": dashboard_aggregate,
            "official_pdf_assets": [
                {
                    "report_slug": report.get("slug"),
                    "report_title": report.get("title"),
                    "date_publication": report.get("date_publication_day"),
                    "url": report.get("pdf_url"),
                }
                for report in reports
                if report.get("pdf_url")
            ],
        },
    })
    if latest:
        totals = latest.get("row_totals") or {}
        row["extracted_counts"] = {
            "dashboard_zone_rows_confirmed_total": totals.get("cas_confirmes", 0),
            "dashboard_zone_rows_suspected_total": totals.get("cas_suspects", 0),
            "dashboard_zone_rows_deaths_total": totals.get("deces", 0),
            "dashboard_all_bulletins_reported_cases_total": dashboard_aggregate["reported_cases"],
            "dashboard_all_bulletins_confirmed_total": dashboard_aggregate["confirmed"],
            "dashboard_all_bulletins_deaths_total": dashboard_aggregate["deaths"],
        }
        row["needs_review"] = True
        row["review_reasons"].append("drc_moh_structured_payload_available")
        row["review_reasons"].append("drc_moh_table_semantics_source_review")
        if latest.get("pdf_url"):
            row["review_reasons"].append("official_pdf_asset_available")
        else:
            row["review_reasons"].append("latest_report_pdf_missing")

    newest_archived = row["newest_archived"]
    if latest_detected and newest_archived and latest_detected > newest_archived:
        row["needs_review"] = True
        row["review_reasons"].append("detected_date_newer_than_archive")
    if (
        latest_detected
        and latest_detected == as_of
        and source.get("archive_target") == "outbreak_manifest"
        and (newest_archived is None or newest_archived < as_of)
    ):
        row["needs_review"] = True
        row["review_reasons"].append("detected_as_of_date")
    if row["content_hash"] not in _manifest_hashes(manifest):
        row["needs_review"] = True
        row["review_reasons"].append("bytes_not_in_manifest")
    return row


def _wp_endpoint(root: str, path: str, params: dict[str, object]) -> str:
    return root.rstrip("/") + path + "?" + urllib.parse.urlencode(params)


def _ascii_request_url(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((
        parts.scheme,
        parts.netloc.encode("idna").decode("ascii"),
        urllib.parse.quote(parts.path, safe="/%"),
        urllib.parse.quote(parts.query, safe="=&?/:;+,%"),
        urllib.parse.quote(parts.fragment, safe="=&?/:;+,%"),
    ))


def _strip_html(text: str) -> str:
    parser = _HTMLTextExtractor()
    parser.feed(text or "")
    return parser.text


def _sitrep_number_from_text(text: str) -> int | None:
    patterns = (
        r"(?<![A-Za-z0-9])N[\s_°ºÂ]*0*(\d{1,3})(?!\d)",
        r"(?<![A-Za-z0-9])SitRep[^\d]{0,20}0*(\d{1,3})(?!\d)",
        r"(?<![A-Za-z0-9])N0*(\d{1,3})(?!\d)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None


def _wp_title(item: dict) -> str:
    title = item.get("title") or {}
    return _strip_html(str(title.get("rendered") or ""))


def _insp_wordpress_payload(source: dict, fetch_fn) -> tuple[list[dict], list[dict], bytes, bytes, int | None, int | None, str, str]:
    request = source.get("api_request") or {}
    root = request["url"]
    search = request.get("search") or "SitRep Ebola Bundibugyo"
    posts_path = request.get("posts_path") or "/posts"
    media_path = request.get("media_path") or "/media"
    posts_url = _wp_endpoint(
        root,
        posts_path,
        {"search": search, "per_page": 20, "_embed": 1},
    )
    media_url = _wp_endpoint(
        root,
        media_path,
        {"search": "SitRep Ebola Bundibugyo", "per_page": 50},
    )
    headers = {"Accept": "application/json"}
    posts_raw, posts_status, posts_type = fetch_fn(posts_url, headers=headers)
    media_raw, media_status, media_type = fetch_fn(media_url, headers=headers)
    posts = json.loads(posts_raw.decode("utf-8"))
    media = json.loads(media_raw.decode("utf-8"))
    if not isinstance(posts, list) or not isinstance(media, list):
        raise ValueError("INSP WordPress posts/media responses must be arrays")
    if not posts:
        posts_url = _wp_endpoint(root, posts_path, {"per_page": 20, "_embed": 1})
        posts_raw, posts_status, posts_type = fetch_fn(posts_url, headers=headers)
        posts = json.loads(posts_raw.decode("utf-8"))
        if not isinstance(posts, list):
            raise ValueError("INSP WordPress posts fallback response must be an array")
    if not media:
        media_url = _wp_endpoint(root, media_path, {"per_page": 50})
        media_raw, media_status, media_type = fetch_fn(media_url, headers=headers)
        media = json.loads(media_raw.decode("utf-8"))
        if not isinstance(media, list):
            raise ValueError("INSP WordPress media fallback response must be an array")
    return posts, media, posts_raw, media_raw, posts_status, media_status, posts_type, media_type


def _insp_latest_sitrep(posts: list[dict], media: list[dict]) -> tuple[dict, dict, int]:
    post_candidates = []
    for post in posts:
        title = _wp_title(post)
        text = " ".join([
            title,
            _strip_html(str((post.get("content") or {}).get("rendered") or "")),
            str(post.get("slug") or ""),
        ])
        number = _sitrep_number_from_text(text)
        if number is not None:
            post_candidates.append((number, str(post.get("date") or ""), post))
    if not post_candidates:
        raise ValueError("INSP WordPress feed returned no parseable SitRep posts")
    number, _, latest_post = max(post_candidates, key=lambda row: (row[0], row[1]))

    media_candidates = []
    for item in media:
        url = str(item.get("source_url") or "")
        title = _wp_title(item)
        text = " ".join([title, str(item.get("slug") or ""), url])
        item_number = _sitrep_number_from_text(text)
        if item_number == number and url.lower().endswith(".pdf"):
            media_candidates.append((str(item.get("date") or ""), item))
    if not media_candidates:
        raise ValueError(f"INSP WordPress feed found SitRep {number} post but no matching PDF media")
    latest_media = max(media_candidates, key=lambda row: row[0])[1]
    return latest_post, latest_media, number


def _insp_wordpress_sidecar(
    source: dict,
    post: dict,
    media: dict,
    number: int,
    *,
    source_id: str,
    retrieved_at: str,
) -> dict:
    post_title = _wp_title(post)
    media_title = _wp_title(media)
    text = " ".join([
        post_title,
        media_title,
        _strip_html(str((post.get("content") or {}).get("rendered") or "")),
    ])
    candidates = extract_dates(text)
    post_day = _day(str(post.get("date") or ""))
    media_day = _day(str(media.get("date") or ""))
    return {
        "registry_id": source["registry_id"],
        "source_id": source_id,
        "url": media.get("source_url") or post.get("link") or source["landing_url"],
        "retrieved_at": retrieved_at,
        "published_at": post.get("date") or media.get("date"),
        "outbreak_id": "bdbv-uga-cod-2026",
        "pathogen": "BDBV",
        "country_scope": ["COD"],
        "geography_id": "COD:national",
        "extraction_status": "source_review",
        "normalized_content": {
            "capture_type": "insp_wordpress_sitrep_feed",
            "sitrep_number": number,
            "latest_post": {
                "id": post.get("id"),
                "title": post_title,
                "link": post.get("link"),
                "date": post.get("date"),
                "date_day": post_day,
            },
            "pdf_asset": {
                "id": media.get("id"),
                "title": media_title,
                "source_url": media.get("source_url"),
                "date": media.get("date"),
                "date_day": media_day,
                "sitrep_number": number,
            },
            "publication_date_candidates": candidates,
            "table_semantics_status": "source_review",
            "model_use": "detection_and_private_staging_only_until_reviewed_sitrep_promotion_json",
        },
    }


def _live_insp_wordpress_check(
    source: dict,
    manifest: dict,
    as_of: str,
    row: dict,
    fetch_fn,
) -> dict:
    posts, media, posts_raw, media_raw, posts_status, media_status, posts_type, media_type = (
        _insp_wordpress_payload(source, fetch_fn)
    )
    latest_post, latest_media, number = _insp_latest_sitrep(posts, media)
    dates = sorted({
        date
        for date in (
            _day(str(latest_post.get("date") or "")),
            _day(str(latest_media.get("date") or "")),
        )
        if date
    })
    latest_detected = max(dates) if dates else None
    row.update({
        "status": "fetched",
        "url": latest_media.get("source_url") or latest_post.get("link") or source.get("landing_url"),
        "api_url": (source.get("api_request") or {}).get("url"),
        "http_status": posts_status,
        "content_type": f"posts:{posts_type}; media:{media_type}",
        "content_length": len(posts_raw) + len(media_raw),
        "content_hash": _sha256_bytes(posts_raw + b"\n" + media_raw),
        "detected_dates": dates,
        "latest_detected_date": latest_detected,
        "outbreak_context_found": True,
        "extracted_counts": {},
        "insp_wordpress": {
            "sitrep_number": number,
            "post": {
                "id": latest_post.get("id"),
                "title": _wp_title(latest_post),
                "date": latest_post.get("date"),
                "link": latest_post.get("link"),
            },
            "pdf_asset": {
                "id": latest_media.get("id"),
                "title": _wp_title(latest_media),
                "date": latest_media.get("date"),
                "source_url": latest_media.get("source_url"),
            },
        },
    })
    row["needs_review"] = True
    row["review_reasons"].append("insp_wordpress_sitrep_available")
    row["review_reasons"].append("insp_wordpress_source_review_required")
    row["review_reasons"].append("official_pdf_asset_available")
    if row["content_hash"] not in _manifest_hashes(manifest):
        row["review_reasons"].append("bytes_not_in_manifest")
    return row


def live_source_check(
    source: dict,
    manifest: dict,
    as_of: str,
    fetch_fn=_fetch_url,
) -> dict:
    """Fetch one registry source and return a source-freshness row."""
    prefix = source.get("manifest_source_prefix")
    archived_entry = newest_entry(manifest, prefix)
    newest_archived = newest_edition(manifest, prefix)
    row = {
        "registry_id": source["registry_id"],
        "title": source.get("title"),
        "publisher": source.get("publisher"),
        "source_tier": source.get("source_tier"),
        "feeds": source.get("feeds", []),
        "url": source.get("landing_url"),
        "archive_target": source.get("archive_target"),
        "extractor_backend": source.get("extractor_backend"),
        "capture_backend": (
            "air_preferred"
            if source.get("extractor_backend") == "air_preferred"
            else "plain_http"
        ),
        "capture_note": (
            "Use AIR for direct text/media extraction when the landing page is "
            "social, dynamic, or otherwise incomplete under plain HTTP; archive "
            "the AIR output through the same manifest review path before scored use."
            if source.get("extractor_backend") == "air_preferred"
            else None
        ),
        "latest_known": source.get("latest_known"),
        "newest_archived": newest_archived,
        "latest_archived_source_id": (archived_entry or {}).get("source_id"),
        "latest_archived_counts": _latest_archived_counts(archived_entry),
        "retrieved_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "status": "not_checked",
        "http_status": None,
        "content_type": None,
        "content_length": None,
        "content_hash": None,
        "detected_dates": [],
        "latest_detected_date": None,
        "extracted_counts": {},
        "needs_review": False,
        "review_reasons": [],
    }

    try:
        if source.get("github_release"):
            return _live_github_release_check(source, manifest, as_of, row, fetch_fn)
        if (source.get("api_request") or {}).get("response_kind") == "insp_wordpress_sitrep_feed":
            return _live_insp_wordpress_check(source, manifest, as_of, row, fetch_fn)
        if (source.get("api_request") or {}).get("response_kind") == "drc_moh_epidemie_dashboard":
            return _live_drc_moh_dashboard_check(source, manifest, as_of, row, fetch_fn)
        if source.get("hdx_package_id"):
            package_doc, raw, http_status, content_type = _fetch_hdx_package(
                source["hdx_package_id"],
                fetch_fn=fetch_fn,
            )
            metadata_modified = package_doc.get("metadata_modified")
            latest_detected = metadata_modified[:10] if metadata_modified else None
            row.update({
                "status": "fetched",
                "http_status": http_status,
                "content_type": content_type,
                "content_length": len(raw),
                "content_hash": _sha256_bytes(raw),
                "detected_dates": [latest_detected] if latest_detected else [],
                "latest_detected_date": latest_detected,
                "outbreak_context_found": True,
                "extracted_counts": {},
                "hdx_package": {
                    "package_id": source["hdx_package_id"],
                    "title": package_doc.get("title"),
                    "license_id": package_doc.get("license_id"),
                    "license_title": package_doc.get("license_title"),
                    "metadata_modified": metadata_modified,
                    "dataset_date": package_doc.get("dataset_date"),
                    "resources": _hdx_resource_summary(package_doc),
                },
            })
            if latest_detected and newest_archived and latest_detected > newest_archived:
                row["needs_review"] = True
                row["review_reasons"].append("hdx_metadata_modified_newer_than_archive")
            if row["content_hash"] not in _manifest_hashes(manifest):
                row["review_reasons"].append("bytes_not_in_manifest")
            return row
        raw, http_status, content_type = fetch_fn(source["landing_url"])
    except (OSError, TimeoutError, urllib.error.URLError, urllib.error.HTTPError, ValueError, json.JSONDecodeError) as exc:
        if source.get("extractor_backend") == "air_preferred":
            row.update({
                "status": "air_capture_required",
                "error": str(exc),
                "needs_review": True,
                "review_reasons": ["air_capture_required"],
            })
            return row
        row.update({
            "status": "fetch_failed",
            "error": str(exc),
            "needs_review": True,
            "review_reasons": ["fetch_failed"],
        })
        return row

    text = _visible_text(raw, content_type)
    detected_dates = extract_dates(text)
    outbreak_context = _has_outbreak_context(text)
    extracted_counts = extract_count_tuple(text) if outbreak_context else {}
    latest_detected = max(detected_dates) if detected_dates else None
    row.update({
        "status": "fetched",
        "http_status": http_status,
        "content_type": content_type,
        "content_length": len(raw),
        "content_hash": _sha256_bytes(raw),
        "detected_dates": detected_dates[-12:],
        "latest_detected_date": latest_detected,
        "outbreak_context_found": outbreak_context,
        "extracted_counts": extracted_counts,
    })

    context_only_date = _context_only_date_reason(source, extracted_counts)
    if latest_detected and newest_archived and latest_detected > newest_archived:
        row["needs_review"] = True
        row["review_reasons"].append(
            "context_update_date_newer_than_archive"
            if context_only_date
            else "detected_date_newer_than_archive"
        )
    if (
        latest_detected
        and latest_detected == as_of
        and source.get("archive_target") == "outbreak_manifest"
        and (newest_archived is None or newest_archived < as_of)
    ):
        row["needs_review"] = True
        row["review_reasons"].append(
            "context_update_as_of_date"
            if context_only_date
            else "detected_as_of_date"
        )
    archived_counts = row["latest_archived_counts"]
    comparable = {
        k: v for k, v in extracted_counts.items()
        if k in archived_counts and archived_counts[k] != v
    }
    if comparable:
        row["needs_review"] = True
        row["review_reasons"].append("count_tuple_differs_from_latest_archive")
        row["count_differences"] = {
            k: {"archived": archived_counts[k], "live": extracted_counts[k]}
            for k in comparable
        }
    if row["content_hash"] not in _manifest_hashes(manifest):
        row["review_reasons"].append("bytes_not_in_manifest")
    return row


def _source_by_registry_id(registry: dict, registry_id: str) -> dict | None:
    return next((source for source in registry["sources"] if source["registry_id"] == registry_id), None)


def pull_github_release_source(
    source: dict,
    as_of: str,
    *,
    fetch_fn=_fetch_url,
    now_fn=_now_utc_iso_z,
) -> int:
    retrieved_at = now_fn()
    try:
        release, _, _, _ = _fetch_github_latest_release(source, fetch_fn)
        asset = _github_release_asset(source, release)
        raw, asset_status, asset_content_type = fetch_fn(asset["browser_download_url"])
        expected_digest = _github_asset_digest(asset)
        actual_digest = _sha256_bytes(raw)
        if expected_digest and expected_digest != actual_digest:
            raise ValueError(
                f"release asset digest mismatch: GitHub={expected_digest} actual={actual_digest}"
            )
        dataset_summary = _inrb_release_dataset_summary(raw)
    except (OSError, TimeoutError, urllib.error.URLError, urllib.error.HTTPError, ValueError, json.JSONDecodeError, tarfile.TarError) as exc:
        print(f"ERROR: failed to pull {source['registry_id']}: {exc}")
        return 2

    tag = str(release.get("tag_name") or as_of)
    source_id = f"{source['manifest_source_prefix']}-{_safe_slug(tag)}"
    asset_name = f"{source_id}.tar.gz"
    asset_path = DROPBOX / asset_name
    _write_dropbox_file(asset_path, raw)
    _write_sidecar(
        asset_path.with_name(asset_path.name + ".meta.json"),
        _inrb_release_sidecar(
            source,
            release,
            asset,
            dataset_summary,
            source_id=source_id,
            retrieved_at=retrieved_at,
        ),
    )

    metrics = dataset_summary.get("metrics") or {}
    confirmed = (metrics.get("national_cumulative_confirmed_cases") or {}).get("value")
    suspected = (metrics.get("national_cumulative_suspected_cases") or {}).get("value")
    suspected_deaths = (metrics.get("national_cumulative_suspected_deaths") or {}).get("value")
    confirmed_deaths = (metrics.get("national_cumulative_confirmed_deaths") or {}).get("value")
    print(_BAR)
    print(f"Pulled {source['registry_id']} GitHub release")
    print(_BAR)
    print(f"  release={tag} published={release.get('published_at')}")
    print(f"  asset status={asset_status} content_type={asset_content_type} bytes={len(raw)}")
    print(f"  asset sha256={actual_digest}")
    # Cumulative epidemiological metrics are laboratory-confirmed only (the
    # cumulative suspected tier was retired 2026-06-02 as non-monotonic and
    # uninterpretable as incidence). The upstream suspected figures are echoed
    # here as raw upstream operational reads for the operator's source-diff
    # awareness; they are never summed into confirmed.
    print(
        "  DRC cumulative (confirmed-only): "
        f"confirmed={confirmed} confirmed_deaths={confirmed_deaths}"
    )
    print(
        "  upstream suspected (raw operational read, not cumulative, not summed): "
        f"suspected={suspected} suspected_deaths={suspected_deaths}"
    )
    print(f"  wrote {_display_path(asset_path)}")
    print("\nNext: review sidecar, then archive with:")
    print(f"  python3 source_ingest.py --ingest '{_display_path(asset_path)}'")
    print(_BAR)
    return 0


def pull_insp_wordpress_source(
    source: dict,
    as_of: str,
    *,
    fetch_fn=_fetch_url,
    now_fn=_now_utc_iso_z,
) -> int:
    retrieved_at = now_fn()
    try:
        posts, media, posts_raw, media_raw, posts_status, media_status, posts_type, media_type = (
            _insp_wordpress_payload(source, fetch_fn)
        )
        latest_post, latest_media, number = _insp_latest_sitrep(posts, media)
        pdf_url = str(latest_media.get("source_url") or "")
        pdf_raw, pdf_status, pdf_type = fetch_fn(pdf_url)
    except (OSError, TimeoutError, urllib.error.URLError, urllib.error.HTTPError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: failed to pull {source['registry_id']}: {exc}")
        return 2

    post_day = _day(str(latest_post.get("date") or "")) or as_of
    post_id = latest_post.get("id")
    media_id = latest_media.get("id")
    stem = f"insp-wordpress-sitrep-n{number:03d}"
    api_name = f"{stem}-api-wp{post_id}-{post_day}.json"
    api_path = DROPBOX / api_name
    api_source_id = f"{stem}-api-wp{post_id}-{post_day}"
    api_bundle = {
        "posts": posts,
        "media": media,
        "latest_post_id": post_id,
        "latest_media_id": media_id,
    }
    api_raw = json.dumps(api_bundle, indent=2, sort_keys=True).encode("utf-8")
    _write_dropbox_file(api_path, api_raw)
    _write_sidecar(
        api_path.with_name(api_path.name + ".meta.json"),
        _insp_wordpress_sidecar(
            source,
            latest_post,
            latest_media,
            number,
            source_id=api_source_id,
            retrieved_at=retrieved_at,
        ),
    )

    pdf_name = f"{stem}-pdf-media{media_id}-{post_day}.pdf"
    pdf_path = DROPBOX / pdf_name
    pdf_source_id = f"{stem}-pdf-media{media_id}-{post_day}"
    _write_dropbox_file(pdf_path, pdf_raw)
    _write_sidecar(
        pdf_path.with_name(pdf_path.name + ".meta.json"),
        _insp_wordpress_sidecar(
            source,
            latest_post,
            latest_media,
            number,
            source_id=pdf_source_id,
            retrieved_at=retrieved_at,
        ),
    )

    print(_BAR)
    print(f"Pulled {source['registry_id']} WordPress SitRep feed")
    print(_BAR)
    print(f"  latest=N{number} published={post_day}")
    print(f"  posts status={posts_status} media status={media_status} bytes={len(posts_raw) + len(media_raw)}")
    print(f"  wrote {_display_path(api_path)}")
    print(f"  latest PDF status={pdf_status} content_type={pdf_type} bytes={len(pdf_raw)} -> {_display_path(pdf_path)}")
    print("\nNext: review sidecars, extract/validate SitRep tables, then archive with:")
    print(f"  python3 source_ingest.py --ingest '{_display_path(api_path)}'")
    print(_BAR)
    return 0


def _write_dropbox_file(path: pathlib.Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_bytes() if path.exists() else None
    if existing != raw:
        path.write_bytes(raw)


def _write_sidecar(path: pathlib.Path, meta: dict) -> None:
    path.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _drc_moh_sidecar(
    source: dict,
    report: dict,
    reports: list[dict],
    *,
    source_id: str,
    url: str,
    retrieved_at: str,
    capture_type: str,
    ) -> dict:
    publication_day = report.get("date_publication_day") or report.get("date_rapportage_day")
    report_day = report.get("date_rapportage_day")
    normalized = _drc_moh_normalized_content(
        source,
        report,
        reports,
        capture_type=capture_type,
    )
    if capture_type == "official_pdf":
        normalized["api_date_rapportage"] = normalized.pop("date_rapportage", None)
        normalized["api_date_publication"] = normalized.pop("date_publication", None)
        normalized["date_review_required"] = True
        normalized["date_review_note"] = (
            "Linked PDF date fields come from the dashboard API and must be "
            "checked against the PDF's printed report/publication labels before "
            "manifest ingest or plotting."
        )
        published_at = None
    else:
        normalized |= {
            "data_as_of": report_day,
            "publication_date": publication_day,
        }
        published_at = f"{publication_day}T00:00:00Z" if publication_day else None
    return {
        "registry_id": source["registry_id"],
        "source_id": source_id,
        "url": url,
        "retrieved_at": retrieved_at,
        "published_at": published_at,
        "outbreak_id": "bdbv-uga-cod-2026",
        "pathogen": "BDBV",
        "country_scope": ["COD"],
        "geography_id": "COD:Ituri/Nord-Kivu/Sud-Kivu",
        "extraction_status": "partial",
        "normalized_content": normalized,
    }


def pull_source(
    registry_id: str,
    as_of: str,
    *,
    include_linked_pdfs: bool = True,
    fetch_fn=_fetch_url,
    now_fn=_now_utc_iso_z,
) -> int:
    registry = _load(REGISTRY)
    source = _source_by_registry_id(registry, registry_id)
    if source is None:
        print(f"ERROR: no registry source {registry_id!r}")
        return 2
    if source.get("github_release"):
        return pull_github_release_source(source, as_of, fetch_fn=fetch_fn, now_fn=now_fn)
    if (source.get("api_request") or {}).get("response_kind") == "insp_wordpress_sitrep_feed":
        return pull_insp_wordpress_source(source, as_of, fetch_fn=fetch_fn, now_fn=now_fn)
    if (source.get("api_request") or {}).get("response_kind") != "drc_moh_epidemie_dashboard":
        print("ERROR: --pull-source currently supports drc_moh_epidemie_dashboard, insp_wordpress_sitrep_feed, and github_release sources only")
        return 2

    retrieved_at = now_fn()
    request = source["api_request"]
    try:
        raw, http_status, content_type = fetch_fn(
            request["url"],
            data=_drc_moh_graphql_body(source),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        payload = json.loads(raw.decode("utf-8"))
        if payload.get("errors"):
            raise ValueError(f"GraphQL errors: {payload['errors']}")
        _, reports = _drc_moh_reports(payload)
    except (OSError, TimeoutError, urllib.error.URLError, urllib.error.HTTPError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: failed to pull {registry_id}: {exc}")
        return 2

    latest = _drc_moh_latest_report(reports, as_of)
    if latest is None:
        print(f"ERROR: {registry_id} returned no reports")
        return 2

    publication_day = latest.get("date_publication_day") or latest.get("date_rapportage_day") or as_of
    report_slug = _safe_slug(str(latest.get("slug") or latest.get("title") or "report"))
    graphql_name = f"{registry_id}-{report_slug}-graphql-{publication_day}.json"
    graphql_path = DROPBOX / graphql_name
    graphql_source_id = f"{registry_id}-{report_slug}-graphql-{publication_day}"
    _write_dropbox_file(graphql_path, raw)
    _write_sidecar(
        graphql_path.with_name(graphql_path.name + ".meta.json"),
        _drc_moh_sidecar(
            source,
            latest,
            reports,
            source_id=graphql_source_id,
            url=request["url"],
            retrieved_at=retrieved_at,
            capture_type="graphql_response",
        ),
    )

    print(_BAR)
    print(f"Pulled {registry_id} dashboard API")
    print(_BAR)
    print(f"  API status={http_status} content_type={content_type} bytes={len(raw)}")
    print(f"  latest_report={latest.get('slug')} published={publication_day}")
    print(f"  wrote {_display_path(graphql_path)}")

    if include_linked_pdfs:
        for report in reports:
            pdf_url = report.get("pdf_url")
            if not pdf_url:
                continue
            pdf_day = report.get("date_publication_day") or report.get("date_rapportage_day") or publication_day
            pdf_slug = _safe_slug(str(report.get("slug") or report.get("title") or "report"))
            pdf_name = f"{registry_id}-{pdf_slug}-official-pdf-{pdf_day}.pdf"
            pdf_path = DROPBOX / pdf_name
            pdf_source_id = f"{registry_id}-{pdf_slug}-pdf-{pdf_day}"
            try:
                pdf_raw, pdf_status, pdf_content_type = fetch_fn(pdf_url)
            except (OSError, TimeoutError, urllib.error.URLError, urllib.error.HTTPError) as exc:
                print(f"  PDF fetch failed for {pdf_url}: {exc}")
                continue
            _write_dropbox_file(pdf_path, pdf_raw)
            _write_sidecar(
                pdf_path.with_name(pdf_path.name + ".meta.json"),
                _drc_moh_sidecar(
                    source,
                    report,
                    reports,
                    source_id=pdf_source_id,
                    url=pdf_url,
                    retrieved_at=retrieved_at,
                    capture_type="official_pdf",
                ),
            )
            print(
                f"  linked PDF status={pdf_status} content_type={pdf_content_type} "
                f"bytes={len(pdf_raw)} -> {_display_path(pdf_path)}"
            )

    print("\nNext: review sidecars, then archive with:")
    print(f"  python3 source_ingest.py --ingest '{_display_path(graphql_path)}'")
    print(_BAR)
    return 0


def live_check(
    as_of: str,
    out_path: pathlib.Path | None = None,
    *,
    slot_id: str | None = None,
) -> int:
    registry = _load(REGISTRY)
    manifest = _load(MANIFEST)
    sources = registry["sources"]
    if slot_id:
        try:
            slot_source_ids = set(source_schedule.source_ids_for_slot(registry, slot_id))
        except source_schedule.SourceScheduleError as exc:
            print(f"ERROR: {exc}")
            return 2
        sources = [source for source in sources if source["registry_id"] in slot_source_ids]
    rows = [
        live_source_check(source, manifest, as_of)
        for source in sources
    ]
    report_doc = {
        "schema_version": 1,
        "outbreak_id": registry.get("_meta", {}).get("outbreak_id"),
        "as_of": as_of,
        "slot_id": slot_id,
        "generated_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "purpose": (
            "Live freshness check for recurring BDBV 2026 sources. This report "
            "does not mutate released snapshots; rows with needs_review=true "
            "should be archived through the manifest before a scored refresh."
        ),
        "sources": rows,
        "summary": {
            "checked": len(rows),
            "fetched": sum(1 for r in rows if r["status"] == "fetched"),
            "fetch_failed": sum(1 for r in rows if r["status"] == "fetch_failed"),
            "air_capture_required": sum(1 for r in rows if r["status"] == "air_capture_required"),
            "needs_review": sum(1 for r in rows if r["needs_review"]),
            "with_extracted_counts": sum(1 for r in rows if r["extracted_counts"]),
            "air_preferred": sum(1 for r in rows if r.get("capture_backend") == "air_preferred"),
        },
    }
    if out_path is None:
        slot_suffix = f"-{slot_id}" if slot_id else ""
        out_path = FRESHNESS_DIR / f"bdbv-2026-{as_of}{slot_suffix}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report_doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(_BAR)
    slot_label = f"  |  slot {slot_id}" if slot_id else ""
    print(f"Live source freshness  |  as of {as_of}{slot_label}")
    print(_BAR)
    for row in rows:
        reasons = ",".join(row["review_reasons"]) if row["review_reasons"] else "-"
        latest = row["latest_detected_date"] or "-"
        counts = "counts" if row["extracted_counts"] else "no-counts"
        print(
            f"  - {row['registry_id']:<22} {row['status']:<12} "
            f"latest_date={latest:<10} {counts:<9} review={row['needs_review']} {reasons}"
        )
    print(f"\nWrote {out_path}")
    print(_BAR)
    return 0


def print_schedule(out_path: pathlib.Path | None = None) -> int:
    registry = _load(REGISTRY)
    try:
        plan = source_schedule.build_schedule(registry)
    except source_schedule.SourceScheduleError as exc:
        print(f"ERROR: {exc}")
        return 2
    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"Wrote {out_path}")
        return 0

    print(_BAR)
    print("Scheduled source-prep checks (UTC cron; unpublished review prep)")
    print(_BAR)
    for slot in plan["slots"]:
        print(f"{slot['cron_utc']:<14} {slot['command']}")
        print(f"  {slot['slot_id']}: {slot['source_count']} source(s)")
        print(f"  {slot['local_rationale']}")
    print(_BAR)
    print(
        "Boundary: these jobs write freshness/prep reports, may stage private "
        "review bytes, and may refresh the unpublished website preview; manifest "
        "promotion and release remain manual/gated."
    )
    return 0


def report(as_of: str) -> int:
    registry = _load(REGISTRY)
    manifest = _load(MANIFEST)
    print(_BAR)
    print(f"Recurring-source gate  |  as of {as_of}")
    print(_BAR)

    print("Cadence status (data/external_sources/source_registry.json):")
    action = 0
    for source in registry["sources"]:
        st = cadence_status(source, manifest, as_of)
        flag = ""
        if st["status"] in ("DUE", "OVERDUE", "UNARCHIVED"):
            flag = "  <-- action"
            action += 1
        gap = f"gap={st['gap_days']}d" if st["gap_days"] is not None else "gap=n/a"
        print(
            f"  - {st['registry_id']:<22} {st['status']:<10} "
            f"newest={str(st['newest_archived']):<12} {gap}{flag}"
        )

    print("\nDropbox (data/bundibugyo-2026/private/sources/):")
    rows = scan_dropbox(registry, manifest)
    if not rows:
        print("  (empty or absent)")
    pending = 0
    for r in rows:
        if r["archived"]:
            tag = "archived"
        elif not r["registry_id"]:
            tag = "no registry match (context)"
        elif r["archive_target"] != "outbreak_manifest":
            tag = f"-> {r['archive_target']}"
        else:
            tag = "PENDING INGEST"
            pending += 1
        rid = r["registry_id"] or "-"
        print(f"  - [{tag:<28}] {rid:<22} {r['file']}")

    print(_BAR)
    if action or pending:
        print(
            f"ACTION: {action} recurring source(s) due/overdue/unarchived; "
            f"{pending} dropbox file(s) pending ingest."
        )
        print("Ingest a pending file: python3 source_ingest.py --ingest '<path>'")
        print("(provide a '<file>.meta.json' sidecar with extracted figures first)")
    else:
        print("OK: all recurring sources current; no pending dropbox files.")
    return 0


def ingest(file_path: str, as_of: str) -> int:
    registry = _load(REGISTRY)
    path = pathlib.Path(file_path)
    if not path.is_absolute():
        path = (REPO_ROOT / path).resolve()
    if not path.exists():
        print(f"ERROR: file not found: {path}")
        return 2
    sidecar = path.with_name(path.name + ".meta.json")
    if not sidecar.exists():
        print(f"ERROR: sidecar not found: {sidecar}")
        print("Create it with: source_id, published_at, retrieved_at, geography_id, normalized_content")
        return 2

    meta = _load(sidecar)
    source = None
    if meta.get("registry_id"):
        source = next((s for s in registry["sources"] if s["registry_id"] == meta["registry_id"]), None)
    if source is None:
        source = match_registry(registry, path.name)
    if source is None:
        print(f"ERROR: no registry source matches {path.name!r}; set 'registry_id' in the sidecar.")
        return 2

    target = source.get("archive_target")
    if target != "outbreak_manifest":
        print(
            f"ERROR: registry source {source['registry_id']!r} has archive_target "
            f"{target!r}; this tool only ingests outbreak_manifest sources. "
            f"(IOM FMR provenance belongs in poe_traveler_counts.restricted.json.)"
        )
        return 2

    raw = path.read_bytes()
    content_hash = hashlib.sha256(raw).hexdigest()
    provenance = lovs_archive.ProvenanceRecord(
        source_id=meta["source_id"],
        source_tier=source["source_tier"],
        publisher=source["publisher"],
        url=meta.get("url") or source["landing_url"],
        retrieved_at=meta["retrieved_at"],
        published_at=meta.get("published_at"),
        content_hash=content_hash,
        license=source["license"],
        extraction_status=meta.get("extraction_status", "success"),
        root_provenance_chain=tuple(meta.get("root_provenance_chain", [])),
    )
    restricted = source.get("redistribution") == "restricted"
    snapshot_meta = {
        "outbreak_id": meta.get("outbreak_id", "bdbv-uga-cod-2026"),
        "pathogen": meta.get("pathogen", "BDBV"),
        "country_scope": meta.get("country_scope", ["COD", "UGA"]),
        "geography_id": meta["geography_id"],
        "normalized_content": meta["normalized_content"],
        "raw_archive_status": "private_restricted_bytes" if restricted else "public_bytes",
        "raw_bytes_relpath": None if restricted else f"raw/{content_hash}",
    }
    if restricted:
        lovs_archive.add_restricted_snapshot(MANIFEST_DIR, provenance, snapshot_meta, raw)
        where = f"private/raw/{content_hash} (hash-recorded; bytes not redistributed)"
    else:
        lovs_archive.add_snapshot(MANIFEST_DIR, provenance, snapshot_meta, raw)
        where = f"raw/{content_hash}"
    print(f"Archived {meta['source_id']} ({source['registry_id']}) -> {where}")
    print(f"  content_hash={content_hash}")
    print(f"  published_at={provenance.published_at}  tier={provenance.source_tier}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--as-of", default=dt.date.today().isoformat(), help="Date for cadence (YYYY-MM-DD).")
    parser.add_argument("--ingest", metavar="PATH", help="Ingest a dropbox file (needs <file>.meta.json).")
    parser.add_argument(
        "--pull-source",
        metavar="REGISTRY_ID",
        help=(
            "Pull a registry-backed source into the private dropbox and create sidecars. "
            "Currently supports the DRC MoH dashboard API/PDF source and GitHub release assets."
        ),
    )
    parser.add_argument(
        "--skip-linked-pdfs",
        action="store_true",
        help="With --pull-source, do not fetch official PDFs linked from the API payload.",
    )
    parser.add_argument(
        "--live-check",
        action="store_true",
        help="Fetch registered source landing URLs and write a freshness report.",
    )
    parser.add_argument(
        "--slot",
        help="With --live-check, fetch only the sources assigned to this scheduled-prep slot.",
    )
    parser.add_argument(
        "--schedule",
        action="store_true",
        help="Print the UTC cron schedule for source-prep live checks without fetching sources.",
    )
    parser.add_argument(
        "--out",
        type=pathlib.Path,
        help=(
            "Output path for --live-check JSON (default: "
            "data/external_sources/freshness/bdbv-2026-<as_of>[-<slot>].json)."
        ),
    )
    args = parser.parse_args(argv)
    if args.ingest:
        return ingest(args.ingest, args.as_of)
    if args.pull_source:
        return pull_source(
            args.pull_source,
            args.as_of,
            include_linked_pdfs=not args.skip_linked_pdfs,
        )
    if args.schedule:
        return print_schedule(args.out)
    if args.live_check:
        return live_check(args.as_of, args.out, slot_id=args.slot)
    return report(args.as_of)


if __name__ == "__main__":
    raise SystemExit(main())
