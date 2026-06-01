# SPDX-License-Identifier: Apache-2.0
"""Tests for lovs.pcr_capacity_prior_modulator.

Property tests assert:
  - Monotonicity in saturation (larger tests at fixed suspected -> larger lo)
  - Bounded within species default
  - Idempotence over repeated apply
  - Graceful fallback for zones without PCR data
"""
from __future__ import annotations

import pathlib
import random
from datetime import date

import pytest

from lovs.insp_per_zone_loader import (
    CoverageAudit,
    INSPPerZoneSnapshot,
    NationalMetrics,
    ZoneMetrics,
    load_per_zone_snapshot,
)
from lovs.lovs_priors_bundibugyo import BUNDIBUGYO_PRIORS_STAGE_TWO
from lovs.pcr_capacity_prior_modulator import (
    MAX_LO_BOOST,
    PCRCapacityTable,
    PCRModulatorError,
    SPECIES_HI,
    SPECIES_LO,
    _band_for_saturation,
    _saturation_ratio,
    _saturation_score,
    apply_with_species_default_fallback,
    coverage_stats,
    load_pcr_capacity_table,
    modulate_per_zone,
)
from lovs.zone_alias_bridge import ZoneAliasBridge


LOCAL_E40BC9E_TARBALL = pathlib.Path("/tmp/inrb-e40bc9e/build.tar.gz")


def _zm(suspected: int, **kwargs: int) -> ZoneMetrics:
    return ZoneMetrics(
        confirmed=kwargs.get("confirmed", 0),
        suspected=suspected,
        confirmed_deaths=kwargs.get("confirmed_deaths", 0),
        suspected_deaths=kwargs.get("suspected_deaths", 0),
    )


def _snapshot_with(by_lovs: dict[str, ZoneMetrics]) -> INSPPerZoneSnapshot:
    return INSPPerZoneSnapshot(
        as_of=date(2026, 5, 26),
        source_id="test",
        by_lovs_zone=by_lovs,
        national=NationalMetrics(confirmed=0, suspected=0, confirmed_deaths=0, suspected_deaths=0),
        unallocated_residual={"confirmed": 0, "suspected": 0, "confirmed_deaths": 0, "suspected_deaths": 0},
        coverage_audit=CoverageAudit(
            present_with_data=tuple(by_lovs),
            present_but_zero=(),
            structurally_absent=(),
        ),
    )


class TestSpeciesDefaultAnchor:
    def test_species_lo_matches_bdbv_priors(self) -> None:
        assert SPECIES_LO == BUNDIBUGYO_PRIORS_STAGE_TWO.under_ascertainment_uniform[0]

    def test_species_hi_matches_bdbv_priors(self) -> None:
        assert SPECIES_HI == BUNDIBUGYO_PRIORS_STAGE_TWO.under_ascertainment_uniform[1]

    def test_max_lo_boost_is_half_the_species_span(self) -> None:
        species_span = SPECIES_HI - SPECIES_LO
        assert MAX_LO_BOOST == pytest.approx(species_span / 2)


class TestSaturationRatio:
    def test_one_to_one(self) -> None:
        assert _saturation_ratio(100, 100) == 1.0

    def test_high_capacity_low_suspected(self) -> None:
        assert _saturation_ratio(5000, 279) == pytest.approx(17.92, rel=1e-3)

    def test_zero_suspected_falls_back_to_one(self) -> None:
        # max(0, 1) = 1 in the denominator
        assert _saturation_ratio(1000, 0) == 1000.0


class TestSaturationScore:
    def test_saturation_one_yields_zero_score(self) -> None:
        assert _saturation_score(1.0) == 0.0

    def test_saturation_below_one_yields_zero_score(self) -> None:
        assert _saturation_score(0.5) == 0.0
        assert _saturation_score(0.1) == 0.0
        assert _saturation_score(1e-10) == 0.0

    def test_saturation_above_one_yields_positive_score(self) -> None:
        assert _saturation_score(2.0) > 0.0
        assert _saturation_score(10.0) > _saturation_score(2.0)

    def test_score_is_bounded_by_one(self) -> None:
        # Even at saturation = 1e9 the score stays < 1
        assert _saturation_score(1e9) < 1.0

    def test_score_zero_saturation_is_zero(self) -> None:
        assert _saturation_score(0.0) == 0.0

    def test_score_negative_saturation_is_zero(self) -> None:
        assert _saturation_score(-1.0) == 0.0


