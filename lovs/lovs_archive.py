"""LOVS Module A: point-in-time archive primitive.

Deterministic, offline-first, content-addressed archive of public-data
snapshots for the Latent Outbreak Visibility System.

Contract (see ops/consulting/idb-latent-outbreak-visibility-product-spec.md §5):
 - Snapshots are immutable. Rewriting an existing (source_id, retrieved_at)
   pair with a different content hash raises ArchiveImmutabilityError.
   Idempotent re-add of identical bytes is allowed.
 - Redistributed raw bytes are addressed by SHA-256 hex of their content. The
   manifest entry carries the same hash; load_archive verifies the on-disk
   bytes match. Restricted publisher bytes may instead be recorded as
   hash-only provenance with raw_archive_status != "public_bytes".
 - Tier-2 derived sources (source_tier == "derived_open_data" etc.) must carry
   a non-empty root_provenance_chain whose entries resolve to T1 sources in
   the same archive.
 - As-of queries return only snapshots whose retrieved_at <= as_of, in
   canonical order on (retrieved_at, source_id, content_hash).

Stdlib only. No network. No clock. No randomness. Single-writer assumption
(concurrent writers are out of scope for Stage One; flagged for Stage Two).
"""
from __future__ import annotations

import dataclasses
import datetime
import hashlib
import json
import pathlib
from typing import Any


MANIFEST_VERSION = 1

_T1_TIERS: frozenset[str] = frozenset({
    "official_who",
    # WHO regional office (AFRO, PAHO, etc.); a WHO regional sitrep counts as
    # T1 because the regional office is part of the WHO official chain.
    "official_who_afro",
    "official_africa_cdc",
    # Africa CDC and similar continental bodies; kept distinct from
    # official_africa_cdc for cases where the publisher is a continental body
    # that is not the Africa CDC itself.
    "official_continental_body",
    "official_cdc",
    "national_moh",
    "regional_body",
    "laboratory",
    # Joint academic-WHO publications (e.g., Imperial College MRC GIDA joint
    # situation reports with WHO HEP). Treated as T1 because both publishers
    # are authoritative; the IGO half of the collaboration anchors the tier.
    "academic_collab_who",
})

_T2_TIERS: frozenset[str] = frozenset({
    "derived_open_data",
    "humanitarian",
    "media",
})

_T3_TIERS: frozenset[str] = frozenset({
    "covariate_conflict",
    "covariate_environmental",
    "covariate_geospatial",
})

_ALL_TIERS: frozenset[str] = _T1_TIERS | _T2_TIERS | _T3_TIERS | frozenset({
    "historical_line_list",
    "historical_aggregate",
    "unknown",
    # Consensus aggregators (Wikipedia, ReliefWeb summaries, etc.) that
    # synthesize across multiple primary sources. Sits outside the T1/T2/T3
    # taxonomy: not authoritative enough for T1, but the T2 derivation chain
    # rule does not fit a synthesis-of-many-sources page either. Loaded with
    # no provenance-chain requirement; the grounding-audit pass should decide
    # whether to promote individual aggregator entries into T2 with explicit
    # chains, or leave them as descriptive metadata only.
    "aggregator",
})

_REQUIRED_PROVENANCE_FIELDS: tuple[str, ...] = (
    "source_id",
    "source_tier",
    "publisher",
    "url",
    "retrieved_at",
    "content_hash",
    "license",
    "extraction_status",
    "root_provenance_chain",
)

_REQUIRED_SNAPSHOT_FIELDS: tuple[str, ...] = (
    "outbreak_id",
    "pathogen",
    "country_scope",
    "geography_id",
    "raw_bytes_relpath",
    "normalized_content",
)

_VALID_EXTRACTION_STATUS: frozenset[str] = frozenset({"success", "partial", "failed"})
_VALID_RAW_ARCHIVE_STATUS: frozenset[str] = frozenset({
    "public_bytes",
    "private_restricted_bytes",
})


class ArchiveContractError(ValueError):
    """Raised when a loaded archive violates a §5.5 contract clause."""


