# SPDX-License-Identifier: Apache-2.0
"""Tests for the threshold-based source-zone promotion criterion (spec §8.1 v1.2).

Pins the named constants so unannounced changes fail loudly. Verifies the
condition matrix (post 2026-06-02 suspected-retirement: condition 2a is now
confirmed-based, since laboratory-confirmed cases are the only cumulative case
metric and confirmed cases by zone are the descriptive spread signal):

|                                  | confirmed>=1 | deaths>=1 | in_BORDER | result |
|----------------------------------|-------------|-----------|-----------|--------|
| present_with_data + confirmed>=1 | yes         | -         | -         | True   |
| present_with_data + deaths>=1    | -           | yes       | -         | True   |
| present_with_data + border zone  | -           | -         | yes       | True   |
| present_with_data, no qualifier  | -           | -         | -         | False  |
| present_but_zero, any qualifier  | -           | -         | -         | False  |
| structurally_absent, any         | -           | -         | -         | False  |
"""
from __future__ import annotations

import unittest

from lovs.insp_per_zone_loader import (
    BORDER_INTL_TARGET_ZONES,
    THRESHOLD_CONFIRMED_DEATHS,
    THRESHOLD_CONFIRMED_LOW,
    ZoneMetrics,
    is_source_zone_promotion_eligible,
)


class TestThresholdConstants(unittest.TestCase):
    """Pinned-constant regression test: refuse unannounced changes."""

    def test_threshold_confirmed_low_is_1(self):
        self.assertEqual(1, THRESHOLD_CONFIRMED_LOW)

    def test_threshold_confirmed_deaths_is_1(self):
        self.assertEqual(1, THRESHOLD_CONFIRMED_DEATHS)

    def test_border_intl_target_zones_set(self):
        # Plan A v2 set (Phase 2 grounded list).
        self.assertEqual(
            frozenset({"mahagi-cod", "aru", "rimba"}),
            BORDER_INTL_TARGET_ZONES,
        )


def _metrics(
    confirmed: int = 0,
    confirmed_deaths: int = 0,
) -> ZoneMetrics:
    return ZoneMetrics(
        confirmed=confirmed,
        confirmed_deaths=confirmed_deaths,
    )


class TestConfirmedThreshold(unittest.TestCase):
    def test_confirmed_above_threshold_passes(self):
        self.assertTrue(
            is_source_zone_promotion_eligible(
                _metrics(confirmed=5), "present_with_data"
            )
        )

    def test_confirmed_at_threshold_passes(self):
        # >=: 1 (the floor) PASSES. A zone carrying a single confirmed case is a
        # descriptive transmission source the watchlist tracks.
        self.assertTrue(
            is_source_zone_promotion_eligible(
                _metrics(confirmed=1), "present_with_data"
            )
        )

    def test_confirmed_below_threshold_fails(self):
        self.assertFalse(
            is_source_zone_promotion_eligible(
                _metrics(confirmed=0), "present_with_data"
            )
        )


class TestConfirmedDeathsThreshold(unittest.TestCase):
    def test_one_confirmed_death_passes(self):
        # The Komanda case (real instance at 2026-05-26): 1 confirmed death and
        # 0 confirmed cases still promotes on the confirmed-deaths qualifier.
        self.assertTrue(
            is_source_zone_promotion_eligible(
                _metrics(confirmed=0, confirmed_deaths=1), "present_with_data"
            )
        )

    def test_zero_confirmed_and_zero_deaths_fails(self):
        self.assertFalse(
            is_source_zone_promotion_eligible(
                _metrics(confirmed=0, confirmed_deaths=0), "present_with_data"
            )
        )


class TestBorderIntlOverride(unittest.TestCase):
    def test_border_zone_passes_at_zero_counts(self):
        # mahagi-cod has 0 INSP data but is a border-intl target.
        self.assertTrue(
            is_source_zone_promotion_eligible(
                _metrics(confirmed=0, confirmed_deaths=0),
                "present_with_data",
                lovs_zone_id="mahagi-cod",
            )
        )

    def test_aru_passes_as_border_zone(self):
        self.assertTrue(
            is_source_zone_promotion_eligible(
                _metrics(),
                "present_with_data",
                lovs_zone_id="aru",
            )
        )

    def test_rimba_passes_as_border_zone(self):
        self.assertTrue(
            is_source_zone_promotion_eligible(
                _metrics(),
                "present_with_data",
                lovs_zone_id="rimba",
            )
        )

    def test_non_border_zone_at_zero_counts_fails(self):
        self.assertFalse(
            is_source_zone_promotion_eligible(
                _metrics(),
                "present_with_data",
                lovs_zone_id="bunia",
            )
        )


class TestClassificationGate(unittest.TestCase):
    def test_present_but_zero_fails_even_with_high_confirmed(self):
        # Logically impossible (high confirmed and present_but_zero contradict),
        # but the criterion must refuse on classification alone for defense in
        # depth.
        self.assertFalse(
            is_source_zone_promotion_eligible(
                _metrics(confirmed=100), "present_but_zero"
            )
        )

    def test_structurally_absent_fails(self):
        self.assertFalse(
            is_source_zone_promotion_eligible(
                _metrics(confirmed=100), "structurally_absent"
            )
        )


class TestRealMay26Data(unittest.TestCase):
    """Verify against the real e40bc9e May 26 numbers for sanity.

    Existing LOVS source zones at as_of 2026-05-26:
    - bunia (confirmed=36, confirmed_deaths=2) -> True
    - bambu (confirmed=0, confirmed_deaths=0)  -> False (present_but_zero in
      the real data; the corridor source-load there is from CDC SitRep007)

    For each new zone, the criterion's result must be reproducible from the
    same data + constants.
    """

    def test_bunia_passes_via_confirmed(self):
        self.assertTrue(
            is_source_zone_promotion_eligible(
                _metrics(confirmed=36, confirmed_deaths=2),
                "present_with_data",
                lovs_zone_id="bunia",
            )
        )

    def test_komanda_passes_via_confirmed_deaths(self):
        # 1 confirmed_death, 0 confirmed: classic mixed_with_metric_floor
        # instance that still promotes on the confirmed-deaths qualifier.
        self.assertTrue(
            is_source_zone_promotion_eligible(
                _metrics(confirmed=0, confirmed_deaths=1),
                "present_with_data",
                lovs_zone_id="komanda",
            )
        )


if __name__ == "__main__":
    unittest.main()
