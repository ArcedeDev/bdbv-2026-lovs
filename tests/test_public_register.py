"""The public rows for calibration Blocks 5, 6 and 7, and for the 2026-06-04 block, are derived from the pinned ledgers."""
from __future__ import annotations

import json
import re
import unittest
import unittest.mock
from pathlib import Path

from lovs.forecast import pins_block8, public_register

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
        september = {pin for pin in public_register.axis_by_pin() if pin.split("-")[0] in ("OP6", "ST7")}
        self.assertEqual(set(pins), september)

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


class SeptemberBlockOutcomeTests(unittest.TestCase):
    """Blocks 5 to 7 resolved on 2026-10-03; each row carries its ledger point's outcome."""

    def test_each_resolved_row_is_its_ledger_outcome_and_nothing_more(self):
        rows = {row["ledger_id"]: row for row in _published_rows()}
        points = public_register._ledger_points()
        self.assertEqual(31, len(points))
        for ledger_id, _block_id, _block, point in points:
            row = rows[ledger_id]
            self.assertIn("outcome", point, ledger_id)
            self.assertEqual("resolved", row["status"], ledger_id)
            self.assertEqual({1: "yes", 0: "no"}[point["outcome"]], row["resolved_value"], ledger_id)
            self.assertEqual("", row["score_after_resolution"], ledger_id)
            self.assertTrue(row["resolution_note"], ledger_id)
            # The resolution note adds no probability or score to the row.
            self.assertIsNone(re.search(r"\d\.\d|%|\bp\s*=", row["resolution_note"]), ledger_id)

    def test_block_5_reads_six_noes(self):
        rows = {row["ledger_id"]: row for row in _published_rows()}
        corridor = [rows[i] for i, b, _, _ in public_register._ledger_points() if b == public_register.CORRIDOR_BLOCK_ID]
        self.assertEqual(["no"] * 6, [row["resolved_value"] for row in corridor])

    def test_an_open_pin_stays_open(self):
        block = {"pinned_at": "2026-09-01", "resolves_at": "2026-10-01T23:59:59Z"}
        self.assertEqual({}, public_register._resolution_fields(block, {"target": "x"}))


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
            self.assertIn("The reading against this outcome: INSP SitRep 47 reports one confirmed case in "
                          "Tshopo province, whose sample tested positive in Kisangani on 29-30 June", note)
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
        # An entry that states no name, such as a superseding recheck, leaves the name alone.
        feed = {"evidence": [{"target_zone": "x-cod", "target_name": "X"}, {"target_zone": "x-cod"}]}
        with unittest.mock.patch.object(public_register, "_load", return_value=feed):
            self.assertEqual({"x-cod": "X"}, public_register._target_names())



def _october_published_rows():
    record = json.loads(PUBLIC_RECORD.read_text(encoding="utf-8"))
    return [row for row in record["commitments"] if row["registered_at"] == pins_block8.PINNED_AT]


class OctoberRegisterTests(unittest.TestCase):
    """Blocks 8-10: derived from the ledger, numbered from 092, no probability."""

    def test_published_rows_are_exactly_what_the_ledger_derives(self):
        self.assertEqual(public_register.october_block_rows(), _october_published_rows())

    def test_rows_are_numbered_from_092_once_each(self):
        rows = _october_published_rows()
        ledger = json.loads(public_register.OPERATIONAL_LEDGER.read_text(encoding="utf-8"))
        expected = sum(len(b["points"]) for b in ledger["blocks"] if b["block_id"] in public_register.OCTOBER_BLOCK_IDS)
        self.assertEqual(len(rows), expected)
        self.assertEqual([row["ledger_id"] for row in rows],
                         [f"bdbv-2026-cal-{92 + i:03d}" for i in range(expected)])

    def test_every_october_pin_has_an_axis_and_a_side(self):
        axes = public_register.axis_by_pin()
        leans = public_register.lean_by_ledger_id()
        for row in _october_published_rows():
            self.assertIn(row["pin_id"], axes)
            self.assertEqual(leans[row["ledger_id"]][:2], ("pin_id", row["pin_id"]))

    def test_no_probability_reaches_the_october_rows(self):
        for row in _october_published_rows():
            text = json.dumps(row)
            self.assertIsNone(re.search(r"\d\.\d{2,}", text), row["ledger_id"])
            self.assertNotIn("probability", text, row["ledger_id"])

    def test_challenger_rows_say_they_are_not_the_forecast_of_record(self):
        for row in _october_published_rows():
            if row["control_role"] == "method_comparison_challenger":
                self.assertIn("not the programme's forecast of record", row["notes"])

    def test_the_site_refresh_carries_october_rows_only_after_registration(self):
        import refresh_pipeline

        before = refresh_pipeline.carry_forward_commitments("2026-10-03")["calibration_commitments"]
        self.assertFalse([r for r in before if r["registered_at"] == pins_block8.PINNED_AT])
        after = refresh_pipeline.carry_forward_commitments("2026-10-05")
        october = [r for r in after["calibration_commitments"] if r["registered_at"] == pins_block8.PINNED_AT]
        self.assertEqual(len(october), len(_october_published_rows()))
        self.assertTrue(all(r["axis"] and r["registered_side"] in ("yes", "no", "none") for r in october))
        self.assertEqual(after["commitments_resolves_at"], "2026-11-01")

    def test_rows_are_appended_once(self):
        with self.assertRaises(ValueError):
            public_register.append_october_rows()


if __name__ == "__main__":
    unittest.main()