class ArchiveImmutabilityError(ValueError):
    """Raised when add_snapshot would rewrite an existing entry with a different hash."""


@dataclasses.dataclass(frozen=True)
class ProvenanceRecord:
    source_id: str
    source_tier: str
    publisher: str
    url: str
    retrieved_at: str
    published_at: str | None
    content_hash: str
    license: str
    extraction_status: str
    root_provenance_chain: tuple[str, ...]


@dataclasses.dataclass(frozen=True)
class ArchivedSnapshot:
    provenance: ProvenanceRecord
    outbreak_id: str
    pathogen: str
    country_scope: tuple[str, ...]
    geography_id: str
    raw_bytes_relpath: str | None
    raw_archive_status: str
    normalized_content: dict[str, Any]


@dataclasses.dataclass(frozen=True)
class Archive:
    root_path: str
    snapshots: tuple[ArchivedSnapshot, ...]


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _validate_iso_utc(value: str, field_name: str) -> None:
    if not isinstance(value, str):
        raise ArchiveContractError(f"{field_name} must be a string, got {type(value).__name__}")
    if not value.endswith("Z"):
        raise ArchiveContractError(
            f"{field_name} must be ISO 8601 UTC ending with 'Z', got {value!r}"
        )
    try:
        datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ArchiveContractError(f"{field_name} is not parseable ISO 8601: {value!r}") from exc


def _validate_content_hash(value: str, field_name: str) -> None:
    if not isinstance(value, str):
        raise ArchiveContractError(f"{field_name} must be a string, got {type(value).__name__}")
    if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ArchiveContractError(
            f"{field_name} must be 64 lowercase hex characters, got {value!r}"
        )


def _build_provenance(entry: dict, position: int) -> ProvenanceRecord:
    for field in _REQUIRED_PROVENANCE_FIELDS:
        if field not in entry:
            raise ArchiveContractError(
                f"manifest entry {position}: missing required field {field!r}"
            )
    source_id = entry["source_id"]
    source_tier = entry["source_tier"]
    if source_tier not in _ALL_TIERS:
        raise ArchiveContractError(
            f"manifest entry {position} ({source_id!r}): unknown source_tier {source_tier!r}"
        )
    extraction_status = entry["extraction_status"]
    if extraction_status not in _VALID_EXTRACTION_STATUS:
        raise ArchiveContractError(
            f"manifest entry {position} ({source_id!r}): "
            f"extraction_status must be one of {sorted(_VALID_EXTRACTION_STATUS)}, "
            f"got {extraction_status!r}"
        )
    _validate_iso_utc(entry["retrieved_at"], f"entry {position} retrieved_at")
    published_at = entry.get("published_at")
    if published_at is not None:
        _validate_iso_utc(published_at, f"entry {position} published_at")
    _validate_content_hash(entry["content_hash"], f"entry {position} content_hash")
    chain = entry["root_provenance_chain"]
    if not isinstance(chain, list) or any(not isinstance(c, str) for c in chain):
        raise ArchiveContractError(
            f"manifest entry {position} ({source_id!r}): "
            f"root_provenance_chain must be a list of strings"
        )
    if source_tier in _T2_TIERS and not chain:
        raise ArchiveContractError(
            f"manifest entry {position} ({source_id!r}): "
            f"T2 source_tier {source_tier!r} requires non-empty root_provenance_chain"
        )
    return ProvenanceRecord(
        source_id=source_id,
        source_tier=source_tier,
        publisher=entry["publisher"],
        url=entry["url"],
        retrieved_at=entry["retrieved_at"],
        published_at=published_at,
        content_hash=entry["content_hash"],
        license=entry["license"],
        extraction_status=extraction_status,
        root_provenance_chain=tuple(chain),
    )


