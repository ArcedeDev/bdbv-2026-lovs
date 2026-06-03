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
  python3 release_snapshot.py --with-website       # also check website bundle parity
  python3 release_snapshot.py --with-website --website-root /path/to/apps/site
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
import re
import subprocess
import sys
import zipfile
from datetime import datetime, timedelta, timezone

from lovs import cdc_date_fidelity
from lovs import cross_surface_parity
from lovs import process_health
from lovs import publication_clock_contract
from lovs import public_repo_hygiene
from lovs import source_dates
from lovs import sitrep_promotion_gate
from lovs import sitrep_promotions
from lovs import website_bundle_parity


REPO_ROOT = pathlib.Path(__file__).parent.resolve()
PY = sys.executable
OUT_PATH = REPO_ROOT / "data" / "live-bdbv-2026-output.json"
README_PATH = REPO_ROOT / "README.md"

# README phrases of the form "<DD Month YYYY> snapshot"; each must name the built as_of.
_README_SNAPSHOT_PHRASE = re.compile(r"(\d{1,2}\s+[A-Z][a-z]+\s+\d{4})\s+snapshot\b")
_MONTH_NAMES = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)


def _format_snapshot_date(as_of: str) -> str:
    """Format a YYYY-MM-DD as_of as the README's 'DD Month YYYY' form (no leading zero).

    Uses an explicit English month table rather than strftime('%B') so the gate is
    locale-independent (a future setlocale must not change the formatted month).
    """
    dt = datetime.strptime(as_of[:10], "%Y-%m-%d")
    return f"{dt.day} {_MONTH_NAMES[dt.month - 1]} {dt.year}"


def find_stale_readme_snapshot_dates(readme_text: str, as_of: str) -> list[str]:
    """Return README '<date> snapshot' phrases whose date is not the built as_of."""
    expected = _format_snapshot_date(as_of)
    return [
        match.group(1)
        for match in _README_SNAPSHOT_PHRASE.finditer(readme_text)
        if match.group(1) != expected
    ]


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
    ("sanitize public export source", [PY, "-m", "lovs.public_exports", "--sanitize-source"]),
    ("write public artifacts", [PY, "-m", "lovs.public_exports"]),
    ("write snapshot contract", [PY, "-m", "lovs.snapshot_contract", "--write"]),
    ("render brief", [PY, "make_brief.py"]),
    (
        "export dataset",
        [PY, "export_public_health_dataset.py",
         "--output-dir", "deliverables/public-health-dataset"],
    ),
    ("finalize public release manifest", [PY, "-m", "lovs.public_exports"]),
)

# Public artifacts staged by --commit. Restricted inputs (data/bundibugyo-2026/
# private/, raw archive bytes) are intentionally NOT auto-staged here; the
# operator commits those deliberately if and when their license allows.
PUBLIC_RELEASE_PATHS = (
    ".gitignore",
    "README.md",
    "CITATIONS.md",
    "NUMBERS_AUDIT.md",
    "PIPELINE.md",
    "refresh_pipeline.py",
    "daily_snapshot_prep.py",
    "daily_snapshot_health.py",
    "make_brief.py",
    "export_public_health_dataset.py",
    "snapshot_preflight.py",
    "source_ingest.py",
    "sitrep_promotion_extract.py",
    "release_snapshot.py",
    "tools/bdbv_daily_prep_cron.sh",
    "lovs",
    "tests",
    "data/live-bdbv-2026-output.json",
    "data/snapshot_contract.json",
    "data/calibration-ledger.json",
    "data/pcr_ascertainment_parallel_scoring.json",
    "data/bundibugyo-2026/manifest.json",
    "data/external_sources",
    "data/sitrep_promotions",
    "data/zones.json",
    "data/natural_earth_outlines.json",
    "data/evidence-chains.json",
    "brief/brief.html",
    "brief/visuals",
    "deliverables/brief.pdf",
    "deliverables/public-health-dataset",
)

