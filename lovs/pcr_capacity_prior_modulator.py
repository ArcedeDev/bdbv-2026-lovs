# SPDX-License-Identifier: Apache-2.0
"""Per-zone under-ascertainment prior modulated by PCR testing capacity.

The LOVS BDBV species default `under_ascertainment_uniform=(0.3, 0.9)`
applies a single uniform band on the reported-cases-to-true-cases ratio
across every zone. With Africa CDC decentralisation-plan PCR capacity per
zone (machines and tests budgeted) we can do better for zones that have
adequate testing capacity relative to their suspected case load: a zone
with 5000 PCR tests budgeted for 279 suspected cases (Bunia at 26-May)
plausibly detects a larger fraction of its true cases than the
species-default band's lower bound implies.

v1 BEHAVIOUR (2026-06-02): the consumed surface is the per-zone
diagnostic-access RING, which keys only on band presence (`lo is not None`),
never on the band's magnitude. `modulate_per_zone` therefore emits the
unboosted species-default band for every zone with a documented PCR-capacity
figure, INDEPENDENT of the suspected count, and `None` only where no capacity
figure exists. The suspected-driven saturation machinery below
(`_saturation_ratio` / `_saturation_score` / `_band_for_saturation`) is
RESERVED, not deleted: the INRB suspected series is non-monotonic and re-based
(national cumulative suspected fell 1077 -> 906 -> 349 over two days) and so
cannot soundly serve as a saturation denominator. It stays unit-tested and
ready to re-wire once a trustworthy per-zone testing-demand series (e.g. a
per-zone operational active-suspect pool) exists.

The modulator is intentionally asymmetric:

- It only shifts the band UPWARD (toward higher ascertainment). PCR
  capacity is a positive signal; below-saturation does not imply
  WORSE-than-species ascertainment (that would require evidence of
  under-detection beyond the species prior, which we do not have).
- It never returns a `hi` greater than the species default `hi`. We do
  not claim better-than-species certainty.
- It returns `None` for zones without PCR data so the caller falls back
  to the species default cleanly.

Saturation ratio: `pcr_tests_budgeted / max(cumulative_suspected, 1)`.
Score: `max(0, sigmoid(log(saturation)) - 0.5) * 2`, mapping saturation to
a [0, 1) value where 1 represents arbitrarily high testing adequacy.
Lower bound: `lo = species_lo + score * (max_lo_boost)` capped at
`species_lo + max_lo_boost = 0.6`. Upper bound: always `species_hi = 0.9`.

Stdlib only. No clock, no network. Functions are pure (deterministic on
input).
"""
from __future__ import annotations

import csv
import io
import math
import pathlib
from dataclasses import dataclass
from typing import Mapping

from lovs.insp_per_zone_loader import (
    _PER_ZONE_DIR,
    INSPPerZoneSnapshot,
    _open_source,
)
from lovs.lovs_priors_bundibugyo import BUNDIBUGYO_PRIORS_STAGE_TWO
from lovs.zone_alias_bridge import ZoneAliasBridge


# Anchor the modulator to the species default at module import. This means
# the modulator stays consistent with whatever Stage Two declares; if the
# species default changes upstream, the modulator follows.
SPECIES_LO, SPECIES_HI = BUNDIBUGYO_PRIORS_STAGE_TWO.under_ascertainment_uniform

# Max upward shift on the lower bound. Half the species span keeps the
# modulator conservative: even at infinite saturation, `lo` only rises to
# 0.6 (= 0.3 + 0.3), leaving the band span (0.6 -> 0.9) at 0.3, never
# collapsing the band to a point estimate.
MAX_LO_BOOST = 0.3

PCR_MACHINES_FILE = "testing_capacity__pcr_machines.csv"
PCR_TESTS_FILE = "testing_capacity__pcr_tests.csv"


class PCRModulatorError(ValueError):
    """Base class for PCR modulator errors."""


@dataclass(frozen=True)
class PCRCapacityTable:
    """Decentralisation-plan PCR capacity per INRB canonical Nom."""

    pcr_machines: Mapping[str, int]
    pcr_tests: Mapping[str, int]

    def has(self, inrb_nom: str) -> bool:
        return inrb_nom in self.pcr_tests or inrb_nom in self.pcr_machines

    def tests_for(self, inrb_nom: str) -> int | None:
        return self.pcr_tests.get(inrb_nom)


def load_pcr_capacity_table(tarball_or_dir: pathlib.Path) -> PCRCapacityTable:
    """Read the Africa CDC PCR capacity tables from an INRB-UMIE artifact.

    Reads parsed CSV values only. Does NOT commit or persist the raw
    Africa CDC workbook bytes; the upstream artifact's published license
    metadata says redistribution terms must be confirmed with the data
    owner.
    """
    source = _open_source(tarball_or_dir)
    machines: dict[str, int] = {}
    tests: dict[str, int] = {}
    for stem, target, value_col in (
        (PCR_MACHINES_FILE, machines, "pcr_machines"),
        (PCR_TESTS_FILE, tests, "pcr_tests"),
    ):
        rel = _PER_ZONE_DIR / stem
        if not source.has(rel):
            raise PCRModulatorError(f"artifact missing {rel!s}")
        text = source.read_text(rel)
        raw_rows = [r for r in csv.reader(io.StringIO(text)) if len(r) >= 2]
        if not raw_rows:
            raise PCRModulatorError(f"{rel!s}: no parseable rows")
        # Tolerate two upstream layouts, both positional (column 0 = INRB Nom,
        # column 1 = count): the older headered form whose first row is
        # `nom,<value_col>` (builds through 2026-05-28) and the newer
        # headerless form whose first row is already data, e.g. `Bunia,10`
        # (builds from 2026-05-30 onward). A leading UTF-8 BOM on column 0 is
        # stripped, mirroring insp_per_zone_loader._read_long_csv.
        c0 = raw_rows[0][0].strip().lstrip("﻿").lower()
        c1 = raw_rows[0][1].strip().lower()
        headered = c0 == "nom" and c1 == value_col
        data_rows = raw_rows[1:] if headered else raw_rows
        offset = 2 if headered else 1
        for i, r in enumerate(data_rows, start=offset):
            nom = r[0].strip().lstrip("﻿")
            if not nom:
                continue
            raw = r[1].strip()
            try:
                target[nom] = int(float(raw))
            except ValueError as exc:
                raise PCRModulatorError(
                    f"{rel!s} row {i} column {value_col!r}: non-integer value {raw!r}"
                ) from exc
    return PCRCapacityTable(pcr_machines=machines, pcr_tests=tests)


