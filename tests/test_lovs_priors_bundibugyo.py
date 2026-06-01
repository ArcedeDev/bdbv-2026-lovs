"""Tests for lovs/lovs_priors_bundibugyo.py.

Stage Two: Bundibugyo-species-specific priors + Module D opt-in override.
"""
from __future__ import annotations

import pathlib
import random
import unittest

from lovs import lovs_archive
from lovs import lovs_priors_bundibugyo
from lovs import lovs_reconciler
from lovs import lovs_transmission


_FIXTURE_ROOT = pathlib.Path(__file__).parent / "data" / "lovs" / "fixture"


class TestTransmissionPriorsContract(unittest.TestCase):

    def test_bundibugyo_priors_exists_with_required_species(self):
        priors = lovs_priors_bundibugyo.BUNDIBUGYO_PRIORS_STAGE_TWO
        self.assertEqual(priors.species, "BDBV")

    def test_zaire_priors_exists_with_required_species(self):
        priors = lovs_priors_bundibugyo.ZAIRE_PRIORS_STAGE_ONE
        self.assertEqual(priors.species, "EBOV-Z")

    def test_bundibugyo_r_prior_mean_is_below_zaire(self):
        bdbv = lovs_priors_bundibugyo.BUNDIBUGYO_PRIORS_STAGE_TWO
        ebov = lovs_priors_bundibugyo.ZAIRE_PRIORS_STAGE_ONE
        bdbv_mean = bdbv.r_prior_gamma[0] / bdbv.r_prior_gamma[1]
        ebov_mean = ebov.r_prior_gamma[0] / ebov.r_prior_gamma[1]
        self.assertLess(
            bdbv_mean, ebov_mean,
            f"BDBV R mean {bdbv_mean} should be < EBOV-Z R mean {ebov_mean}",
        )

    def test_bundibugyo_serial_interval_shorter_than_zaire(self):
        bdbv = lovs_priors_bundibugyo.BUNDIBUGYO_PRIORS_STAGE_TWO
        ebov = lovs_priors_bundibugyo.ZAIRE_PRIORS_STAGE_ONE
        bdbv_si_mean = bdbv.serial_interval_gamma[0] / bdbv.serial_interval_gamma[1]
        ebov_si_mean = ebov.serial_interval_gamma[0] / ebov.serial_interval_gamma[1]
        self.assertLess(
            bdbv_si_mean, ebov_si_mean,
            f"BDBV SI mean {bdbv_si_mean} should be < EBOV-Z SI mean {ebov_si_mean}",
        )

    def test_citations_cite_wamala_2010(self):
        priors = lovs_priors_bundibugyo.BUNDIBUGYO_PRIORS_STAGE_TWO
        joined = " ".join(priors.citations)
        self.assertIn("Wamala", joined)
        self.assertIn("10.3201/eid1607.091525", joined)

    def test_citations_cite_macneil_2010(self):
        priors = lovs_priors_bundibugyo.BUNDIBUGYO_PRIORS_STAGE_TWO
        joined = " ".join(priors.citations)
        self.assertIn("MacNeil", joined)
        self.assertIn("10.3201/eid1612.100627", joined)

    def test_rejects_invalid_gamma_alpha(self):
        with self.assertRaises(ValueError):
            lovs_priors_bundibugyo.TransmissionPriors(
                serial_interval_gamma=(-1.0, 0.5),
                r_prior_gamma=(4.0, 3.0),
                under_ascertainment_uniform=(0.3, 0.9),
                incubation_gamma=(4.0, 0.6),
                citations=("test",),
                species="BDBV",
                notes=(),
                version="test",
            )

    def test_rejects_invalid_under_ascertainment_range(self):
        with self.assertRaises(ValueError):
            lovs_priors_bundibugyo.TransmissionPriors(
                serial_interval_gamma=(4.0, 0.55),
                r_prior_gamma=(4.0, 3.0),
                under_ascertainment_uniform=(0.9, 0.3),  # inverted
                incubation_gamma=(4.0, 0.6),
                citations=("test",),
                species="BDBV",
                notes=(),
                version="test",
            )

    def test_rejects_empty_citations(self):
        with self.assertRaises(ValueError):
            lovs_priors_bundibugyo.TransmissionPriors(
                serial_interval_gamma=(4.0, 0.55),
                r_prior_gamma=(4.0, 3.0),
                under_ascertainment_uniform=(0.3, 0.9),
                incubation_gamma=(4.0, 0.6),
                citations=(),
                species="BDBV",
                notes=(),
                version="test",
            )

    def test_priors_is_frozen(self):
        priors = lovs_priors_bundibugyo.BUNDIBUGYO_PRIORS_STAGE_TWO
        with self.assertRaises(dataclasses_error := Exception):
            priors.species = "OTHER"  # type: ignore[misc]


