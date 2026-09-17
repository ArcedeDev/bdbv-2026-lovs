"""The public rows for calibration Blocks 5, 6 and 7 are derived from the pinned ledgers."""
from __future__ import annotations

import json
import re
import unittest
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


if __name__ == "__main__":
    unittest.main()
