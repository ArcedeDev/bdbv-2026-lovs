"""LOVS evidence-chain registry.

The archive module tracks byte provenance for outbreak source snapshots. This
module tracks claim provenance for methodology priors, derived numbers, and
audit decisions that do not naturally live in the source archive.

Evidence chain IDs use this form:
    ec:<scope>:<module-or-artifact>:<claim-slug>:<yyyy-mm-dd>

Stdlib only. No network. No clock. No randomness.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
from collections.abc import Iterable, Mapping
from typing import Any


SCHEMA_VERSION = 1

VALID_SOURCE_TIERS: frozenset[str] = frozenset({
    "T1_PRIMARY",
    "T1_REVIEW",
    "T2_DERIVED",
    "T3_CONTEXT",
    "LOCAL_ARCHIVE",
})

VALID_VERDICTS: frozenset[str] = frozenset({
    "supported",
    "derived_supported",
    "corrected",
    "unsupported_attribution",
    "needs_primary_source",
    "pending",
})

VALID_STEP_KINDS: frozenset[str] = frozenset({
    "source_quote",
    "math_check",
    "derivation",
    "cross_check",
    "correction",
    "blocker",
})

_CHAIN_ID_RE = re.compile(r"^ec:[a-z0-9][a-z0-9_.:-]*:[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_CHAIN_ID_INLINE_RE = re.compile(r"ec:[a-z0-9][a-z0-9_.:-]*:[0-9]{4}-[0-9]{2}-[0-9]{2}")
_CLAIM_ID_RE = re.compile(r"^claim:[a-z0-9][a-z0-9_.:-]*$")

# A headline-promotion chain DECLARES, in its claim.locator, which published
# metric and which source it backs, as one or more clauses of the form
#     reported_counts.confirmed.primary_source_id == inrb-sitrep-019-2026-06-02
# (semicolon-separated, with optional trailing prose after the source id). This
# is the binding the published snapshot must honour: the chain whose locator
# names `<metric>.primary_source_id == <id>` is the one that backs that metric's
# `primary_source_id`. Parsing the locator (rather than guessing from the source
# list) keeps the mapping anchored to the chain's own explicit claim, so a chain
# that merely *cites* a SitRep as a secondary/conflict source is never mistaken
# for the chain that backs the headline.
_LOCATOR_BINDING_RE = re.compile(
    r"(?P<path>[a-zA-Z_][\w.]*\.primary_source_id)\s*==\s*(?P<source_id>[a-z0-9][a-z0-9_.:-]*)"
)

# Canonical locator paths for the two headline metrics the publish gate enforces:
# cumulative laboratory-confirmed cases and cumulative laboratory-confirmed
# deaths. Keyed by the snapshot metric address (`reported_counts`/`reported_deaths`
# block -> `confirmed` row) so the gate and the generator share one source of
# truth for "which locator backs which headline figure".
HEADLINE_CONFIRMED_LOCATOR = "reported_counts.confirmed.primary_source_id"
HEADLINE_CONFIRMED_DEATHS_LOCATOR = "reported_deaths.confirmed.primary_source_id"
_SOURCE_ID_RE = re.compile(r"^src:[a-z0-9][a-z0-9_.:-]*$")
_STEP_ID_RE = re.compile(r"^step:[a-z0-9][a-z0-9_.:-]*$")
_AUDIT_GAP_RE = re.compile(r"audit_gap:[a-z0-9][a-z0-9_.:-]*")

_REQUIRED_CHAIN_FIELDS: tuple[str, ...] = (
    "chain_id",
    "claim",
    "verdict",
    "reviewed_at",
    "reviewer",
    "sources",
    "steps",
    "next_action",
)

_REQUIRED_CLAIM_FIELDS: tuple[str, ...] = (
    "claim_id",
    "artifact",
    "locator",
    "statement",
    "value",
)

_REQUIRED_SOURCE_FIELDS: tuple[str, ...] = (
    "source_id",
    "tier",
    "citation",
    "url",
    "finding",
)

_REQUIRED_STEP_FIELDS: tuple[str, ...] = (
    "step_id",
    "kind",
    "finding",
)


class EvidenceChainError(ValueError):
    """Raised when an evidence-chain registry violates the contract."""


def default_registry_path() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parent.parent / "data" / "evidence-chains.json"


def default_numbers_audit_path() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parent.parent / "NUMBERS_AUDIT.md"


def default_manifest_path() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parent.parent / "data" / "bundibugyo-2026" / "manifest.json"


def default_source_registry_path() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parent.parent / "data" / "external_sources" / "source_registry.json"


def load_registry(path: str | pathlib.Path | None = None) -> dict[str, Any]:
    registry_path = pathlib.Path(path) if path is not None else default_registry_path()
    try:
        payload = json.loads(registry_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise EvidenceChainError(f"{registry_path}: invalid JSON: {exc}") from exc
    validate_registry(payload)
    return payload


def validate_source_anchors(
    registry_payload: dict[str, Any],
    manifest_path: str | pathlib.Path | None = None,
    source_registry_path: str | pathlib.Path | None = None,
    repo_root: str | pathlib.Path | None = None,
) -> dict[str, int]:
    """Validate local anchors for evidence-chain source rows.

    External literature and ordinary web citations can stand on their URL. If a
    citation points at a URL already present in the outbreak manifest or source
    registry, the evidence-chain row must also name the local anchor
    (`manifest_source_id` or `registry_id`). This catches the failure mode where
    a chain cites a source-like slug but does not actually bind to archived
    bytes or a monitored source.
    """
    root = pathlib.Path(repo_root) if repo_root is not None else pathlib.Path(__file__).resolve().parent.parent
    manifest = _load_optional_json(pathlib.Path(manifest_path) if manifest_path else default_manifest_path())
    source_registry = _load_optional_json(
        pathlib.Path(source_registry_path) if source_registry_path else default_source_registry_path()
    )
    manifest_ids = {
        entry["source_id"]
        for entry in manifest.get("entries", [])
        if isinstance(entry, dict) and entry.get("source_id")
    }
    manifest_urls: dict[str, set[str]] = {}
    for entry in manifest.get("entries", []):
        if isinstance(entry, dict) and entry.get("url"):
            manifest_urls.setdefault(entry["url"], set()).add(str(entry.get("published_at", ""))[:10])
    registry_ids = {
        source["registry_id"]
        for source in source_registry.get("sources", [])
        if isinstance(source, dict) and source.get("registry_id")
    }
    registry_urls = {
        source["landing_url"]
        for source in source_registry.get("sources", [])
        if isinstance(source, dict) and source.get("landing_url")
    }

    counts = {
        "sources": 0,
        "manifest_anchored": 0,
        "registry_anchored": 0,
        "artifact_anchored": 0,
        "external_url": 0,
    }
    for chain_idx, chain in enumerate(registry_payload.get("chains", [])):
        for source_idx, source in enumerate(chain.get("sources", [])):
            path = f"chains[{chain_idx}].sources[{source_idx}]"
            if not isinstance(source, dict):
                continue
            counts["sources"] += 1
            url = source.get("url", "")
            manifest_source_id = source.get("manifest_source_id")
            registry_id = source.get("registry_id")
            artifact_path = source.get("artifact_path")

            if manifest_source_id is not None:
                _require_string(manifest_source_id, f"{path}.manifest_source_id")
                if manifest_source_id not in manifest_ids:
                    raise EvidenceChainError(
                        f"{path}.manifest_source_id {manifest_source_id!r} is not in manifest.json"
                    )
                counts["manifest_anchored"] += 1
            elif url in manifest_urls and _source_dates(source).intersection(manifest_urls[url]):
                raise EvidenceChainError(
                    f"{path} cites a manifest URL but lacks manifest_source_id"
                )

            if registry_id is not None:
                _require_string(registry_id, f"{path}.registry_id")
                if registry_id not in registry_ids:
                    raise EvidenceChainError(
                        f"{path}.registry_id {registry_id!r} is not in source_registry.json"
                    )
                counts["registry_anchored"] += 1
            elif url in registry_urls:
                raise EvidenceChainError(f"{path} cites a registry URL but lacks registry_id")

            if artifact_path is not None:
                artifact = pathlib.Path(_require_string(artifact_path, f"{path}.artifact_path"))
                if artifact.is_absolute() or ".." in artifact.parts:
                    raise EvidenceChainError(f"{path}.artifact_path must be repo-relative")
                if not (root / artifact).exists():
                    raise EvidenceChainError(
                        f"{path}.artifact_path does not exist: {artifact_path!r}"
                    )
                counts["artifact_anchored"] += 1
            elif isinstance(url, str) and url.startswith("file:"):
                rel = url.removeprefix("file:")
                # Website paths are anchored in the website repo, not this LOVS
                # package. Require an explicit external_artifact marker for those.
                if rel.startswith("apps/site/"):
                    if source.get("external_artifact") != "website":
                        raise EvidenceChainError(
                            f"{path} cites website file URL but lacks external_artifact='website'"
                        )
                else:
                    artifact = pathlib.Path(rel)
                    if artifact.is_absolute() or ".." in artifact.parts:
                        raise EvidenceChainError(f"{path}.url file path must be repo-relative")
                    if not (root / artifact).exists():
                        raise EvidenceChainError(f"{path}.url file path does not exist: {rel!r}")
                    counts["artifact_anchored"] += 1
            elif isinstance(url, str) and url.startswith(("http://", "https://", "doi:")):
                counts["external_url"] += 1

    return counts


def _source_dates(source: dict[str, Any]) -> set[str]:
    text = " ".join(
        str(source.get(field, ""))
        for field in ("source_id", "citation", "finding")
    )
    return set(re.findall(r"20[0-9]{2}-[0-9]{2}-[0-9]{2}", text))


def validate_numbers_audit(
    path: str | pathlib.Path | None = None,
    registry: dict[str, Any] | None = None,
) -> dict[str, int]:
    audit_path = pathlib.Path(path) if path is not None else default_numbers_audit_path()
    registry_payload = registry if registry is not None else load_registry()
    chain_ids = {chain["chain_id"] for chain in registry_payload["chains"]}
    rows = _numbers_audit_data_rows(audit_path)
    counts = {"rows": 0, "evidence_chain": 0, "audit_gap": 0}

    for line_number, row in rows:
        row_chain_ids = _CHAIN_ID_INLINE_RE.findall(row)
        row_gap_markers = _AUDIT_GAP_RE.findall(row)
        if not row_chain_ids and not row_gap_markers:
            raise EvidenceChainError(
                f"{audit_path}:{line_number}: table row missing ec:... or audit_gap:... marker"
            )
        for chain_id in row_chain_ids:
            if chain_id not in chain_ids:
                raise EvidenceChainError(
                    f"{audit_path}:{line_number}: unknown evidence-chain id {chain_id!r}"
                )
        counts["rows"] += 1
        if row_chain_ids:
            counts["evidence_chain"] += 1
        if row_gap_markers:
            counts["audit_gap"] += 1

    return counts


def validate_registry(payload: dict[str, Any]) -> dict[str, int]:
    if not isinstance(payload, dict):
        raise EvidenceChainError("registry root must be an object")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise EvidenceChainError(
            f"schema_version must be {SCHEMA_VERSION}, got {payload.get('schema_version')!r}"
        )
    chains = payload.get("chains")
    if not isinstance(chains, list):
        raise EvidenceChainError("registry.chains must be a list")

    seen_chain_ids: set[str] = set()
    verdict_counts: dict[str, int] = {}
    for idx, chain in enumerate(chains):
        _validate_chain(chain, idx, seen_chain_ids)
        verdict = chain["verdict"]
        verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1
    return verdict_counts


# ---------------------------------------------------------------------------
# Headline source -> backing-chain mapping.
#
# The published snapshot promotes a metric's `primary_source_id` (e.g.
# `reported_counts.confirmed.primary_source_id == inrb-sitrep-019-2026-06-02`).
# The chain that BACKS that promotion declares the same `<path> == <source_id>`
# binding in its `claim.locator`. These helpers turn that declaration into a
# deterministic `(locator_path, source_id) -> chain_id` index so the generator
# can embed the backing chain and the publish gate can enforce that the embedded
# chain actually matches the metric's source. No clock, no network, pure
# function of the registry contents.
# ---------------------------------------------------------------------------
def _iter_locator_bindings(chain: Mapping[str, Any]) -> Iterable[tuple[str, str]]:
    """Yield each ``(locator_path, source_id)`` declared in a chain's locator."""
    locator = ""
    claim = chain.get("claim")
    if isinstance(claim, Mapping):
        locator = str(claim.get("locator", ""))
    for match in _LOCATOR_BINDING_RE.finditer(locator):
        yield match.group("path"), match.group("source_id")