def _saturation_ratio(pcr_tests_budgeted: int, cumulative_suspected: int) -> float:
    """Saturation = budgeted tests / max(suspected, 1).

    Capped suspected at 1 below so a zone with 0 suspected does not produce
    an infinite ratio. NOTE: the caller in `modulate_per_zone` short-circuits
    zero-suspected zones to None (species default fallback) BEFORE computing
    saturation, because a zone with zero detected suspected cases provides
    no evidence of ascertainment quality regardless of how much capacity
    sits idle. The clamp here only matters for the unit-tested limit
    behaviour of the function as a pure mapping.
    """
    return float(pcr_tests_budgeted) / max(float(cumulative_suspected), 1.0)


def _saturation_score(saturation: float) -> float:
    """Map saturation to a [0, 1) score via shifted sigmoid of log.

    At saturation = 1 (one test per suspected, the WHO ideal floor) the
    score is 0 (no upward shift). At saturation = e (~2.72) the score is
    ~0.46. At saturation = 10 the score is ~0.82. Approaches 1 as
    saturation grows unboundedly. Saturation below 1 (saturated capacity)
    produces score 0: we do not LOWER the species default lower bound
    from a saturation signal.
    """
    if saturation <= 0:
        return 0.0
    log_sat = math.log(saturation)
    sigmoid = 1.0 / (1.0 + math.exp(-log_sat))
    return max(0.0, (sigmoid - 0.5) * 2.0)


def _band_for_saturation(saturation: float) -> tuple[float, float]:
    """Translate saturation to a per-zone `(lo, hi)` ascertainment band."""
    score = _saturation_score(saturation)
    lo = SPECIES_LO + score * MAX_LO_BOOST
    hi = SPECIES_HI
    # Numerical guard: never let lo drift above (hi - eps); modulator design
    # keeps lo <= SPECIES_LO + MAX_LO_BOOST = 0.6 which is well below
    # SPECIES_HI = 0.9, but float arithmetic gets a belt-and-suspenders check.
    if lo >= hi:  # pragma: no cover - protected by max_lo_boost design
        lo = hi - 1e-6
    return (lo, hi)


def modulate_per_zone(
    snapshot: INSPPerZoneSnapshot,
    pcr_table: PCRCapacityTable,
    *,
    bridge: ZoneAliasBridge | None = None,
) -> dict[str, tuple[float, float] | None]:
    """Return per-LOVS-zone `(lo, hi)` ascertainment bands or `None`.

    v1 capacity-presence semantics (see module docstring):
      - If the bridge has no INRB mapping for the zone, returns None.
      - If the PCR table does not cover the INRB zone, returns None.
      - Otherwise returns the unboosted species-default band
        `(SPECIES_LO, SPECIES_HI)`, INDEPENDENT of the suspected count, so the
        per-zone diagnostic-access ring reflects documented PCR capacity, not
        case load.

    The returned dict has exactly the same keys as `snapshot.by_lovs_zone`.
    """
    if bridge is None:
        bridge = ZoneAliasBridge.load_default()
    out: dict[str, tuple[float, float] | None] = {}
    for lovs_id, zm in snapshot.by_lovs_zone.items():
        inrb_nom = bridge.inrb_for(lovs_id)
        if inrb_nom is None:
            out[lovs_id] = None
            continue
        tests_budgeted = pcr_table.tests_for(inrb_nom)
        if tests_budgeted is None:
            out[lovs_id] = None
            continue
        # v1 capacity-presence: a documented PCR-capacity figure yields a
        # non-null species-default band (a firm diagnostic-access ring),
        # independent of the suspected count. The per-zone suspected series is
        # non-monotonic, re-based, and revision-capped to 0 on this snapshot,
        # so it cannot soundly modulate ascertainment: it could only withhold
        # an upward boost, never fabricate one. We emit the unboosted species
        # default and make no better-than-species claim. The saturation
        # machinery is reserved for a future trustworthy per-zone demand
        # series (see module docstring).
        out[lovs_id] = (SPECIES_LO, SPECIES_HI)
    return out


def apply_with_species_default_fallback(
    modulated: Mapping[str, tuple[float, float] | None],
) -> dict[str, tuple[float, float]]:
    """Helper: replace any `None` with the species default band.

    Most consumers want a concrete band per zone; this helper folds the
    "no PCR data" cases back to the species default in a single pass.
    """
    return {
        zone: band if band is not None else (SPECIES_LO, SPECIES_HI)
        for zone, band in modulated.items()
    }


def coverage_stats(
    modulated: Mapping[str, tuple[float, float] | None],
) -> dict[str, int]:
    """Counts of zones that received a band vs fell back to species default."""
    modulated_count = sum(1 for b in modulated.values() if b is not None)
    fallback_count = sum(1 for b in modulated.values() if b is None)
    return {
        "modulated_zones": modulated_count,
        "species_default_fallback_zones": fallback_count,
        "total_zones": len(modulated),
    }
