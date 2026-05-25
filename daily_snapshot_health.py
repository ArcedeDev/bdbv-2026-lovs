#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Build a daily readiness health report for BDBV snapshot prep."""
from __future__ import annotations

import argparse
import json
import sys

from lovs import daily_prep_health


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", required=True, help="Prep date in YYYY-MM-DD.")
    parser.add_argument("--slot", default=None, help="Optional source schedule slot.")
    parser.add_argument(
        "--live-public-check",
        action="store_true",
        help="Fetch arcede.com dataset artifacts and compare hashes to local deliverables.",
    )
    parser.add_argument(
        "--live-base-url",
        default=daily_prep_health.DEFAULT_LIVE_BASE_URL,
        help=f"Public BDBV base URL (default: {daily_prep_health.DEFAULT_LIVE_BASE_URL}).",
    )
    parser.add_argument(
        "--max-age-hours",
        type=float,
        default=30.0,
        help="Maximum age for freshness/prep artifacts before red status.",
    )
    parser.add_argument(
        "--write-report",
        action="store_true",
        help="Write data/external_sources/health/bdbv-2026-<date>[-slot]-health.json.",
    )
    parser.add_argument(
        "--fail-on",
        choices=("red", "yellow", "never"),
        default="red",
        help="Exit nonzero on this traffic-light threshold (default: red).",
    )
    args = parser.parse_args(argv)

    report = daily_prep_health.build_health_report(
        args.as_of,
        args.slot,
        check_live_public=args.live_public_check,
        max_age_hours=args.max_age_hours,
        live_base_url=args.live_base_url,
    )
    if args.write_report:
        path = daily_prep_health.write_health_report(report)
        report["health_report"] = str(path.relative_to(daily_prep_health.REPO_ROOT))

    print(json.dumps(report, indent=2, sort_keys=True))
    if args.fail_on == "never":
        return 0
    if args.fail_on == "yellow" and report["traffic_light"] in {"yellow", "red"}:
        return 1
    if args.fail_on == "red" and report["traffic_light"] == "red":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
