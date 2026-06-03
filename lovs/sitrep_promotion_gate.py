"""Gate for reviewed SitRep promotion payloads."""
from __future__ import annotations

import argparse
import pathlib
import sys

from lovs import sitrep_promotions


def validate(
    directory: pathlib.Path = sitrep_promotions.PROMOTIONS_DIR,
    *,
    require_through: str = "",
) -> dict[str, object]:
    rows = sitrep_promotions.load_reviewed_promotions(directory)
    latest = max(str(row["data_as_of"]) for row in rows)
    if require_through and latest < require_through:
        raise sitrep_promotions.SitRepPromotionError(
            f"latest reviewed promotion {latest} is older than required {require_through}"
        )
    return {
        "reviewed_count": len(rows),
        "latest_data_as_of": latest,
        "source_ids": [row["source_id"] for row in rows],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", type=pathlib.Path, default=sitrep_promotions.PROMOTIONS_DIR)
    parser.add_argument(
        "--require-through",
        default="",
        help="Fail if no reviewed promotion exists through this YYYY-MM-DD data date.",
    )
    args = parser.parse_args(argv)
    try:
        result = validate(args.dir, require_through=args.require_through)
    except sitrep_promotions.SitRepPromotionError as exc:
        sys.stderr.write(f"[FAIL] SitRep promotion gate: {exc}\n")
        return 1
    print(
        "SitRep promotion gate OK "
        f"({result['reviewed_count']} reviewed; latest {result['latest_data_as_of']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
