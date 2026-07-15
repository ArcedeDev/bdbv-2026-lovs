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

PROMO = pathlib.Path(__file__).resolve().parents[1] / "data/sitrep_promotions/sitrep-060-2026-07-13.json"


class TestInspZoneCoverageGate(unittest.TestCase):
    def setUp(self) -> None:
        self.figures = json.loads(PROMO.read_text())["figures"]

    def test_current_sitrep60_promotion_passes(self) -> None:
        # 45 mapped named rows + 0 documented collapses == INSP's 45 affected zones.
        # Tshopo now maps three distinct named rows (Makiso-Kisangani, Mangobo,
        # Lubunga) after the Kisangani communes were un-collapsed into their own
        # GRID3 v8.0 polygons; Haut-Uele maps four named zones (Wamba, Pawa,
        # Isiro, Boma Mangbetu).
        rp._check_insp_zone_coverage(60, self.figures)

    def test_unmapped_new_zone_fails_loud(self) -> None:
        figures = copy.deepcopy(self.figures)
        for pt in figures["health_zone_table"]["province_totals"]:
            if pt["province"] == "Ituri":
                pt["health_zones_touched"] = "27/36"  # INSP now says 27, promotion still maps 26
        with self.assertRaises(RuntimeError) as ctx:
            rp._check_insp_zone_coverage(58, figures)
        self.assertIn("Ituri", str(ctx.exception))
        self.assertIn("unmapped", str(ctx.exception))

    def test_no_documented_collapses_kisangani_communes_are_distinct_rows(self) -> None:
        # The Kisangani communes (Mangobo, Lubunga) were un-collapsed into their own
        # GRID3 v8.0 polygons, so the allowlist is now empty and Tshopo maps three
        # distinct named per-zone rows instead of one collapsed Makiso-Kisangani row.
        self.assertEqual(rp.COLLAPSED_INSP_ZONES, {})
        tshopo_zones = {
            row["zone"]
            for row in self.figures["health_zone_table"]["rows"]
            if row.get("province") == "Tshopo"
        }
        self.assertEqual(
            {"Makiso-Kisangani", "Mangobo", "Lubunga"}, tshopo_zones
        )


if __name__ == "__main__":
    unittest.main()
