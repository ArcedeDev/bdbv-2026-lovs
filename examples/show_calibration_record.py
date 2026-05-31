#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Inspect the public BDBV calibration record.

Renders the pre-registered public calibration commitments as a readable
scorecard, recomputes each row's commitment hash so an inspector can confirm
the pre-registered payload is unchanged since registration, and (once
commitments resolve) shows the full-cohort outcome with no row selection.

It is presentation-only and read-only. It prints public counts, tier labels,
control roles, resolution dates, source policies, and resolution states. It
never computes or prints probabilities, intervals, scores, weights, or any
model internals, and it imports no private implementation.

    python3 examples/show_calibration_record.py

Reads data/public_calibration_ledger.csv and data/public_calibration_status.json.
"""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
LEDGER = REPO_ROOT / "data/public_calibration_ledger.csv"
STATUS = REPO_ROOT / "data/public_calibration_status.json"


def recompute_commitment_hash(row: dict[str, str]) -> str:
    """Reproduce the public commitment hash: SHA-256 over the row payload with
    the hash column removed. This is the same recipe documented in
    DATA_DICTIONARY.md, so anyone can verify a row independently."""
    payload = {key: value for key, value in row.items() if key != "commitment_hash"}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def main() -> int:
    if not LEDGER.is_file() or not STATUS.is_file():
        raise SystemExit("calibration record not found; run from the repository root")
    with LEDGER.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    status = json.loads(STATUS.read_text(encoding="utf-8"))

    print("BDBV Public Calibration Record")
    print("==============================")
    print("This record lets anyone independently check that these corridor-watch")
    print("commitments were registered before their outcomes were known, and that")
    print("none was altered afterward. It recomputes every commitment hash from the")
    print("published ledger and reports whether each one still matches.")
    print("Presentation-only: it shows tier labels and resolution states, never")
    print("probabilities, intervals, scores, or model internals.")
    print("")
    print(f"as_of: {status.get('as_of')}")
    print(
        f"commitments: {len(rows)}  open: {status.get('open_commitments')}  "
        f"resolved: {status.get('resolved_commitments')}"
    )
    print(f"next resolution date: {status.get('next_resolution_date')}")
    print("")

    verified = 0
    mismatched: list[str] = []
    for row in rows:
        if recompute_commitment_hash(row) == (row.get("commitment_hash") or ""):
            verified += 1
        else:
            mismatched.append(row.get("ledger_id") or "(unknown)")
    print("Commitment-hash verification")
    print(f"- rows with a verified pre-registration hash: {verified}/{len(rows)}")
    if mismatched:
        print(f"- MISMATCH (payload changed since registration): {', '.join(mismatched)}")
    else:
        print("- every row matches its pre-registered hash (payloads unchanged since registration)")
    print("- recipe: SHA-256 over each ledger row minus its commitment_hash column;")
    print("  see CALIBRATION_RESOLUTION_PUBLIC.md to reproduce it independently.")
    print("")

    print("Commitments")
    for row in rows:
        print(f"- {row.get('ledger_id')} [{row.get('status')}] resolves {row.get('resolution_date')}")
        print(f"    Q: {row.get('public_question')}")
        print(
            f"    tier: {row.get('public_value_or_tier')}  role: {row.get('control_role')}  "
            f"source: {row.get('resolution_source_policy')}"
        )
        if row.get("resolved_value"):
            print(f"    outcome: {row.get('resolved_value')}")
    print("")

    resolved = [row for row in rows if row.get("resolved_value")]
    print("Full-cohort outcome view (no row selection)")
    if not resolved:
        print("- no commitments have resolved yet; re-run after a resolution date to see outcomes")
        print("- to verify independently: on or after each resolution date, check the public sources")
        print("  named per commitment and apply the states in CALIBRATION_RESOLUTION_PUBLIC.md")
    else:
        print(f"- resolved: {len(resolved)}/{len(rows)} (full cohort, no selection)")
        tally: dict[str, int] = {}
        for row in resolved:
            outcome = row.get("resolved_value") or "(blank)"
            tally[outcome] = tally.get(outcome, 0) + 1
        for outcome in sorted(tally):
            print(f"- outcome '{outcome}': {tally[outcome]}")
        controls = [
            row for row in resolved
            if "positive" in (row.get("control_role") or "") or "negative" in (row.get("control_role") or "")
        ]
        if controls:
            print("- pre-registered control commitments (registered role -> public outcome):")
            for row in controls:
                print(f"    {row.get('ledger_id')}: {row.get('control_role')} -> {row.get('resolved_value')}")
        print("- score each row with the published protocol in CALIBRATION_RESOLUTION_PUBLIC.md;")
        print("  this view reports outcomes, it does not assign scores.")
    print("")
    print("Boundary: this is a public accountability record. It publishes tier labels and")
    print("resolution states only, never probabilities, intervals, scores, or model internals.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
