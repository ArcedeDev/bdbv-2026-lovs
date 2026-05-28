# SPDX-License-Identifier: Apache-2.0
"""Diagnostic dump: load INSP per-zone snapshot from an INRB-UMIE release and report.

This CLI is a diagnostic surface alongside `refresh_pipeline.py`. It does
not write the canonical snapshot, does not pin any calibration block, and
does not touch `data/live-bdbv-2026-output.json`. Its purpose is to produce
a per-zone band table for inspection (per-zone source_zones plus
PCR-modulated ascertainment band) at any data date the INRB-UMIE artifact
covers.

Usage:

  # Use a pre-downloaded tarball or extracted directory
  python3 -m tools.insp_per_zone_dump --source /path/to/build.tar.gz --as-of 2026-05-26

  # Or fetch by release tag from the INRB-UMIE GitHub releases (network)
  python3 -m tools.insp_per_zone_dump --release-tag build-2026-05-27-e40bc9e \\
      --as-of 2026-05-26 --verify-hash

Output: text report by default; pass `--json` for machine-readable.

The dump verifies content hash against the LOVS manifest entry when
`--verify-hash` is set; this avoids silent drift between the published
artifact and the pre-committed manifest.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import pathlib
import shutil
import sys
import urllib.request
from datetime import date
from typing import Any

# Allow running as `python3 tools/insp_per_zone_dump.py` from repo root
_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from lovs.insp_per_zone_loader import (  # noqa: E402
    INSPPerZoneSnapshot,
    load_per_zone_snapshot,
)
from lovs.pcr_capacity_prior_modulator import (  # noqa: E402
    SPECIES_HI,
    SPECIES_LO,
    coverage_stats,
    load_pcr_capacity_table,
    modulate_per_zone,
)
from lovs.process_status import _atomic_write_text  # noqa: E402
from lovs.source_ids import find_manifest_entry_by_source_id  # noqa: E402
from lovs.zone_alias_bridge import ZoneAliasBridge  # noqa: E402


DEFAULT_MANIFEST = (
    _REPO_ROOT / "data" / "bundibugyo-2026" / "manifest.json"
)


def _sha256_of_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _resolve_release_tag_to_source(
    release_tag: str,
    *,
    download_dir: pathlib.Path,
    verify_hash: bool,
    manifest_path: pathlib.Path,
) -> tuple[pathlib.Path, str | None]:
    """Download the release asset and return its path + expected source_id.

    Looks up the source_id in the manifest by release-tag suffix. If
    `verify_hash` is True, asserts the downloaded sha256 matches the
    manifest's `content_hash`.
    """
    expected_source_id = f"inrb-umie-ebola-drc-2026-{release_tag}"
    expected_hash = None
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        entry = find_manifest_entry_by_source_id(
            manifest.get("entries", []), expected_source_id
        )
        if entry is not None:
            expected_hash = entry.get("content_hash")
    url = (
        f"https://github.com/INRB-UMIE/Ebola_DRC_2026/releases/download/"
        f"{release_tag}/{release_tag}.tar.gz"
    )
    download_dir.mkdir(parents=True, exist_ok=True)
    out_path = download_dir / f"{release_tag}.tar.gz"
    if not out_path.exists():
        with urllib.request.urlopen(url) as response, out_path.open("wb") as fh:
            shutil.copyfileobj(response, fh)
    if verify_hash:
        if expected_hash is None:
            raise SystemExit(
                f"--verify-hash requested but manifest has no entry for "
                f"source_id={expected_source_id!r} at {manifest_path}"
            )
        actual_hash = _sha256_of_file(out_path)
        if actual_hash != expected_hash:
            raise SystemExit(
                f"sha256 mismatch for {release_tag}: "
                f"got {actual_hash}, manifest declares {expected_hash}"
            )
    return out_path, expected_source_id


def _format_text_report(
    snapshot: INSPPerZoneSnapshot,
    modulated: dict[str, tuple[float, float] | None],
) -> str:
    lines: list[str] = []
    lines.append(
        f"INRB-UMIE INSP per-zone diagnostic report, as_of={snapshot.as_of.isoformat()}"
    )
    lines.append(f"source_id: {snapshot.source_id}")
    lines.append(f"method_basis: {snapshot.method_basis}")
    lines.append("")
    lines.append("National (INRB-UMIE INSP national rollup):")
    lines.append(
        f"  confirmed={snapshot.national.confirmed}  "
        f"suspected={snapshot.national.suspected}  "
        f"confirmed_deaths={snapshot.national.confirmed_deaths}  "
        f"suspected_deaths={snapshot.national.suspected_deaths}"
    )
    lines.append("")
    lines.append("Unallocated residual (national minus full INRB zone-sum):")
    for metric in ("confirmed", "suspected", "confirmed_deaths", "suspected_deaths"):
        lines.append(f"  {metric:<18} {snapshot.unallocated_residual[metric]:>6}")
    lines.append("")
    lines.append("Coverage audit (LOVS source zones vs INSP):")
    audit = snapshot.coverage_audit
    lines.append(
        f"  present_with_data ({len(audit.present_with_data)}): "
        f"{', '.join(audit.present_with_data) or '(none)'}"
    )
    lines.append(
        f"  present_but_zero  ({len(audit.present_but_zero)}): "
        f"{', '.join(audit.present_but_zero) or '(none)'}"
    )
    lines.append(
        f"  structurally_absent ({len(audit.structurally_absent)}): "
        f"{', '.join(audit.structurally_absent) or '(none)'}"
    )
    lines.append("")
    lines.append("Per-zone snapshot (LOVS canonical zone ids):")
    lines.append(
        f"  {'zone':<14} {'conf':>5} {'sus':>6} {'cdth':>5} {'sdth':>5}  "
        f"{'PCR band (lo, hi)':<22}  {'fallback?'}"
    )
    for lovs_id in sorted(snapshot.by_lovs_zone):
        zm = snapshot.by_lovs_zone[lovs_id]
        band = modulated.get(lovs_id)
        if band is None:
            band_str = f"({SPECIES_LO:.2f}, {SPECIES_HI:.2f})"
            fallback = "species default"
        else:
            lo, hi = band
            band_str = f"({lo:.2f}, {hi:.2f})"
            fallback = "modulated"
        lines.append(
            f"  {lovs_id:<14} {zm.confirmed:>5} {zm.suspected:>6} "
            f"{zm.confirmed_deaths:>5} {zm.suspected_deaths:>5}  "
            f"{band_str:<22}  {fallback}"
        )
    lines.append("")
    stats = coverage_stats(modulated)
    lines.append(
        f"PCR modulator coverage: {stats['modulated_zones']} of "
        f"{stats['total_zones']} zones modulated, "
        f"{stats['species_default_fallback_zones']} fell back to species default."
    )
    return "\n".join(lines) + "\n"


def _format_json_report(
    snapshot: INSPPerZoneSnapshot,
    modulated: dict[str, tuple[float, float] | None],
) -> str:
    payload: dict[str, Any] = {
        "schema": "poc-insp-runner/v1",
        "as_of": snapshot.as_of.isoformat(),
        "source_id": snapshot.source_id,
        "method_basis": snapshot.method_basis,
        "national": dataclasses.asdict(snapshot.national),
        "unallocated_residual": dict(snapshot.unallocated_residual),
        "coverage_audit": {
            "present_with_data": list(snapshot.coverage_audit.present_with_data),
            "present_but_zero": list(snapshot.coverage_audit.present_but_zero),
            "structurally_absent": list(snapshot.coverage_audit.structurally_absent),
        },
        "by_lovs_zone": {
            lovs_id: {
                "confirmed": zm.confirmed,
                "suspected": zm.suspected,
                "confirmed_deaths": zm.confirmed_deaths,
                "suspected_deaths": zm.suspected_deaths,
                "inrb_collapsed_from": list(zm.inrb_collapsed_from),
                "pcr_band": (
                    {"lo": modulated[lovs_id][0], "hi": modulated[lovs_id][1]}
                    if modulated.get(lovs_id) is not None
                    else None
                ),
                "pcr_fallback_to_species_default": modulated.get(lovs_id) is None,
            }
            for lovs_id, zm in snapshot.by_lovs_zone.items()
        },
        "pcr_modulator_coverage": coverage_stats(modulated),
        "species_default_band": {"lo": SPECIES_LO, "hi": SPECIES_HI},
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Diagnostic dump: load an INRB-UMIE INSP per-zone snapshot, "
            "modulate the under-ascertainment band with Africa CDC PCR "
            "capacity, and print a per-zone report. This driver does not "
            "promote any snapshot primary, does not write to the calibration "
            "ledger, and does not touch the live BDBV output."
        )
    )
    src_group = parser.add_mutually_exclusive_group(required=True)
    src_group.add_argument(
        "--source",
        type=pathlib.Path,
        help="Path to a pre-downloaded release tarball or extracted directory.",
    )
    src_group.add_argument(
        "--release-tag",
        type=str,
        help=(
            "INRB-UMIE GitHub release tag (e.g. build-2026-05-27-e40bc9e). "
            "Downloads to --download-dir."
        ),
    )
    parser.add_argument(
        "--as-of",
        type=str,
        required=True,
        help="Target data date in ISO format YYYY-MM-DD.",
    )
    parser.add_argument(
        "--download-dir",
        type=pathlib.Path,
        default=pathlib.Path("/tmp/inrb-umie-poc"),
        help="Where to cache downloaded tarballs.",
    )
    parser.add_argument(
        "--verify-hash",
        action="store_true",
        help="Verify downloaded tarball sha256 against the LOVS manifest entry.",
    )
    parser.add_argument(
        "--manifest",
        type=pathlib.Path,
        default=DEFAULT_MANIFEST,
        help="Path to the LOVS manifest (default: data/bundibugyo-2026/manifest.json).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON instead of text.",
    )
    parser.add_argument(
        "--write",
        type=pathlib.Path,
        default=None,
        help="If set, also write the report (text or JSON) to this path atomically.",
    )
    args = parser.parse_args(argv)

    target_date = date.fromisoformat(args.as_of)

    if args.source is not None:
        source_path = args.source
        explicit_source_id: str | None = None
    else:
        source_path, explicit_source_id = _resolve_release_tag_to_source(
            args.release_tag,
            download_dir=args.download_dir,
            verify_hash=args.verify_hash,
            manifest_path=args.manifest,
        )

    bridge = ZoneAliasBridge.load_default()
    snapshot = load_per_zone_snapshot(
        source_path, target_date, bridge=bridge, source_id=explicit_source_id
    )
    pcr_table = load_pcr_capacity_table(source_path)
    modulated = modulate_per_zone(snapshot, pcr_table, bridge=bridge)

    output = (
        _format_json_report(snapshot, modulated)
        if args.json
        else _format_text_report(snapshot, modulated)
    )
    sys.stdout.write(output)

    if args.write is not None:
        _atomic_write_text(args.write, output)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
