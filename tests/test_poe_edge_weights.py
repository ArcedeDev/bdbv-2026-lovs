# SPDX-License-Identifier: Apache-2.0
"""Tests for the PoE-throughput corridor edge weights (situational mobility lever).

Covers the new arua-uga PoE mapping in lovs_poe_corridor and the
source-province-aware edge-weight recipe in snapshot_sensitivity. Uses a
synthetic in-memory PoE fixture so the tests do not depend on the restricted
data file or on data/external_sources/bdbv-2026.observed.json.
"""
from __future__ import annotations

import json
import pathlib
import tempfile
import unittest

import snapshot_sensitivity as sens
from lovs import lovs_poe_corridor as poe


# Minimal observed-shape stand-in: only the mobility fields the recipe reads.
OBSERVED = {
    "mobility": {
        "admin2_movement_share": {"djugu": 0.66, "mahagi": 0.15},
        "zone_to_territory": {
            "mongbwalu": "djugu",
            "rwampara": "irumu",
            "bunia": "bunia",
        },
        "border_crossings": [],
    }
}


def _write_poe(directory: str) -> str:
    path = pathlib.Path(directory) / "poe.json"
    payload = {
        # Synthetic passenger counts (not the restricted Imperial Table 3 values).
        # Magnitudes are illustrative; only the relative orderings drive the tests.
        "counts": [
            {"poe": "Goli", "drc_province": "Ituri", "mean_daily_passengers": 500},
            {"poe": "Vurra", "drc_province": "Ituri", "mean_daily_passengers": 400},
            {"poe": "Odramacaku", "drc_province": "Ituri", "mean_daily_passengers": 300},
            {"poe": "Ntoroko Main", "drc_province": "Ituri", "mean_daily_passengers": 200},
            {"poe": "Mpondwe", "drc_province": "Nord Kivu", "mean_daily_passengers": 600},
            {"poe": "Busunga", "drc_province": "Nord Kivu", "mean_daily_passengers": 700},
        ],
        "totals": {"ituri_plus_nord_kivu_total_daily_passengers": 2700},
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


class TestAruaMapping(unittest.TestCase):
    def test_arua_maps_to_west_nile_crossings(self):
        self.assertEqual(
            set(poe.CORRIDOR_TO_POE_NAMES["arua-uga"]),
            {"Goli", "Vurra", "Odramacaku"},
        )

    def test_arua_daily_passengers_sum(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                poe.corridor_daily_passengers("arua-uga", _write_poe(tmp)),
                500 + 400 + 300,
            )


class TestPoeEdgeWeights(unittest.TestCase):
    def _weights(self, sources):
        with tempfile.TemporaryDirectory() as tmp:
            return sens.edge_weights_from_observed(
                OBSERVED, sources, poe_path=_write_poe(tmp)
            )

    def test_arua_outranks_bundibugyo_for_ituri_source(self):
        w = self._weights(["bunia"])
        self.assertGreater(w["bunia->arua-uga"], w["bunia->bundibugyo-uga"])

    def test_ituri_source_gets_no_kasese_weight(self):
        # Kasese is fed by the Nord-Kivu crossings (Mpondwe/Busunga); an Ituri
        # source is not credited with that throughput.
        w = self._weights(["bunia"])
        self.assertNotIn("bunia->kasese-uga", w)

    def test_movement_share_amplifies_same_target(self):
        w = self._weights(["mongbwalu", "rwampara"])
        # mongbwalu (Djugu share 0.66) outweighs rwampara (Irumu, no share).
        self.assertGreater(w["mongbwalu->arua-uga"], w["rwampara->arua-uga"])

    def test_kampala_and_beni_have_no_throughput_weight(self):
        # No direct border PoE: the crossing-throughput lever does not apply.
        w = self._weights(["bunia"])
        self.assertNotIn("bunia->kampala-uga", w)
        self.assertNotIn("bunia->beni-cod", w)

    def test_nebbi_is_downstream_of_arua(self):
        w = self._weights(["bunia"])
        self.assertIn("bunia->nebbi-uga", w)
        self.assertLess(w["bunia->nebbi-uga"], w["bunia->arua-uga"])

    def test_deterministic(self):
        self.assertEqual(self._weights(["bunia"]), self._weights(["bunia"]))

    def test_fallback_when_no_poe_file(self):
        # Absent PoE file -> documented share recipe, no crash, no throughput keys.
        w = sens.edge_weights_from_observed(
            OBSERVED, ["mongbwalu"], poe_path="/nonexistent/poe.json"
        )
        # mongbwalu has share 0.66 and no flagged crossing -> 1.66 for each target.
        self.assertTrue(w)
        self.assertTrue(all(abs(v - 1.66) < 1e-9 for v in w.values()))


if __name__ == "__main__":
    unittest.main()
