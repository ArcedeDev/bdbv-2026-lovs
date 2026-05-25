#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Autonomous prep loop for unpublished BDBV daily snapshot review.

This script intentionally composes the existing release/source-ingest tools:

* wake Earth MCP if requested;
* run a slot-specific or full registered-source freshness check;
* pull machine-readable source bytes into the private review dropbox when safe;
* rebuild the deterministic LOVS artifacts and sync an unpublished website
  review snapshot for the requested date.

It does not archive reviewed sources into the outbreak manifest, promote watch
signals into scored counts, commit, push, or publish.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import select
import subprocess
import sys
import time
from typing import Any

import release_snapshot
import source_ingest
from lovs import daily_prep_health

REPO_ROOT = pathlib.Path(__file__).resolve().parent
PREP_DIR = REPO_ROOT / "data" / "external_sources" / "prep"
DEFAULT_WEBSITE_ROOT = pathlib.Path("/private/tmp/lovs-rc-preview/apps/site")
EARTH_MCP_STDIO = pathlib.Path.home() / ".arcede" / "bin" / "earth-mcp-stdio"
DEFAULT_EARTH_AGENT_ID = os.environ.get("LOVS_EARTH_AGENT_ID", "")
PY = sys.executable


def _today_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).date().isoformat()


def _freshness_path(as_of: str, slot_id: str | None) -> pathlib.Path:
    suffix = f"-{slot_id}" if slot_id else ""
    return source_ingest.FRESHNESS_DIR / f"bdbv-2026-{as_of}{suffix}.json"


