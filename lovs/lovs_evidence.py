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


def load_registry(path: str | pathlib.Path | None = None) -> dict[str, Any]:
    registry_path = pathlib.Path(path) if path is not None else default_registry_path()
    try:
        payload = json.loads(registry_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise EvidenceChainError(f"{registry_path}: invalid JSON: {exc}") from exc
    validate_registry(payload)
    return payload


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
    except EvidenceChainError as exc:
        print(f"evidence-chain validation failed: {exc}", file=sys.stderr)
        return 1
    sys.stdout.write(render_summary(payload))
    sys.stdout.write(render_numbers_audit_summary(audit_counts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
