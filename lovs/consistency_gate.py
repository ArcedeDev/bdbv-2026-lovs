# SPDX-License-Identifier: Apache-2.0
"""Release gate: edition-by-edition consistency of reviewed SitRep figures.

Mode comes from LOVS_CONSISTENCY_GATE (report | block | off; default report).

Founder policy table (2026-10-16):
- A Tableau 3 count that passes its own arithmetic stays the served value.
- A secondary figure (key messages, prose) that contradicts it is never served
  and needs a reviewed preserved_conflicts line printing it.
- A Tableau 3 rate that fails its own counts is withheld (null), with the
  printed value kept, as in the care-occupancy template.

Report mode prints every policy breach as a warning and fails only when the
check cannot run: zero editions scanned, or a key message present in the text
but unparseable. Block mode also fails on an undisclosed mismatch in an
edition at or after BLOCK_FROM_EDITION; earlier editions stay in the residue
ledger. Key messages are read from the private PDF layer
(data/bundibugyo-2026/private/raw/<sha256>) when it and pdftotext exist, and
are NOT_EVALUABLE otherwise (as in CI).
"""
from __future__ import annotations

import argparse
import os
import pathlib
import re
import shutil
import subprocess
import sys
from typing import Any, Mapping

from lovs import sitrep_promotions
from lovs.consistency import bdbv
from lovs.consistency.core import MISMATCH, Finding, find_note, tuple_matches

ENV = "LOVS_CONSISTENCY_GATE"
MODES = ("report", "block", "off")
BLOCK_FROM_EDITION = 154
PRIVATE_RAW = sitrep_promotions.REPO_ROOT / "data" / "bundibugyo-2026" / "private" / "raw"


class ConsistencyGateError(RuntimeError):
    """The check could not run; the gate fails in every mode but off."""


def mode_from_env() -> str:
    mode = os.environ.get(ENV, "report").strip().lower() or "report"
    if mode not in MODES:
        raise ConsistencyGateError(f"{ENV}={mode!r}; expected one of {', '.join(MODES)}")
    return mode


def _pdf_text(path: pathlib.Path) -> str | None:
    if shutil.which("pdftotext") is None:
        return None
    result = subprocess.run(["pdftotext", str(path), "-"], capture_output=True, text=True)
    return result.stdout if result.returncode == 0 else None


SHA256 = re.compile(r"[0-9a-f]{64}")
NOT_READ = "not read"


def key_message_finding(payload: Mapping[str, Any], private_raw: pathlib.Path) -> Finding | str | None:
    """The edition's key-message investigation figures against its own Tableau 3.

    Returns a Finding for a contradiction, None when the key message agrees or
    prints no investigation figures, and NOT_READ when the edition's private
    PDF could not be read (no receipt, no file, or pdftotext failed)."""
    receipt = payload.get("source_receipt") or {}
    sha = receipt.get("sha256") if isinstance(receipt, dict) else None
    if not isinstance(sha, str) or not SHA256.fullmatch(sha):
        return NOT_READ
    path = private_raw / sha
    text = _pdf_text(path) if path.is_file() else None
    if text is None:
        return NOT_READ
    number = int(payload["sitrep_number"])
    km = bdbv.extract_key_message(text)
    if km.status == bdbv.UNPARSEABLE:
        raise ConsistencyGateError(f"SR{number}: key-message investigation statement present but unparseable")
    edition = bdbv.edition_from_promotion(payload)
    if km.status != bdbv.PRESENT or edition is None:
        return None
    table = bdbv.national_figures(edition)
    if tuple_matches(km.figures, table, rate_fields=bdbv.rate_fields_for(km.decimals))[0]:
        return None
    notes = [line for line in (payload.get("figures") or {}).get("preserved_conflicts") or [] if isinstance(line, str)]
    # Disclosure needs a note printing every key-message value that the table
    # does not reproduce, not only the counts.
    _, matched = tuple_matches(km.figures, table, rate_fields=bdbv.rate_fields_for(km.decimals))
    unmatched = {k: v for k, v in km.figures.items() if v is not None and k not in matched}
    if "inv" in unmatched or "val" in unmatched:
        unmatched.pop("pct", None)  # the percentage follows from the printed counts
    differing = [v for v in unmatched.values() if not isinstance(v, str)]
    labels = [v for v in unmatched.values() if isinstance(v, str)]
    note = find_note(notes, *differing) if differing else None
    if note is not None and any(label not in note for label in labels):
        note = None
    return Finding(bdbv.LANE, number, "national", "key message vs Tableau 3", "MESSAGES CLES",
                   dict(km.figures), table, MISMATCH, note)


