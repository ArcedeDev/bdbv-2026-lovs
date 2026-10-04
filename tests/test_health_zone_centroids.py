# SPDX-License-Identifier: Apache-2.0
"""Tests for the generator-owned DRC health-zone points.

The distance model and the tournament universe both stand on this file, so a zone
that goes missing, moves, or matches the wrong SitRep row would shrink or bend the
common target set for every model in a round.
"""
from __future__ import annotations

import copy
import pathlib
import tempfile
import unittest

from lovs import health_zone_centroids as hzc
from lovs import sitrep_promotions
from lovs.sitrep_overlays import per_zone_canonical_id
from lovs.zone_alias_bridge import ZoneAliasBridge
from tools import gen_health_zone_centroids as gen

# Pinned on 2026-10-04 after regenerating the file twice from the two pinned inputs
# and getting identical bytes. A change here must come from a reviewed regeneration.
COMMITTED_SHA256 = "34977c4f37448632037ff17813399e0a58faee337a3269a5d62e720453c20f5f"


class TestCommittedFile(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.doc = hzc.load()
        cls.index = hzc.CentroidIndex(cls.doc)

    def test_file_is_hash_pinned(self):
        self.assertEqual(COMMITTED_SHA256, hzc.file_sha256(hzc.CENTROIDS_PATH))

    def test_file_is_in_its_one_serialization(self):
        self.assertEqual(hzc.render(self.doc), hzc.CENTROIDS_PATH.read_text(encoding="utf-8"))

    def test_519_unique_zones_in_26_provinces(self):
        zones = self.doc["zones"]
        self.assertEqual(519, len(zones))
        self.assertEqual(519, len({z["zone_id"] for z in zones}))
        self.assertEqual(26, len({hzc.province_key(z["province"]) for z in zones}))

    def test_every_point_is_a_finite_coordinate_inside_drc(self):
        for zone in self.doc["zones"]:
            self.assertTrue(hzc.DRC_LAT_RANGE[0] <= zone["lat"] <= hzc.DRC_LAT_RANGE[1], zone)
            self.assertTrue(hzc.DRC_LON_RANGE[0] <= zone["lon"] <= hzc.DRC_LON_RANGE[1], zone)
            self.assertEqual("population_peak", zone["method"])

    def test_inputs_carry_source_version_licence_and_hash(self):
        self.assertEqual(gen.INPUTS, self.doc["inputs"])
        for meta in self.doc["inputs"].values():
            self.assertEqual("CC-BY-4.0", meta["license"])
            self.assertTrue(meta["url"].startswith("https://"))
            self.assertRegex(meta["sha256"], r"^[0-9a-f]{64}$")
            self.assertIn("CC BY 4.0", meta["attribution"])
        self.assertEqual(
            [meta["attribution"] for meta in gen.INPUTS.values()], self.doc["attribution"]
        )

    def test_repeated_grid3_names_get_distinct_ids(self):
        self.assertEqual("Tshopo", self.index.zone("lubunga")["province"])
        self.assertEqual("Kasaï-Central", self.index.zone("lubunga-kasai-central")["province"])
        self.assertEqual("Bas-Uele", self.index.zone("bili-bas-uele")["province"])
        self.assertEqual("Nord-Ubangi", self.index.zone("bili-nord-ubangi")["province"])

    def test_province_totals_match_the_sitrep_footprint_denominators(self):
        # SitRep 140 prints "health_zones_touched" as n/total per province.
        promotion = sitrep_promotions.reviewed_promotions_by_number()[140]
        for row in promotion["figures"]["province_table"]:
            _, total = row["health_zones_touched"].split("/")
            self.assertEqual(int(total), len(self.index.zones_in_provinces([row["province"]])), row)


class TestSitRepMatching(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.index = hzc.CentroidIndex.load_default()
        cls.bridge = ZoneAliasBridge.load_default()
        cls.rows = [
            (promotion["sitrep_number"], row)
            for promotion in sitrep_promotions.load_reviewed_promotions()
            for row in ((promotion.get("figures") or {}).get("health_zone_table") or {}).get("rows") or []
            if "ventil" not in str(row.get("zone") or "").lower()
        ]

    def test_reviewed_tables_exist(self):
        self.assertGreater(len(self.rows), 1000)

    def test_every_reviewed_table_zone_resolves_to_exactly_one_point(self):
        for edition, row in self.rows:
            zone_id = self.index.resolve_sitrep_row(row["province"], row["zone"])
            zone = self.index.zone(zone_id)
            self.assertEqual(hzc.province_key(row["province"]), hzc.province_key(zone["province"]), (edition, row))

    def test_every_affected_zone_is_in_the_alias_bridge(self):
        for edition, row in self.rows:
            lovs_id = per_zone_canonical_id(row["zone"])
            self.assertIsNotNone(self.bridge.inrb_for(lovs_id), (edition, row["zone"], lovs_id))

    def test_spelling_overrides_reach_the_gazetteer_ids(self):
        self.assertEqual("wanie-rukula", self.index.resolve_sitrep_row("Tshopo", "Wanie-Rukula"))
        self.assertEqual("makiso-kisangani-cod", self.index.resolve_sitrep_row("Tshopo", "Makiso-Kisangani"))
        self.assertEqual("gety", self.index.resolve_sitrep_row("Ituri", "Gethy"))
        self.assertEqual("bili-nord-ubangi", self.index.resolve_sitrep_row("Nord-Ubangi", "Bili"))

    def test_unknown_spelling_raises_instead_of_guessing(self):
        with self.assertRaises(hzc.CentroidError):
            self.index.resolve_sitrep_row("Ituri", "Nowhere")
        with self.assertRaises(hzc.CentroidError):
            # Right name, wrong province.
            self.index.resolve_sitrep_row("Kinshasa", "Bunia")


class TestValidation(unittest.TestCase):
    def setUp(self) -> None:
        self.doc = hzc.load()

    def test_a_dropped_zone_is_rejected(self):
        doc = copy.deepcopy(self.doc)
        doc["zones"].pop()
        doc["zone_count"] -= 1
        with self.assertRaises(hzc.CentroidError):
            hzc.validate(doc)

    def test_a_point_outside_drc_is_rejected(self):
        doc = copy.deepcopy(self.doc)
        doc["zones"][0]["lat"], doc["zones"][0]["lon"] = doc["zones"][0]["lon"], doc["zones"][0]["lat"]
        with self.assertRaises(hzc.CentroidError):
            hzc.validate(doc)

    def test_a_hand_edited_id_is_rejected(self):
        doc = copy.deepcopy(self.doc)
        doc["zones"][0]["zone_id"] = "aaa-renamed"
        with self.assertRaises(hzc.CentroidError):
            hzc.validate(doc)

    def test_haversine_known_distance(self):
        # One degree of latitude on the mean-radius sphere.
        self.assertAlmostEqual(111.195, hzc.haversine_km(0.0, 25.0, 1.0, 25.0), places=2)


class TestGeneratorInputPins(unittest.TestCase):
    def test_unpinned_input_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "not-grid3.geojson"
            path.write_text("{}", encoding="utf-8")
            with self.assertRaises(SystemExit) as ctx:
                gen._check_input("health_zone_polygons", path)
            self.assertIn("unpinned", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
