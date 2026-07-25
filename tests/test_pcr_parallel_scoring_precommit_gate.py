# SPDX-License-Identifier: Apache-2.0
"""Tests for lovs.pcr_parallel_scoring_precommit_gate (spec section 8.2).

Focus: the frozen-cohort contract (2026-07-17). The scoring cohort is frozen at
registration and the modulated surface may grow underneath it; the anti-retrofit property
must survive that change, and cohort shrinkage must still fail loud.
"""
from __future__ import annotations

import copy
import json
import pathlib
import tempfile
import unittest

from lovs import pcr_parallel_score
from lovs import pcr_parallel_scoring_precommit_gate as gate


BAND = {"lo": 0.3, "hi": 0.9}


def _snapshot(modulated: list[str], fallback: list[str] | None = None) -> dict:
    by_zone: dict[str, dict] = {z: dict(BAND) for z in modulated}
    for z in fallback or []:
        by_zone[z] = {"lo": None, "hi": None}
    return {
        "resolves_at": "2026-08-04T23:59:59Z",
        "per_zone_under_ascertainment_bands": {
            "method_basis": "africa_cdc_pcr_capacity_modulated_v1",
            "surface_role": "shadow_in_v1",
            "species_default_band": dict(BAND),
            "by_lovs_zone": by_zone,
            "coverage_stats": {
                "modulated_zones": len(modulated),
                "species_default_fallback_zones": len(fallback or []),
                "total_zones": len(modulated) + len(fallback or []),
            },
        },
    }


def _artifact(in_scope: list[str]) -> dict:
    art = {
        "schema_version": 1,
        "precommit_id": "pcr-ascertainment-parallel-scoring:bdbv-uga-cod-2026:2026-07-15",
        "outbreak_id": "bdbv-uga-cod-2026",
        "data_cohort_as_of": "2026-07-15",
        "resolution_checkpoint": "2026-08-04",
        "scored_surface_role_at_pin": "shadow_in_v1",
        "method_basis": "africa_cdc_pcr_capacity_modulated_v1",
        "in_scope_zones": sorted(in_scope),
        "estimators": {
            "E0_species_default": {"band_by_zone": {z: dict(BAND) for z in sorted(in_scope)}},
            "E1_pcr_modulated": {"band_by_zone": {z: dict(BAND) for z in sorted(in_scope)}},
        },
        "scoring_rule": {"primary": {"aggregate": "mean over in-scope zones"}},
        "promotion_bar": {"cycles_required": 2, "relative_margin": 0.1},
    }
    art["content_hash"] = pcr_parallel_score._canonical_hash(art)
    return art


class _Case(unittest.TestCase):
    def check(self, artifact: dict, snapshot: dict) -> list[str]:
        with tempfile.TemporaryDirectory() as td:
            d = pathlib.Path(td)
            (d / "pin.json").write_text(json.dumps(artifact), encoding="utf-8")
            (d / "snap.json").write_text(json.dumps(snapshot), encoding="utf-8")
            return gate.check_pcr_parallel_scoring_precommit(d / "pin.json", d / "snap.json")

    def out_of_cohort(self, artifact: dict, snapshot: dict) -> list[str]:
        with tempfile.TemporaryDirectory() as td:
            d = pathlib.Path(td)
            (d / "pin.json").write_text(json.dumps(artifact), encoding="utf-8")
            (d / "snap.json").write_text(json.dumps(snapshot), encoding="utf-8")
            return gate.out_of_cohort_zones(d / "pin.json", d / "snap.json")


class TestFrozenCohort(_Case):
    def test_exact_cohort_passes(self):
        """Back-compat: a cohort that still exactly equals the modulated set is fine."""
        zones = ["aru", "bunia", "goma-cod"]
        self.assertEqual([], self.check(_artifact(zones), _snapshot(zones)))

    def test_outbreak_growth_does_not_break_the_pin(self):
        """THE REGRESSION THIS FIXES.

        SitRep62 made Mahagi an affected zone; Mahagi already carried documented PCR
        capacity, so the DERIVED modulated set grew 15 -> 16 with no estimator or editorial
        change. Under the old `in_scope_zones == modulated` equality that forced a fresh
        registration on the spread of the disease itself, twice in two cycles, and a pin
        re-registered every time the data moves can never reach its own checkpoint.
        """
        frozen = ["aru", "bunia", "goma-cod"]
        grown = frozen + ["mahagi-cod"]
        self.assertEqual([], self.check(_artifact(frozen), _snapshot(grown)))

    def test_growth_is_reported_not_silent(self):
        frozen = ["aru", "bunia"]
        snap = _snapshot(frozen + ["mahagi-cod", "drodro"])
        self.assertEqual(
            ["drodro", "mahagi-cod"], self.out_of_cohort(_artifact(frozen), snap)
        )

    def test_no_growth_reports_nothing(self):
        zones = ["aru", "bunia"]
        self.assertEqual([], self.out_of_cohort(_artifact(zones), _snapshot(zones)))

    def test_cohort_shrinkage_fails_loud(self):
        """A scored zone losing its band breaks the frozen experiment.

        The modulated set only grows as the outbreak spreads, so shrinkage means the
        capacity table or the affected-zone roster regressed. That is a real defect and
        must not be tolerated the way growth is.
        """
        frozen = ["aru", "bunia", "goma-cod"]
        shrunk = _snapshot(["aru", "bunia"], fallback=["goma-cod"])
        problems = self.check(_artifact(frozen), shrunk)
        self.assertTrue(
            any("NO LONGER modulated" in p and "goma-cod" in p for p in problems),
            f"expected a loud shrinkage failure, got {problems!r}",
        )

    def test_unsorted_cohort_is_refused(self):
        art = _artifact(["aru", "bunia"])
        art["in_scope_zones"] = ["bunia", "aru"]
        art["content_hash"] = pcr_parallel_score._canonical_hash(art)
        problems = self.check(art, _snapshot(["aru", "bunia"]))
        self.assertTrue(any("must be sorted" in p for p in problems), problems)

    def test_empty_cohort_is_refused(self):
        art = _artifact(["aru"])
        art["in_scope_zones"] = []
        art["content_hash"] = pcr_parallel_score._canonical_hash(art)
        problems = self.check(art, _snapshot(["aru"]))
        self.assertTrue(any("non-empty" in p for p in problems), problems)


