"""Cadence guard: newly-reported INSP affected health zones must be on the map.

`_promotion_table_zone_ids` blocks a promotion *row* whose zone lacks GRID3 geometry,
but a zone the transcriber has not yet added as a row (or geolocated) would otherwise
slip through and leave the map stale. `_check_insp_zone_coverage` compares each
province's INSP `health_zones_touched` numerator against the transcribed row count
(plus documented collapses) and fails loud on any gap.
"""
import copy
import json
import pathlib
import unittest

import refresh_pipeline as rp

PROMO = pathlib.Path(__file__).resolve().parents[1] / "data/sitrep_promotions/sitrep-058-2026-07-11.json"


class TestInspZoneCoverageGate(unittest.TestCase):
    def setUp(self) -> None:
        self.figures = json.loads(PROMO.read_text())["figures"]

    def test_current_sitrep58_promotion_passes(self) -> None:
        # 40 mapped rows + 2 documented Kisangani-commune collapses == INSP's 42 affected zones.
        rp._check_insp_zone_coverage(58, self.figures)

    def test_unmapped_new_zone_fails_loud(self) -> None:
        figures = copy.deepcopy(self.figures)
        for pt in figures["health_zone_table"]["province_totals"]:
            if pt["province"] == "Ituri":
                pt["health_zones_touched"] = "27/36"  # INSP now says 27, promotion still maps 26
        with self.assertRaises(RuntimeError) as ctx:
            rp._check_insp_zone_coverage(58, figures)
        self.assertIn("Ituri", str(ctx.exception))
        self.assertIn("unmapped", str(ctx.exception))

    def test_documented_collapse_is_allowlisted(self) -> None:
        # Kisangani's Mangobo + Lubunga detection communes roll into makiso-kisangani-cod.
        self.assertEqual(rp.COLLAPSED_INSP_ZONES.get("Mangobo"), "Tshopo")
        self.assertEqual(rp.COLLAPSED_INSP_ZONES.get("Lubunga"), "Tshopo")


if __name__ == "__main__":
    unittest.main()
