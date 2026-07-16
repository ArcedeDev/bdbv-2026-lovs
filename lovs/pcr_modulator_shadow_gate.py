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
import os
import pathlib
import re

from lovs import snapshot_contract


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_SNAPSHOT_PATH = REPO_ROOT / "data" / "live-bdbv-2026-output.json"


def _allow_missing() -> bool:
    """True when a missing bands surface is deliberately acknowledged."""
    return bool(
        re.fullmatch(
            r"(1|true|yes)",
            os.environ.get("BDBV_ALLOW_MISSING_PCR_BANDS", "").strip(),
            re.IGNORECASE,
        )
    )


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
        # PRESENCE check (added 2026-07-16). The shadow firewall below only asks
        # "if the surface is present, is it still shadow?", so a MISSING surface
        # trivially satisfied it and this gate returned green. That is how the
        # diagnostic-access surface vanished for 34 cycles without one gate
        # objecting: the upstream INRB-UMIE bundle stopped reconciling on
        # 2026-06-11, the bands were dropped on the fallback path, and the website
        # carry-forward kept a ring on the map so nothing looked wrong. A gate that
        # goes green because its subject disappeared is worse than no gate.
        #
        # The bands are now built from the vendored static capacity table over the
        # reviewed SitRep's own zone set (refresh_pipeline._sitrep_pcr_bands), so
        # they no longer depend on the epi bundle and absence is a real defect.
        # BDBV_ALLOW_MISSING_PCR_BANDS=1 is the documented, deliberate escape hatch
        # (e.g. a cycle with no capacity table at all); it must be an explicit,
        # visible act, never a silent default.
        if _allow_missing():
            return problems
        return [
            "per_zone_under_ascertainment_bands is absent from the snapshot. The "
            "diagnostic-access surface is expected every cycle: it is built from the "
            "vendored Africa CDC capacity table over the reviewed SitRep zone set and "
            "does not depend on the INRB-UMIE epi bundle. A silent drop here renders a "
            "carried-forward ring on the live map with no fresh basis. Set "
            "BDBV_ALLOW_MISSING_PCR_BANDS=1 to acknowledge deliberately."
        ]
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