def build_headline_chain_index(
    registry: Mapping[str, Any],
) -> dict[tuple[str, str], str]:
    """Index ``(locator_path, source_id) -> chain_id`` from every chain locator.

    A binding is the chain's own explicit claim that it backs
    ``<locator_path> == <source_id>``. Raises :class:`EvidenceChainError` if two
    distinct chains both claim the same binding (an ambiguous backing that the
    publish gate could not resolve deterministically).
    """
    index: dict[tuple[str, str], str] = {}
    for chain in registry.get("chains", []):
        if not isinstance(chain, Mapping):
            continue
        chain_id = chain.get("chain_id")
        if not isinstance(chain_id, str):
            continue
        for path, source_id in _iter_locator_bindings(chain):
            key = (path, source_id)
            existing = index.get(key)
            if existing is not None and existing != chain_id:
                raise EvidenceChainError(
                    f"ambiguous headline backing for {path} == {source_id}: "
                    f"both {existing!r} and {chain_id!r} claim it"
                )
            index[key] = chain_id
    return index


def headline_chain_for(
    registry: Mapping[str, Any], locator_path: str, source_id: str
) -> str | None:
    """Return the chain id that backs ``locator_path == source_id``, or None.

    ``None`` means no chain declares that binding: the source has not been
    promoted through a reviewed evidence chain for that metric. The publish gate
    treats that as a FAIL for a headline metric.
    """
    if not source_id:
        return None
    return build_headline_chain_index(registry).get((locator_path, source_id))


