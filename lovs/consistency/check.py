# SPDX-License-Identifier: Apache-2.0
"""Edition-by-edition consistency check over reviewed SitRep promotions.

Usage:

    python -m lovs.consistency.check --ledger residue.jsonl [--receipt receipt.json]

Evaluates every arithmetic relation in Tableau 3 (rates against their own
counts, province rows against the national row, remainders), flags national
tuples carried unchanged from the previous edition, and, when a private
reconstruction receipt is given, adds every key-message figure that its own
edition's table does not reproduce. Mismatches with no reviewed note that
prints both sides go to the residue ledger. Reruns give identical bytes.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections import Counter
from typing import Any, Iterable, Mapping

from lovs import sitrep_promotions
from lovs.consistency import bdbv
from lovs.consistency.core import MISMATCH, Finding, ResidueEntry, write_ledger

TABLE_RELATIONS = ["rate", "sum", "difference"]


def table_tuples(promotions: Iterable[Mapping[str, Any]]) -> dict[int, dict[str, Any]]:
    out = {}
    for payload in promotions:
        edition = bdbv.edition_from_promotion(payload)
        if edition is not None:
            out[edition.number] = {k: v for k, v in edition.total.items() if v is not None}
    return out


def key_message_findings(receipt: Mapping[str, Any]) -> list[Finding]:
    """Key-message tuples that same-edition Tableau 3 (C1) does not reproduce."""
    out = []
    for verdict in receipt["verdicts"]:
        c1 = next((r for r in verdict["results"] if r["candidate_id"] == "C1"), None)
        if c1 and c1["reproduces"]:
            continue
        mechanism = ", ".join(verdict["accepted"]) or "none accepted"
        out.append(Finding(
            bdbv.LANE, verdict["edition"], "national",
            f"key message vs Tableau 3 (judge v{receipt['judge_version']}: {verdict['verdict']}, {mechanism})",
            "MESSAGES CLES", verdict["target"], None, MISMATCH, None,
        ))
    return out


def run(promotions_dir: pathlib.Path, receipt: Mapping[str, Any] | None) -> tuple[list[Finding], list[ResidueEntry]]:
    promotions = sitrep_promotions.load_reviewed_promotions(promotions_dir)
    if not promotions:
        raise SystemExit("zero editions scanned")
    findings = [f for payload in promotions for f in bdbv.table_findings(payload)]
    findings += bdbv.carried_forward_findings(table_tuples(promotions), section="Tableau 3")
    residue = [f.residue(TABLE_RELATIONS) for f in findings if f.unexplained]
    if receipt is not None:
        tried = [c["id"] for c in receipt["candidates"]]
        km = key_message_findings(receipt)
        findings += km
        residue += [f.residue(tried) for f in km]
    return findings, residue


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--promotions-dir", type=pathlib.Path, default=sitrep_promotions.PROMOTIONS_DIR)
    parser.add_argument("--receipt", type=pathlib.Path, help="private reconstruction receipt (key messages)")
    parser.add_argument("--ledger", type=pathlib.Path, required=True)
    args = parser.parse_args(argv)
    receipt = json.loads(args.receipt.read_text(encoding="utf-8")) if args.receipt else None
    findings, residue = run(args.promotions_dir, receipt)
    count = write_ledger(args.ledger, residue)
    status = Counter(f.status for f in findings)
    editions = len({f.edition for f in findings})
    print(f"consistency check: {editions} editions, {len(findings)} relations {dict(sorted(status.items()))}, "
          f"{count} residue entries -> {args.ledger}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
