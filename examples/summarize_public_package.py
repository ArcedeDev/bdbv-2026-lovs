#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Summarize the public BDBV package without running private LOVS logic."""
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


def count_rows(rows: list[dict[str, str]], field: str, value: str) -> int:
    return sum(1 for row in rows if row.get(field) == value)


def main() -> int:
    snapshot = read_json("data/public_snapshot.json")
    calibration = read_json("data/public_calibration_status.json")
    blindspots = read_json("data/public_blindspots.json")
    nowcast = read_json("data/public_nowcast_status.json")
    zone_rows = read_csv("data/public_zone_counts_2026-05-26.csv")
    latency_rows = read_csv("data/public_latency_observatory.csv")
    source_rows = read_csv("data/public_source_index.csv")

    reported = snapshot["reported_counts"]
    confirmed = reported["confirmed"]
    suspected = reported["suspected"]
    deaths = reported["deaths"]
    measured_latency = count_rows(latency_rows, "latency_status", "measured")

    print("BDBV Public Package Summary")
    print("===========================")
    print(f"outbreak_id: {snapshot['outbreak_id']}")
    print(f"snapshot_as_of: {snapshot['as_of']}")
    print(f"data_as_of: {snapshot['data_as_of']}")
    print("")
    print("Headline public counts")
    print(f"- confirmed cases: {confirmed['primary']} ({confirmed['min']} to {confirmed['max']})")
    print(f"- suspected/reported cases: {suspected['primary']} ({suspected['min']} to {suspected['max']})")
    print(f"- deaths: {deaths['primary']} ({deaths['min']} to {deaths['max']})")
    print("")
    print("Reusable public artifacts")
    print(f"- source index rows: {len(source_rows)}")
    print(f"- health-zone rows: {len(zone_rows)}")
    print(f"- measured latency rows: {measured_latency} of {len(latency_rows)}")
    print(f"- blindspots tracked: {len(blindspots['blindspots'])}")
    print("")
    print("Calibration accountability")
    print(f"- ledger rows: {calibration['ledger_rows']}")
    print(f"- open commitments: {calibration['open_commitments']}")
    print(f"- resolved commitments: {calibration['resolved_commitments']}")
    print(f"- next resolution date: {calibration['next_resolution_date']}")
    print("")
    print("Nowcast interface")
    print(f"- status: {nowcast['status']}")
    print("- candidate quantities: " + ", ".join(nowcast["candidate_quantities"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
