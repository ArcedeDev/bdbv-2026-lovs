# SPDX-License-Identifier: Apache-2.0
"""Tests for run_local.py, the bring-your-own point-of-care local runner."""
from __future__ import annotations

import contextlib
import io
import json
import pathlib
import tempfile
import unittest

import run_local

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
EXAMPLE = REPO_ROOT / "point_of_care_input.example.json"


class TestRunLocal(unittest.TestCase):

    def setUp(self):
        self.poc = json.loads(EXAMPLE.read_text(encoding="utf-8"))

    def test_example_runs_and_ranks_corridors(self):
        result = run_local.run(self.poc)
        corridors = result["corridors"]
        self.assertTrue(corridors, "expected corridor estimates")
        # One estimate per (source zone x target); the example sets are disjoint.
        n_zones = len(self.poc["source_zones"])
        n_targets = len(self.poc["candidate_target_zones"])
        self.assertEqual(len(corridors), n_zones * n_targets)
        # Returned already sorted by ascertainment-adjusted upper-50, descending.
        ups = [c.risk_visibility_adjusted.upper_50 for c in corridors]
        self.assertEqual(ups, sorted(ups, reverse=True))

    def test_per_zone_counts_drive_the_ranking(self):
        # With edge weights removed, the zone with far more cases must top the list.
        poc = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        poc["corridor_edge_weights"] = {}
        for zone in poc["source_zones"]:
            zone["confirmed"] = 1
        poc["source_zones"][0]["confirmed"] = 100
        result = run_local.run(poc)
        self.assertEqual(
            result["corridors"][0].source_geography_id,
            poc["source_zones"][0]["zone_id"],
        )

    def test_to_json_shape(self):
        out = run_local.to_json(run_local.run(self.poc))
        for key in ("outbreak_id", "observed", "visibility", "transmission", "corridors"):
            self.assertIn(key, out)
        self.assertIn("reporting_completeness_50", out["visibility"])
        self.assertTrue(out["corridors"])

    def test_missing_zones_raises(self):
        with self.assertRaises(ValueError):
            run_local.run({"candidate_target_zones": ["x"]})

    def test_missing_targets_raises(self):
        with self.assertRaises(ValueError):
            run_local.run({"source_zones": [{"zone_id": "a", "confirmed": 5}]})

    def test_zone_requires_zone_id(self):
        with self.assertRaisesRegex(ValueError, "zone_id"):
            run_local.run(
                {"source_zones": [{"confirmed": 5}], "candidate_target_zones": ["x"]}
            )

    def test_horizon_must_be_valid(self):
        poc = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        poc["horizon_days"] = 60
        with self.assertRaisesRegex(ValueError, "horizon_days"):
            run_local.run(poc)

    def test_drivers_are_per_zone_not_aggregate(self):
        # The whole point of the local runner: each corridor is driven by the
        # zone's OWN count, so the public method's "aggregate" wording must go.
        result = run_local.run(self.poc)
        drivers = [d for c in result["corridors"] for d in c.drivers]
        self.assertTrue(drivers)
        self.assertFalse(any(d.startswith("aggregate confirmed count") for d in drivers))
        self.assertTrue(any(d.startswith("per-zone confirmed count") for d in drivers))
        caveats = [cv for c in result["corridors"] for cv in c.caveats]
        self.assertFalse(
            any(cv.startswith("confirmed cases are aggregate") for cv in caveats)
        )

    def test_edge_weights_parse(self):
        self.assertIsNone(run_local._edge_weights(None))
        self.assertIsNone(run_local._edge_weights({}))
        self.assertIsNone(run_local._edge_weights({"_help": "ignored"}))
        parsed = run_local._edge_weights({"a->b": 1.5, "_note": "x", "c -> d": 2})
        self.assertEqual(parsed, {("a", "b"): 1.5, ("c", "d"): 2.0})

    def test_null_counts_are_tolerated(self):
        poc = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        poc["source_zones"][0]["suspected"] = None
        del poc["source_zones"][0]["deaths"]
        # Null / missing counts are treated as 0, not a crash.
        result = run_local.run(poc)
        self.assertTrue(result["corridors"])

    def test_to_json_includes_corridor_caveats(self):
        out = run_local.to_json(run_local.run(self.poc))
        self.assertIn("caveats", out["corridors"][0])
        self.assertIsInstance(out["corridors"][0]["caveats"], list)

    def test_main_writes_json_atomically(self):
        with tempfile.TemporaryDirectory() as d:
            out = pathlib.Path(d) / "run.json"
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = run_local.main(["--input", str(EXAMPLE), "--json-out", str(out)])
            self.assertEqual(rc, 0)
            self.assertTrue(out.exists())
            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertTrue(data["corridors"])
            self.assertFalse((pathlib.Path(d) / "run.json.tmp").exists())

    def test_main_bad_json_returns_nonzero(self):
        with tempfile.TemporaryDirectory() as d:
            bad = pathlib.Path(d) / "bad.json"
            bad.write_text("{not json", encoding="utf-8")
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                rc = run_local.main(["--input", str(bad)])
            self.assertEqual(rc, 2)


