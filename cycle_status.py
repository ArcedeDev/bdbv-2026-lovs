# SPDX-License-Identifier: Apache-2.0
"""Read-only composer for the BDBV 2026 daily prep cycle status.

Each prep cycle already emits structured outputs (a freshness/full-health
report, the calibration-resolution report, the release readiness verdict). They
live in different files and are described by different agents with slightly
different meanings, which is the semantic drift documented in
``labs/bdbv-snapshot-prep-manager-handoff-review-2026-05-24.md``. This module
joins them into ONE consolidated cycle-status artifact plus a human-readable
routing plan, so the next cycle is one deterministic command.

What it consolidates:
  - publication-route date + basis (``daily_snapshot_prep.resolve_review_snapshot_date``),
  - snapshot-readiness verdict (``release_snapshot.detect_snapshot_readiness``, reused),
  - the analytic data date (``data/live-bdbv-2026-output.json`` ``as_of``),
  - the prep full-health review queue, routed by ``classification``,
  - the calibration resolution summary (``data/calibration-resolution-report.json``),
  - an open human-decision register derived ONLY from the above.

It computes nothing new about the outbreak and promotes nothing. It NEVER writes
the immutable ledger, the outbreak manifest, the live output, or any released
snapshot; it writes only the cycle-status JSON + routing plan (atomic). Reusing
the canonical readiness/date helpers means it cannot drift from the pipeline's
own definition of "snapshot due".

Stdlib only.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import sys

import daily_snapshot_prep
import release_snapshot

REPO_ROOT = pathlib.Path(__file__).parent.resolve()
DATA_DIR = REPO_ROOT / "data"
HEALTH_DIR = DATA_DIR / "external_sources" / "health"
RESOLUTION_REPORT_PATH = DATA_DIR / "calibration-resolution-report.json"
OUT_DIR = REPO_ROOT / "deliverables" / "cycle-status"

# Immutable / released artifacts this composer must never overwrite.
PROTECTED_PATHS = (
    DATA_DIR / "calibration-ledger.json",
    DATA_DIR / "bundibugyo-2026" / "manifest.json",
    DATA_DIR / "live-bdbv-2026-output.json",
)

SCHEMA_VERSION = "bdbv-cycle-status/v1"

# A review item's classification fully determines how it is routed; this is a
# general mapping over the freshness classifier's vocabulary, not a per-source
# table, so a new source of an existing class routes without a code change.
ROUTING = {
    "source_review_required": (
        "source-librarian",
        "verify the detected date and archive the bytes into the outbreak manifest before any promotion",
    ),
    "source_review_blocked": (
        "source-review-owner",
        "resolve the table/semantics review before any counts can be promoted into the model",
    ),
    "fetch_blocked": (
        "devops / source-librarian",
        "re-fetch via an alternate backend (AIR) and investigate the HTTP block; do not infer counts from a failed fetch",
    ),
    "watch_only": (
        "none",
        "watch only; this source can never be promoted into scored counts",
    ),
    "context_update_review": (
        "source-librarian",
        "context/guidance freshness only; archive if useful, but do not route as a count or model-input blocker",
    ),
}
ROUTING_DEFAULT = ("manual", "manual review: unrecognized source classification")


def _atomic_write_text(path: pathlib.Path, text: str) -> None:
    """Write text atomically (temp + os.replace), refusing protected paths."""
    path = pathlib.Path(path)
    resolved = path.resolve()
    for protected in PROTECTED_PATHS:
        if resolved == protected.resolve():
            raise RuntimeError(
                f"refusing to write protected artifact {protected.name}; cycle_status is read-only"
            )
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _load_json(path: pathlib.Path) -> dict | None:
    path = pathlib.Path(path)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def route_review_item(item: dict) -> dict:
    """Map one full-health review-queue item to a routing action by classification."""
    classification = item.get("classification", "")
    owner_role, action = ROUTING.get(classification, ROUTING_DEFAULT)
    return {
        "registry_id": item.get("registry_id"),
        "publisher": item.get("publisher"),
        "classification": classification,
        "latest_detected_date": item.get("latest_detected_date"),
        "review_reasons": item.get("review_reasons", []),
        "extracted_counts": item.get("extracted_counts", {}),
        "owner_role": owner_role,
        "action": action,
    }


def summarize_calibration(report: dict | None) -> dict:
    """Summarize the calibration resolution report (advisory; founder-gated append)."""
    if not report:
        return {"status": "no_report"}
    summary = report.get("summary", {})
    resolved = [
        {"corridor": p.get("corridor"), "status": p.get("status"), "brier": p.get("brier")}
        for p in report.get("points", [])
        if str(p.get("status", "")).startswith("resolved")
    ]
    advisory = bool(
        report.get("proposed_ledger_outcomes", {}).get("advisory_not_written", True)
    )
    return {
        "status": "ok",
        "as_of": report.get("as_of"),
        "by_status": summary.get("by_status", {}),
        "mean_brier_resolved": summary.get("mean_brier_resolved"),
        "resolved": resolved,
        "ledger_append": "founder-gated" if advisory else "written",
    }


def open_human_decisions(health: dict, calibration: dict) -> list[dict]:
    """Derive the open-decision register from structured inputs only (nothing invented)."""
    decisions: list[dict] = []
    queue = health.get("freshness", {}).get("classified_review_queue", [])
    for item in queue:
        if item.get("classification", "") in ("source_review_required", "source_review_blocked", "fetch_blocked"):
            decisions.append({
                "kind": "source_review",
                "registry_id": item.get("registry_id"),
                "question": "resolve source review before this source can affect counts/geography",
            })
    if health.get("ready_for_public_release") is False:
        decisions.append({
            "kind": "publication",
            "question": "publication-state route is preserved; promoting a new public snapshot requires ready_for_public_release=true",
        })
    if calibration.get("status") == "ok" and calibration.get("resolved") and calibration.get("ledger_append") == "founder-gated":
        decisions.append({
            "kind": "calibration_ledger_append",
            "question": "founder-gated append of resolved outcomes at resolves_at; the resolver is advisory only",
        })
    return decisions


def build_cycle_status(as_of: str) -> dict:
    """Compose the consolidated cycle status for ``as_of`` from existing outputs."""
    route = daily_snapshot_prep.resolve_review_snapshot_date("")
    live = _load_json(release_snapshot.OUT_PATH) or {}
    analytic_data_date = str(live.get("as_of", ""))[:10]

    health_path = HEALTH_DIR / f"bdbv-2026-{as_of}-full-health.json"
    health = _load_json(health_path)
    calibration = summarize_calibration(_load_json(RESOLUTION_REPORT_PATH))

    if health is None:
        health_block = {"report_present": False, "review_queue": []}
        decisions = []
        if calibration.get("status") == "ok" and calibration.get("resolved") and calibration.get("ledger_append") == "founder-gated":
            decisions.append({
                "kind": "calibration_ledger_append",
                "question": "founder-gated append of resolved outcomes at resolves_at; the resolver is advisory only",
            })
    else:
        prep = health.get("prep", {})
        health_block = {
            "report_present": True,
            "traffic_light": health.get("traffic_light"),
            "ready_for_public_release": health.get("ready_for_public_release"),
            "release_check_returncode": prep.get("release_check_returncode"),
            "website_sync_status": prep.get("website_sync_status"),
            "live_public_parity": health.get("live_public_parity", {}).get("status"),
            "issues": health.get("issues", []),
            "review_queue": [
                route_review_item(item)
                for item in health.get("freshness", {}).get("classified_review_queue", [])
            ],
        }
        decisions = open_human_decisions(health, calibration)

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_by": "cycle_status.py",
        "cycle_date": as_of,
        "analytic_data_date": analytic_data_date,
        "publication_route": {
            "date": route.get("snapshot_date"),
            "basis": route.get("basis"),
        },
        "readiness": {
            "snapshot_due": bool(route.get("ready")),
            "reason": route.get("reason"),
            "latest_source_date": route.get("latest_source_date"),
        },
        "health": health_block,
        "calibration": calibration,
        "open_human_decisions": decisions,
    }


def _md_table(rows: list[dict]) -> str:
    if not rows:
        return "_(none)_\n"
    out = ["| source | classification | owner | action |", "|---|---|---|---|"]
    for r in rows:
        out.append(
            f"| {r['registry_id']} | {r['classification']} | {r['owner_role']} | {r['action']} |"
        )
    return "\n".join(out) + "\n"


def render_routing_plan(status: dict) -> str:
    """Render the human-readable routing plan markdown from the cycle status."""
    h = status["health"]
    cal = status["calibration"]
    route = status["publication_route"]
    lines = [
        f"# BDBV 2026 cycle status + routing plan — {status['cycle_date']}",
        "",
        "_Read-only prep artifact. Not a publication. Nothing here is committed, pushed, or synced to the live site._",
        "",
        "## Dates",
        f"- Cycle date: {status['cycle_date']}",
        f"- Analytic data date: {status['analytic_data_date']}",
        f"- Publication-route date: {route['date']} (basis: `{route['basis']}`)",
        "",
        "## Readiness",
        f"- New snapshot due: {'YES' if status['readiness']['snapshot_due'] else 'no'}",
        f"- Reason: {status['readiness']['reason']}",
        "",
        "## Prep health",
    ]
    if h.get("report_present"):
        lines += [
            f"- Traffic light: {h.get('traffic_light')}",
            f"- Ready for public release: {h.get('ready_for_public_release')}",
            f"- release --check returncode: {h.get('release_check_returncode')}",
            f"- Website sync: {h.get('website_sync_status')} (route preserved when skipped)",
            f"- Live public parity: {h.get('live_public_parity')}",
        ]
    else:
        lines.append("- Health report: missing for this cycle (run daily_snapshot_prep first)")
    lines += [
        "",
        "## Review queue routing",
        _md_table(h.get("review_queue", [])),
        "## Calibration resolution (advisory; ledger append founder-gated)",
    ]
    if cal.get("status") == "ok":
        lines.append(f"- By status: {cal.get('by_status')}")
        lines.append(f"- Mean Brier (resolved): {cal.get('mean_brier_resolved')}")
        for r in cal.get("resolved", []):
            lines.append(f"- {r['corridor']}: {r['status']} (Brier {r['brier']})")
    else:
        lines.append("- No calibration resolution report present.")
    lines += ["", "## Open human epistemic decisions"]
    if status["open_human_decisions"]:
        for d in status["open_human_decisions"]:
            tag = d.get("registry_id") or d["kind"]
            lines.append(f"- [{d['kind']}] {tag}: {d['question']}")
    else:
        lines.append("- _(none)_")
    lines.append("")
    return "\n".join(lines)


def write_artifacts(status: dict, out_dir: pathlib.Path = OUT_DIR) -> dict:
    """Write the cycle-status JSON and routing-plan markdown atomically."""
    out_dir = pathlib.Path(out_dir)
    stem = f"bdbv-2026-{status['cycle_date']}-cycle-status"
    json_path = out_dir / f"{stem}.json"
    md_path = out_dir / f"bdbv-2026-{status['cycle_date']}-routing-plan.md"
    _atomic_write_text(json_path, json.dumps(status, indent=2, sort_keys=True) + "\n")
    _atomic_write_text(md_path, render_routing_plan(status))
    return {"json": str(json_path), "routing_plan": str(md_path)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--as-of", default=dt.date.today().isoformat(), help="Cycle date YYYY-MM-DD.")
    parser.add_argument("--out-dir", default=str(OUT_DIR), help="Output directory for the cycle-status artifacts.")
    parser.add_argument("--print", action="store_true", dest="print_only", help="Print the cycle-status JSON to stdout; write nothing.")
    args = parser.parse_args(argv)

    status = build_cycle_status(args.as_of[:10])
    if args.print_only:
        print(json.dumps(status, indent=2, sort_keys=True))
        return 0
    written = write_artifacts(status, pathlib.Path(args.out_dir))
    print(f"wrote {written['json']}")
    print(f"wrote {written['routing_plan']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
