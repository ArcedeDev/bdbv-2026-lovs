#!/usr/bin/env python3
"""Propose fail-closed SitRep promotion JSON from staged source sidecars."""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

from lovs import sitrep_promotions

REPO_ROOT = pathlib.Path(__file__).resolve().parent
DROPBOX = REPO_ROOT / "data" / "bundibugyo-2026" / "private" / "sources"


def _load_json(path: pathlib.Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _latest_wordpress_sidecar() -> pathlib.Path:
    sidecars = sorted(DROPBOX.glob("insp-wordpress-sitrep-*.json.meta.json"))
    if not sidecars:
        raise sitrep_promotions.SitRepPromotionError("no INSP WordPress SitRep sidecars found")
    return sidecars[-1]


def write_candidate(sidecar_path: pathlib.Path) -> pathlib.Path:
    payload = sitrep_promotions.candidate_payload_from_sidecar(_load_json(sidecar_path))
    sitrep_no = int(payload["sitrep_number"])
    data_date = str(payload["data_as_of"] or "undated")
    out_dir = sitrep_promotions.CANDIDATES_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"sitrep-{sitrep_no:03d}-{data_date}.candidate.json"
    sitrep_promotions.validate_promotion(payload, path=out_path)
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return out_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--latest-from-dropbox", action="store_true")
    group.add_argument("--sidecar", type=pathlib.Path)
    args = parser.parse_args(argv)
    try:
        sidecar = _latest_wordpress_sidecar() if args.latest_from_dropbox else args.sidecar
        out = write_candidate(sidecar)
    except (OSError, json.JSONDecodeError, sitrep_promotions.SitRepPromotionError) as exc:
        sys.stderr.write(f"[FAIL] SitRep promotion candidate: {exc}\n")
        return 1
    print(f"Wrote fail-closed candidate promotion: {out}")
    print("Next: fill figures + evidence_chain_id after PDF table review, then set ready_for_model_use=true.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