def chain_by_id(registry: Mapping[str, Any], chain_id: str) -> Mapping[str, Any] | None:
    """Return the chain object with ``chain_id``, or None."""
    for chain in registry.get("chains", []):
        if isinstance(chain, Mapping) and chain.get("chain_id") == chain_id:
            return chain
    return None


def chain_manifest_sources(chain: Mapping[str, Any]) -> set[str]:
    """Return the ``manifest_source_id`` values a chain anchors to.

    These are the archived/monitored sources the chain actually binds to (its
    primary plus any anchor sources). The publish gate checks that the metric's
    ``primary_source_id`` is among them, so an embedded chain can never be a
    chain that merely names a different source in its locator.
    """
    out: set[str] = set()
    for source in chain.get("sources", []):
        if isinstance(source, Mapping):
            msid = source.get("manifest_source_id")
            if isinstance(msid, str) and msid:
                out.add(msid)
    return out


# Per-metric headline provenance entries are keyed by the public metric address,
# not the locator path, so the snapshot surface reads naturally
# (``confirmed`` / ``confirmed_deaths``) while the gate still anchors on the
# canonical locator. Order is fixed (cases before deaths) for deterministic
# output.
HEADLINE_METRICS: tuple[tuple[str, str], ...] = (
    ("confirmed", HEADLINE_CONFIRMED_LOCATOR),
    ("confirmed_deaths", HEADLINE_CONFIRMED_DEATHS_LOCATOR),
)


