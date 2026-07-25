# SPDX-License-Identifier: Apache-2.0
"""Regenerate examples/local_aggregate_input.example.json from the built public artifacts.

The example ships in the public release bundle as a worked "here is what an aggregate
input looks like" file, and it is expected to mirror the CURRENT public snapshot. It was
previously hand-edited each cadence cycle, which is 47 zone rows plus headline counts of
transcription risk per cycle for a file that is fully derivable. Everything written here
comes from data/public_snapshot.json and data/public_zone_counts_2026-05-29.csv; the
narrative fields (privacy_notice, schema_note, blindspots) are carried forward from the
existing file because they are editorial, not derived.

Run after release_snapshot.py has rebuilt the public artifacts.
"""
from __future__ import annotations

import csv
import json
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
EXAMPLE = REPO_ROOT / "examples" / "local_aggregate_input.example.json"
SNAPSHOT = REPO_ROOT / "data" / "public_snapshot.json"
ZONE_COUNTS = REPO_ROOT / "data" / "public_zone_counts_2026-05-29.csv"

# Fields that are editorial prose rather than derived from the snapshot, and so are
# preserved across refreshes instead of being regenerated.
CARRIED_FORWARD = (
    "blindspots",
    "derived_from_public_artifacts",
    "example_role",
    "privacy_notice",
    "schema_note",
    "schema_version",
)


def _conflict_range(block: dict) -> dict:
    return {"max": int(block["max"]), "min": int(block["min"])}


def _operational_status(snapshot_ops: dict, previous_ops: dict) -> dict:
    """Restate the snapshot's operational axes in the example's aggregate-input shape.

    The snapshot publishes each axis as {primary, min, max, primary_source_id, ...};
    the aggregate-input schema uses {value, conflict_range, primary_source_id, ...}.
    Scalar header fields (basis, note, summable_into_confirmed) pass through.
    """
    out: dict = {}
    for key, row in snapshot_ops.items():
        if not isinstance(row, dict) or "primary" not in row:
            out[key] = row
            continue
        previous_row = previous_ops.get(key, {})
        out[key] = {
            "conflict_range": {"max": int(row["max"]), "min": int(row["min"])},
            "primary_source_id": row["primary_source_id"],
            "status": previous_row.get("status", "published"),
            "unit": previous_row.get("unit", "people"),
            "value": int(row["primary"]),
        }
    return out


def build() -> dict:
    snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    previous = json.loads(EXAMPLE.read_text(encoding="utf-8"))

    with ZONE_COUNTS.open(encoding="utf-8") as handle:
        zone_rows = list(csv.DictReader(handle))

    confirmed = snapshot["reported_counts"]["confirmed"]
    deaths = snapshot["reported_deaths"]["confirmed"]

    payload = {key: previous[key] for key in CARRIED_FORWARD}
    payload.update(
        {
            "as_of": snapshot["as_of"],
            "data_as_of": snapshot["data_as_of"],
            "outbreak_id": snapshot["outbreak_id"],
            "operational_status": _operational_status(
                snapshot["operational_status"], previous["operational_status"]
            ),
            "reported_counts": {
                "confirmed_cases": {
                    "conflict_range": _conflict_range(confirmed),
                    "primary_source_id": confirmed["primary_source_id"],
                    "status": "public_snapshot_primary",
                    "value": int(confirmed["primary"]),
                },
                "confirmed_deaths": {
                    "conflict_range": _conflict_range(deaths),
                    "primary_source_id": deaths["primary_source_id"],
                    "status": "public_snapshot_primary",
                    "value": int(deaths["primary"]),
                },
            },
            "snapshot": {
                "as_of": snapshot["as_of"],
                "countries": previous["snapshot"]["countries"],
                "data_as_of": snapshot["data_as_of"],
                "pathogen": previous["snapshot"]["pathogen"],
            },
            "health_zone_counts": [
                {
                    "confirmed": int(row["confirmed"]),
                    "confirmed_deaths": int(row["confirmed_deaths"]),
                    "source_data_date": row["source_data_date"],
                    "source_id": row["source_id"],
                    "source_row_status": row["source_row_status"],
                    "zone_id": row["zone_id"],
                }
                for row in zone_rows
            ],
            "zone_counts": [
                {
                    "confirmed": int(row["confirmed"]),
                    "source_data_date": row["source_data_date"],
                    "source_id": row["source_id"],
                    "zone_id": row["zone_id"],
                }
                for row in zone_rows
            ],
        }
    )
    return payload


def main() -> int:
    payload = build()
    EXAMPLE.write_text(
        json.dumps(payload, indent=1, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(
        f"refreshed {EXAMPLE.relative_to(REPO_ROOT)}: "
        f"data_as_of={payload['data_as_of']}, "
        f"confirmed={payload['reported_counts']['confirmed_cases']['value']}, "
        f"{len(payload['health_zone_counts'])} zone rows"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