class TestRunLocalHistory(unittest.TestCase):
    """The fork must let a partner with prior snapshots opt into empirical
    history so the visibility nowcast can drop the conservative 7-day default
    and the 'single as-of snapshot in window' uncertainty driver."""

    def _minimal_poc(self, with_history: bool) -> dict:
        poc = {
            "outbreak_id": "fork-test",
            "as_of": "2026-05-20",
            "pathogen": "BDBV",
            "country_scope": ["COD"],
            "source_zones": [
                {"zone_id": "a", "confirmed": 15, "suspected": 120, "deaths": 4},
                {"zone_id": "b", "confirmed": 12, "suspected": 90, "deaths": 3},
            ],
            "candidate_target_zones": ["x", "y"],
            "horizon_days": 14,
        }
        if with_history:
            poc["history"] = [
                {
                    "as_of": "2026-05-10",
                    "source_zones": [
                        {"zone_id": "a", "confirmed": 6, "suspected": 60, "deaths": 1},
                        {"zone_id": "b", "confirmed": 4, "suspected": 45, "deaths": 1},
                    ],
                },
                {
                    "as_of": "2026-05-15",
                    "source_zones": [
                        {"zone_id": "a", "confirmed": 10, "suspected": 90, "deaths": 2},
                        {"zone_id": "b", "confirmed": 8, "suspected": 70, "deaths": 2},
                    ],
                },
            ]
        return poc

    def test_default_method_basis_is_single_snapshot(self):
        result = run_local.run(self._minimal_poc(with_history=False))
        self.assertEqual(result["method_basis"], "single_snapshot")
        self.assertEqual(len(result["history"]), 0)
        drivers = result["visibility"].uncertainty_drivers
        self.assertTrue(
            any("single as-of snapshot" in d for d in drivers),
            "expected single-snapshot uncertainty driver when no history is supplied",
        )

    def test_two_history_snapshots_switch_to_empirical_history(self):
        result = run_local.run(self._minimal_poc(with_history=True))
        self.assertEqual(result["method_basis"], "empirical_history")
        self.assertEqual(len(result["history"]), 2)
        drivers = result["visibility"].uncertainty_drivers
        self.assertFalse(
            any("single as-of snapshot" in d for d in drivers),
            "single-snapshot driver must be dropped once history is supplied",
        )

    def test_history_must_be_a_list(self):
        poc = self._minimal_poc(with_history=False)
        poc["history"] = {"not": "a list"}
        with self.assertRaisesRegex(ValueError, "history must be a list"):
            run_local.run(poc)

    def test_history_entry_must_have_source_zones(self):
        poc = self._minimal_poc(with_history=False)
        poc["history"] = [{"as_of": "2026-05-10"}]
        with self.assertRaisesRegex(ValueError, "history\\[0\\].*source_zones"):
            run_local.run(poc)

    def test_history_out_of_order_is_sorted(self):
        poc = self._minimal_poc(with_history=True)
        poc["history"] = list(reversed(poc["history"]))
        result = run_local.run(poc)
        history = result["history"]
        self.assertEqual([s.as_of for s in history], sorted(s.as_of for s in history))

    def test_history_entry_at_or_after_base_as_of_is_rejected(self):
        # Reviewer-flagged M1: silently accepted, the max(0.5, ...) clamp in
        # visibility hides the negative days-between, and the partner sees a
        # misleadingly tight completeness band with no error.
        poc = self._minimal_poc(with_history=False)
        poc["history"] = [
            {
                "as_of": "2026-05-20",
                "source_zones": [
                    {"zone_id": "a", "confirmed": 5, "suspected": 40, "deaths": 1}
                ],
            }
        ]
        with self.assertRaisesRegex(ValueError, "must be strictly earlier"):
            run_local.run(poc)
        # Same check for an as_of strictly later than the base.
        poc["history"][0]["as_of"] = "2026-06-25"
        with self.assertRaisesRegex(ValueError, "must be strictly earlier"):
            run_local.run(poc)

    def test_duplicate_history_as_of_is_rejected(self):
        poc = self._minimal_poc(with_history=False)
        poc["history"] = [
            {
                "as_of": "2026-05-13",
                "source_zones": [
                    {"zone_id": "a", "confirmed": 5, "suspected": 40, "deaths": 1}
                ],
            },
            {
                "as_of": "2026-05-13",
                "source_zones": [
                    {"zone_id": "a", "confirmed": 6, "suspected": 50, "deaths": 1}
                ],
            },
        ]
        with self.assertRaisesRegex(ValueError, "duplicates"):
            run_local.run(poc)

    def test_seed_mixes_history_so_two_runs_with_same_input_match(self):
        # Reviewer-flagged M2: nowcast's seed was being derived from the base
        # snapshot only, so the same base with different history would share
        # a seed but produce different output. Mixing history into the seed
        # restores the "same seed implies same draws" invariant.
        poc_with = self._minimal_poc(with_history=True)
        poc_without = self._minimal_poc(with_history=False)
        r1 = run_local.run(poc_with)
        r2 = run_local.run(poc_with)
        self.assertEqual(
            r1["visibility"].reporting_completeness.lower_50,
            r2["visibility"].reporting_completeness.lower_50,
        )
        # Same base + different history must now also produce different seeds,
        # which is visible in the visibility output being different.
        r3 = run_local.run(poc_without)
        self.assertNotEqual(
            r1["visibility"].reporting_completeness.lower_50,
            r3["visibility"].reporting_completeness.lower_50,
        )

    def test_method_basis_threshold_is_a_named_constant(self):
        # Defensive: keeps the run() threshold and the visibility module's
        # _uncertainty_drivers threshold honest. If we ever raise it to 3
        # without updating both call sites, this test fails loudly.
        self.assertEqual(run_local.EMPIRICAL_HISTORY_MIN_SNAPSHOTS, 2)

    def test_to_json_carries_method_block(self):
        out = run_local.to_json(run_local.run(self._minimal_poc(with_history=True)))
        method = out["method"]
        self.assertEqual(method["basis"], "empirical_history")
        self.assertEqual(method["history_snapshot_count"], 2)
        self.assertEqual(method["history_earliest_as_of"][:10], "2026-05-10")
        self.assertFalse(method["priors_overridden"])


