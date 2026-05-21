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

  python3 source_ingest.py                         # --report (default)
  python3 source_ingest.py --ingest '<path-to-file-in-dropbox>'

Ingest is byte + provenance automated; the EXTRACTED FIGURES must be supplied
in a sidecar JSON next to the file: '<file>.meta.json'. Public sources are
archived under raw/<sha256> (redistributed); restricted publisher material is
hash-recorded with bytes kept under the gitignored private/raw/<sha256>.

Read-only in --report mode; writes only the manifest + a private/raw copy in
--ingest mode. Stdlib only.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import pathlib

from lovs import lovs_archive

REPO_ROOT = pathlib.Path(__file__).parent.resolve()
DATA = REPO_ROOT / "data"
REGISTRY = DATA / "external_sources" / "source_registry.json"
MANIFEST_DIR = DATA / "bundibugyo-2026"
MANIFEST = MANIFEST_DIR / "manifest.json"
DROPBOX = MANIFEST_DIR / "private" / "sources"

_BAR = "=" * 74


def _load(path: pathlib.Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256_file(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
    args = parser.parse_args(argv)
    if args.ingest:
        return ingest(args.ingest, args.as_of)
    return report(args.as_of)


if __name__ == "__main__":
    raise SystemExit(main())
