"""LOVS PoE-weighted corridor helper.

Reads an optional point-of-entry traveler-count JSON payload. The public
repository intentionally does not ship the restricted Imperial-derived PoE
table; pass a local, permission-cleared file path to use this helper.

Each corridor in the LOVS calibration set is mapped to one or more named
PoEs based on geography. The PoE-weighted approach replaces the
qualitative "Mahagi/Goli is busy" treatment with observed traveler
counts.

Stdlib only.
"""
from __future__ import annotations

import json
import os
from typing import Any


MODEL_VERSION = "lovs_poe_corridor-v0.1.0"


_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DEFAULT_POE_COUNTS_PATH = os.path.join(
    _REPO_ROOT, "data", "bundibugyo-2026", "poe_traveler_counts.restricted.json"
)


# Corridor-to-PoE mapping based on geography of DRC-Uganda border crossings.
# Documented for review:
#   - "kasese" (Kasese district, southwest Uganda) receives travelers from
#     Nord Kivu via Mpondwe (the busy Mpondwe-Kasindi post) and Busunga.
#   - "bundibugyo-uga" (Bundibugyo town, west Uganda) receives travelers
#     from Ituri / Nord Kivu via Ntoroko Main and Busanza.
#   - "kampala" (Uganda capital) receives onward travelers from ALL PoEs.
#   - "beni-cod" (Beni HZ, DRC Nord Kivu) is the reverse direction of the
#     Kasese corridor: same physical crossings (Mpondwe, Busunga) measured
#     from the DRC side.
#   - "arua-uga" (Arua / West Nile, NW Uganda) receives travelers from Ituri
#     (Mahagi / Aru) via the Goli, Vurra and Odramacaku crossings. Added
#     2026-05-21 to close the documented Mahagi/Goli<->Arua corridor, which is
#     the largest Ituri-side outflow in the WHO PoE screening data.
#
# The mapping is exposed as a module constant so future refinements can
# update it transparently.
CORRIDOR_TO_POE_NAMES: dict[str, tuple[str, ...]] = {
    "arua-uga": ("Goli", "Vurra", "Odramacaku"),
    "kasese": ("Mpondwe", "Busunga"),
    "bundibugyo-uga": ("Ntoroko Main", "Busanza"),
    "kampala": (
        "Goli",
        "Ntoroko Main",
        "Odramacaku",
        "Vurra",
        "Busanza",
        "Busunga",
        "Mpondwe",
    ),
    "beni-cod": ("Mpondwe", "Busunga"),
}


def load_poe_counts(path: str | None = None) -> dict[str, Any]:
    """Load the PoE traveler counts JSON.

    Args:
        path: permission-cleared PoE-count JSON path. If omitted, the helper
            looks for a local restricted file that is intentionally not shipped
            in the public repo.

    Returns:
        Parsed PoE-count payload.
    """
    if path is None:
        path = DEFAULT_POE_COUNTS_PATH
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"PoE count file not available at {path!r}. The public repository "
            "does not redistribute restricted third-party PoE table data; pass "
            "a local permission-cleared path."
        )
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def poe_entries_for_corridor(
    corridor_id: str, path: str | None = None
) -> list[dict[str, Any]]:
    """Return the PoE entries relevant to the named corridor."""
    if corridor_id not in CORRIDOR_TO_POE_NAMES:
        raise KeyError(
            f"Unknown corridor_id {corridor_id!r}; known: "
            f"{sorted(CORRIDOR_TO_POE_NAMES)}"
        )
    data = load_poe_counts(path)
    poe_names = set(CORRIDOR_TO_POE_NAMES[corridor_id])
    return [c for c in data.get("counts", []) if c.get("poe") in poe_names]


def corridor_daily_passengers(corridor_id: str, path: str | None = None) -> int:
    """Sum of mean_daily_passengers across the PoEs mapped to this corridor."""
    entries = poe_entries_for_corridor(corridor_id, path)
    return int(sum(e.get("mean_daily_passengers", 0) for e in entries))


def corridor_weight(corridor_id: str, path: str | None = None) -> float:
    """Normalized corridor weight in [0, 1].

    Defined as: corridor's daily passengers / total Ituri+NordKivu daily passengers.
    """
    data = load_poe_counts(path)
    total = data.get("totals", {}).get("ituri_plus_nord_kivu_total_daily_passengers")
    if not total or total <= 0:
        raise ValueError(
            f"PoE count totals missing or invalid: {data.get('totals')}"
        )
    return corridor_daily_passengers(corridor_id, path) / float(total)