def _build_snapshot(entry: dict, position: int, provenance: ProvenanceRecord) -> ArchivedSnapshot:
    for field in _REQUIRED_SNAPSHOT_FIELDS:
        if field not in entry:
            raise ArchiveContractError(
                f"manifest entry {position} ({provenance.source_id!r}): "
                f"missing required field {field!r}"
            )
    country_scope = entry["country_scope"]
    if not isinstance(country_scope, list) or any(not isinstance(c, str) for c in country_scope):
        raise ArchiveContractError(
            f"manifest entry {position} ({provenance.source_id!r}): "
            f"country_scope must be a list of strings"
        )
    normalized = entry["normalized_content"]
    if not isinstance(normalized, dict):
        raise ArchiveContractError(
            f"manifest entry {position} ({provenance.source_id!r}): "
            f"normalized_content must be an object"
        )
    raw_archive_status = entry.get("raw_archive_status", "public_bytes")
    if raw_archive_status not in _VALID_RAW_ARCHIVE_STATUS:
        raise ArchiveContractError(
            f"manifest entry {position} ({provenance.source_id!r}): "
            f"raw_archive_status must be one of {sorted(_VALID_RAW_ARCHIVE_STATUS)}, "
            f"got {raw_archive_status!r}"
        )
    raw_relpath = entry["raw_bytes_relpath"]
    if raw_archive_status == "public_bytes":
        if not isinstance(raw_relpath, str) or not raw_relpath:
            raise ArchiveContractError(
                f"manifest entry {position} ({provenance.source_id!r}): "
                "public_bytes entries require non-empty raw_bytes_relpath"
            )
    elif raw_relpath is not None:
        raise ArchiveContractError(
            f"manifest entry {position} ({provenance.source_id!r}): "
            "private_restricted_bytes entries must set raw_bytes_relpath to null"
        )
    return ArchivedSnapshot(
        provenance=provenance,
        outbreak_id=entry["outbreak_id"],
        pathogen=entry["pathogen"],
        country_scope=tuple(country_scope),
        geography_id=entry["geography_id"],
        raw_bytes_relpath=raw_relpath,
        raw_archive_status=raw_archive_status,
        normalized_content=dict(normalized),
    )


def _canonical_key(snapshot: ArchivedSnapshot) -> tuple[str, str, str]:
    p = snapshot.provenance
    return (p.retrieved_at, p.source_id, p.content_hash)


def _verify_hash_on_disk(root: pathlib.Path, snapshot: ArchivedSnapshot) -> None:
    if snapshot.raw_archive_status != "public_bytes":
        return
    if snapshot.raw_bytes_relpath is None:
        raise ArchiveContractError(
            f"{snapshot.provenance.source_id!r}: public raw archive missing relpath"
        )
    raw_path = root / snapshot.raw_bytes_relpath
    if not raw_path.exists():
        raise ArchiveContractError(
            f"{snapshot.provenance.source_id!r}: raw bytes file missing at {raw_path}"
        )
    actual = _sha256_hex(raw_path.read_bytes())
    if actual != snapshot.provenance.content_hash:
        raise ArchiveContractError(
            f"{snapshot.provenance.source_id!r}: content hash mismatch "
            f"(manifest says {snapshot.provenance.content_hash}, raw bytes hash to {actual})"
        )


def _verify_t2_chains(snapshots: tuple[ArchivedSnapshot, ...]) -> None:
    by_id: dict[str, ArchivedSnapshot] = {s.provenance.source_id: s for s in snapshots}
    for snap in snapshots:
        if snap.provenance.source_tier not in _T2_TIERS:
            continue
        for ref in snap.provenance.root_provenance_chain:
            if ref not in by_id:
                raise ArchiveContractError(
                    f"{snap.provenance.source_id!r}: "
                    f"root_provenance_chain references unknown source_id {ref!r}"
                )
            if by_id[ref].provenance.source_tier not in _T1_TIERS:
                raise ArchiveContractError(
                    f"{snap.provenance.source_id!r}: "
                    f"root_provenance_chain entry {ref!r} is not a T1 source "
                    f"(tier {by_id[ref].provenance.source_tier!r})"
                )


