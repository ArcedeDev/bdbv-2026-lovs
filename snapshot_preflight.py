#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Preflight check for a LOVS snapshot run: confirm no lever or target is missing.

Run this before generating a snapshot (for example the 2026-05-21 run) to verify
that every data leverage in data/external_sources/catalog.json is accounted for,
that every candidate target zone in data/snapshot_targets.json has a map centroid
in data/zones.json, and that the manifest is fresh enough for the requested as_of.

Exits 0 when ready, 3 when a hard gap is found (a target without a centroid, or an
as_of newer than the newest archived source). Read-only: writes nothing.

  python3 snapshot_preflight.py
  python3 snapshot_preflight.py --as-of 2026-05-21
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib

import source_ingest
import refresh_pipeline
from lovs import lovs_staged_observations

REPO_ROOT = pathlib.Path(__file__).parent.resolve()
DATA = REPO_ROOT / "data"
CATALOG = DATA / "external_sources" / "catalog.json"
OBSERVED = DATA / "external_sources" / "bdbv-2026.observed.json"
WATCH = DATA / "external_sources" / "bdbv-2026.watch.json"
REGISTRY = DATA / "external_sources" / "source_registry.json"
TARGETS = DATA / "snapshot_targets.json"
ZONES = DATA / "zones.json"
MANIFEST = DATA / "bundibugyo-2026" / "manifest.json"
LEDGER = DATA / "calibration-ledger.json"

PUBLIC_SURFACE_GLOBS: tuple[str, ...] = (
    "*.md",
    "brief/brief.html",
    "data/external_sources/README.md",
    "deliverables/public-health-dataset/*.csv",
    "deliverables/public-health-dataset/*.json",
)

PUBLIC_SURFACE_ALLOWED_TERMS: dict[str, tuple[str, ...]] = {
    "deliverables/public-health-dataset/lovs-public-health-dataset.manifest.json": (
        "staged_observations.csv",
    ),
}

PUBLIC_SURFACE_INTERNAL_TERMS: tuple[str, ...] = (
    "Evidence promotion standard",
    "complete evidence change",
    "internal checklist",
    "private operating doctrine",
    "source_chase",
    "source chase",
    "source-chasing",
    "promotion_criteria",
    "credibility_assessment",
    "not_model_input",
    "blocked_pending_official_confirmation",
    "official_origin_",
    "watch_signals",
    "staged_observations",
    "not a model input",
)


