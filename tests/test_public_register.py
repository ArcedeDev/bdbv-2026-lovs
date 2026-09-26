"""The public rows for calibration Blocks 5, 6 and 7, and for the 2026-06-04 block, are derived from the pinned ledgers."""
from __future__ import annotations

import json
import re
import unittest
import unittest.mock
from pathlib import Path

from lovs.forecast import public_register

REPO_ROOT = Path(__file__).resolve().parents[1]
PUBLIC_RECORD = REPO_ROOT / "data" / "public_calibration_commitments.json"


def _published_rows() -> list[dict]:
    record = json.loads(PUBLIC_RECORD.read_text(encoding="utf-8"))
    return [row for row in record["commitments"] if row["registered_at"] == "2026-09-01"]


class PublicRegisterTests(unittest.TestCase):
    def test_published_rows_are_exactly_what_the_ledgers_derive(self):
        # A hand edit to a question, threshold or tier in the public record fails here.
        self.assertEqual(public_register.public_rows(), _published_rows())

    def test_all_thirty_one_pins_are_registered_once(self):
        rows = _published_rows()
        self.assertEqual(31, len(rows))
        self.assertEqual(31, len({row["ledger_id"] for row in rows}))
        pins = [row["pin_id"] for row in rows if "pin_id" in row]
        self.assertEqual(25, len(pins))
        self.assertEqual(set(pins), set(public_register.axis_by_pin()))

    def test_no_probability_reaches_the_public_record(self):
        for row in _published_rows():
            text = json.dumps(row)
            self.assertIsNone(re.search(r"\d\.\d{2,}", text), row["ledger_id"])
            self.assertNotIn("p=", text, row["ledger_id"])

    def test_every_row_states_its_publication_date(self):
        for row in _published_rows():
            self.assertEqual("2026-09-17", row["first_published_at"], row["ledger_id"])
            self.assertIn("first published 2026-09-17", row["notes"], row["ledger_id"])

    def test_lean_never_prints_a_number(self):
        self.assertEqual("leans NO", public_register.lean(0.1285))
        self.assertEqual("leans YES", public_register.lean(0.7443))
        self.assertEqual("is close to even", public_register.lean(0.5))



CORRIDOR_LEDGER = REPO_ROOT / "data" / "calibration-ledger.json"


def _june_rows() -> list[dict]:
    record = json.loads(PUBLIC_RECORD.read_text(encoding="utf-8"))
    return [row for row in record["commitments"] if row["registered_at"] == "2026-06-04"]


def _june_points() -> list[dict]:
    ledger = json.loads(CORRIDOR_LEDGER.read_text(encoding="utf-8"))
    block = next(b for b in ledger["blocks"] if b["block_id"] == public_register.JUNE_BLOCK_ID)
    return block["points"]


class JuneBlockRowTests(unittest.TestCase):
    """The 2026-06-04 block entered the public record on 2026-09-26, resolved, from the ledger."""

    def test_published_rows_are_exactly_what_the_ledger_derives(self):
        self.assertEqual(public_register.june_block_rows(), _june_rows())

    def test_four_rows_088_to_091_in_ledger_order(self):
        rows = _june_rows()
        self.assertEqual([f"bdbv-2026-cal-{n:03d}" for n in range(88, 92)], [row["ledger_id"] for row in rows])
        self.assertEqual([p["target"] for p in _june_points()], [row["target_geography"] for row in rows])

    def test_each_outcome_is_the_ledger_outcome(self):
        # Founder ruling 2026-09-26: kisangani-cod resolves on zone-attributed counts, so all four are NO.
        for row, point in zip(_june_rows(), _june_points()):
            self.assertEqual({1: "yes", 0: "no"}[point["outcome"]], row["resolved_value"], row["ledger_id"])
            self.assertEqual("resolved", row["status"], row["ledger_id"])
        self.assertEqual(["no", "no", "no", "no"], [row["resolved_value"] for row in _june_rows()])

    def test_no_probability_no_pin_and_no_side(self):
        # No decimal at all ("0.5", ".54", "[36.6, 71.3]"), no percentage in any spelling,
        # and no "p=", so no form of a band or its midpoint can reach these rows.
        for row, point in zip(_june_rows(), _june_points()):
            text = json.dumps(row)
            self.assertIsNone(re.search(r"\.\d", text), row["ledger_id"])
            self.assertIsNone(re.search(r"%|percent|\bp\s*=", text, re.IGNORECASE), row["ledger_id"])
            lo, hi = point["risk_adj_50"]
            for value in (lo, hi, (lo + hi) / 2):
                self.assertIsNone(re.search(rf"\b{round(value * 100)}\b", text), (row["ledger_id"], value))
            self.assertNotIn("pin_id", row)
            self.assertNotIn("registered_side", row)

    def test_row_text_is_plain_ascii(self):
        # Every value is printable ASCII, so no look-alike letter or invisible character can
        # carry a marker or a number past a text check.
        for row in _june_rows():
            for key, value in row.items():
                for text in value if isinstance(value, list) else [value]:
                    if isinstance(text, str):
                        self.assertTrue(all(" " <= ch <= "~" for ch in text), (row["ledger_id"], key))

    def test_every_row_states_its_publication_date_and_provenance(self):
        for row in _june_rows():
            self.assertEqual("2026-06-12", row["first_published_at"], row["ledger_id"])
            for needle in ("first published in this repository 2026-06-12", "571ab58", "770327d", "2026-06-12T22:20:12Z",
                           "bdbv-sitrep25-build", "4e489e7"):
                self.assertIn(needle, row["notes"], row["ledger_id"])

    def test_the_corrected_rows_disclose_the_correction(self):
        corrected = [row for row in _june_rows() if row["target_geography"] == "kisangani-cod"]
        self.assertEqual(2, len(corrected))
        for row in corrected:
            note = row["resolution_note"]
            self.assertIn("Corrected on 2026-09-26 from YES to NO", note)
            self.assertIn("from 7 YES / 12 NO to 5 YES / 14 NO", note)
            self.assertIn("in the model's favour", note)
            self.assertEqual("insp-zone-attribution-kisangani-2026-09-26", row["resolution_evidence_source_ids"][0])
            # The strongest reading against a correction that favours the model travels with it.
            self.assertIn("The reading against this outcome: INSP SitRep 47 reports a sample that tested "
                          "positive in Kisangani on 29-30 June", note)
            self.assertIn("SitRep 58", note)
            self.assertIn("the YES could stand", note)

    def test_a_duplicate_block_or_a_disputed_name_raises(self):
        block = {"block_id": public_register.JUNE_BLOCK_ID, "points": []}
        with self.assertRaisesRegex(ValueError, "2 blocks with id"):
            public_register._block({"blocks": [block, dict(block)]}, public_register.JUNE_BLOCK_ID)
        feed = {"evidence": [{"target_zone": "x-cod", "target_name": "X"}, {"target_zone": "x-cod", "target_name": "Y"}]}
        with unittest.mock.patch.object(public_register, "_load", return_value=feed):
            with self.assertRaisesRegex(ValueError, "disagree on target_name"):
                public_register._target_names()


if __name__ == "__main__":
    unittest.main()
