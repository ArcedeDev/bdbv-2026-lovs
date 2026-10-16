# SPDX-License-Identifier: Apache-2.0
"""Formal mechanism-reconstruction run for SitRep key-message investigation figures.

Usage (private corpus; PDFs never enter the repository):

    python -m lovs.consistency.reconstruct --judge 2 --pdf-dir DIR --out receipt.json

Every run, whatever its result, is appended to the run log next to the receipt
(standard step 7: record every run, not only the one that worked).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import subprocess
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from lovs import sitrep_promotions
from lovs.consistency import bdbv, judge


def pdftotext_version() -> str:
    result = subprocess.run(["pdftotext", "-v"], capture_output=True, text=True)
    return (result.stderr or result.stdout).splitlines()[0].strip()


def pdf_text(path: pathlib.Path) -> str:
    result = subprocess.run(["pdftotext", str(path), "-"], capture_output=True, text=True, check=True)
    return result.stdout


def load_corpus(pdf_dir: pathlib.Path) -> tuple[dict[int, str], list[dict[str, Any]]]:
    """Text per edition, plus a manifest of every file read.

    An edition with several files (a reissued printing) uses the last file in
    name order. The run refuses when two files of one edition print different
    key-message figures, because the target would then be ambiguous."""
    files: dict[int, list[tuple[str, str, bdbv.KeyMessage]]] = {}
    manifest: list[dict[str, Any]] = []
    for path in sorted(pdf_dir.glob("*.pdf")):
        number = bdbv.edition_number_from_path(path)
        if number is None:
            continue
        text = pdf_text(path)
        km = bdbv.extract_key_message(text)
        manifest.append({
            "edition": number,
            "file": path.name,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "key_message_status": km.status,
        })
        files.setdefault(number, []).append((path.name, text, km))
    texts: dict[int, str] = {}
    for number, versions in files.items():
        printed = {json.dumps(km.figures, sort_keys=True) for _, _, km in versions if km.status != bdbv.ABSENT}
        if len(printed) > 1:
            raise SystemExit(f"edition {number} files print different key-message figures")
        texts[number] = versions[-1][1]
    return texts, manifest


REGISTERED_M_EFF = {1: 88, 2: 89}


def run(pdf_dir: pathlib.Path, promotions_dir: pathlib.Path, judge_version: int) -> dict[str, Any]:
    judge_sha = judge.verify_judge(judge_version)
    texts, manifest = load_corpus(pdf_dir)
    if not texts:
        raise SystemExit("zero editions scanned")
    key_messages: dict[int, dict[str, Any]] = {}
    decimals: dict[int, int | None] = {}
    unparseable: list[int] = []
    for number, text in texts.items():
        km = bdbv.extract_key_message(text)
        if km.status == bdbv.PRESENT:
            key_messages[number] = km.figures
            decimals[number] = km.decimals
        elif km.status == bdbv.UNPARSEABLE:
            unparseable.append(number)
    if unparseable:
        raise SystemExit(f"key-message statement present but unparseable in editions {sorted(unparseable)}")
    bodies = {n: b for n, t in texts.items() if (b := bdbv.extract_body(t)) is not None}
    editions = bdbv.load_editions(sitrep_promotions.load_reviewed_promotions(promotions_dir))
    candidates = bdbv.build_candidates(editions, key_messages, bodies, judge_version=judge_version)
    m_eff = judge.effective_hypotheses(candidates)
    if m_eff != REGISTERED_M_EFF[judge_version]:
        raise SystemExit(f"judge v{judge_version} registers m_eff {REGISTERED_M_EFF[judge_version]}, built {m_eff}")
    universe = sorted(set(editions) | set(key_messages))

    # Negative-control editions, each judge read in its own words. In every
    # control edition the key message agrees with same-edition Tableau 3 (C1
    # reproduces it). Judge v1 adds "and differs from the prior edition" (the
    # prior key message does not reproduce it), one set for all carry-forward
    # candidates. Judge v2 adds "and the candidate's output differs from it"
    # (the candidate's output is not C1's), a set per candidate.
    same = next(c for c in candidates if c.candidate_id == "C1")

    def reproduces(n: int, predicted: Any) -> bool:
        return judge.tuple_matches(key_messages[n], predicted, rate_fields=bdbv.rate_fields_for(decimals[n]))[0]

    agrees = [n for n in sorted(key_messages) if reproduces(n, same.generate(n))]
    carry = [c for c in candidates if c.candidate_id.startswith(("C7", "C8", "C9", "C13"))]
    if judge_version == 1:
        control_set = [n for n in agrees if n - 1 in key_messages and not reproduces(n, key_messages[n - 1])]
        controls = {c.candidate_id: control_set for c in carry}
        control_definition = "C1 reproduces KM(j); KM(j-1) exists and does not reproduce KM(j)"
    else:
        controls = {
            c.candidate_id: [n for n in agrees if c.generate(n) not in (None, same.generate(n))]
            for c in carry
        }
        control_definition = "C1 reproduces KM(j); the candidate's output at j exists and differs from C1's"

    verdicts = []
    for number in sorted(key_messages):
        rate_fields = bdbv.rate_fields_for(decimals[number])
        verdict = judge.judge_target(
            number,
            key_messages[number],
            candidates,
            editions=universe,
            seen=bdbv.SEEN_EDITIONS,
            control_editions=controls,
            targets=key_messages,
            rate_fields=rate_fields,
            m_eff=m_eff,
            field_groups=bdbv.field_groups(key_messages[number]) if judge_version >= 2 else None,
            control_rate_fields=lambda j: bdbv.rate_fields_for(decimals[j]),
        )
        record = asdict(verdict)
        record["results"] = [r for r in record["results"] if r["reproduces"] or r["matched_fields"]]
        record["candidates_tried"] = len(candidates)
        record["seen"] = number in bdbv.SEEN_EDITIONS
        verdicts.append(record)
    return {
        "lane": bdbv.LANE,
        "question": "SitRep key-message suspect-investigation figures (SR153 page 10: 506/513, 98.6%, 26 in Ituri)",
        "judge_version": judge_version,
        "judge_sha256": judge_sha,
        "family_alpha": judge.FAMILY_ALPHA,
        "m_eff": m_eff,
        "candidates": [{"id": c.candidate_id, "description": c.description} for c in candidates],
        "seen_editions": sorted(bdbv.SEEN_EDITIONS),
        "pdftotext": pdftotext_version(),
        "corpus": manifest,
        "promotion_editions": sorted(editions),
        "body_prose_editions": sorted(bodies),
        "control_editions": {k: v for k, v in sorted(controls.items())},
        "control_definition": control_definition + "; seen editions excluded",
        "verdicts": verdicts,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--judge", type=int, choices=sorted(REGISTERED_M_EFF), required=True)
    parser.add_argument("--pdf-dir", type=pathlib.Path, required=True)
    parser.add_argument("--promotions-dir", type=pathlib.Path, default=sitrep_promotions.PROMOTIONS_DIR)
    parser.add_argument("--out", type=pathlib.Path, required=True)
    args = parser.parse_args(argv)
    started = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    receipt = run(args.pdf_dir, args.promotions_dir, args.judge)
    text = json.dumps(receipt, ensure_ascii=False, indent=1, sort_keys=True)
    if "\u2014" in text:
        raise SystemExit("receipt text must not contain an em dash")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text + "\n", encoding="utf-8")
    log = args.out.with_name("runs.jsonl")
    with log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "started": started,
            "receipt": args.out.name,
            "receipt_sha256": hashlib.sha256(args.out.read_bytes()).hexdigest(),
            "judge_version": receipt["judge_version"],
            "judge_sha256": receipt["judge_sha256"],
            "verdict_counts": {
                v: sum(r["verdict"] == v for r in receipt["verdicts"])
                for v in (judge.EXPLAINED, judge.AMBIGUOUS, judge.UNEXPLAINED)
            },
        }) + "\n")
    print(f"receipt {args.out} ({len(receipt['verdicts'])} key-message targets)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