def _load(path: pathlib.Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _public_surface_paths() -> list[pathlib.Path]:
    paths: set[pathlib.Path] = set()
    for pattern in PUBLIC_SURFACE_GLOBS:
        paths.update(REPO_ROOT.glob(pattern))
    return sorted(path for path in paths if path.is_file())


def validate_public_surfaces() -> list[str]:
    """Return public-facing docs/export text that still exposes internal workflow terms."""
    gaps: list[str] = []
    for path in _public_surface_paths():
        rel = path.relative_to(REPO_ROOT).as_posix()
        text = path.read_text(encoding="utf-8", errors="ignore")
        for allowed in PUBLIC_SURFACE_ALLOWED_TERMS.get(rel, ()):
            text = text.replace(allowed, "")
        lower = text.lower()
        for term in PUBLIC_SURFACE_INTERNAL_TERMS:
            if term.lower() in lower:
                gaps.append(f"{rel}: internal workflow term {term!r}")
    return gaps


def run(as_of: str, data_as_of: str | None = None) -> int:
    """Print the preflight checklist; return 0 if ready, 3 if a hard gap exists.

    `as_of` is the analytic/publication date (may run ahead under forward-dated
    versioning, Model 1). `data_as_of` is the snapshot's data cutoff (the newest
    source DATA date); when supplied, the manifest-freshness gate checks evidence
    against it rather than the publication date. Defaults to `as_of`.
    """
    data_cutoff = (data_as_of or as_of)[:10]
    catalog = _load(CATALOG)
    targets_cfg = _load(TARGETS)
    zones = {z["id"]: z for z in _load(ZONES)["zones"]}
    manifest = _load(MANIFEST)
    ledger = _load(LEDGER)
    observed = _load(OBSERVED) if OBSERVED.exists() else {}
    watch = _load(WATCH) if WATCH.exists() else {}
    manifest_source_ids = {
        entry["source_id"]
        for entry in manifest.get("entries", [])
        if entry.get("source_id")
    }

    gaps = 0
    bar = "=" * 74
    print(bar)
    print(f"LOVS snapshot preflight  |  as of {as_of}  |  data as of {data_cutoff}")
    print(bar)

    # 1. Data leverages. Partner-only levers are gaps unless a partner supplies them.
    print("Data leverages (data/external_sources/catalog.json):")
    for lever in catalog["levers"]:
        access = lever["access"]
        note = "supplied publicly" if access != "partner_only" else "PARTNER-ONLY (supply or accept the gap)"
        print(f"  - {lever['lever']:<24} access={access:<18} {note}")

    # 2. Every candidate target must have a map centroid in zones.json.
    print("\nCandidate target zones (data/snapshot_targets.json) and map centroids:")
    for target in targets_cfg["candidate_target_zones"]:
        zid = target["id"]
        zone = zones.get(zid)
        has_coord = zone is not None and zone.get("lat") is not None and zone.get("lon") is not None
        if not has_coord:
            gaps += 1
        print(f"  - {zid:<16} centroid={'ok' if has_coord else 'MISSING CENTROID'}")

    # 2b. Every official confirmed health zone must enter the model source set,
    # have a map centroid, and not remain classified as a target-only zone.
    print("\nOfficial source-zone coverage (manifest affected_health_zones):")
    try:
        zone_counts, zone_meta = refresh_pipeline.load_zone_attributed_counts()
        snapshot = refresh_pipeline.build_snapshot()
    except Exception as exc:
        print(f"  GAP: cannot derive official source-zone set: {exc}")
        gaps += 1
        zone_counts = {}
        zone_meta = {}
        snapshot = None
    if zone_counts and snapshot is not None:
        source_zones = set(snapshot.affected_zones)
        target_zones = {target["id"] for target in targets_cfg["candidate_target_zones"]}
        print(
            f"  - source table: {zone_meta.get('source_id', '<unknown>')} "
            f"({len(zone_counts)} confirmed health zone(s))"
        )
        missing_from_model = sorted(set(zone_counts) - source_zones)
        if missing_from_model:
            gaps += len(missing_from_model)
            for zid in missing_from_model:
                print(f"  GAP: official confirmed zone {zid} is absent from model affected_zones")
        missing_centroids = []
        for zid in sorted(zone_counts):
            zone = zones.get(zid)
            has_coord = zone is not None and zone.get("lat") is not None and zone.get("lon") is not None
            if not has_coord:
                missing_centroids.append(zid)
        if missing_centroids:
            gaps += len(missing_centroids)
            for zid in missing_centroids:
                print(f"  GAP: official confirmed zone {zid} lacks a zones.json centroid")
        overlaps = sorted(source_zones & target_zones)
        # Self-edge doctrine (matches snapshot_contract.py self-edge handling):
        # a zone may be both a source and a target only if it is pinned as a
        # target in an active calibration block. That makes the overlap explicit
        # and falsifiable. Without a covering block, the overlap is still a GAP
        # (silent drift in the watch set).
        calibration_target_zones = set()
        for block in ledger.get("blocks", []):
            if block.get("status") != "active":
                continue
            for point in block.get("points", []):
                tgt = point.get("target")
                if tgt:
                    calibration_target_zones.add(tgt)
        uncovered_overlaps = [zid for zid in overlaps if zid not in calibration_target_zones]
        covered_overlaps = [zid for zid in overlaps if zid in calibration_target_zones]
        if uncovered_overlaps:
            gaps += len(uncovered_overlaps)
            for zid in uncovered_overlaps:
                print(f"  GAP: {zid} is both a confirmed source zone and a candidate target with no active calibration block")
        for zid in covered_overlaps:
            print(f"  - self-edge {zid}: source+target, covered by an active calibration block (self-edge corridor count exclusion applies)")
        if not missing_from_model and not missing_centroids and not uncovered_overlaps:
            total_confirmed = sum(int(row.get("confirmed") or 0) for row in zone_counts.values())
            print(
                f"  - model source zones match official table; "
                f"zone-attributed confirmed total={total_confirmed}"
            )

    print("\nExternal-source staging consistency:")
    outstanding = observed.get("centroids", {}).get("outstanding", [])
    stale_outstanding = []
    for item in outstanding:
        zid = item.get("id")
        zone = zones.get(zid)
        if zone is not None and zone.get("lat") is not None and zone.get("lon") is not None:
            stale_outstanding.append(zid)
    if stale_outstanding:
        gaps += len(stale_outstanding)
        for zid in stale_outstanding:
            print(f"  GAP: {zid} is still marked outstanding but has a zones.json centroid")
    else:
        print("  - centroid outstanding list matches zones.json")

    staged_gaps = lovs_staged_observations.validate_staged_observations(
        observed,
        manifest_source_ids=manifest_source_ids,
    )
    if staged_gaps:
        gaps += len(staged_gaps)
        for gap in staged_gaps:
            print(f"  GAP: staged_observations: {gap}")
    else:
        count = len(observed.get("staged_observations", []))
        print(f"  - staged_observations contract ok ({count} row(s))")

    watch_gaps = lovs_staged_observations.validate_watch_signals(watch)
    if watch_gaps:
        gaps += len(watch_gaps)
        for gap in watch_gaps:
            print(f"  GAP: watch_signals: {gap}")
    else:
        count = len(watch.get("watch_signals", []))
        print(f"  - watch_signals contract ok ({count} row(s); non-model inputs)")

    print("\nPublic-facing source policy surfaces:")
    public_gaps = validate_public_surfaces()
    if public_gaps:
        gaps += len(public_gaps)
        for gap in public_gaps:
            print(f"  GAP: public surface: {gap}")
    else:
        print("  - public surface policy gate ok")

    # Recurring-source cadence (advisory): which monitored publications are due or
    # overdue for a fresh pull, and which dropbox files are pending ingest. This is
    # the "pull these whenever they become available" gate. Advisory (does not
    # increment hard gaps): we cannot force a publisher to release, but we surface
    # what to chase. The hard freshness gate is the manifest check below.
    print("\nRecurring sources (data/external_sources/source_registry.json):")
    if REGISTRY.exists():
        registry = _load(REGISTRY)
        for src in registry["sources"]:
            st = source_ingest.cadence_status(src, manifest, as_of)
            flag = "  <-- pull/check" if st["status"] in ("DUE", "OVERDUE", "UNARCHIVED") else ""
            gap = f"gap={st['gap_days']}d" if st["gap_days"] is not None else ""
            print(f"  - {st['registry_id']:<22} {st['status']:<10} newest={str(st['newest_archived']):<12} {gap}{flag}")
        pending = [
            r for r in source_ingest.scan_dropbox(registry, manifest)
            if not r["archived"] and r["registry_id"]
            and r.get("archive_target") == "outbreak_manifest"
        ]
        if pending:
            print(
                f"  {len(pending)} dropbox file(s) pending ingest "
                f"(run: python3 source_ingest.py)"
            )
    else:
        print("  (no source_registry.json; skipping)")

    # 3. Manifest freshness: the snapshot's DATA cutoff (not its publication
    #    as_of) must be provenance-backed. Under forward-dated versioning the
    #    publication as_of can run ahead of the newest source; what must not run
    #    ahead is the data cutoff the counts are pinned to.
    dates = [e["published_at"][:10] for e in manifest.get("entries", []) if e.get("published_at")]
    newest = max(dates) if dates else "(none)"
    print(f"\nManifest: {len(manifest.get('entries', []))} sources; newest published_at = {newest}")
    if newest != "(none)" and newest < data_cutoff:
        print(
            f"  GAP: newest archived source ({newest}) predates data cutoff ({data_cutoff}). "
            f"Archive {data_cutoff} sources in the manifest before pinning new counts."
        )
        gaps += 1
    if as_of[:10] < data_cutoff:
        print(
            f"  GAP: publication as_of ({as_of[:10]}) predates the data cutoff "
            f"({data_cutoff}); a snapshot cannot be published before its data."
        )
        gaps += 1

    # 4. Calibration: remind that a new as_of carries forward or appends, never re-derives.
    active = [b for b in ledger["blocks"] if b.get("status") == "active"]
    print(f"\nCalibration ledger: {len(active)} active block(s).")
    for block in active:
        print(
            f"  - {block['block_id']} pinned {block['pinned_at']} "
            f"resolves {block['resolves_at']} ({len(block['points'])} points)"
        )
    print("  Reminder: a new as_of carries these forward unchanged or appends a NEW block; never re-derive.")

    print(bar)
    if gaps:
        print(f"NOT READY: {gaps} gap(s) above must be resolved before this snapshot.")
        return 3
    print("READY: all leverages accounted for, all targets have centroids, manifest current.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--as-of",
        default=dt.date.today().isoformat(),
        help="Snapshot analytic/publication as-of date (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--data-as-of",
        default=None,
        help="Snapshot data cutoff (YYYY-MM-DD); manifest freshness is checked "
        "against this, not the publication as_of. Defaults to --as-of.",
    )
    args = parser.parse_args(argv)
    return run(args.as_of, args.data_as_of)


if __name__ == "__main__":
    raise SystemExit(main())