class TestRunLocalCaseDefinition(unittest.TestCase):
    """The visibility uncertainty drivers flag a missing case-definition
    version. A partner who has declared one should not see that driver."""

    def _poc(self, case_def: str | None) -> dict:
        return {
            "outbreak_id": "cd-test",
            "as_of": "2026-05-20",
            "pathogen": "BDBV",
            "source_zones": [
                {"zone_id": "a", "confirmed": 10, "suspected": 80, "deaths": 2},
            ],
            "candidate_target_zones": ["x"],
            "case_definition_version": case_def,
        }

    def test_missing_case_definition_surfaces_driver(self):
        result = run_local.run(self._poc(None))
        drivers = result["visibility"].uncertainty_drivers
        self.assertTrue(any("case-definition version not declared" in d for d in drivers))

    def test_declared_case_definition_drops_driver(self):
        result = run_local.run(self._poc("partner-cd-v3"))
        drivers = result["visibility"].uncertainty_drivers
        self.assertFalse(any("case-definition version not declared" in d for d in drivers))
        self.assertEqual(result["snapshot"].case_definition_version, "partner-cd-v3")


class TestRunLocalPriorsOverride(unittest.TestCase):
    """The fork must let a partner who has fitted a 2026-outbreak serial
    interval or R from their own line list replace the species-default prior,
    while keeping the partial-override pattern (omitted fields fall back)."""

    def _poc(self, override: dict | None) -> dict:
        return {
            "outbreak_id": "prior-test",
            "as_of": "2026-05-20",
            "pathogen": "BDBV",
            "source_zones": [
                {"zone_id": "a", "confirmed": 20, "suspected": 180, "deaths": 5},
            ],
            "candidate_target_zones": ["x"],
            "transmission_priors_override": override,
        }

    def test_no_override_uses_species_default(self):
        result = run_local.run(self._poc(None))
        self.assertFalse(result["priors_overridden"])
        self.assertEqual(
            result["priors"].r_prior_gamma,
            (4.0, 3.0),
            "default BUNDIBUGYO_PRIORS_STAGE_TWO R prior",
        )

    def test_partial_override_only_replaces_named_fields(self):
        result = run_local.run(
            self._poc({"r_prior_gamma": [6.0, 4.5], "notes": "partner-fitted R"})
        )
        priors = result["priors"]
        self.assertTrue(result["priors_overridden"])
        self.assertEqual(priors.r_prior_gamma, (6.0, 4.5))
        # serial-interval and incubation must fall back to defaults
        self.assertEqual(priors.serial_interval_gamma, (4.0, 0.55))
        self.assertEqual(priors.incubation_gamma, (4.0, 0.6))
        self.assertEqual(priors.under_ascertainment_uniform, (0.3, 0.9))
        # partner note is prepended for audit; species default carried after
        self.assertEqual(priors.notes[0], "partner-fitted R")

    def test_override_validates_gamma_shape(self):
        with self.assertRaisesRegex(ValueError, "r_prior_gamma"):
            run_local.run(self._poc({"r_prior_gamma": [6.0]}))
        with self.assertRaisesRegex(ValueError, "r_prior_gamma"):
            run_local.run(self._poc({"r_prior_gamma": ["bad", 1.0]}))

    def test_override_rejects_invalid_uniform(self):
        # TransmissionPriors.__post_init__ enforces 0 <= lo < hi <= 1
        with self.assertRaises(ValueError):
            run_local.run(self._poc({"under_ascertainment_uniform": [0.9, 0.3]}))

    def test_to_json_surfaces_priors_metadata(self):
        out = run_local.to_json(
            run_local.run(self._poc({"r_prior_gamma": [6.0, 4.5]}))
        )
        self.assertTrue(out["method"]["priors_overridden"])
        self.assertEqual(out["method"]["priors_r_gamma"], [6.0, 4.5])

    def test_override_must_be_an_object(self):
        with self.assertRaisesRegex(ValueError, "must be an object"):
            run_local.run(self._poc("not a dict"))

    def test_empty_override_dict_is_treated_as_no_override(self):
        # Reviewer-flagged M3: an empty override dict (or one with no
        # recognised field) was setting priors_overridden=True with the
        # species default values. The audit trail must not lie about an
        # override that did not actually change anything.
        result = run_local.run(self._poc({}))
        self.assertFalse(result["priors_overridden"])
        self.assertEqual(result["priors"].r_prior_gamma, (4.0, 3.0))

    def test_override_with_only_unrecognised_keys_is_treated_as_no_override(self):
        result = run_local.run(
            self._poc({"_help": "comment-only", "future_field": 123})
        )
        self.assertFalse(result["priors_overridden"])
        self.assertEqual(result["priors"].r_prior_gamma, (4.0, 3.0))

    def test_override_with_only_species_still_counts_as_override(self):
        # species is a recognised override field even though it does not
        # change the gamma math, so a partner who declared an override
        # species explicitly must see it in the audit trail.
        result = run_local.run(self._poc({"species": "BDBV-2026-partner"}))
        self.assertTrue(result["priors_overridden"])
        self.assertEqual(result["priors"].species, "BDBV-2026-partner")


class TestRunLocalExampleSchemaShipsWithNewFields(unittest.TestCase):
    """Regression gate: the shipped example must exercise the new fields so a
    partner cloning the repo sees the recommended pattern in their first run."""

    def test_example_includes_history_case_def_and_priors_override(self):
        poc = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        self.assertIn("history", poc, "example must include a history block")
        self.assertGreaterEqual(len(poc["history"]), 2)
        self.assertIn("case_definition_version", poc)
        self.assertIn("transmission_priors_override", poc)

    def test_example_runs_with_empirical_history_basis(self):
        poc = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        result = run_local.run(poc)
        self.assertEqual(result["method_basis"], "empirical_history")
        self.assertTrue(result["priors_overridden"])


if __name__ == "__main__":
    unittest.main()
