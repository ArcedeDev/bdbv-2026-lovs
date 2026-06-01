# SPDX-License-Identifier: Apache-2.0
"""Retrospective attribution audit release gate (spec section 9.2, founder decision).

The gate enforces the forward-only calibration ledger discipline (spec
section 6.1): pinned blocks MUST NOT mutate after the cycle in which they
were pinned. The retrospective attribution audit surface is a SEPARATE
deliverable; it presents disclosure data alongside, but never rewrites,
pinned history.

Mechanism: a pinned-hash sidecar at `data/calibration-ledger.pinned-block-hashes.json`
records the canonical SHA-256 of each pinned block at the moment it was
landed. The gate reads the current `data/calibration-ledger.json`, computes
per-block hashes, and compares against the pinned reference. Any block in the
reference whose current hash differs is refused. New blocks added after the
reference was recorded are tolerated (they have no reference to compare
against until a future pinning step extends the reference).

This is intentionally a defensive surface. The pre-existing snapshot pipeline
also carries-forward ledger blocks without modifying them; the gate provides
the read-modify-compare belt-and-suspenders so a refactor that accidentally
rewrites the ledger surface fails closed at release time.

Stdlib only.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
from typing import Any


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_LEDGER_PATH = REPO_ROOT / "data" / "calibration-ledger.json"
DEFAULT_PINNED_HASHES_PATH = (
    REPO_ROOT / "data" / "calibration-ledger.pinned-block-hashes.json"
)


def compute_block_hash(block: dict[str, Any]) -> str:
    """Compute the canonical SHA-256 of a calibration block.

    The block is JSON-serialized with `sort_keys=True` and `separators=(',',':')`
    so the hash is content-addressable and stable across reformat-only changes.
    """
    canonical = json.dumps(block, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def check_pinned_blocks_unchanged(
    ledger_path: pathlib.Path = DEFAULT_LEDGER_PATH,
    pinned_hashes_path: pathlib.Path = DEFAULT_PINNED_HASHES_PATH,
) -> list[str]:
    """Return human-readable problem lines (empty = clean gate)."""
    if not ledger_path.is_file():
        return [f"calibration-ledger.json missing at {ledger_path}"]
    if not pinned_hashes_path.is_file():
        # No pinned reference yet; gate stays silent. The first cycle after
        # gate installation lands the reference (S7), so absence is legitimate
        # only briefly.
        return []
    try:
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"{ledger_path}: invalid JSON: {exc}"]
    try:
        pinned = json.loads(pinned_hashes_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"{pinned_hashes_path}: invalid JSON: {exc}"]
    pinned_hashes: dict[str, str] = pinned.get("block_hashes") or {}

    problems: list[str] = []
    current_hashes: dict[str, str] = {}
    for block in ledger.get("blocks", []) or []:
        block_id = block.get("block_id") or block.get("pinned_at")
        if not block_id:
            problems.append(
                "calibration-ledger.json carries a block with no block_id or "
                "pinned_at; cannot hash for retrospective audit gate"
            )
            continue
        current_hashes[str(block_id)] = compute_block_hash(block)

    for block_id, expected_hash in pinned_hashes.items():
        actual = current_hashes.get(block_id)
        if actual is None:
            problems.append(
                f"calibration block {block_id!r} declared in pinned hashes "
                f"reference but missing from the current ledger; pinned blocks "
                "must not be removed (spec section 6.1 forward-only ledger)"
            )
            continue
        if actual != expected_hash:
            problems.append(
                f"calibration block {block_id!r} hash changed from "
                f"{expected_hash[:16]}... to {actual[:16]}...; pinned blocks "
                "must not mutate (spec section 6.1 forward-only ledger)"
            )
    return problems


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=pathlib.Path, default=DEFAULT_LEDGER_PATH)
    parser.add_argument(
        "--pinned-hashes",
        type=pathlib.Path,
        default=DEFAULT_PINNED_HASHES_PATH,
    )
    args = parser.parse_args(argv)
    problems = check_pinned_blocks_unchanged(args.ledger, args.pinned_hashes)
    for line in problems:
        sys.stderr.write(f"[FAIL] retrospective_attribution_audit_gate: {line}\n")
    if problems:
        return 1
    print("retrospective_attribution_audit_gate ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
