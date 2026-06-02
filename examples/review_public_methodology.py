#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Review the public BDBV methodology surface using current public artifacts."""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]


def read_json(relpath: str) -> Any:
    return json.loads((REPO_ROOT / relpath).read_text(encoding="utf-8"))


def read_csv(relpath: str) -> list[dict[str, str]]:
    with (REPO_ROOT / relpath).open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def int_cell(value: str) -> int:
    return int(value) if value else 0


def main() -> int:
    snapshot = read_json("data/public_snapshot.json")
    blindspots = read_json("data/public_blindspots.json")
    calibration = read_json("data/public_calibration_status.json")
    nowcast = read_json("data/public_nowcast_status.json")
    zones = read_csv("data/public_zone_counts_2026-05-29.csv")
    source_rows = read_csv("data/public_source_index.csv")
    latency_rows = read_csv("data/public_latency_observatory.csv")

    reported = snapshot["reported_counts"]
    confirmed = reported["confirmed"]
    zone_confirmed = sum(int_cell(row["confirmed"]) for row in zones)
    attribution_gap = int(confirmed["primary"]) - zone_confirmed
    measured_latency = sum(1 for row in latency_rows if row["latency_status"] == "measured")
    missing_latency = len(latency_rows) - measured_latency

    print("BDBV Public Methodology Review")
    print("==============================")
    print(f"snapshot_as_of: {snapshot['as_of']}")
    print(f"data_as_of: {snapshot['data_as_of']}")
    print("")
    print("1. Source reconciliation")
    print(f"- confirmed primary: {confirmed['primary']}")
    print(f"- confirmed public range: {confirmed['min']} to {confirmed['max']}")
    print(f"- confirmed conflict anchors: {len(confirmed['conflicting_source_ids'])}")
    print("")
    print("2. Source clocks")
    print(f"- source index rows: {len(source_rows)}")
    print(f"- measured latency rows: {measured_latency}")
    print(f"- rows missing data_as_of for latency: {missing_latency}")
    print("")
    print("3. Health-zone attribution lag")
    print(f"- health-zone rows: {len(zones)}")
    print(f"- source-attributed confirmed total: {zone_confirmed}")
    print(f"- headline confirmed total: {confirmed['primary']}")
    print(f"- documented attribution gap: {attribution_gap}")
    print("")
    print("4. Blindspots")
    for row in blindspots["blindspots"]:
        print(f"- {row['blindspot_id']}: {row['status']} ({row['affected_count']})")
    print("")
    print("5. Calibration accountability")
    print(f"- open commitments: {calibration['open_commitments']}")
    print(f"- resolved commitments: {calibration['resolved_commitments']}")
    print(f"- next resolution date: {calibration['next_resolution_date']}")
    print("")
    print("6. Nowcast boundary")
    print(f"- status: {nowcast['status']}")
    print("- excluded fields: " + ", ".join(nowcast["excluded_fields"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