class TestBandForSaturation:
    def test_saturation_one_returns_species_default(self) -> None:
        lo, hi = _band_for_saturation(1.0)
        assert lo == SPECIES_LO
        assert hi == SPECIES_HI

    def test_band_lo_is_bounded_above_by_lo_plus_boost(self) -> None:
        for sat in (2.0, 10.0, 100.0, 1e9):
            lo, hi = _band_for_saturation(sat)
            assert lo <= SPECIES_LO + MAX_LO_BOOST + 1e-9

    def test_band_lo_is_strictly_greater_than_species_lo_when_sat_gt_one(self) -> None:
        lo, _ = _band_for_saturation(2.0)
        assert lo > SPECIES_LO

    def test_band_hi_is_always_species_hi(self) -> None:
        for sat in (0.01, 1.0, 2.0, 100.0):
            _, hi = _band_for_saturation(sat)
            assert hi == SPECIES_HI


class TestMonotonicity:
    """Larger PCR tests at fixed suspected => larger (or equal) lo."""

    def test_monotone_at_fixed_suspected(self) -> None:
        rng = random.Random(20260528)
        suspected = 100
        prev_lo = -1.0
        for tests_budgeted in sorted({rng.randint(0, 100_000) for _ in range(200)}):
            saturation = _saturation_ratio(tests_budgeted, suspected)
            lo, _ = _band_for_saturation(saturation)
            assert lo >= prev_lo, (
                f"monotonicity broken at tests_budgeted={tests_budgeted}: "
                f"lo={lo} < prev_lo={prev_lo}"
            )
            prev_lo = lo


class TestBoundedness:
    """Every returned band satisfies SPECIES_LO <= lo < hi <= SPECIES_HI."""

    def test_bounded_for_200_random_inputs(self) -> None:
        rng = random.Random(202605282)
        for _ in range(200):
            tests_budgeted = rng.randint(0, 100_000)
            suspected = rng.randint(0, 5_000)
            saturation = _saturation_ratio(tests_budgeted, suspected)
            lo, hi = _band_for_saturation(saturation)
            assert SPECIES_LO <= lo < hi <= SPECIES_HI, (
                f"bounded property failed at tests={tests_budgeted}, "
                f"suspected={suspected}: (lo, hi)=({lo}, {hi})"
            )


class TestModulatePerZone:
    @pytest.fixture
    def bridge(self) -> ZoneAliasBridge:
        return ZoneAliasBridge.load_default()

    @pytest.fixture
    def pcr_table_partial(self) -> PCRCapacityTable:
        # Cover only 2 of N zones to exercise the fallback path
        return PCRCapacityTable(
            pcr_machines={"Bunia": 10, "Goma": 2},
            pcr_tests={"Bunia": 5000, "Goma": 2000},
        )

    def test_zones_without_pcr_data_return_none(
        self, bridge: ZoneAliasBridge, pcr_table_partial: PCRCapacityTable
    ) -> None:
        snap = _snapshot_with({
            "bunia": _zm(279),
            "butembo": _zm(10),  # has PCR data + nonzero suspected -> modulated
            "kilo": _zm(18),     # no PCR data -> fallback
            "rwampara": _zm(240),  # no PCR data -> fallback
        })
        # butembo is NOT in pcr_table_partial; add a row so we exercise the
        # has-PCR-data + nonzero-suspected branch.
        pcr_table = PCRCapacityTable(
            pcr_machines={**pcr_table_partial.pcr_machines, "Butembo": 2},
            pcr_tests={**pcr_table_partial.pcr_tests, "Butembo": 2000},
        )
        out = modulate_per_zone(snap, pcr_table, bridge=bridge)
        assert out["bunia"] is not None
        assert out["butembo"] is not None
        assert out["kilo"] is None
        assert out["rwampara"] is None

    def test_bunia_band_lifts_above_species_default(
        self, bridge: ZoneAliasBridge, pcr_table_partial: PCRCapacityTable
    ) -> None:
        snap = _snapshot_with({"bunia": _zm(279)})
        out = modulate_per_zone(snap, pcr_table_partial, bridge=bridge)
        lo, hi = out["bunia"]  # type: ignore[misc]
        # Bunia saturation = 5000/279 ~ 17.9 -> well above 1 -> lo > species_lo
        assert lo > SPECIES_LO
        assert hi == SPECIES_HI

    def test_modulator_is_idempotent(
        self, bridge: ZoneAliasBridge, pcr_table_partial: PCRCapacityTable
    ) -> None:
        snap = _snapshot_with({"bunia": _zm(279), "kilo": _zm(18)})
        first = modulate_per_zone(snap, pcr_table_partial, bridge=bridge)
        second = modulate_per_zone(snap, pcr_table_partial, bridge=bridge)
        assert first == second

    def test_coverage_stats(
        self, bridge: ZoneAliasBridge, pcr_table_partial: PCRCapacityTable
    ) -> None:
        snap = _snapshot_with({
            "bunia": _zm(279),
            "kilo": _zm(18),
            "rwampara": _zm(240),
        })
        out = modulate_per_zone(snap, pcr_table_partial, bridge=bridge)
        stats = coverage_stats(out)
        assert stats == {
            "modulated_zones": 1,  # only Bunia
            "species_default_fallback_zones": 2,
            "total_zones": 3,
        }

    def test_zero_suspected_falls_back_to_species_default(
        self, bridge: ZoneAliasBridge, pcr_table_partial: PCRCapacityTable
    ) -> None:
        """Reviewer Important: a quiescent zone with idle capacity provides
        no evidence of high ascertainment; modulator must fall back, not
        return the maximal upward boost."""
        snap = _snapshot_with({"goma-cod": _zm(suspected=0, confirmed=1)})
        out = modulate_per_zone(snap, pcr_table_partial, bridge=bridge)
        assert out["goma-cod"] is None, (
            "Zone with 0 suspected and non-trivial PCR capacity should fall "
            "back to species default, not receive the max ascertainment boost"
        )

    def test_apply_with_species_default_fallback(
        self, bridge: ZoneAliasBridge, pcr_table_partial: PCRCapacityTable
    ) -> None:
        snap = _snapshot_with({"bunia": _zm(279), "kilo": _zm(18)})
        modulated = modulate_per_zone(snap, pcr_table_partial, bridge=bridge)
        applied = apply_with_species_default_fallback(modulated)
        assert applied["kilo"] == (SPECIES_LO, SPECIES_HI)
        # Bunia keeps its modulated band
        assert applied["bunia"][0] > SPECIES_LO


