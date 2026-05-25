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
import datetime as dt
import hashlib
import html.parser
import json
import pathlib
import re
import urllib.error
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
        iso = _iso_date(
            int(match.group(3)),
            _MONTHS[match.group(1).lower()],
            int(match.group(2)),
        )
        if iso:
            dates.add(iso)
    for match in _DATE_PATTERNS[2].finditer(text):
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
        url,
        data=data,
        headers=request_headers,
    )
    context = lovs_live_ingest._resolve_ssl_context()
    with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
        return (
            response.read(),
            getattr(response, "status", None),
            response.headers.get("Content-Type", ""),
        )


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
    report_day = report.get("date_rapportage_day") or publication_day
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
    if (source.get("api_request") or {}).get("response_kind") != "drc_moh_epidemie_dashboard":
        print(f"ERROR: --pull-source currently supports drc_moh_epidemie_dashboard sources only")
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
    print(f"  wrote {graphql_path.relative_to(REPO_ROOT)}")

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
                f"bytes={len(pdf_raw)} -> {pdf_path.relative_to(REPO_ROOT)}"
            )

    print("\nNext: review sidecars, then archive with:")
    print(f"  python3 source_ingest.py --ingest '{graphql_path.relative_to(REPO_ROOT)}'")
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
            "Currently supports the DRC MoH dashboard API/PDF source."
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
