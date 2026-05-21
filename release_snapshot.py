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
  python3 release_snapshot.py --with-website       # also dry-run the website sync
  python3 release_snapshot.py --commit             # check, show review gate, confirm, commit
  python3 release_snapshot.py --commit --yes       # non-interactive confirm (CI)

Pipeline stages (in order)
  1. refresh_pipeline.py             -> data/live-bdbv-2026-output.json
  2. make_brief.py                   -> brief/brief.html, brief/visuals/*, deliverables/brief.pdf
  3. export_public_health_dataset.py -> deliverables/public-health-dataset/*
  4. python -m unittest discover -s tests

Byte-determinism: every generated artifact except deliverables/brief.pdf must be
identical when the generators run twice (headless Chrome stamps a build time into
the PDF, so its bytes change even when its content does not). This proves the
shipped deliverables are exactly what the pipeline produces, with no hand drift.

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
from datetime import datetime, timedelta, timezone


REPO_ROOT = pathlib.Path(__file__).parent.resolve()
PY = sys.executable
OUT_PATH = REPO_ROOT / "data" / "live-bdbv-2026-output.json"

# Generated artifacts that MUST be byte-deterministic across runs. brief.pdf is
# included now that make_brief.py normalizes Chrome's embedded render timestamp.
DETERMINISTIC_GLOBS = (
    "data/live-bdbv-2026-output.json",
    "brief/brief.html",
    "brief/visuals/*.svg",
    "deliverables/brief.pdf",
    "deliverables/public-health-dataset/*",
)

PIPELINE_STAGES = (
    ("refresh pipeline", [PY, "refresh_pipeline.py"]),
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
    "refresh_pipeline.py",
    "make_brief.py",
    "export_public_health_dataset.py",
    "sync_to_website.py",
    "release_snapshot.py",
    "lovs",
    "tests",
    "data/live-bdbv-2026-output.json",
    "data/calibration-ledger.json",
    "data/bundibugyo-2026/manifest.json",
    "data/zones.json",
    "data/natural_earth_outlines.json",
    "data/evidence-chains.json",
    "brief/brief.html",
    "brief/visuals",
    "deliverables/brief.pdf",
    "deliverables/public-health-dataset",
)

# Website assets the live site serves, paired with their repo source. The
# --with-website in-sync gate proves the published brief and dataset match the
# regenerated deliverables byte-for-byte.
DEFAULT_WEBSITE_PUBLIC = (
    REPO_ROOT.parent.parent / "website" / "arcede-site" / "apps" / "site" / "public" / "bdbv-2026"
).resolve()
WEBSITE_ASSETS = (
    ("deliverables/brief.pdf", "brief.pdf"),
    ("deliverables/public-health-dataset/lovs-public-health-dataset.xlsx", "lovs-public-health-dataset.xlsx"),
    ("deliverables/public-health-dataset/lovs-public-health-dataset.schema.json", "lovs-public-health-dataset.schema.json"),
    ("deliverables/public-health-dataset/lovs-public-health-dataset.manifest.json", "lovs-public-health-dataset.manifest.json"),
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
    print("Committed. Push when ready (git push origin main).")
    return 0


def check_website_in_sync(public_dir: pathlib.Path) -> bool:
    """Confirm the live site's served assets are byte-identical to the repo deliverables."""
    if not public_dir.exists():
        print(f"  website public dir not found ({public_dir}); skipping in-sync check")
        return True
    drift: list[str] = []
    for repo_rel, web_name in WEBSITE_ASSETS:
        repo_path = REPO_ROOT / repo_rel
        web_path = public_dir / web_name
        if not web_path.exists() or repo_path.read_bytes() != web_path.read_bytes():
            drift.append(web_name)
    if drift:
        sys.stderr.write("  website assets OUT OF SYNC with repo deliverables:\n")
        for name in drift:
            sys.stderr.write(f"    {name}\n")
        sys.stderr.write("  re-run sync_to_website.py and commit the website repo.\n")
        return False
    print(f"  website assets in sync ({len(WEBSITE_ASSETS)} files byte-identical)")
    return True


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
    parser.add_argument("--with-website", action="store_true", help="Also run the website sync (dry-run unless --commit) and gate on asset in-sync.")
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

    if args.with_website:
        print("\nWebsite sync:")
        sync_cmd = [PY, "sync_to_website.py"]
        if not args.commit:
            sync_cmd.append("--dry-run")
        if not _run("website sync", sync_cmd):
            return 1
        if not check_website_in_sync(DEFAULT_WEBSITE_PUBLIC):
            return 1
        print("  (website lives in a separate repo; review and commit it there)")

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
