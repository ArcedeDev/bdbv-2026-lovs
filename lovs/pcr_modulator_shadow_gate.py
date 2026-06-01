# SPDX-License-Identifier: Apache-2.0
"""PCR-modulator shadow-surface release gate (spec §7.2, Rec J).

This gate is the R3 belt-and-suspenders backstop: it refuses any snapshot
whose `per_zone_under_ascertainment_bands.surface_role` is anything other
than `shadow_in_v1`. Plan C parallel scoring is the only mechanism that may
graduate the surface to `primary`. Until then, this gate refuses regardless
of how the contract was derived.

This is intentionally redundant with `snapshot_contract._validate_per_zone_bands`
so that an attempted bypass at the contract layer is still caught by the
release script.

Stdlib-only.
"""
from __future__ import annotations

import json
import pathlib

from lovs import snapshot_contract


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_SNAPSHOT_PATH = REPO_ROOT / "data" / "live-bdbv-2026-output.json"


def check_pcr_modulator_shadow(
    snapshot_path: pathlib.Path = DEFAULT_SNAPSHOT_PATH,
) -> list[str]:
    if not snapshot_path.is_file():
        return [f"snapshot file missing at {snapshot_path}"]
    try:
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"{snapshot_path}: invalid JSON: {exc}"]

    problems: list[str] = []
    bands = snapshot.get("per_zone_under_ascertainment_bands")
    if bands is None:
        return problems
    if not isinstance(bands, dict):
        return ["per_zone_under_ascertainment_bands must be an object"]
    surface_role = bands.get("surface_role")
    expected = snapshot_contract.ALLOWED_PER_ZONE_BANDS_SURFACE_ROLE_THIS_CYCLE
    if surface_role != expected:
        problems.append(
            f"per_zone_under_ascertainment_bands.surface_role={surface_role!r}; "
            f"only {expected!r} is permitted until Plan C parallel-scoring lands"
        )
    method_basis = bands.get("method_basis")
    if method_basis != snapshot_contract.PCR_MODULATED_BANDS_METHOD_BASIS:
        problems.append(
            "per_zone_under_ascertainment_bands.method_basis must be "
            f"{snapshot_contract.PCR_MODULATED_BANDS_METHOD_BASIS!r}; got "
            f"{method_basis!r}"
        )
    return problems


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--snapshot",
        type=pathlib.Path,
        default=DEFAULT_SNAPSHOT_PATH,
    )
    args = parser.parse_args(argv)
    problems = check_pcr_modulator_shadow(args.snapshot)
    for line in problems:
        sys.stderr.write(f"[FAIL] pcr_modulator_shadow: {line}\n")
    if problems:
        return 1
    print("pcr_modulator_shadow_gate ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