def headline_evidence_provenance(
    registry: Mapping[str, Any],
    *,
    confirmed_primary_source_id: str | None,
    confirmed_deaths_primary_source_id: str | None,
) -> list[dict[str, Any]]:
    """Build the headline evidence-chain provenance entries for a snapshot.

    For each present headline metric (confirmed cases, confirmed deaths), resolve
    the backing chain from the metric's ``primary_source_id`` via the locator
    index and emit a deterministic entry:

        {
          "metric": "confirmed",
          "primary_source_id": "inrb-sitrep-019-2026-06-02",
          "evidence_chain_id": "ec:lovs:data:inrb-sitrep-019-visual-promotion:2026-06-02",
          "chain_source": "inrb-sitrep-019-2026-06-02",
          "backed": true
        }

    ``chain_source`` is the chain's anchored source that MATCHES the metric's
    ``primary_source_id`` (the binding the gate enforces). ``backed`` is False
    when no chain declares the binding or the resolved chain does not actually
    anchor to the metric's source, so the embedded surface itself records the
    failure rather than silently dropping the metric. A metric whose
    ``primary_source_id`` is empty/absent is skipped (no headline figure to back).

    The raw ``evidence_chain_id`` (the sensitive ``ec:lovs:`` needle) is included
    here for the internal snapshot and the gate; the public export projects a
    redacted form (see ``public_exports``).
    """
    index = build_headline_chain_index(registry)
    metric_sources = {
        "confirmed": confirmed_primary_source_id,
        "confirmed_deaths": confirmed_deaths_primary_source_id,
    }
    entries: list[dict[str, Any]] = []
    for metric, locator_path in HEADLINE_METRICS:
        source_id = metric_sources.get(metric)
        if not source_id:
            continue
        chain_id = index.get((locator_path, source_id))
        chain_source: str | None = None
        backed = False
        if chain_id is not None:
            chain = chain_by_id(registry, chain_id)
            if chain is not None and source_id in chain_manifest_sources(chain):
                chain_source = source_id
                backed = True
        entries.append(
            {
                "metric": metric,
                "locator": locator_path,
                "primary_source_id": source_id,
                "evidence_chain_id": chain_id,
                "chain_source": chain_source,
                "backed": backed,
            }
        )
    return entries


