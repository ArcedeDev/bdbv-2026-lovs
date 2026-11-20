# SPDX-License-Identifier: Apache-2.0
"""Weekly loop, test 5: vintage sources, the cross-check, exclusive writes, seed counter."""
from __future__ import annotations

import copy
import datetime as dt
import glob
import json
import os
import tempfile
import unittest
from pathlib import Path

from lovs.forecast.weekly import promotion_rows, register, source, spec, vintage
from tests.weekly_fixtures import EXTRACT_LAST_DAY, R, fixture_inputs
from tools.extract_operational_series import row_from_packet

REPO = Path(__file__).resolve().parents[1]
# The evidence packets are private and never enter this repository. When a local copy is
# present (the loop's clone or a sibling checkout), the real cross-check runs on it.
PACKETS = Path(os.environ.get(
    "LOVS_EVIDENCE_PACKETS", REPO.parent / "lovs-evidence-mcp" / "data" / "sitrep-packets"))


class FixtureCrossCheckTest(unittest.TestCase):
    def setUp(self) -> None:
        self.inputs = fixture_inputs()

    def test_promotion_rows_equal_packet_rows_on_shared_fields(self) -> None:
        report = vintage.cross_check(self.inputs["promotions"], self.inputs["packets"], self.inputs["extract"])
        self.assertEqual(report["mismatches"], [])
        self.assertGreater(report["packet_comparisons"], 100)
        self.assertGreater(report["extract_comparisons"], 100)
        for promo, packet in zip(self.inputs["promotions"], self.inputs["packets"]):
            a, b = promotion_rows.row_from_promotion(promo), row_from_packet(packet)
            self.assertEqual(promotion_rows.compare_rows(a, b), [])

    def test_a_differing_packet_fails_the_build(self) -> None:
        packets = copy.deepcopy(self.inputs["packets"])
        packets[3]["figures"]["headline"]["confirmed_total"] += 1
        with self.assertRaises(vintage.CrossCheckError):
            vintage.require_cross_check(self.inputs["promotions"], packets, self.inputs["extract"])

    def test_a_differing_extract_row_fails_the_build(self) -> None:
        extract = copy.deepcopy(self.inputs["extract"])
        extract[-1]["province_split"]["Ituri"]["confirmed"] += 5
        with self.assertRaises(vintage.CrossCheckError):
            vintage.require_cross_check(self.inputs["promotions"], self.inputs["packets"], extract)

    def test_history_from_extract_then_promotions(self) -> None:
        cutoff = dt.date(2026, 10, 11)
        rows = vintage.build_vintage(self.inputs["extract"], self.inputs["promotions"], R, cutoff,
                                     extract_last_sitrep=self.inputs["extract_last_sitrep"])
        sources = {r["data_as_of"]: r["source"] for r in rows}
        self.assertEqual(sources[EXTRACT_LAST_DAY.isoformat()], "extract")
        self.assertEqual(sources["2026-10-04"], "promotion")
        self.assertEqual(max(sources), cutoff.isoformat())
        days = [r["data_as_of"] for r in rows]
        self.assertEqual(days, sorted(days))
        self.assertEqual(len(days), len(set(days)))

    def test_cutoff_and_publication_respected(self) -> None:
        cutoff = dt.date(2026, 10, 8)
        rows = vintage.build_vintage(self.inputs["extract"], self.inputs["promotions"], R, cutoff,
                                     extract_last_sitrep=self.inputs["extract_last_sitrep"])
        self.assertEqual(max(r["data_as_of"] for r in rows), "2026-10-08")
        early_r = dt.date(2026, 10, 12)  # 10-10's SitRep is first published on 10-12
        rows = vintage.build_vintage(self.inputs["extract"], self.inputs["promotions"], early_r,
                                     dt.date(2026, 10, 10), extract_last_sitrep=self.inputs["extract_last_sitrep"])
        self.assertEqual(max(r["data_as_of"] for r in rows), "2026-10-09")


class RealDataCrossCheckTest(unittest.TestCase):
    """R3-I1 on the repository's own record: the 11-07 extract equals the promotions."""

    def test_extract_equals_promotions_where_both_exist(self) -> None:
        tree = source.DirTree(REPO)
        extract = vintage.load_extract(tree)
        promotions = [doc for _, doc, _ in source.load_promotions(tree)]
        report = vintage.cross_check(promotions, [], extract)
        self.assertEqual(report["mismatches"], [])
        self.assertGreater(report["extract_comparisons"], 1000)

    @unittest.skipUnless(PACKETS.is_dir(), "evidence packets not available locally")
    def test_packets_equal_promotions_where_both_exist(self) -> None:
        tree = source.DirTree(REPO)
        promotions = [doc for _, doc, _ in source.load_promotions(tree)]
        packets = [json.loads(Path(p).read_text(encoding="utf-8"))
                   for p in sorted(glob.glob(str(PACKETS / "sitrep-*.json"))) if "nopost" not in p]
        report = vintage.cross_check(promotions, packets, [])
        self.assertEqual(report["mismatches"], [])
        self.assertGreater(report["packet_comparisons"], 1000)

    def test_extract_is_the_pinned_file(self) -> None:
        tree = source.DirTree(REPO)
        rows = vintage.load_extract(tree)
        self.assertEqual(int(rows[-1]["sitrep"]), vintage.EXTRACT_LAST_SITREP)


class ExclusiveWriteTest(unittest.TestCase):
    def test_never_overwrites(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "vintage.json"
            vintage.write_exclusive(path, b"first")
            with self.assertRaises(FileExistsError):
                vintage.write_exclusive(path, b"second")
            self.assertEqual(path.read_bytes(), b"first")

    def test_module_never_replaces(self) -> None:
        for name in ("vintage.py", "register.py"):
            text = (REPO / "lovs" / "forecast" / "weekly" / name).read_text(encoding="utf-8")
            self.assertNotIn("os.replace", text)
            self.assertNotIn(".replace(tmp", text)


class SeedTest(unittest.TestCase):
    def test_deterministic(self) -> None:
        a = register.weekly_seed(R, "N1", spec.M_DRIFT, frozenset())
        self.assertEqual(a, register.weekly_seed(R, "N1", spec.M_DRIFT, frozenset()))
        self.assertNotEqual(a, register.weekly_seed(R, "N2", spec.M_DRIFT, frozenset()))

    def test_forced_collision_steps_the_counter(self) -> None:
        first = register.weekly_seed(R, "N1", spec.M_DRIFT, frozenset())
        second = register.weekly_seed(R, "N1", spec.M_DRIFT, frozenset({first}))
        self.assertNotEqual(first, second)
        third = register.weekly_seed(R, "N1", spec.M_DRIFT, frozenset({first, second}))
        self.assertNotIn(third, {first, second})
        self.assertEqual(second, register.weekly_seed(R, "N1", spec.M_DRIFT, frozenset({first})))

    def test_registered_seeds_are_taken(self) -> None:
        taken = register.registered_seeds(REPO / "data" / "operational-calibration-ledger.json")
        self.assertTrue(taken)
        # Blocks 14 and 15 are on main; their seeds must be in the taken set.
        ledger = json.loads((REPO / "data" / "operational-calibration-ledger.json").read_text(encoding="utf-8"))
        b14 = [p["generator"]["seed"] for b in ledger["blocks"] if b["block_id"].endswith("2026-11-18:response")
               for p in b["points"]]
        self.assertTrue(b14)
        self.assertTrue(set(b14) <= taken)


if __name__ == "__main__":
    unittest.main()