class TestAntiRetrofitSurvives(_Case):
    def test_e1_drift_on_a_scored_zone_still_fails(self):
        """The anti-retrofit property, preserved exactly where it matters.

        If the modulator starts producing a different band for a zone inside the frozen
        cohort, that is a METHOD change: a different E1 is a different experiment, and it
        still demands an append-only re-registration.
        """
        frozen = ["aru", "bunia"]
        snap = _snapshot(frozen)
        snap["per_zone_under_ascertainment_bands"]["by_lovs_zone"]["bunia"] = {
            "lo": 0.45, "hi": 0.95
        }
        problems = self.check(_artifact(frozen), snap)
        self.assertTrue(
            any("does not match snapshot band" in p and "bunia" in p for p in problems),
            f"E1 drift inside the cohort must fail, got {problems!r}",
        )

    def test_e1_drift_on_an_out_of_cohort_zone_does_not_fail(self):
        """A zone outside the frozen cohort is not being scored, so its band is not pinned.

        This is the whole point of freezing the cohort: the pinned comparison is over the
        cohort period, and zones the outbreak reached later cannot make it wrong.
        """
        frozen = ["aru", "bunia"]
        snap = _snapshot(frozen + ["mahagi-cod"])
        snap["per_zone_under_ascertainment_bands"]["by_lovs_zone"]["mahagi-cod"] = {
            "lo": 0.45, "hi": 0.95
        }
        self.assertEqual([], self.check(_artifact(frozen), snap))

    def test_missing_e1_band_for_a_scored_zone_fails(self):
        frozen = ["aru", "bunia"]
        art = _artifact(frozen)
        del art["estimators"]["E1_pcr_modulated"]["band_by_zone"]["bunia"]
        art["content_hash"] = pcr_parallel_score._canonical_hash(art)
        problems = self.check(art, _snapshot(frozen))
        self.assertTrue(any("missing or non-numeric" in p for p in problems), problems)

    def test_tampered_content_hash_still_fails(self):
        art = _artifact(["aru", "bunia"])
        art["in_scope_zones"] = ["aru"]  # edited after pinning, hash not recomputed
        problems = self.check(art, _snapshot(["aru", "bunia"]))
        self.assertTrue(any("content_hash" in p for p in problems), problems)

    def test_non_shadow_surface_still_refused(self):
        zones = ["aru"]
        art = _artifact(zones)
        art["scored_surface_role_at_pin"] = "primary"
        art["content_hash"] = pcr_parallel_score._canonical_hash(art)
        problems = self.check(art, _snapshot(zones))
        self.assertTrue(any("shadow_in_v1" in p for p in problems), problems)

    def test_checkpoint_before_snapshot_resolution_still_refused(self):
        zones = ["aru"]
        art = _artifact(zones)
        art["resolution_checkpoint"] = "2026-06-19"
        art["content_hash"] = pcr_parallel_score._canonical_hash(art)
        problems = self.check(art, _snapshot(zones))
        self.assertTrue(any("precedes" in p for p in problems), problems)

    def test_absent_bands_surface_is_not_this_gate_s_business(self):
        """When the surface is absent the shadow gate owns the failure, not this one."""
        with tempfile.TemporaryDirectory() as td:
            d = pathlib.Path(td)
            (d / "pin.json").write_text(json.dumps(_artifact(["aru"])), encoding="utf-8")
            (d / "snap.json").write_text(json.dumps({}), encoding="utf-8")
            self.assertEqual(
                [], gate.check_pcr_parallel_scoring_precommit(d / "pin.json", d / "snap.json")
            )


class TestLiveArtifact(unittest.TestCase):
    def test_the_committed_pin_passes_against_the_live_snapshot(self):
        self.assertEqual([], gate.check_pcr_parallel_scoring_precommit())

    def test_the_committed_pin_is_hash_valid(self):
        artifact = json.loads(gate.DEFAULT_PRECOMMIT_PATH.read_text(encoding="utf-8"))
        self.assertEqual(
            artifact["content_hash"], pcr_parallel_score._canonical_hash(artifact)
        )

    def test_every_superseded_pin_is_preserved_verbatim_and_hash_valid(self):
        """Append-only history: superseding never edits the artifact it replaces."""
        archived = sorted(
            gate.DEFAULT_PRECOMMIT_PATH.parent.glob(
                "pcr_ascertainment_parallel_scoring.superseded-*.json"
            )
        )
        self.assertGreaterEqual(len(archived), 1, "expected archived pre-commitments")
        for path in archived:
            artifact = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(
                artifact["content_hash"],
                pcr_parallel_score._canonical_hash(artifact),
                f"{path.name} is not hash-valid; a superseded pin was edited in place",
            )


if __name__ == "__main__":
    unittest.main()
