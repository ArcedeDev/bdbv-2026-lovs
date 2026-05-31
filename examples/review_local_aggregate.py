#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Review a local aggregate input file using the public BDBV method cards.

This is a presentation-only, read-only walkthrough. It applies the public
method-card discipline (source reconciliation, source clocks, health-zone
attribution lag, and blindspots) to an aggregate input file that follows
``schemas/local_aggregate_input.schema.json``.

It performs transparent arithmetic and echoing only. It does not weight,
score, threshold, forecast, or compute probability intervals, and it never
imports the private implementation. A partner can point it at their own
approved aggregate file:

    python3 examples/review_local_aggregate.py [path/to/your_aggregate.json]

With no argument it reviews ``examples/local_aggregate_input.example.json``.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = REPO_ROOT / "examples/local_aggregate_input.example.json"

REQUIRED_TOP_LEVEL = (
    "snapshot",
    "reported_counts",
    "health_zone_counts",
    "blindspots",
)


def load_input(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise SystemExit(f"input {path} is not valid JSON: {error}")
    if not isinstance(data, dict):
        raise SystemExit(f"input {path} must be a JSON object")
    missing = [key for key in REQUIRED_TOP_LEVEL if key not in data]
    if missing:
        raise SystemExit(
            f"input {path} is missing required keys: {', '.join(missing)}. "
            "See schemas/local_aggregate_input.schema.json."
        )
    return data


def as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def headline_confirmed(reported: dict[str, Any]) -> int | None:
    for key in ("confirmed_cases", "confirmed"):
        body = reported.get(key)
        if isinstance(body, dict) and body.get("value") is not None:
            return as_int(body.get("value"))
    return None


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args:
        path = Path(args[0]).expanduser()
        if not path.is_absolute():
            path = (Path.cwd() / path).resolve()
    else:
        path = DEFAULT_INPUT
    if not path.exists():
        raise SystemExit(f"input file not found: {path}")

    data = load_input(path)
    snapshot = data.get("snapshot", {})
    reported = data.get("reported_counts", {})
    zones = data.get("health_zone_counts", [])
    blindspots = data.get("blindspots", [])

    print("BDBV Local Aggregate Review")
    print("===========================")
    print("Presentation-only review of an aggregate input file. No scoring,")
    print("weighting, thresholds, intervals, or forecasts are computed.")
    print("")
    print(f"input_file: {path.name}")
    print(f"as_of: {snapshot.get('as_of')}")
    print(f"data_as_of: {snapshot.get('data_as_of')}")
    countries = snapshot.get("countries", [])
    if countries:
        print(f"countries: {', '.join(str(item) for item in countries)}")
    print("")

    print("1. Source reconciliation (headline counts as source-attributed claims)")
    if not reported:
        print("- no reported_counts present")
    for metric, body in reported.items():
        if not isinstance(body, dict):
            continue
        value = body.get("value")
        primary = body.get("primary_source_id", "(no source id)")
        conflict_range = body.get("conflict_range") or {}
        range_min = conflict_range.get("min")
        range_max = conflict_range.get("max")
        if range_min is not None and range_max is not None:
            print(f"- {metric}: {value} (public range {range_min} to {range_max}); primary source {primary}")
        else:
            print(f"- {metric}: {value}; primary source {primary}")
    print("")

    print("2. Health-zone attribution lag")
    zone_confirmed = sum(as_int(zone.get("confirmed")) for zone in zones)
    print(f"- health-zone rows: {len(zones)}")
    print(f"- source-attributed confirmed total: {zone_confirmed}")
    confirmed = headline_confirmed(reported)
    if confirmed is not None:
        gap = confirmed - zone_confirmed
        print(f"- headline confirmed total: {confirmed}")
        print(f"- documented attribution gap: {gap}")
        if gap > 0:
            print("  (the headline total is timelier than zone attribution; the gap")
            print("   is reported as lag, not spread across zones)")
    else:
        print("- headline confirmed total: not present in reported_counts")
    print("")

    print("3. Source clocks (zone-row data-date coverage)")
    rows_with_date = sum(1 for zone in zones if zone.get("source_data_date"))
    print(f"- zone rows with source_data_date: {rows_with_date}")
    print(f"- zone rows missing source_data_date: {len(zones) - rows_with_date}")
    print("  (rows without a data date stay visible instead of being dropped)")
    print("")

    print("4. Blindspots (tracked evidence states)")
    if not blindspots:
        print("- none recorded")
    for blindspot in blindspots:
        if not isinstance(blindspot, dict):
            continue
        blindspot_id = blindspot.get("blindspot_id", "(unnamed)")
        status = blindspot.get("status", "")
        effect = blindspot.get("public_effect", "")
        print(f"- {blindspot_id} [{status}]: {effect}")
    print("")

    print("Boundary: this review echoes and sums public aggregate inputs only. It")
    print("does not publish or compute model parameters, feature weights,")
    print("thresholds, probability intervals, or forecasts.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