def load_archive(root: pathlib.Path) -> Archive:
    """Load and verify an archive rooted at `root`.

    Verifies:
     1. manifest.json exists and parses as JSON.
     2. manifest_version is supported.
     3. Each entry has all required provenance and snapshot fields.
     4. Each public_bytes entry's content_hash matches the SHA-256 of its raw bytes.
     5. (source_id, retrieved_at) pairs are unique.
     6. T2 source_tier entries have non-empty root_provenance_chain referencing T1 entries.

    Returns snapshots in canonical order on (retrieved_at, source_id, content_hash).
    """
    root = pathlib.Path(root)
    manifest_path = root / "manifest.json"
    if not manifest_path.exists():
        raise ArchiveContractError(f"manifest.json missing at {manifest_path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ArchiveContractError(f"manifest.json is not valid JSON: {exc}") from exc

    if not isinstance(manifest, dict):
        raise ArchiveContractError("manifest.json root must be an object")
    if "manifest_version" not in manifest:
        raise ArchiveContractError("manifest.json missing 'manifest_version'")
    if manifest["manifest_version"] != MANIFEST_VERSION:
        raise ArchiveContractError(
            f"manifest_version {manifest['manifest_version']!r} not supported "
            f"(this loader handles version {MANIFEST_VERSION})"
        )
    if "entries" not in manifest:
        raise ArchiveContractError("manifest.json missing 'entries' field")
    entries = manifest["entries"]
    if not isinstance(entries, list):
        raise ArchiveContractError("manifest.json 'entries' must be a list")

    snapshots: list[ArchivedSnapshot] = []
    seen_pairs: set[tuple[str, str]] = set()
    for position, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ArchiveContractError(f"manifest entry {position} is not an object")
        provenance = _build_provenance(entry, position)
        snapshot = _build_snapshot(entry, position, provenance)
        pair = (provenance.source_id, provenance.retrieved_at)
        if pair in seen_pairs:
            raise ArchiveContractError(
                f"manifest entry {position}: duplicate (source_id, retrieved_at) pair "
                f"{pair}"
            )
        seen_pairs.add(pair)
        _verify_hash_on_disk(root, snapshot)
        snapshots.append(snapshot)

    snapshots_tuple = tuple(sorted(snapshots, key=_canonical_key))
    _verify_t2_chains(snapshots_tuple)
    return Archive(root_path=str(root), snapshots=snapshots_tuple)


def query_as_of(
    archive: Archive,
    outbreak_id: str,
    as_of: str,
) -> tuple[ArchivedSnapshot, ...]:
    """Return snapshots for `outbreak_id` whose retrieved_at <= `as_of`, in canonical order."""
    _validate_iso_utc(as_of, "as_of")
    matches = tuple(
        s for s in archive.snapshots
        if s.outbreak_id == outbreak_id and s.provenance.retrieved_at <= as_of
    )
    return matches


def verify_archive(archive: Archive) -> None:
    """Re-run all loader contracts on a constructed Archive.

    This is the post-construction integrity check: useful for callers who
    received an Archive from somewhere they do not fully trust, or who want
    to assert invariants after mutating-style operations like add_snapshot.
    """
    seen_pairs: set[tuple[str, str]] = set()
    last_key: tuple[str, str, str] | None = None
    for snap in archive.snapshots:
        pair = (snap.provenance.source_id, snap.provenance.retrieved_at)
        if pair in seen_pairs:
            raise ArchiveContractError(f"verify_archive: duplicate pair {pair}")
        seen_pairs.add(pair)
        key = _canonical_key(snap)
        if last_key is not None and key < last_key:
            raise ArchiveContractError(
                f"verify_archive: snapshots not in canonical order at {snap.provenance.source_id!r}"
            )
        last_key = key
        _verify_hash_on_disk(pathlib.Path(archive.root_path), snap)
    _verify_t2_chains(archive.snapshots)


def _atomic_write_json(path: pathlib.Path, data: object) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload = json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(path)


_SNAPSHOT_META_FIELDS: tuple[str, ...] = (
    "outbreak_id",
    "pathogen",
    "country_scope",
    "geography_id",
    "raw_bytes_relpath",
    "raw_archive_status",
    "normalized_content",
)


def add_snapshot(
    root: pathlib.Path,
    provenance: ProvenanceRecord,
    snapshot_meta: dict,
    raw_bytes: bytes,
) -> None:
    """Append a new snapshot to the archive at `root`.

    Append-only semantics:
     - If (source_id, retrieved_at) does not exist yet, the new entry is written.
     - If it exists with the same content_hash AND the same provenance fields AND
       the same snapshot_meta fields, the call is a no-op (idempotent).
     - If it exists with a different content_hash, ArchiveImmutabilityError is raised.
     - If it exists with the same content_hash but any provenance field or
       snapshot_meta field differs, ArchiveImmutabilityError is raised. Idempotent
       means "byte-identical re-add," not "content-identical re-add with drift."

    Verifies that SHA-256(raw_bytes) == provenance.content_hash before any write.
    Atomic manifest write via tempfile + os.replace.
    Runs `verify_archive` on the resulting state as a post-write integrity check;
    if verification fails the manifest is left in place (auditable) and the error
    propagates to the caller.
    """
    root = pathlib.Path(root)
    actual_hash = _sha256_hex(raw_bytes)
    if actual_hash != provenance.content_hash:
        raise ArchiveContractError(
            f"add_snapshot: raw bytes hash to {actual_hash} but "
            f"provenance.content_hash is {provenance.content_hash!r}"
        )

    manifest_path = root / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    else:
        manifest = {"manifest_version": MANIFEST_VERSION, "entries": []}
        (root / "raw").mkdir(parents=True, exist_ok=True)

    if manifest.get("manifest_version") != MANIFEST_VERSION:
        raise ArchiveContractError(
            f"add_snapshot: existing manifest_version "
            f"{manifest.get('manifest_version')!r} not supported"
        )

    raw_relpath = snapshot_meta["raw_bytes_relpath"]
    raw_archive_status = snapshot_meta.get("raw_archive_status", "public_bytes")
    if raw_archive_status != "public_bytes":
        raise ArchiveContractError("add_snapshot only writes public_bytes archive entries")
    raw_path = root / raw_relpath
    raw_path.parent.mkdir(parents=True, exist_ok=True)

    for existing in manifest["entries"]:
        if (
            existing["source_id"] == provenance.source_id
            and existing["retrieved_at"] == provenance.retrieved_at
        ):
            if existing["content_hash"] != provenance.content_hash:
                raise ArchiveImmutabilityError(
                    f"add_snapshot: (source_id={provenance.source_id!r}, "
                    f"retrieved_at={provenance.retrieved_at!r}) already exists with hash "
                    f"{existing['content_hash']!r}; cannot rewrite with {provenance.content_hash!r}"
                )
            # Idempotent path: every provenance and snapshot_meta field must match
            # the existing entry. A same-hash re-add with different metadata is a
            # caller mistake to surface, not silently discard.
            divergences: list[str] = []
            for field in (
                "publisher", "url", "license", "extraction_status",
                "source_tier", "published_at",
            ):
                existing_value = existing.get(field)
                new_value = getattr(provenance, field)
                if existing_value != new_value:
                    divergences.append(
                        f"provenance.{field}: existing={existing_value!r}, new={new_value!r}"
                    )
            if tuple(existing["root_provenance_chain"]) != provenance.root_provenance_chain:
                divergences.append(
                    f"provenance.root_provenance_chain: existing="
                    f"{existing['root_provenance_chain']!r}, "
                    f"new={list(provenance.root_provenance_chain)!r}"
                )
            for field in _SNAPSHOT_META_FIELDS:
                existing_value = existing.get(field)
                new_value = snapshot_meta.get(field)
                if field == "raw_archive_status":
                    existing_value = existing_value or "public_bytes"
                    new_value = new_value or "public_bytes"
                if field == "country_scope":
                    existing_value = list(existing_value) if existing_value is not None else None
                    new_value = list(new_value) if new_value is not None else None
                if existing_value != new_value:
                    divergences.append(
                        f"snapshot_meta.{field}: existing={existing_value!r}, new={new_value!r}"
                    )
            if divergences:
                raise ArchiveImmutabilityError(
                    f"add_snapshot: (source_id={provenance.source_id!r}, "
                    f"retrieved_at={provenance.retrieved_at!r}) idempotent re-add "
                    f"rejected because metadata diverges: {'; '.join(divergences)}"
                )
            if raw_path.exists() and raw_path.read_bytes() != raw_bytes:
                raise ArchiveImmutabilityError(
                    f"add_snapshot: existing raw bytes at {raw_path} differ from supplied"
                )
            if not raw_path.exists():
                raw_path.write_bytes(raw_bytes)
            return

    if not raw_path.exists():
        raw_path.write_bytes(raw_bytes)

    entry = {
        "source_id": provenance.source_id,
        "source_tier": provenance.source_tier,
        "publisher": provenance.publisher,
        "url": provenance.url,
        "retrieved_at": provenance.retrieved_at,
        "published_at": provenance.published_at,
        "content_hash": provenance.content_hash,
        "license": provenance.license,
        "extraction_status": provenance.extraction_status,
        "root_provenance_chain": list(provenance.root_provenance_chain),
        "outbreak_id": snapshot_meta["outbreak_id"],
        "pathogen": snapshot_meta["pathogen"],
        "country_scope": list(snapshot_meta["country_scope"]),
        "geography_id": snapshot_meta["geography_id"],
        "raw_bytes_relpath": raw_relpath,
        "raw_archive_status": raw_archive_status,
        "normalized_content": snapshot_meta["normalized_content"],
    }
    manifest["entries"].append(entry)
    manifest["entries"].sort(
        key=lambda e: (e["retrieved_at"], e["source_id"], e["content_hash"])
    )
    _atomic_write_json(manifest_path, manifest)
    # Post-write integrity check: re-load and verify the resulting archive
    # satisfies every loader contract. If verification fails the manifest is
    # left in place (so it can be inspected) and the error propagates.
    archive = load_archive(root)
    verify_archive(archive)


def add_restricted_snapshot(
    root: pathlib.Path,
    provenance: ProvenanceRecord,
    snapshot_meta: dict,
    raw_bytes: bytes,
    private_raw_dir: str = "private/raw",
) -> None:
    """Append a private_restricted_bytes snapshot entry.

    Unlike add_snapshot (which redistributes public bytes under raw/<sha256>),
    this records hash-only provenance in the manifest (raw_bytes_relpath=null,
    raw_archive_status="private_restricted_bytes") and stores the actual bytes
    under the gitignored private/raw/<sha256> for local re-verification only.
    Use for third-party publisher material we cannot redistribute in the public
    repo (e.g. WHO/Imperial/IOM PDFs under restrictive or unconfirmed terms).

    Append-only and idempotent on (source_id, retrieved_at), same contract as
    add_snapshot: a same-key re-add with a different content_hash raises
    ArchiveImmutabilityError. Verifies sha256(raw_bytes) == content_hash before
    any write, then atomically rewrites the manifest and re-runs the full loader
    contract (load_archive + verify_archive).
    """
    root = pathlib.Path(root)
    actual_hash = _sha256_hex(raw_bytes)
    if actual_hash != provenance.content_hash:
        raise ArchiveContractError(
            f"add_restricted_snapshot: raw bytes hash to {actual_hash} but "
            f"provenance.content_hash is {provenance.content_hash!r}"
        )
    if snapshot_meta.get("raw_archive_status") != "private_restricted_bytes":
        raise ArchiveContractError(
            "add_restricted_snapshot requires "
            "raw_archive_status='private_restricted_bytes'"
        )
    if snapshot_meta.get("raw_bytes_relpath") is not None:
        raise ArchiveContractError(
            "add_restricted_snapshot requires raw_bytes_relpath=None "
            "(restricted bytes are not redistributed in the manifest)"
        )

    manifest_path = root / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    else:
        manifest = {"manifest_version": MANIFEST_VERSION, "entries": []}
    if manifest.get("manifest_version") != MANIFEST_VERSION:
        raise ArchiveContractError(
            f"add_restricted_snapshot: existing manifest_version "
            f"{manifest.get('manifest_version')!r} not supported"
        )

    # Local-only retrievable copy of the restricted bytes (gitignored).
    private_path = root / private_raw_dir / provenance.content_hash
    private_path.parent.mkdir(parents=True, exist_ok=True)

    for existing in manifest["entries"]:
        if (
            existing["source_id"] == provenance.source_id
            and existing["retrieved_at"] == provenance.retrieved_at
        ):
            if existing["content_hash"] != provenance.content_hash:
                raise ArchiveImmutabilityError(
                    f"add_restricted_snapshot: (source_id={provenance.source_id!r}, "
                    f"retrieved_at={provenance.retrieved_at!r}) already exists with "
                    f"hash {existing['content_hash']!r}; cannot rewrite with "
                    f"{provenance.content_hash!r}"
                )
            if private_path.exists() and private_path.read_bytes() != raw_bytes:
                raise ArchiveImmutabilityError(
                    f"add_restricted_snapshot: existing private bytes at "
                    f"{private_path} differ from supplied"
                )
            if not private_path.exists():
                private_path.write_bytes(raw_bytes)
            return

    if not private_path.exists():
        private_path.write_bytes(raw_bytes)

    entry = {
        "source_id": provenance.source_id,
        "source_tier": provenance.source_tier,
        "publisher": provenance.publisher,
        "url": provenance.url,
        "retrieved_at": provenance.retrieved_at,
        "published_at": provenance.published_at,
        "content_hash": provenance.content_hash,
        "license": provenance.license,
        "extraction_status": provenance.extraction_status,
        "root_provenance_chain": list(provenance.root_provenance_chain),
        "outbreak_id": snapshot_meta["outbreak_id"],
        "pathogen": snapshot_meta["pathogen"],
        "country_scope": list(snapshot_meta["country_scope"]),
        "geography_id": snapshot_meta["geography_id"],
        "raw_bytes_relpath": None,
        "raw_archive_status": "private_restricted_bytes",
        "normalized_content": snapshot_meta["normalized_content"],
    }
    manifest["entries"].append(entry)
    manifest["entries"].sort(
        key=lambda e: (e["retrieved_at"], e["source_id"], e["content_hash"])
    )
    _atomic_write_json(manifest_path, manifest)
    archive = load_archive(root)
    verify_archive(archive)


# Demo entry-point: byte-deterministic summary of the fixture archive.


def _render_summary(archive: Archive) -> str:
    by_outbreak: dict[str, list[ArchivedSnapshot]] = {}
    by_tier: dict[str, int] = {}
    for snap in archive.snapshots:
        by_outbreak.setdefault(snap.outbreak_id, []).append(snap)
        by_tier[snap.provenance.source_tier] = by_tier.get(snap.provenance.source_tier, 0) + 1

    lines: list[str] = []
    lines.append(f"LOVS archive: {archive.root_path}")
    lines.append(f"Total snapshots: {len(archive.snapshots)}")
    lines.append("By outbreak:")
    for outbreak_id in sorted(by_outbreak):
        snaps = by_outbreak[outbreak_id]
        first = snaps[0].provenance.retrieved_at
        last = snaps[-1].provenance.retrieved_at
        lines.append(
            f"  {outbreak_id}: {len(snaps)} snapshot{'s' if len(snaps) != 1 else ''}, "
            f"retrieved_at range {first} to {last}"
        )
    lines.append("By source tier:")
    for tier in sorted(by_tier):
        lines.append(f"  {tier}: {by_tier[tier]}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    import sys
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) > 1:
        print("usage: python -m lovs.lovs_archive [<archive_root>]", file=sys.stderr)
        return 2
    default_root = pathlib.Path(__file__).resolve().parent.parent / "tests" / "data" / "lovs" / "fixture"
    root = pathlib.Path(argv[0]) if argv else default_root
    archive = load_archive(root)
    sys.stdout.write(_render_summary(archive))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