def _load_json(path: pathlib.Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def earth_awake(timeout_s: float = 8.0) -> dict[str, Any]:
    """Initialize installed Earth MCP once so scheduled runs can use its runtime."""
    if not EARTH_MCP_STDIO.exists():
        return {"status": "skipped", "reason": f"missing {EARTH_MCP_STDIO}"}

    proc = subprocess.Popen(
        [str(EARTH_MCP_STDIO)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    def send(obj: dict[str, Any]) -> None:
        assert proc.stdin is not None
        proc.stdin.write(json.dumps(obj) + "\n")
        proc.stdin.flush()

    def read_response(request_id: int) -> dict[str, Any]:
        assert proc.stdout is not None
        end = time.time() + timeout_s
        while time.time() < end:
            ready, _, _ = select.select([proc.stdout], [], [], 0.2)
            if not ready:
                continue
            line = proc.stdout.readline()
            if not line:
                continue
            payload = json.loads(line)
            if payload.get("id") == request_id:
                return payload
        raise TimeoutError(f"Earth MCP did not respond within {timeout_s:.1f}s")

    try:
        send({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "bdbv-lovs-daily-prep", "version": "1"},
            },
        })
        init = read_response(1)
        send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        return {
            "status": "ok",
            "server": (init.get("result") or {}).get("serverInfo", {}),
        }
    except Exception as exc:  # noqa: BLE001 - prep should record, not crash, on Earth wake failure.
        return {"status": "failed", "reason": str(exc)}
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            proc.kill()


def earth_tool_call(tool_name: str, arguments: dict[str, Any], timeout_s: float = 8.0) -> dict[str, Any]:
    """Call one installed Earth MCP tool and return the raw result envelope."""
    if not EARTH_MCP_STDIO.exists():
        return {"status": "skipped", "reason": f"missing {EARTH_MCP_STDIO}"}
    proc = subprocess.Popen(
        [str(EARTH_MCP_STDIO)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    def send(obj: dict[str, Any]) -> None:
        assert proc.stdin is not None
        proc.stdin.write(json.dumps(obj) + "\n")
        proc.stdin.flush()

    def read_response(request_id: int) -> dict[str, Any]:
        assert proc.stdout is not None
        end = time.time() + timeout_s
        while time.time() < end:
            ready, _, _ = select.select([proc.stdout], [], [], 0.2)
            if not ready:
                continue
            line = proc.stdout.readline()
            if not line:
                continue
            payload = json.loads(line)
            if payload.get("id") == request_id:
                return payload
        raise TimeoutError(f"Earth MCP tool {tool_name} did not respond within {timeout_s:.1f}s")

    try:
        send({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "bdbv-lovs-daily-prep", "version": "1"},
            },
        })
        read_response(1)
        send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        send({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        })
        return {"status": "ok", "result": read_response(2).get("result")}
    except Exception as exc:  # noqa: BLE001 - prep records Earth failures as review metadata.
        return {"status": "failed", "reason": str(exc)}
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            proc.kill()


def summarize_for_journal(packet: dict[str, Any], packet_path: pathlib.Path) -> str:
    review_ids = [row.get("registry_id") for row in packet.get("review_queue", [])]
    review_snapshot_date = packet.get("review_snapshot_date") or {}
    review_date_text = ""
    if review_snapshot_date.get("snapshot_date"):
        review_date_text = (
            f" review_snapshot_date={review_snapshot_date.get('snapshot_date')}"
            f" basis={review_snapshot_date.get('basis')};"
        )
    return (
        f"Daily BDBV snapshot prep completed for {packet['as_of']} "
        f"slot={packet.get('slot_id') or 'full'}; "
        f"freshness={packet.get('freshness_summary')}; "
        f"review_items={review_ids}; "
        f"auto_pulled={packet.get('auto_pulled')}; "
        f"{review_date_text} "
        f"website_sync={(packet.get('website_sync') or {}).get('status')}; "
        f"prep_packet={packet_path.relative_to(REPO_ROOT)}. "
        "No manifest promotion, commit, push, or public release was performed."
    )


def run_release_check() -> dict[str, Any]:
    result = subprocess.run(
        [PY, "release_snapshot.py", "--check"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    return {
        "returncode": result.returncode,
        "stdout_tail": result.stdout[-4000:],
        "stderr_tail": result.stderr[-4000:],
    }


def resolve_review_snapshot_date(explicit_snapshot_date: str) -> dict[str, Any]:
    """Choose the website review date from source-publication readiness.

    The website now uses publication-state snapshots: the route date is the
    latest completed source-publication date, while the analytic model endpoint
    may still be an earlier report/data date. Falling back to the wall-clock
    prep date creates fake daily snapshots and breaks the live standard.
    """
    if explicit_snapshot_date:
        return {
            "snapshot_date": explicit_snapshot_date,
            "basis": "explicit_override",
            "ready": None,
            "reason": "operator supplied --snapshot-date",
            "latest_source_date": explicit_snapshot_date,
        }

    if not release_snapshot.OUT_PATH.exists():
        return {
            "snapshot_date": "",
            "basis": "unresolved",
            "ready": False,
            "reason": f"missing pipeline output: {release_snapshot.OUT_PATH}",
            "latest_source_date": "",
        }

    summary = _load_json(release_snapshot.OUT_PATH)
    last_analytic_date = str(summary.get("as_of", ""))[:10]
    manifest = (
        _load_json(release_snapshot.MANIFEST_PATH)
        if release_snapshot.MANIFEST_PATH.exists()
        else {"entries": []}
    )
    verdict = release_snapshot.detect_snapshot_readiness(
        manifest,
        last_analytic_date,
        dt.datetime.now(dt.timezone.utc),
    )
    latest_source_date = str(verdict.get("latest_source_date") or "")
    if verdict.get("ready") and latest_source_date:
        return {
            "snapshot_date": latest_source_date,
            "basis": "latest_completed_source_publication_date",
            **verdict,
        }
    return {
        "snapshot_date": last_analytic_date,
        "basis": "analytic_as_of_no_new_completed_source_publication",
        **verdict,
    }


def sync_review_website(website_root: pathlib.Path, snapshot_date: str) -> dict[str, Any]:
    script = website_root / "lib" / "scripts" / "sync-bdbv-lovs.py"
    if not script.exists():
        return {"status": "skipped", "reason": f"missing {script}"}
    result = subprocess.run(
        [
            PY,
            str(script),
            "--website-root",
            str(website_root),
            "--lovs-root",
            str(REPO_ROOT),
            "--snapshot-date",
            snapshot_date,
        ],
        cwd=website_root.parents[1],
        text=True,
        capture_output=True,
        check=False,
    )
    return {
        "status": "ok" if result.returncode == 0 else "failed",
        "returncode": result.returncode,
        "stdout_tail": result.stdout[-3000:],
        "stderr_tail": result.stderr[-3000:],
    }


def run_website_gates(website_root: pathlib.Path) -> dict[str, Any]:
    """Run focused website gates for the local BDBV review surface."""
    checkout_root = website_root.parents[1]
    commands = [
        [
            "npm",
            "--workspace",
            "@arcede/site",
            "run",
            "test",
            "--",
            "--run",
            "tests/bdbv-date-semantics.test.ts",
            "tests/bdbv-evidence-copy.test.ts",
        ],
        ["npx", "tsc", "-p", "apps/site/tsconfig.json", "--noEmit"],
        ["npm", "--workspace", "@arcede/site", "run", "lint", "--", "--quiet"],
    ]
    results = []
    ok = True
    for command in commands:
        result = subprocess.run(
            command,
            cwd=checkout_root,
            text=True,
            capture_output=True,
            check=False,
        )
        ok = ok and result.returncode == 0
        results.append({
            "command": command,
            "returncode": result.returncode,
            "stdout_tail": result.stdout[-2000:],
            "stderr_tail": result.stderr[-2000:],
        })
    return {"status": "ok" if ok else "failed", "results": results}


def review_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for row in report.get("sources", []):
        if row.get("needs_review") or row.get("extracted_counts"):
            rows.append({
                "registry_id": row.get("registry_id"),
                "title": row.get("title"),
                "url": row.get("url"),
                "capture_backend": row.get("capture_backend"),
                "latest_detected_date": row.get("latest_detected_date"),
                "extracted_counts": row.get("extracted_counts") or {},
                "review_reasons": row.get("review_reasons") or [],
            })
    return rows


def auto_pull_candidates(rows: list[dict[str, Any]], as_of: str) -> list[dict[str, Any]]:
    pulled = []
    for row in rows:
        if row.get("registry_id") != "drc-moh-epidemie-dashboard":
            continue
        code = source_ingest.pull_source("drc-moh-epidemie-dashboard", as_of)
        pulled.append({
            "registry_id": "drc-moh-epidemie-dashboard",
            "status": "pulled_to_private_dropbox" if code == 0 else "pull_failed",
            "returncode": code,
            "note": (
                "Bytes and sidecars are staged only. A reviewer must confirm "
                "table/date semantics before manifest ingest."
            ),
        })
    return pulled


def write_prep_packet(packet: dict[str, Any], as_of: str, slot_id: str | None) -> pathlib.Path:
    PREP_DIR.mkdir(parents=True, exist_ok=True)
    suffix = f"-{slot_id}" if slot_id else "-full"
    path = PREP_DIR / f"bdbv-2026-{as_of}{suffix}-prep.json"
    path.write_text(json.dumps(packet, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def run_prep(args: argparse.Namespace) -> int:
    as_of = args.as_of or _today_utc()
    generated_at = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    earth = earth_awake() if args.earth_awake else {"status": "skipped"}

    live_code = source_ingest.live_check(as_of, slot_id=args.slot)
    freshness_path = _freshness_path(as_of, args.slot)
    report = _load_json(freshness_path) if freshness_path.exists() else {}
    rows = review_rows(report)
    pulled = auto_pull_candidates(rows, as_of) if args.auto_pull else []

    release_check = None
    review_snapshot_date = None
    website_sync = None
    website_gates = None
    if args.build_review_snapshot:
        release_check = run_release_check()
        if release_check["returncode"] == 0:
            review_snapshot_date = resolve_review_snapshot_date(args.snapshot_date)
            snapshot_date = review_snapshot_date.get("snapshot_date") or as_of
            website_sync = sync_review_website(args.website_root, snapshot_date)
            if args.website_gates and website_sync["status"] == "ok":
                website_gates = run_website_gates(args.website_root)
        else:
            website_sync = {"status": "skipped", "reason": "release_snapshot.py --check failed"}

    packet = {
        "schema_version": 1,
        "outbreak_id": "bdbv-uga-cod-2026",
        "as_of": as_of,
        "slot_id": args.slot,
        "generated_at": generated_at,
        "purpose": (
            "Autonomous unpublished daily snapshot prep. This packet can stage "
            "review evidence and refresh the local website preview, but it does "
            "not publish or promote unreviewed source claims into scored counts."
        ),
        "earth_awake": earth,
        "freshness_report": str(freshness_path.relative_to(REPO_ROOT)) if freshness_path.exists() else None,
        "freshness_summary": report.get("summary", {}),
        "review_queue": rows,
        "auto_pulled": pulled,
        "release_check": release_check,
        "review_snapshot_date": review_snapshot_date,
        "website_sync": website_sync,
        "website_gates": website_gates,
    }
    packet_path = write_prep_packet(packet, as_of, args.slot)
    if args.earth_agent_id:
        packet["earth_journal"] = earth_tool_call(
            "write_agent_journal",
            {
                "agent_id": args.earth_agent_id,
                "entry": summarize_for_journal(packet, packet_path),
                "source_harness": "daily_snapshot_prep.py",
                "entry_type": "daily_snapshot_prep",
                "source_channel": "cron" if args.slot else "manual",
            },
        )
        packet_path = write_prep_packet(packet, as_of, args.slot)
    if not args.skip_health_report:
        health = daily_prep_health.build_health_report(as_of, args.slot)
        health_path = daily_prep_health.write_health_report(health)
        packet["health_report"] = str(health_path.relative_to(REPO_ROOT))
        packet["health_traffic_light"] = health["traffic_light"]
        packet_path = write_prep_packet(packet, as_of, args.slot)
    print(f"prep_packet={packet_path}")
    print(f"review_items={len(rows)} auto_pulled={len(pulled)}")
    if website_sync:
        snapshot_date = (
            review_snapshot_date or {"snapshot_date": args.snapshot_date or as_of}
        ).get("snapshot_date")
        print(f"website_sync={website_sync.get('status')} snapshot_date={snapshot_date}")
    if website_gates:
        print(f"website_gates={website_gates.get('status')}")
    if packet.get("health_report"):
        print(f"health_report={packet['health_report']} traffic_light={packet['health_traffic_light']}")
    return 0 if (
        live_code == 0
        and (not release_check or release_check["returncode"] == 0)
        and (not website_sync or website_sync.get("status") == "ok")
        and (not website_gates or website_gates["status"] == "ok")
    ) else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", default="", help="Prep date in YYYY-MM-DD; defaults to current UTC date.")
    parser.add_argument("--slot", default=None, help="Optional source schedule slot to run.")
    parser.add_argument("--earth-awake", action="store_true", help="Initialize installed Earth MCP before prep.")
    parser.add_argument("--auto-pull", action="store_true", help="Stage supported review sources in the private dropbox.")
    parser.add_argument(
        "--build-review-snapshot",
        action="store_true",
        help="Run release_snapshot.py --check and sync an unpublished website preview snapshot.",
    )
    parser.add_argument(
        "--website-gates",
        action="store_true",
        help="After website sync, run focused BDBV website tests, typecheck, and lint.",
    )
    parser.add_argument(
        "--skip-health-report",
        action="store_true",
        help="Do not write the generated daily prep health report.",
    )
    parser.add_argument(
        "--snapshot-date",
        default="",
        help=(
            "Website review snapshot date override. By default, derive the date "
            "from release_snapshot.py source-publication readiness, not wall clock."
        ),
    )
    parser.add_argument(
        "--website-root",
        type=pathlib.Path,
        default=DEFAULT_WEBSITE_ROOT,
        help=f"Path to apps/site review worktree (default: {DEFAULT_WEBSITE_ROOT}).",
    )
    parser.add_argument(
        "--earth-agent-id",
        default=DEFAULT_EARTH_AGENT_ID,
        help=(
            "Earth agent journal to update after prep. Defaults to LOVS_EARTH_AGENT_ID; "
            "empty disables journaling."
        ),
    )
    return run_prep(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