class TestModuleDPriorsOverride(unittest.TestCase):

    def setUp(self) -> None:
        archive = lovs_archive.load_archive(_FIXTURE_ROOT)
        self.bdb_snapshot = lovs_reconciler.reconcile(
            archive, outbreak_id="ebv-bdb-2026", as_of="2026-05-19T00:00:00Z"
        )

    def test_default_priors_path_unchanged(self):
        """No priors argument: Stage One default behavior preserved (citations cite Faye)."""
        out = lovs_transmission.transmission_plausibility(
            self.bdb_snapshot, n_trajectories=200, seed=42
        )
        joined = " ".join(out.priors_cited)
        self.assertIn("Faye", joined)
        self.assertIn("NEJM", joined)

    def test_bundibugyo_priors_path_swaps_citations(self):
        """Pass BUNDIBUGYO priors: output cites Wamala + MacNeil."""
        out = lovs_transmission.transmission_plausibility(
            self.bdb_snapshot,
            n_trajectories=200,
            seed=42,
            priors=lovs_priors_bundibugyo.BUNDIBUGYO_PRIORS_STAGE_TWO,
        )
        joined = " ".join(out.priors_cited)
        self.assertIn("Wamala", joined)
        self.assertIn("MacNeil", joined)

    def test_bundibugyo_priors_assumptions_carry_species_label(self):
        out = lovs_transmission.transmission_plausibility(
            self.bdb_snapshot,
            n_trajectories=200,
            seed=42,
            priors=lovs_priors_bundibugyo.BUNDIBUGYO_PRIORS_STAGE_TWO,
        )
        joined = " ".join(out.assumptions)
        self.assertIn("BDBV", joined)

    def test_default_priors_assumptions_carry_transferability_note(self):
        out = lovs_transmission.transmission_plausibility(
            self.bdb_snapshot, n_trajectories=200, seed=42
        )
        joined = " ".join(out.assumptions)
        self.assertIn("transferred", joined.lower())

    def test_zaire_priors_explicit_matches_default(self):
        """Passing ZAIRE_PRIORS_STAGE_ONE should produce same generation distribution as default."""
        out_default = lovs_transmission.transmission_plausibility(
            self.bdb_snapshot, n_trajectories=300, seed=99
        )
        out_explicit = lovs_transmission.transmission_plausibility(
            self.bdb_snapshot,
            n_trajectories=300,
            seed=99,
            priors=lovs_priors_bundibugyo.ZAIRE_PRIORS_STAGE_ONE,
        )
        # Random.gammavariate is deterministic for same alpha/beta and seed;
        # the explicit Zaire priors and the default constants are the same numbers
        # so generation dists should match exactly.
        self.assertEqual(
            out_default.generations_before_detection,
            out_explicit.generations_before_detection,
        )

    def test_bundibugyo_priors_changes_generation_distribution(self):
        """Different priors should produce a different generation distribution under same seed."""
        out_default = lovs_transmission.transmission_plausibility(
            self.bdb_snapshot, n_trajectories=300, seed=99
        )
        out_bdbv = lovs_transmission.transmission_plausibility(
            self.bdb_snapshot,
            n_trajectories=300,
            seed=99,
            priors=lovs_priors_bundibugyo.BUNDIBUGYO_PRIORS_STAGE_TWO,
        )
        self.assertNotEqual(
            out_default.generations_before_detection,
            out_bdbv.generations_before_detection,
            "Switching to Bundibugyo priors should change generation distribution",
        )


if __name__ == "__main__":
    unittest.main()
