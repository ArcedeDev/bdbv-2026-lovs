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
  python3 source_ingest.py --live-check --as-of 2026-05-21
  python3 source_ingest.py --ingest '<path-to-file-in-dropbox>'

Ingest is byte + provenance automated; the EXTRACTED FIGURES must be supplied
in a sidecar JSON next to the file: '<file>.meta.json'. Public sources are
archived under raw/<sha256> (redistributed); restricted publisher material is
hash-recorded with bytes kept under the gitignored private/raw/<sha256>.

Read-only in --report mode. --live-check fetches registered landing URLs and
writes a freshness JSON report; it does not mutate released snapshots. --ingest
writes only the manifest + a private/raw copy. Stdlib only.
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

REPO_ROOT = pathlib.Path(__file__).parent.resolve()
DATA = REPO_ROOT / "data"
REGISTRY = DATA / "external_sources" / "source_registry.json"
MANIFEST_DIR = DATA / "bundibugyo-2026"
MANIFEST = MANIFEST_DIR / "manifest.json"
DROPBOX = MANIFEST_DIR / "private" / "sources"
FRESHNESS_DIR = DATA / "external_sources" / "freshness"

_BAR = "=" * 74


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
        if not path.is_file() or path.suffix.lower() == ".json":
            continue  # skip sidecars and non-files
        h = _sha256_file(path)
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


def _fetch_url(url: str, timeout: int = 30) -> tuple[bytes, int | None, str]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "bdbv-2026-lovs/0.1.0 "
                "(public-health surveillance validation; source freshness check)"
            ),
            "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.8",
        },
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


def live_check(as_of: str, out_path: pathlib.Path | None = None) -> int:
    registry = _load(REGISTRY)
    manifest = _load(MANIFEST)
    rows = [
        live_source_check(source, manifest, as_of)
        for source in registry["sources"]
    ]
    report_doc = {
        "schema_version": 1,
        "outbreak_id": registry.get("_meta", {}).get("outbreak_id"),
        "as_of": as_of,
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
        },
    }
    if out_path is None:
        out_path = FRESHNESS_DIR / f"bdbv-2026-{as_of}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report_doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(_BAR)
    print(f"Live source freshness  |  as of {as_of}")
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
        "--live-check",
        action="store_true",
        help="Fetch registered source landing URLs and write a freshness report.",
    )
    parser.add_argument(
        "--out",
        type=pathlib.Path,
        help="Output path for --live-check JSON (default: data/external_sources/freshness/bdbv-2026-<as_of>.json).",
    )
    args = parser.parse_args(argv)
    if args.ingest:
        return ingest(args.ingest, args.as_of)
    if args.live_check:
        return live_check(args.as_of, args.out)
    return report(args.as_of)


if __name__ == "__main__":
    raise SystemExit(main())
