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


if __name__ == "__main__":
    unittest.main()