@pytest.mark.skipif(
    not LOCAL_E40BC9E_TARBALL.exists(),
    reason="canonical e40bc9e tarball not locally cached",
)
class TestAgainstRealE40BC9ETarball:
    def test_load_pcr_capacity_table(self) -> None:
        table = load_pcr_capacity_table(LOCAL_E40BC9E_TARBALL)
        assert table.pcr_machines.get("Bunia") == 10
        assert table.pcr_tests.get("Bunia") == 5000
        assert table.pcr_machines.get("Goma") == 2
        assert table.pcr_tests.get("Goma") == 2000

    def test_e2e_modulator_on_real_snapshot(self) -> None:
        snap = load_per_zone_snapshot(LOCAL_E40BC9E_TARBALL, date(2026, 5, 26))
        table = load_pcr_capacity_table(LOCAL_E40BC9E_TARBALL)
        modulated = modulate_per_zone(snap, table)
        # Plan A 2026-05-28: bridge expanded from 11 to 18 zones. Of the 18,
        # 6 receive modulated bands (bunia, butembo, mongbwalu, nyankunde from
        # the pre-Plan-A set, plus aru and rimba from the new 7); 12 fall
        # back to species default (goma-cod 0-suspected; bambu/damas/karisimbi-cod/
        # katwa/kilo/komanda/mambasa/miti-murhesa/nizi/oicha/rwampara either
        # 0-suspected or not in the PCR capacity table).
        stats = coverage_stats(modulated)
        assert stats["modulated_zones"] == 6
        assert stats["species_default_fallback_zones"] == 12
        # Bunia has the highest non-zero-suspected saturation (5000 / 279 ~ 17.9)
        bunia_lo, bunia_hi = modulated["bunia"]  # type: ignore[misc]
        assert bunia_lo > SPECIES_LO
        assert bunia_hi == SPECIES_HI
        # Goma falls back: 0 suspected means no surveillance load to interpret
        assert modulated["goma-cod"] is None


class TestErrors:
    def test_missing_pcr_file_raises(self, tmp_path: pathlib.Path) -> None:
        d = tmp_path / "f"
        (d / "build" / "long").mkdir(parents=True)
        # Only create one of the two files
        (d / "build" / "long" / "testing_capacity__pcr_machines.csv").write_text(
            "nom,pcr_machines\nBunia,10\n"
        )
        with pytest.raises(PCRModulatorError, match="missing"):
            load_pcr_capacity_table(d)

    def test_malformed_pcr_value_raises(self, tmp_path: pathlib.Path) -> None:
        d = tmp_path / "f"
        (d / "build" / "long").mkdir(parents=True)
        (d / "build" / "long" / "testing_capacity__pcr_machines.csv").write_text(
            "nom,pcr_machines\nBunia,not-a-number\n"
        )
        (d / "build" / "long" / "testing_capacity__pcr_tests.csv").write_text(
            "nom,pcr_tests\nBunia,5000\n"
        )
        with pytest.raises(PCRModulatorError, match="non-integer"):
            load_pcr_capacity_table(d)
