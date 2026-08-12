#!/usr/bin/env python3.14
"""Probe whether the publisher still serves the SitRep PDFs this brief cites.

A citation is a promise the reader can check. When INSP withdraws a PDF, that
promise silently breaks: the brief keeps rendering a live-looking hyperlink that
returns 404, and a reader who clicks it has no way to tell whether the document
ever existed. The bytes are still pinned by sha256 in the promotion receipt, so
what is lost is not the evidence but the reader's ability to verify it against
the publisher. That distinction is what this file records.

Deliberately NOT part of the deterministic build. Network state changes between
runs, and the release proves byte-determinism across two consecutive pipeline
runs; folding a live probe into that would make the proof meaningless. Run this
on the cadence, commit the result, and let the deterministic pipeline read the
committed file.

    python3.14 tools/probe_source_availability.py
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys
import urllib.error

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import source_ingest  # noqa: E402  (needs REPO_ROOT on the path first)
from lovs.sitrep_promotions import load_reviewed_promotions  # noqa: E402

OUTPUT_PATH = REPO_ROOT / "data" / "source_availability.json"
SCHEMA_VERSION = "source-availability/v1"


def _now() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def probe(url: str, fetch_fn=source_ingest._fetch_url) -> dict[str, object]:
    """Resolve one cited URL to a typed availability verdict."""
    try:
        raw, status, _content_type = fetch_fn(url)
    except urllib.error.HTTPError as exc:
        return {"state": "withdrawn", "http_status": int(exc.code)}
    except (OSError, TimeoutError, urllib.error.URLError) as exc:
        # A transport failure is not evidence of withdrawal. Say so, rather than
        # letting a flaky network quietly mark a live document as gone.
        return {"state": "unverified", "error": f"{type(exc).__name__}: {exc}"}
    return {"state": "available", "http_status": int(status), "byte_length": len(raw)}


def build(promotions: list[dict], fetch_fn=source_ingest._fetch_url) -> dict:
    checked_at = _now()
    entries: dict[str, dict[str, object]] = {}
    for promotion in promotions:
        url = str(promotion.get("source_url") or "")
        receipt = promotion.get("source_receipt") or {}
        if not url:
            continue
        verdict = probe(url, fetch_fn=fetch_fn)
        entries[str(promotion["source_id"])] = {
            "url": url,
            "checked_at": checked_at,
            # The retained receipt travels with the verdict: when the publisher
            # copy is gone these two fields are the whole basis on which a reader
            # can still audit the figures.
            "retained_sha256": str(receipt.get("sha256") or ""),
            "retained_byte_length": receipt.get("byte_length"),
            **verdict,
        }
    return {
        "schema_version": SCHEMA_VERSION,
        "checked_at": checked_at,
        "sources": dict(sorted(entries.items())),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="probe and report without writing data/source_availability.json",
    )
    args = parser.parse_args()

    payload = build(load_reviewed_promotions())
    by_state: dict[str, list[str]] = {}
    for source_id, row in payload["sources"].items():
        by_state.setdefault(str(row["state"]), []).append(source_id)

    for state in ("withdrawn", "unverified", "available"):
        ids = by_state.get(state, [])
        if not ids:
            continue
        print(f"{state}: {len(ids)}")
        if state != "available":
            for source_id in ids:
                row = payload["sources"][source_id]
                print(f"    {source_id}  {row.get('http_status') or row.get('error')}")

    if args.check:
        return 1 if by_state.get("withdrawn") else 0

    OUTPUT_PATH.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"wrote {OUTPUT_PATH.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