def _load_optional_json(path: pathlib.Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise EvidenceChainError(f"{path}: invalid JSON: {exc}") from exc


def _numbers_audit_data_rows(path: pathlib.Path) -> list[tuple[int, str]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError as exc:
        raise EvidenceChainError(f"{path}: file not found") from exc

    rows: list[tuple[int, str]] = []
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        if _is_markdown_separator_row(stripped):
            continue
        next_line = lines[idx + 1].strip() if idx + 1 < len(lines) else ""
        if next_line.startswith("|") and _is_markdown_separator_row(next_line):
            continue
        rows.append((idx + 1, stripped))
    return rows


def _is_markdown_separator_row(line: str) -> bool:
    cells = [cell.strip() for cell in line.strip("|").split("|")]
    return bool(cells) and all(cell and set(cell) <= {"-", ":"} for cell in cells)


def _require_object(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EvidenceChainError(f"{path} must be an object")
    return value


def _require_string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EvidenceChainError(f"{path} must be a non-empty string")
    return value


def _require_fields(obj: dict[str, Any], fields: tuple[str, ...], path: str) -> None:
    for field in fields:
        if field not in obj:
            raise EvidenceChainError(f"{path} missing required field {field!r}")


def _validate_chain(chain_value: Any, idx: int, seen_chain_ids: set[str]) -> None:
    path = f"chains[{idx}]"
    chain = _require_object(chain_value, path)
    _require_fields(chain, _REQUIRED_CHAIN_FIELDS, path)

    chain_id = _require_string(chain["chain_id"], f"{path}.chain_id")
    if not _CHAIN_ID_RE.fullmatch(chain_id):
        raise EvidenceChainError(f"{path}.chain_id has invalid format: {chain_id!r}")
    if chain_id in seen_chain_ids:
        raise EvidenceChainError(f"{path}.chain_id duplicates {chain_id!r}")
    seen_chain_ids.add(chain_id)

    verdict = _require_string(chain["verdict"], f"{path}.verdict")
    if verdict not in VALID_VERDICTS:
        raise EvidenceChainError(f"{path}.verdict must be one of {sorted(VALID_VERDICTS)}")
    _require_string(chain["reviewed_at"], f"{path}.reviewed_at")
    _require_string(chain["reviewer"], f"{path}.reviewer")
    _require_string(chain["next_action"], f"{path}.next_action")
    _validate_claim(chain["claim"], path)
    source_ids = _validate_sources(chain["sources"], path)
    has_blocker = _validate_steps(chain["steps"], path, source_ids)

    if verdict in {"unsupported_attribution", "needs_primary_source"} and not has_blocker:
        raise EvidenceChainError(f"{path}: verdict {verdict!r} requires a blocker step")


def _validate_claim(claim_value: Any, path: str) -> None:
    claim = _require_object(claim_value, f"{path}.claim")
    _require_fields(claim, _REQUIRED_CLAIM_FIELDS, f"{path}.claim")
    claim_id = _require_string(claim["claim_id"], f"{path}.claim.claim_id")
    if not _CLAIM_ID_RE.fullmatch(claim_id):
        raise EvidenceChainError(f"{path}.claim.claim_id has invalid format: {claim_id!r}")
    for field in ("artifact", "locator", "statement", "value"):
        _require_string(claim[field], f"{path}.claim.{field}")


def _validate_sources(sources_value: Any, path: str) -> set[str]:
    if not isinstance(sources_value, list) or not sources_value:
        raise EvidenceChainError(f"{path}.sources must be a non-empty list")
    source_ids: set[str] = set()
    for idx, source_value in enumerate(sources_value):
        source_path = f"{path}.sources[{idx}]"
        source = _require_object(source_value, source_path)
        _require_fields(source, _REQUIRED_SOURCE_FIELDS, source_path)
        source_id = _require_string(source["source_id"], f"{source_path}.source_id")
        if not _SOURCE_ID_RE.fullmatch(source_id):
            raise EvidenceChainError(f"{source_path}.source_id has invalid format: {source_id!r}")
        if source_id in source_ids:
            raise EvidenceChainError(f"{source_path}.source_id duplicates {source_id!r}")
        source_ids.add(source_id)
        tier = _require_string(source["tier"], f"{source_path}.tier")
        if tier not in VALID_SOURCE_TIERS:
            raise EvidenceChainError(f"{source_path}.tier must be one of {sorted(VALID_SOURCE_TIERS)}")
        for field in ("citation", "url", "finding"):
            _require_string(source[field], f"{source_path}.{field}")
    return source_ids


def _validate_steps(steps_value: Any, path: str, source_ids: set[str]) -> bool:
    if not isinstance(steps_value, list) or not steps_value:
        raise EvidenceChainError(f"{path}.steps must be a non-empty list")
    seen_step_ids: set[str] = set()
    has_blocker = False
    for idx, step_value in enumerate(steps_value):
        step_path = f"{path}.steps[{idx}]"
        step = _require_object(step_value, step_path)
        _require_fields(step, _REQUIRED_STEP_FIELDS, step_path)
        step_id = _require_string(step["step_id"], f"{step_path}.step_id")
        if not _STEP_ID_RE.fullmatch(step_id):
            raise EvidenceChainError(f"{step_path}.step_id has invalid format: {step_id!r}")
        if step_id in seen_step_ids:
            raise EvidenceChainError(f"{step_path}.step_id duplicates {step_id!r}")
        seen_step_ids.add(step_id)
        kind = _require_string(step["kind"], f"{step_path}.kind")
        if kind not in VALID_STEP_KINDS:
            raise EvidenceChainError(f"{step_path}.kind must be one of {sorted(VALID_STEP_KINDS)}")
        if kind == "blocker":
            has_blocker = True
        _require_string(step["finding"], f"{step_path}.finding")
        source_id = step.get("source_id")
        if source_id is not None:
            _require_string(source_id, f"{step_path}.source_id")
            if source_id not in source_ids:
                raise EvidenceChainError(
                    f"{step_path}.source_id {source_id!r} is not declared in sources"
                )
    return has_blocker


def render_summary(payload: dict[str, Any]) -> str:
    verdict_counts = validate_registry(payload)
    chain_count = len(payload["chains"])
    pieces = [f"LOVS evidence chains: {chain_count} chain(s)"]
    for verdict in sorted(verdict_counts):
        pieces.append(f"{verdict}: {verdict_counts[verdict]}")
    return "\n".join(pieces) + "\n"


def render_numbers_audit_summary(counts: dict[str, int]) -> str:
    return (
        "NUMBERS_AUDIT rows: "
        f"{counts['rows']} row(s), "
        f"{counts['evidence_chain']} evidence-chain-backed, "
        f"{counts['audit_gap']} explicit audit-gap marker(s)\n"
    )


def render_source_anchor_summary(counts: dict[str, int]) -> str:
    return (
        "Evidence source anchors: "
        f"{counts['sources']} source row(s), "
        f"{counts['manifest_anchored']} manifest, "
        f"{counts['registry_anchored']} registry, "
        f"{counts['artifact_anchored']} artifact, "
        f"{counts['external_url']} external URL\n"
    )


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) > 2:
        print(
            "usage: python -m lovs.lovs_evidence [<registry_json> [<numbers_audit_md>]]",
            file=sys.stderr,
        )
        return 2
    path = pathlib.Path(args[0]) if args else default_registry_path()
    numbers_audit_path = pathlib.Path(args[1]) if len(args) == 2 else default_numbers_audit_path()
    try:
        payload = load_registry(path)
        audit_counts = validate_numbers_audit(numbers_audit_path, payload)
        anchor_counts = validate_source_anchors(payload)
    except EvidenceChainError as exc:
        print(f"evidence-chain validation failed: {exc}", file=sys.stderr)
        return 1
    sys.stdout.write(render_summary(payload))
    sys.stdout.write(render_numbers_audit_summary(audit_counts))
    sys.stdout.write(render_source_anchor_summary(anchor_counts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