def evaluate(
    promotions_dir: pathlib.Path = sitrep_promotions.PROMOTIONS_DIR,
    *,
    mode: str | None = None,
    private_raw: pathlib.Path = PRIVATE_RAW,
) -> dict[str, Any]:
    if mode is None:
        mode = mode_from_env()
    else:
        mode = mode.strip().lower()
        if mode not in MODES:
            raise ConsistencyGateError(f"{ENV}={mode!r}; expected one of {', '.join(MODES)}")
    if mode == "off":
        return {"mode": mode, "ok": True, "editions": 0, "relations": 0, "warnings": [], "blocking": []}
    promotions = sitrep_promotions.load_reviewed_promotions(promotions_dir)
    findings: list[Finding] = []
    evaluable = private_raw.is_dir() and shutil.which("pdftotext") is not None
    read, unread = 0, []
    for payload in promotions:
        findings += bdbv.table_findings(payload)
        if not evaluable:
            continue
        km = key_message_finding(payload, private_raw)
        if km == NOT_READ:
            unread.append(int(payload["sitrep_number"]))
            continue
        read += 1
        if isinstance(km, Finding):
            findings.append(km)
    editions = {f.edition for f in findings}
    if not editions:
        raise ConsistencyGateError("zero editions scanned")
    undisclosed = [f for f in findings if f.unexplained]
    warnings = [_warning(f) for f in undisclosed]
    blocking = [_warning(f) for f in undisclosed if f.edition >= BLOCK_FROM_EDITION]
    # Once the private layer is present, a recent edition it cannot read is a
    # check that did not run, never a clean one.
    blocking += [f"SR{n}: private PDF not readable; key message not checked" for n in unread if n >= BLOCK_FROM_EDITION]
    return {
        "mode": mode,
        "ok": not (mode == "block" and blocking),
        "editions": len(editions),
        "relations": len(findings),
        "key_messages": f"{read} read, {len(unread)} not read" if evaluable else "not evaluable",
        "warnings": warnings,
        "blocking": blocking,
    }


def _warning(f: Finding) -> str:
    action = "withhold the rate (null, printed value kept)" if "rate_pct" in f.relation else "add a preserved_conflicts line"
    return f"SR{f.edition} {f.scope} {f.relation}: printed {f.printed}, expected {f.expected}; {action}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--promotions-dir", type=pathlib.Path, default=sitrep_promotions.PROMOTIONS_DIR)
    parser.add_argument("--private-raw", type=pathlib.Path, default=PRIVATE_RAW)
    parser.add_argument("--mode", choices=MODES)
    args = parser.parse_args(argv)
    try:
        result = evaluate(args.promotions_dir, mode=args.mode, private_raw=args.private_raw)
    except (ConsistencyGateError, sitrep_promotions.SitRepPromotionError) as exc:
        print(f"[FAIL] consistency gate: {exc}", file=sys.stderr)
        return 1
    for line in result["warnings"]:
        print(f"  [warn] {line}")
    print(f"consistency gate {result['mode']}: {result['editions']} editions, {result['relations']} relations, "
          f"{len(result['warnings'])} undisclosed, key messages {result.get('key_messages')}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