# Website assets the live site serves, paired with their repo source. The
# website bundle gate proves the snapshot JSON plus these public assets match
# the regenerated canonical LOVS release bundle.
DEFAULT_WEBSITE_PUBLIC = (
    REPO_ROOT.parent.parent / "website" / "arcede-site" / "apps" / "site" / "public" / "bdbv-2026"
).resolve()
DEFAULT_WEBSITE_ROOT = DEFAULT_WEBSITE_PUBLIC.parent.parent
WEBSITE_ASSETS = (
    ("brief/visuals/ascertainment_band_per_zone.svg", "visuals/ascertainment_band_per_zone.svg"),
    ("brief/visuals/corridor_risk.svg", "visuals/corridor_risk.svg"),
    ("brief/visuals/detection_depth.svg", "visuals/detection_depth.svg"),
    ("brief/visuals/per_zone_snapshot.svg", "visuals/per_zone_snapshot.svg"),
    ("brief/visuals/pre_registration_timeline.svg", "visuals/pre_registration_timeline.svg"),
    ("brief/visuals/visibility_gap.svg", "visuals/visibility_gap.svg"),
    ("deliverables/brief.pdf", "brief.pdf"),
    ("deliverables/public-health-dataset/lovs-public-health-dataset.xlsx", "lovs-public-health-dataset.xlsx"),
    ("deliverables/public-health-dataset/lovs-public-health-dataset.schema.json", "lovs-public-health-dataset.schema.json"),
    ("deliverables/public-health-dataset/lovs-public-health-dataset.manifest.json", "lovs-public-health-dataset.manifest.json"),
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


def run_pipeline(refresh_extra_args: tuple[str, ...] = ()) -> bool:
    """Run every pipeline stage. ``refresh_extra_args`` is appended to the
    refresh_pipeline.py invocation (e.g. ``--as-of 2026-05-29`` to emit a
    carried-forward snapshot for a later cycle).
    """
    for label, cmd in PIPELINE_STAGES:
        full_cmd = list(cmd)
        if label == "refresh pipeline" and refresh_extra_args:
            full_cmd = full_cmd + list(refresh_extra_args)
        print(f"  - {label} ...", flush=True)
        if not _run(label, full_cmd):
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


def scan_public_artifacts_for_leaks() -> list[str]:
    """Return public artifact leak findings; empty means the hard scan is clean."""
    findings: list[str] = []
    paths: list[pathlib.Path] = []
    for pattern in PUBLIC_TEXT_ARTIFACTS:
        paths.extend(path for path in sorted(REPO_ROOT.glob(pattern)) if path.is_file())
    for path in paths:
        rel = path.relative_to(REPO_ROOT)
        for chunk in _artifact_text_chunks(path):
            for needle in INTERNAL_LEAK_NEEDLES:
                if needle in chunk:
                    findings.append(f"{rel}: contains {needle!r}")
    return findings


def scan_website_source_for_release_hazards(
    website_root: pathlib.Path = DEFAULT_WEBSITE_ROOT,
) -> list[str]:
    """Return website-source hazards that can desync the release surface.

    The website may still copy brief.pdf for direct URLs, but the sidebar and
    page chrome should not promote it while the workbook is the canonical public
    appendix. This catches reintroduced PDF download links in source before a
    daily snapshot goes live.
    """
    app_root = website_root / "app" / "bdbv-2026"
    if not app_root.exists():
        return []
    findings: list[str] = []
    for path in sorted(app_root.rglob("*")):
        if path.suffix not in {".ts", ".tsx"} or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "brief.pdf" in text and ("href=" in text or "BRIEF_URL" in text or "PDF_URL" in text):
            findings.append(f"{path.relative_to(website_root)}: links or promotes brief.pdf")
    return findings


def _manifest_validity() -> dict:
    """Map canonical source_id -> count-eligibility status from the manifest.

    Used by the reconciliation-invariant guard to tell a valid primary from a
    source_review source. A source may keep health-zone table semantics under
    review while still exposing national headline counts that are explicitly
    reviewed for model use; INRB/INSP/UMIE May-27 is that edge case. Indexed by
    the suffix-stripped (canonical) id so it matches the ids the reconciler
    writes into reported_counts.
    """
    manifest_path = REPO_ROOT / "data" / "bundibugyo-2026" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    out: dict[str, str | None] = {}
    for entry in manifest.get("entries", []):
        source_id = str(entry.get("source_id", ""))
        canonical = source_id[:-5] if source_id.endswith("-live") else source_id
        normalized = entry.get("normalized_content", {})
        out[canonical] = {
            "table_semantics_status": normalized.get("table_semantics_status"),
            "headline_count_status": normalized.get("headline_count_status"),
        }
    return out


def check_reconciliation_invariants(summary: dict) -> list[str]:
    """Enforce the higher-of-valid-primaries reconciliation doctrine structurally.

    The doctrine is otherwise carried only in prose and code comments, which no
    gate keys off; this is the structural seam for the multi-primary,
    data-latency, irregular-cadence edges the snapshot reconciles. For each
    reported_counts metric it asserts:

      - the promoted primary lies within its reconciled [min, max] band;
      - the promoted primary equals the band maximum, so the endpoint is the
        ceiling of the reconciled band and never a lower in-band figure;
      - the primary source is a VALID primary, not a source_review source; a
        higher source_review figure must be held as a dated conflict anchor, not
        promoted (this is the exact defect that once shipped 179 over 177);
      - the primary source does not also appear in its own conflict trail;
      - the conflict trail is preserved (non-empty) so demoted and lower figures
        stay auditable.

    Returns a list of human-readable violations; empty means the snapshot is
    clean.
    """
    validity = _manifest_validity()
    problems: list[str] = []
    reported = summary.get("reported_counts", {})
    if not reported:
        return ["reported_counts missing from snapshot summary"]
    for metric, rc in reported.items():
        primary = rc.get("primary")
        low, high = rc.get("min"), rc.get("max")
        primary_source_id = str(rc.get("primary_source_id", ""))
        trail = rc.get("conflicting_source_ids", []) or []
        if primary is None or low is None or high is None:
            problems.append(f"{metric}: incomplete reconciled band (min/max/primary)")
            continue
        if not (low <= primary <= high):
            problems.append(f"{metric}: primary {primary} outside reconciled band [{low}, {high}]")
        if primary != high:
            problems.append(
                f"{metric}: primary {primary} is not the band ceiling {high}; "
                "higher-of-valid-primaries requires the endpoint to be the highest in-band figure"
            )
        if primary_source_id and primary_source_id in trail:
            problems.append(
                f"{metric}: primary source {primary_source_id} also appears in its own conflict trail"
            )
        if not trail:
            problems.append(f"{metric}: empty conflict trail; demoted and lower figures must stay auditable")
        primary_status = validity.get(primary_source_id) or {}
        table_status = primary_status.get("table_semantics_status")
        headline_status = primary_status.get("headline_count_status")
        if (
            table_status == "source_review"
            and headline_status != "national_counts_promoted_health_zones_source_review"
        ):
            problems.append(
                f"{metric}: primary source {primary_source_id} is source_review; a source_review "
                "figure must be held as a dated conflict anchor, never promoted to the endpoint"
            )
    return problems


def run_release_gates(summary: dict) -> bool:
    """Run release gates whose value is broader than ordinary unit tests."""
    as_of = str(summary.get("as_of", ""))[:10]
    data_as_of = str(summary.get("data_as_of", as_of))[:10]
    print("Running release gates ...", flush=True)
    if not _run(
        "snapshot preflight",
        [PY, "snapshot_preflight.py", "--as-of", as_of, "--data-as-of", data_as_of],
    ):
        return False
    if not _run("evidence chains", [PY, "-m", "lovs.lovs_evidence"]):
        return False
    if not _run("source registry", [PY, "-m", "lovs.source_registry_gate"]):
        return False
    try:
        sitrep_result = sitrep_promotion_gate.validate(require_through=data_as_of)
    except sitrep_promotions.SitRepPromotionError as exc:
        sys.stderr.write(f"[FAIL] SitRep promotion gate: {exc}\n")
        return False
    print(
        "  SitRep promotion gate OK "
        f"({sitrep_result['reviewed_count']} reviewed; latest {sitrep_result['latest_data_as_of']})"
    )
    if not _run(
        "snapshot contract",
        [PY, "-m", "lovs.snapshot_contract", "--check-text", "--check-dataset"],
    ):
        return False
    # Plan A 2026-05-28 (spec section 7.2): additive release gates.
    if not _run(
        "INSP per-zone consistency",
        [PY, "-m", "lovs.insp_per_zone_consistency_gate"],
    ):
        return False
    if not _run(
        "attribution-lag disclosure",
        [PY, "-m", "lovs.attribution_lag_disclosure_gate"],
    ):
        return False
    if not _run(
        "PCR modulator shadow surface (R3 belt-and-suspenders)",
        [PY, "-m", "lovs.pcr_modulator_shadow_gate"],
    ):
        return False
    if not _run(
        "PCR modulator parallel-scoring pre-commitment (evidence-gated promotion, spec 8.2)",
        [PY, "-m", "lovs.pcr_parallel_scoring_precommit_gate"],
    ):
        return False
    if not _run(
        "zone alias bridge coverage",
        [PY, "-m", "lovs.zone_alias_bridge_gate"],
    ):
        return False
    if not _run(
        "retrospective attribution audit (forward-only ledger)",
        [PY, "-m", "lovs.retrospective_attribution_audit_gate"],
    ):
        return False
    try:
        publication_result = publication_clock_contract.validate()
    except publication_clock_contract.PublicationClockContractError as exc:
        sys.stderr.write(f"[FAIL] publication-clock contract gate: {exc}\n")
        return False
    print(
        "  publication-clock contract OK "
        f"({publication_result['primaries_checked']} primaries checked; "
        f"{publication_result['publication_clock_only']} publication-clock-only)"
    )
    reconciliation_problems = check_reconciliation_invariants(summary)
    if reconciliation_problems:
        sys.stderr.write("[FAIL] reconciliation-invariant gate (higher-of-valid-primaries):\n")
        for problem in reconciliation_problems[:40]:
            sys.stderr.write(f"    {problem}\n")
        return False
    print("  reconciliation-invariant gate OK (each primary is the highest valid in-band figure)")
    hygiene_findings = public_repo_hygiene.scan_all()
    if hygiene_findings:
        sys.stderr.write("[FAIL] public repository hygiene gate:\n")
        for finding in hygiene_findings[:40]:
            sys.stderr.write(f"    {finding}: disallowed automation provenance marker\n")
        return False
    print("  public repository hygiene gate clean")
    leaks = scan_public_artifacts_for_leaks()
    if leaks:
        sys.stderr.write("[FAIL] public artifact leak scan:\n")
        for finding in leaks[:40]:
            sys.stderr.write(f"    {finding}\n")
        return False
    print("  public artifact leak scan clean")
    fidelity_sources_dir = (
        REPO_ROOT / "data" / "bundibugyo-2026" / "private" / "sources"
    )
    if fidelity_sources_dir.is_dir():
        fidelity = cdc_date_fidelity.check_cdc_data_as_of_matches_raw(
            REPO_ROOT / "data" / "bundibugyo-2026" / "manifest.json",
            fidelity_sources_dir,
        )
        if fidelity["mismatches"]:
            sys.stderr.write("[FAIL] CDC data-as-of fidelity:\n")
            for finding in fidelity["mismatches"][:40]:
                sys.stderr.write(f"    {finding}\n")
            return False
        print(
            "  CDC data-as-of fidelity OK "
            f"({fidelity['checked']} entries checked; "
            f"{len(fidelity['unverifiable'])} unverifiable)"
        )
        for line in fidelity["unverifiable"][:40]:
            print(f"    info: {line}")
    else:
        # private/sources/ is gitignored (copyrighted source HTML stays
        # local-only). The fidelity gate's protection runs on the founder
        # machine before push, where raw bytes exist; public CI clones
        # never see the raw, so the gate gracefully skips here. Symmetric
        # with the cross_surface_parity SKIPPED branch below.
        print(
            "  CDC data-as-of fidelity SKIPPED "
            f"(private sources not present at {fidelity_sources_dir})"
        )
    if DEFAULT_WEBSITE_PUBLIC.is_dir():
        parity = cross_surface_parity.check_cross_surface_parity(
            REPO_ROOT, DEFAULT_WEBSITE_PUBLIC
        )
        if parity["mismatches"] or parity["missing"]:
            sys.stderr.write("[FAIL] cross-surface byte-parity:\n")
            for finding in (parity["mismatches"] + parity["missing"])[:40]:
                sys.stderr.write(f"    {finding}\n")
            return False
        print(
            f"  cross-surface byte-parity OK ({parity['checked']} mirrored file pairs match)"
        )
    else:
        print(
            f"  cross-surface byte-parity SKIPPED (website sibling not present at {DEFAULT_WEBSITE_PUBLIC})"
        )
    website_process_root = (
        DEFAULT_WEBSITE_PUBLIC.parent.parent.parent.parent / ".process"
        if DEFAULT_WEBSITE_PUBLIC.is_dir()
        else None
    )
    process_roots = [REPO_ROOT / ".process"]
    if website_process_root and website_process_root.is_dir():
        process_roots.append(website_process_root)
    health = process_health.check_process_health(process_roots)
    if health["hard"]:
        sys.stderr.write("[FAIL] process-health (active change-id sidecars + em-dashes):\n")
        for finding in health["hard"][:40]:
            sys.stderr.write(f"    {finding}\n")
        return False
    print(
        "  process-health OK "
        f"({health['scanned']} change-id dirs scanned; "
        f"{len(health['soft'])} soft findings)"
    )
    for line in health["soft"][:40]:
        print(f"    info: {line}")
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
    readme_stale = find_stale_readme_snapshot_dates(
        README_PATH.read_text(encoding="utf-8"), as_of
    )
    if readme_stale:
        sys.stderr.write("[FAIL] README snapshot-date currency:\n")
        for finding in readme_stale:
            sys.stderr.write(
                f"    README references '{finding} snapshot' but the built snapshot is "
                f"{_format_snapshot_date(as_of)}\n"
            )
        return False
    print("  README snapshot-date currency OK")
    publication_findings = public_repo_hygiene.scan_new_commit_publication_state()
    if publication_findings:
        sys.stderr.write("[FAIL] publish-state guard (commit subjects ahead of baseline):\n")
        for finding in publication_findings[:40]:
            sys.stderr.write(f"    {finding}\n")
        return False
    print("  publish-state guard clean")
    try:
        clock_result = publication_clock_contract.validate()
    except publication_clock_contract.PublicationClockContractError as exc:
        sys.stderr.write(f"[FAIL] publication-clock contract gate: {exc}\n")
        return False
    print(
        "  publication-clock contract OK "
        f"({clock_result['primaries_checked']} primaries; "
        f"{clock_result['publication_clock_only']} publication-clock-only)"
    )
    return True


def _diff_bytes_inline(rel: str, a: bytes, b: bytes,
                       max_chunks: int = 6, chunk_size: int = 64) -> None:
    """Print up to ``max_chunks`` hex/ascii byte windows where ``a`` and ``b`` differ.

    Temporary CI diagnostic: writes to stderr so a non-deterministic artifact
    surfaces its first divergent regions directly in the release-gate log,
    avoiding a separate artifact-upload round-trip while we identify what
    Chrome (or another generator) is varying between runs.
    """
    sys.stderr.write(f"      sizes: first={len(a)} second={len(b)}\n")
    n = min(len(a), len(b))
    chunks = 0
    i = 0
    while i < n and chunks < max_chunks:
        if a[i] != b[i]:
            start = max(0, i - 8)
            end = min(n, start + chunk_size)
            ascii_a = "".join(chr(c) if 32 <= c < 127 else "." for c in a[start:end])
            ascii_b = "".join(chr(c) if 32 <= c < 127 else "." for c in b[start:end])
            sys.stderr.write(f"      @{start:08x} ({rel}):\n")
            sys.stderr.write(f"        first  hex  : {a[start:end].hex()}\n")
            sys.stderr.write(f"        second hex  : {b[start:end].hex()}\n")
            sys.stderr.write(f"        first  ascii: {ascii_a}\n")
            sys.stderr.write(f"        second ascii: {ascii_b}\n")
            chunks += 1
            i = end
        else:
            i += 1
    if len(a) != len(b) and chunks < max_chunks:
        sys.stderr.write(f"      (size differs; tail not diffed)\n")


def check_determinism(refresh_extra_args: tuple[str, ...] = ()) -> bool:
    """Generate once more and confirm every non-PDF artifact is byte-identical."""
    print("Verifying byte-determinism (second run) ...", flush=True)
    first = _hash_artifacts()
    first_bytes: dict[str, bytes] = {}
    for pattern in DETERMINISTIC_GLOBS:
        for path in sorted(REPO_ROOT.glob(pattern)):
            if path.is_file():
                rel = str(path.relative_to(REPO_ROOT))
                first_bytes[rel] = path.read_bytes()
    if not run_pipeline(refresh_extra_args):
        return False
    second = _hash_artifacts()
    drift = sorted(k for k in first.keys() | second.keys() if first.get(k) != second.get(k))
    if drift:
        sys.stderr.write("[FAIL] non-deterministic artifacts:\n")
        for k in drift:
            sys.stderr.write(f"    {k}\n")
            second_path = REPO_ROOT / k
            a = first_bytes.get(k, b"")
            b = second_path.read_bytes() if second_path.exists() else b""
            _diff_bytes_inline(k, a, b)
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
    print("Committed. Push when ready (git push origin main).")
    return 0


def check_website_bundle_parity(website_root: pathlib.Path) -> bool:
    """Confirm the latest website snapshot and served assets match LOVS."""
    result = website_bundle_parity.check_website_bundle_parity(REPO_ROOT, website_root)
    if result["status"] == "skipped":
        print(f"  website bundle parity SKIPPED ({result.get('reason', 'no reason given')})")
        return True
    if result["status"] != "ok":
        sys.stderr.write("[FAIL] website canonical release-bundle parity:\n")
        for finding in result["findings"][:60]:
            sys.stderr.write(f"    {finding}\n")
        if len(result["findings"]) > 60:
            sys.stderr.write(f"    ... {len(result['findings']) - 60} more finding(s)\n")
        return False
    checked = result.get("checked", {})
    print(
        "  website bundle parity OK "
        f"(latest={result.get('latest_snapshot_date')}; "
        f"counts={checked.get('counts')}; "
        f"source_refs={checked.get('source_refs')}; "
        f"asset_pairs={checked.get('asset_pairs')})"
    )
    return True


def detect_snapshot_readiness(manifest: dict, last_snapshot_date: str, now_utc: datetime) -> dict:
    """Decide whether a new snapshot is due, by source publication availability.

    A snapshot is a knowledge-state artifact, so cadence keys off when a source
    became publicly available, not the report/data date plotted in charts. A
    report published on 23 May with data_as_of/date_rapportage 22 May should
    trigger a 23 May candidate; it should not rewrite the frozen 22 May snapshot.
    """
    # Key off source publication availability, never retrieved_at. A re-fetch of
    # an older report carries a fresh retrieved_at but an unchanged published_at,
    # and must not read as a new snapshot day.
    publication_dates = sorted(
        source_dates.source_publication_date(e) or ""
        for e in manifest.get("entries", [])
        if source_dates.source_triggers_snapshot(e)
    )
    latest = publication_dates[-1] if publication_dates else ""
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
    parser.add_argument("--with-website", action="store_true", help="Also check website release-bundle parity; does not sync or publish.")
    parser.add_argument("--website-root", type=pathlib.Path, default=DEFAULT_WEBSITE_ROOT, help=f"Path to apps/site for --with-website (default: {DEFAULT_WEBSITE_ROOT})")
    parser.add_argument("--detect", action="store_true", help="Report whether a new snapshot is due (data recency + outbreak-local evening), then exit.")
    args = parser.parse_args(argv)

    if args.detect:
        return run_detect()

    # Pass --as-of through to refresh_pipeline so a carried-forward snapshot is
    # built when the founder is publishing a LOCF cycle (e.g. May 29-31 after
    # an INRB declaration pause). Default (no --as-of) builds the base snapshot.
    refresh_args: tuple[str, ...] = (
        ("--as-of", args.as_of) if args.as_of else ()
    )
    print("Running snapshot pipeline ...", flush=True)
    if not run_pipeline(refresh_args):
        return 1
    print("Running test suite ...", flush=True)
    if not run_tests():
        return 1
    if not check_determinism(refresh_args):
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

    if args.with_website:
        print("\nWebsite parity check (website repo is separate; update and commit it there):")
        website_hazards = scan_website_source_for_release_hazards(args.website_root)
        if website_hazards:
            sys.stderr.write("[FAIL] website release-surface hazards:\n")
            for finding in website_hazards[:40]:
                sys.stderr.write(f"    {finding}\n")
            return 1
        print("  website release-surface scan clean")
        if not check_website_bundle_parity(args.website_root):
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
