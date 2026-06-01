# SPDX-License-Identifier: Apache-2.0
"""Retrospective attribution audit (spec section 9.2, founder decision).

The forward-only calibration ledger discipline (spec section 6.1) prevents
in-place rewrites of pinned calibration blocks: once a block is pinned, the
methodology basis it was pinned against stays frozen for resolution scoring.
The retrospective attribution audit is a SEPARATE deliverable surface that
discloses what the per-zone attribution WOULD look like under newly available
data (INRB-UMIE INSP per-zone) without restating any pinned block.

Audit shape per block:
- block_id + pinned_at + corridor list (source, target pairs)
- For each (source, target) pair, the corridor risk pinned in the block
- Per-source-zone INSP confirmed_deaths at the data-as-of of the block (where
  the INSP artifact contains data at that date)
- Attribution status: `pinned_corroborated` (INSP agrees the source has
  attributable deaths), `pinned_no_insp_deaths` (INSP shows source has no
  confirmed deaths), `insp_not_available_at_pin_date`

The audit is presentational: it does NOT mutate the ledger, does NOT change
the pinned methodology basis, and does NOT touch the resolution scoring
contract. The gate (retrospective_attribution_audit_gate) enforces this
separation.

Stdlib only. Pure functions of inputs.
"""
from __future__ import annotations

import json
import pathlib
from typing import Any


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_LEDGER_PATH = REPO_ROOT / "data" / "calibration-ledger.json"
DEFAULT_SNAPSHOT_PATH = REPO_ROOT / "data" / "live-bdbv-2026-output.json"


def audit_block(
    block: dict[str, Any],
    insp_per_zone_block: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Audit a pinned calibration block against current INSP per-zone data.

    Returns a list of dicts (one per (source, target) corridor in the
    pinned block) carrying:

    - block_id: from the pinned block
    - pinned_at: ISO date the block was pinned
    - source, target: corridor endpoints
    - risk_adj_50_at_pin: the pinned 50% interval [lo, hi]
    - insp_confirmed_deaths_at_data_as_of: per-LOVS-zone INSP deaths at the
      block's `pinned_at` date when the INSP block carries that data
    - attribution_status: pinned_corroborated | pinned_no_insp_deaths |
      insp_not_available_at_pin_date
    - audit_note: short prose for the deliverable
    """
    rows: list[dict[str, Any]] = []
    block_id = block.get("block_id") or block.get("pinned_at")
    pinned_at = block.get("pinned_at")
    points = block.get("points") or block.get("mode_b_hypotheses") or []
    insp_as_of = (
        (insp_per_zone_block or {}).get("as_of_data_date")
        if insp_per_zone_block
        else None
    )
    insp_by_zone = (insp_per_zone_block or {}).get("by_lovs_zone") or {}
    for point in points:
        source = point.get("source") or ""
        target = point.get("target") or ""
        risk_adj = point.get("risk_adj_50") or []
        if insp_per_zone_block is None or insp_as_of != pinned_at:
            status = "insp_not_available_at_pin_date"
            insp_deaths = None
            audit_note = (
                "INSP per-zone block was not available at the block's pin "
                "date; retrospective audit lower-bounds the source-zone "
                "deaths from the CDC-attributed table that was current at "
                "pin time"
            )
        else:
            source_row = insp_by_zone.get(source) or {}
            insp_deaths = int(source_row.get("confirmed_deaths", 0))
            if insp_deaths > 0:
                status = "pinned_corroborated"
                audit_note = (
                    f"INSP per-zone now shows {insp_deaths} confirmed deaths "
                    f"attributed to source zone {source!r} at the block's pin "
                    "date; corridor risk that the block pinned remains the "
                    "binding methodology basis for resolution scoring"
                )
            else:
                status = "pinned_no_insp_deaths"
                audit_note = (
                    f"INSP per-zone shows 0 confirmed deaths attributed to "
                    f"source zone {source!r} at the block's pin date; the "
                    "block remains pinned at its as-pinned methodology basis "
                    "(forward-only ledger), and this audit row discloses the "
                    "attribution gap"
                )
        rows.append(
            {
                "block_id": block_id,
                "pinned_at": pinned_at,
                "source": source,
                "target": target,
                "risk_adj_50_at_pin": list(risk_adj),
                "insp_confirmed_deaths_at_data_as_of": insp_deaths,
                "insp_data_as_of": insp_as_of,
                "attribution_status": status,
                "audit_note": audit_note,
            }
        )
    return rows


def audit_ledger(
    ledger: dict[str, Any],
    insp_per_zone_block: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Audit every block in the ledger against the INSP per-zone block."""
    rows: list[dict[str, Any]] = []
    for block in ledger.get("blocks", []):
        rows.extend(audit_block(block, insp_per_zone_block))
    return rows


def load_default_ledger(path: pathlib.Path = DEFAULT_LEDGER_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_default_insp_block(
    path: pathlib.Path = DEFAULT_SNAPSHOT_PATH,
) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    snap = json.loads(path.read_text(encoding="utf-8"))
    return snap.get("insp_per_zone_block")
