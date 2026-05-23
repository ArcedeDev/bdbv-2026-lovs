#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Release orchestrator for a LOVS outbreak snapshot.

Runs the full snapshot pipeline end to end, proves it is byte-deterministic,
surfaces the pre-commitment-critical facts for human review, and (only behind an
explicit go-ahead) commits the regenerated public artifacts.

Usage
-----
  python3 release_snapshot.py                     # --check (default): regenerate + verify, no commit
  python3 release_snapshot.py --as-of 2026-05-20  # also assert the built snapshot date
  python3 release_snapshot.py --commit             # check, show review gate, confirm, commit
  python3 release_snapshot.py --commit --yes       # non-interactive confirm (CI)

Pipeline stages (in order)
  1. refresh_pipeline.py             -> data/live-bdbv-2026-output.json
  2. make_brief.py                   -> brief/brief.html, brief/visuals/*, deliverables/brief.pdf
  3. export_public_health_dataset.py -> deliverables/public-health-dataset/*
  4. python -m unittest discover -s tests

Byte-determinism: every generated artifact, including deliverables/brief.pdf,
must be identical when the generators run twice. This proves the shipped
deliverables are exactly what the pipeline produces, with no hand drift.

Human-review gate: --commit prints the snapshot date, reconciled counts, the
carried-forward calibration points, and the resolution date, then requires the
operator to type "release" (or pass --yes) before anything is committed. This is
the pre-commitment checkpoint: once a snapshot is released it is immutable, so a
later correction must become a new dated snapshot, not an edit of this one.

Stdlib only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import subprocess
import sys
import zipfile
from datetime import datetime, timedelta, timezone


REPO_ROOT = pathlib.Path(__file__).parent.resolve()
PY = sys.executable
OUT_PATH = REPO_ROOT / "data" / "live-bdbv-2026-output.json"


def _needle(*parts: str) -> str:
    return "".join(parts)

# Generated artifacts that MUST be byte-deterministic across runs. brief.pdf is
# included now that make_brief.py normalizes Chrome's embedded render timestamp.
DETERMINISTIC_GLOBS = (
    "data/live-bdbv-2026-output.json",
    "data/snapshot_contract.json",
    "brief/brief.html",
    "brief/visuals/*.svg",
    "deliverables/brief.pdf",
    "deliverables/public-health-dataset/*",
)

PIPELINE_STAGES = (
    ("refresh pipeline", [PY, "refresh_pipeline.py"]),
    ("write snapshot contract", [PY, "-m", "lovs.snapshot_contract", "--write"]),
    ("render brief", [PY, "make_brief.py"]),
    (
        "export dataset",
        [PY, "export_public_health_dataset.py",
         "--output-dir", "deliverables/public-health-dataset"],
    ),
)

# Public artifacts staged by --commit. Restricted inputs (data/bundibugyo-2026/
# private/, raw archive bytes) are intentionally NOT auto-staged here; the
# operator commits those deliberately if and when their license allows.
PUBLIC_RELEASE_PATHS = (
    "README.md",
    "CITATIONS.md",
    "NUMBERS_AUDIT.md",
    "PIPELINE.md",
    "refresh_pipeline.py",
    "make_brief.py",
    "export_public_health_dataset.py",
    "tools",
    "snapshot_preflight.py",
    "source_ingest.py",
    "release_snapshot.py",
    "lovs",
    "tests",
    "data/live-bdbv-2026-output.json",
    "data/snapshot_contract.json",
    "data/calibration-ledger.json",
    "data/bundibugyo-2026/manifest.json",
    "data/external_sources",
    "data/zones.json",
    "data/natural_earth_outlines.json",
    "data/evidence-chains.json",
    "brief/brief.html",
    "brief/visuals",
    "deliverables/brief.pdf",
    "deliverables/public-health-dataset",
)

PUBLIC_TEXT_ARTIFACTS = (
    "README.md",
    "NUMBERS_AUDIT.md",
    "CITATIONS.md",
    "brief/brief.html",
    "data/live-bdbv-2026-output.json",
    "data/snapshot_contract.json",
    "data/evidence-chains.json",
    "data/external_sources/*.json",
    "data/external_sources/README.md",
    "deliverables/public-health-dataset/*.csv",
    "deliverables/public-health-dataset/*.json",
    "deliverables/public-health-dataset/*.xlsx",
    "deliverables/brief.pdf",
)
INTERNAL_LEAK_NEEDLES = (
    _needle("Clau", "de"),
    _needle("Anth", "ropic"),
    _needle("Co", "dex"),
    _needle(".co", "dex"),
    str(pathlib.Path.home()),
    _needle("agent_", "workspace"),
    _needle("read_", "handoffs"),
    _needle("runtime", ".env"),
)
PUBLIC_REPO_BOUNDARY_NEEDLES = (
    _needle("apps", "/site/"),
    _needle("arcede", "-site"),
    _needle("sync", "_to_", "website"),
    _needle("sync", "-bdbv-", "lovs.py"),
    _needle("--", "website", "-public-dir"),
    _needle("website", "_public_dir"),
    _needle("website", "_workbook"),
    _needle("website", "_schema"),
    _needle("website", "_manifest"),
    _needle("external", "_artifact"),
)
# Snapshot-readiness cadence. A new snapshot is due only when the manifest holds
# data dated after the last snapshot AND that reporting day is complete: the
# outbreak-local clock (Ituri Province, eastern DRC, CAT = UTC+2) has passed the
# evening hour, so the day's figures are no longer being revised.
MANIFEST_PATH = REPO_ROOT / "data" / "bundibugyo-2026" / "manifest.json"
OUTBREAK_UTC_OFFSET_HOURS = 2
EVENING_HOUR_LOCAL = 18


def _run(label: str, cmd: list[str]) -> bool:
    """Run a subprocess in the repo root; print a short failure tail on error."""
    result = subprocess.run(cmd, cwd=REPO_ROOT, text=True, capture_output=True)
    if result.returncode != 0:
        sys.stderr.write(f"\n[FAIL] {label} (exit {result.returncode})\n")
        tail = (result.stdout or "") + (result.stderr or "")
        sys.stderr.write("\n".join(tail.splitlines()[-25:]) + "\n")
        return False
    return True


def _hash_artifacts() -> dict[str, str]:
    digests: dict[str, str] = {}
    for pattern in DETERMINISTIC_GLOBS:
        for path in sorted(REPO_ROOT.glob(pattern)):
            if path.is_file():
                rel = str(path.relative_to(REPO_ROOT))
                digests[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return digests


def run_pipeline() -> bool:
    for label, cmd in PIPELINE_STAGES:
        print(f"  - {label} ...", flush=True)
        if not _run(label, cmd):
            return False
    return True


def run_tests() -> bool:
    print("  - tests ...", flush=True)
    return _run("tests", [PY, "-m", "unittest", "discover", "-s", "tests"])


def _artifact_text_chunks(path: pathlib.Path) -> list[str]:
    if path.suffix == ".xlsx":
        chunks: list[str] = []
        with zipfile.ZipFile(path) as workbook:
            for name in workbook.namelist():
                if name.endswith((".xml", ".rels")):
                    chunks.append(workbook.read(name).decode("utf-8", "ignore"))
        return chunks
    return [path.read_bytes().decode("utf-8", "ignore")]


def _public_release_text_paths() -> list[pathlib.Path]:
    """Return text-like files that are part of the public release surface."""
    suffixes = {".csv", ".html", ".json", ".md", ".py", ".rels", ".svg", ".txt", ".xlsx", ".xml", ".yml"}
    paths: set[pathlib.Path] = set()
    for release_path in PUBLIC_RELEASE_PATHS:
        path = REPO_ROOT / release_path
        if path.is_file() and path.suffix in suffixes:
            paths.add(path)
        elif path.is_dir():
            for child in path.rglob("*"):
                if child.is_file() and child.suffix in suffixes:
                    paths.add(child)
    for pattern in PUBLIC_TEXT_ARTIFACTS:
        paths.update(path for path in REPO_ROOT.glob(pattern) if path.is_file())
    return sorted(paths)


def scan_public_artifacts_for_leaks() -> list[str]:
    """Return public artifact leak findings; empty means the hard scan is clean."""
    findings: list[str] = []
    for path in _public_release_text_paths():
        rel = path.relative_to(REPO_ROOT)
        for chunk in _artifact_text_chunks(path):
            for needle in (*INTERNAL_LEAK_NEEDLES, *PUBLIC_REPO_BOUNDARY_NEEDLES):
                if needle in chunk:
                    findings.append(f"{rel}: contains {needle!r}")
    return findings


def run_release_gates(summary: dict) -> bool:
    """Run release gates whose value is broader than ordinary unit tests."""
    as_of = str(summary.get("as_of", ""))[:10]
    print("Running release gates ...", flush=True)
    if not _run("snapshot preflight", [PY, "snapshot_preflight.py", "--as-of", as_of]):
        return False
    if not _run("evidence chains", [PY, "-m", "lovs.lovs_evidence"]):
        return False
    if not _run("source registry", [PY, "-m", "lovs.source_registry_gate"]):
        return False
    if not _run(
        "snapshot contract",
        [PY, "-m", "lovs.snapshot_contract", "--check-text", "--check-dataset"],
    ):
        return False
    leaks = scan_public_artifacts_for_leaks()
    if leaks:
        sys.stderr.write("[FAIL] public artifact leak scan:\n")
        for finding in leaks[:40]:
            sys.stderr.write(f"    {finding}\n")
        return False
    print("  public artifact leak scan clean")
    contract = json.loads((REPO_ROOT / "data" / "snapshot_contract.json").read_text(encoding="utf-8"))
    partition = contract["confirmed_case_partition"]
    watchlist = contract["corridor_watchlist"]
    print(
        "  snapshot contract OK "
        f"({partition['headline_confirmed_total']} headline confirmed; "
        f"{partition['zone_attributed_confirmed_total']} zone-attributed; "
        f"{partition['unallocated_confirmed_total']} unallocated; "
        f"{watchlist['corridor_count']} corridors)"
    )
    print("  public dataset evidence contract OK")
    print("  stale corridor narrative scan clean")
    return True


def check_determinism() -> bool:
    """Generate once more and confirm every non-PDF artifact is byte-identical."""
    print("Verifying byte-determinism (second run) ...", flush=True)
    first = _hash_artifacts()
    if not run_pipeline():
        return False
    second = _hash_artifacts()
    drift = sorted(k for k in first.keys() | second.keys() if first.get(k) != second.get(k))
    if drift:
        sys.stderr.write("[FAIL] non-deterministic artifacts:\n")
        for k in drift:
            sys.stderr.write(f"    {k}\n")
        return False
    print(f"  deterministic across {len(second)} artifacts")
    return True


def print_review(summary: dict) -> None:
    bar = "=" * 62
    print("\n" + bar)
    print("RELEASE REVIEW  (pre-commitment checkpoint)")
    print(bar)
    print(f"  outbreak    : {summary.get('outbreak_id')}")
    print(f"  as_of       : {summary.get('as_of')}")
    print(f"  resolves_at : {summary.get('resolves_at')}")
    print("  reported counts  (primary [min-max]):")
    for metric, count in summary.get("reported_counts", {}).items():
        print(f"      {metric:<10} {count.get('primary')} [{count.get('min')}-{count.get('max')}]")
    zone_counts = summary.get("zone_attributed_counts") or {}
    if zone_counts:
        zone_confirmed = sum(
            int(row.get("confirmed") or 0)
            for row in zone_counts.values()
            if isinstance(row, dict)
        )
        headline_confirmed = (
            summary.get("reported_counts", {})
            .get("confirmed", {})
            .get("primary")
        )
        print(
            f"  source zones: {len(zone_counts)} zones, "
            f"{zone_confirmed} zone-attributed confirmed "
            f"(headline confirmed {headline_confirmed})"
        )
    corridors = summary.get("corridors") or []
    if corridors:
        lower_bounds = [float(c["risk_adj_lower_50"]) * 100 for c in corridors]
        upper_bounds = [float(c["risk_adj_upper_50"]) * 100 for c in corridors]
        print(
            "  current corridor watchlist 50% adjusted range: "
            f"{min(lower_bounds):.1f}-{max(lower_bounds):.1f}% lower, "
            f"{min(upper_bounds):.1f}-{max(upper_bounds):.1f}% upper "
            f"({len(corridors)} corridors)"
        )
    print("  carried-forward calibration points:")
    for hyp in summary.get("mode_b_hypotheses", []):
        band = hyp.get("risk_adj_50") or ["?", "?"]
        lo, hi = band[0], band[1]
        print(f"      {hyp.get('corridor'):<28} 50%=[{lo}, {hi}]  {hyp.get('hypothesis_id')}")
    print(bar)


def staged_release_status() -> list[str]:
    """git status --porcelain limited to the public release allowlist."""
    result = subprocess.run(
        ["git", "status", "--porcelain", "--", *PUBLIC_RELEASE_PATHS],
        cwd=REPO_ROOT, text=True, capture_output=True,
    )
    return result.stdout.splitlines()


def do_commit(summary: dict, assume_yes: bool) -> int:
    subprocess.run(["git", "add", "--", *PUBLIC_RELEASE_PATHS], cwd=REPO_ROOT, check=True)
    staged = subprocess.run(
        ["git", "diff", "--cached", "--stat"], cwd=REPO_ROOT, text=True, capture_output=True
    ).stdout.strip()
    if not staged:
        print("Nothing to commit: the released artifacts already match HEAD.")
        return 0
    print("\nStaged for this release:")
    print(staged)

    if not assume_yes:
        if not sys.stdin.isatty():
            sys.stderr.write(
                "\n[ABORT] --commit needs confirmation but stdin is not a TTY. "
                "Re-run interactively or pass --yes.\n"
            )
            return 2
        reply = input('\nType "release" to commit this immutable snapshot: ').strip()
        if reply != "release":
            print("Aborted; nothing committed.")
            return 1

    date = str(summary.get("as_of", ""))[:10]
    message = (
        f"Release LOVS snapshot {date}\n\n"
        f"Regenerated and verified via release_snapshot.py: pipeline output, brief, "
        f"and public-health dataset are byte-deterministic and the full test suite "
        f"passes. resolves_at {summary.get('resolves_at')}."
    )
    subprocess.run(["git", "commit", "-m", message], cwd=REPO_ROOT, check=True)
    print("Committed. Push the release branch or open a PR when ready; do not push directly to main.")
    return 0


def detect_snapshot_readiness(manifest: dict, last_snapshot_date: str, now_utc: datetime) -> dict:
    """Decide whether a new snapshot is due, by data recency and outbreak-local cadence.

    Ready only when the manifest holds a source dated after the last snapshot AND
    that reporting day is complete: either it predates the outbreak-local today, or
    the outbreak-local clock has passed the evening hour (today's picture settled).
    """
    # Key off published_at, the source's REPORT date, never retrieved_at: a
    # re-fetch of an older report carries a fresh retrieved_at but an unchanged
    # published_at, and must not read as a new reporting day (see the regression
    # test test_re_retrieval_uses_report_date_not_retrieval_time).
    source_dates = sorted(
        e.get("published_at", "")[:10]
        for e in manifest.get("entries", [])
        if e.get("published_at")
    )
    latest = source_dates[-1] if source_dates else ""
    local_now = now_utc + timedelta(hours=OUTBREAK_UTC_OFFSET_HOURS)
    local_today = local_now.date().isoformat()
    evening_reached = local_now.hour >= EVENING_HOUR_LOCAL

    if not latest or latest <= last_snapshot_date:
        reason = f"no source dated after the last snapshot ({last_snapshot_date}); latest is {latest or 'none'}"
        return {"ready": False, "reason": reason, "latest_source_date": latest}
    if latest < local_today:
        return {"ready": True, "reason": f"new data for {latest}, a completed prior day", "latest_source_date": latest}
    if latest == local_today and evening_reached:
        reason = f"new data for today ({latest}); outbreak-local evening reached ({local_now:%H:%M} CAT)"
        return {"ready": True, "reason": reason, "latest_source_date": latest}
    if latest == local_today:
        reason = f"new data for today ({latest}) but outbreak-local time is {local_now:%H:%M} CAT (before {EVENING_HOUR_LOCAL}:00); day not complete"
        return {"ready": False, "reason": reason, "latest_source_date": latest}
    reason = f"latest source {latest} is ahead of outbreak-local today ({local_today}); verify source dating"
    return {"ready": False, "reason": reason, "latest_source_date": latest}


def run_detect() -> int:
    """Report whether a new snapshot is due, without running the pipeline."""
    if not OUT_PATH.exists():
        sys.stderr.write(f"[FAIL] missing pipeline output: {OUT_PATH}\n")
        return 1
    last = str(json.loads(OUT_PATH.read_text(encoding="utf-8")).get("as_of", ""))[:10]
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8")) if MANIFEST_PATH.exists() else {"entries": []}
    verdict = detect_snapshot_readiness(manifest, last, datetime.now(timezone.utc))
    print(f"last snapshot : {last}")
    print(f"latest source : {verdict['latest_source_date'] or 'none'}")
    print(f"snapshot due  : {'YES' if verdict['ready'] else 'no'}")
    print(f"reason        : {verdict['reason']}")
    return 0 if verdict["ready"] else 3


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="Regenerate and verify only (default).")
    parser.add_argument("--commit", action="store_true", help="Commit after a clean check and confirmation.")
    parser.add_argument("--yes", action="store_true", help="Skip the interactive confirmation (CI).")
    parser.add_argument("--as-of", default=None, help="Assert the built snapshot date is this YYYY-MM-DD.")
    parser.add_argument("--detect", action="store_true", help="Report whether a new snapshot is due (data recency + outbreak-local evening), then exit.")
    args = parser.parse_args(argv)

    if args.detect:
        return run_detect()

    print("Running snapshot pipeline ...", flush=True)
    if not run_pipeline():
        return 1
    print("Running test suite ...", flush=True)
    if not run_tests():
        return 1
    if not check_determinism():
        return 1

    if not OUT_PATH.exists():
        sys.stderr.write(f"[FAIL] missing pipeline output: {OUT_PATH}\n")
        return 1
    summary = json.loads(OUT_PATH.read_text(encoding="utf-8"))

    if not run_release_gates(summary):
        return 1

    if args.as_of:
        built = str(summary.get("as_of", ""))[:10]
        if built != args.as_of:
            sys.stderr.write(f"[FAIL] --as-of {args.as_of} but snapshot is {built}\n")
            return 1
        print(f"as_of assertion OK ({built})")

    print_review(summary)

    readiness = detect_snapshot_readiness(
        json.loads(MANIFEST_PATH.read_text(encoding="utf-8")) if MANIFEST_PATH.exists() else {"entries": []},
        str(summary.get("as_of", ""))[:10],
        datetime.now(timezone.utc),
    )
    print(f"\nNext-snapshot check: {'DUE' if readiness['ready'] else 'not due'} ({readiness['reason']})")

    if args.commit:
        return do_commit(summary, args.yes)

    pending = staged_release_status()
    print("\nCheck passed.")
    if pending:
        print("Working tree differs from HEAD for these release paths:")
        for line in pending:
            print(f"    {line}")
        print("Re-run with --commit to release.")
    else:
        print("Working tree already matches the regenerated artifacts.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
