# SPDX-License-Identifier: Apache-2.0
"""Every carried calibration pin states which outcome it was registered to expect.

A resolved pin went "as registered" only when its outcome matches its registered side,
and for a pin registered to resolve NO that is a NO. Labelling every YES as registered
mislabelled 12 of the 41 pins in the 2026-07-05 block. These tests pin the side of each
carried pin to the rule that fixed it, and hold the public surface to the side alone,
never the probability behind it.
"""
from __future__ import annotations

import json
import pathlib
import tempfile
import unittest
from unittest import mock

import calibration_resolver
import refresh_pipeline
from lovs.forecast import public_register, registered_side

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
PUBLIC_RECORD = REPO_ROOT / "data" / "public_calibration_commitments.json"
CORRIDOR_LEDGER = REPO_ROOT / "data" / "calibration-ledger.json"
OPERATIONAL_LEDGER = REPO_ROOT / "data" / "operational-calibration-ledger.json"
AS_OF = "2026-09-24T00:00:00Z"

# Independent witness, hard-coded on purpose: the side of every 2026-07-05 pin as scored
# in the Block 4 results note (score_block4.py, Section 5.3 of the Zenodo
# pre-registration), keyed by ledger number. The test must not read the value it checks
# from the code under test.
BLOCK4_SIDES = {
    "016": "yes", "017": "none", "018": "none", "019": "yes", "020": "no",
    "021": "none", "022": "none", "023": "none", "024": "yes", "025": "yes",
    "026": "yes", "027": "yes", "028": "no", "029": "no", "030": "yes",
    "031": "yes", "032": "no", "033": "no", "034": "yes", "035": "yes",
    "036": "no", "037": "yes", "038": "yes", "039": "no", "040": "yes",
    "041": "yes", "042": "no", "043": "yes", "044": "yes", "045": "no",
    "046": "yes", "047": "yes", "048": "yes", "049": "yes", "050": "yes",
    "051": "yes", "052": "yes", "053": "yes", "054": "yes", "055": "yes",
    "056": "yes",
}

# The evaluated pins whose label changes when judged against the registered side rather
# than "YES means as registered": three mid-tier pins carry no side, two YES outcomes
# went against a NO registration, and six NO outcomes went as registered.
NAIVE_RULE_MISLABELS = {
    "017": "none", "018": "none", "021": "none",
    "020": "no", "036": "no",
    "028": "no", "029": "no", "032": "no", "033": "no", "042": "no", "045": "no",
}


def _carried() -> list[dict]:
    return refresh_pipeline.carry_forward_commitments(AS_OF)["calibration_commitments"]


def _floats(value) -> list[float]:
    if isinstance(value, bool):
        return []
    if isinstance(value, float):
        return [value]
    if isinstance(value, dict):
        return [f for item in value.values() for f in _floats(item)]
    if isinstance(value, list):
        return [f for item in value for f in _floats(item)]
    return []


class CarriedSideTests(unittest.TestCase):
    def test_every_carried_pin_has_a_side(self):
        carried = _carried()
        self.assertEqual(66, len(carried), "41 Block 4 pins plus 25 Blocks 6 and 7 pins")
        for pin in carried:
            self.assertIn(pin.get("registered_side"), registered_side.SIDES, pin["ledger_id"])

    def test_block4_pins_the_naive_rule_mislabels_have_their_registered_side(self):
        by_number = {pin["ledger_id"][-3:]: pin for pin in _carried()}
        for number, side in NAIVE_RULE_MISLABELS.items():
            self.assertEqual(side, by_number[number]["registered_side"], f"cal-{number}")
        # cal-039 is the twelfth: it resolved YES against a NO registration, and is
        # excluded from evaluation as pre-disclosed rather than relabelled.
        self.assertEqual("no", by_number["039"]["registered_side"])
        self.assertEqual("excluded_pre_disclosed", by_number["039"]["evaluation_status"])

    def test_block4_sides_match_the_results_note_and_give_25_of_35(self):
        block4 = [pin for pin in _carried() if pin["registered_at"] == "2026-07-05"]
        self.assertEqual(BLOCK4_SIDES, {pin["ledger_id"][-3:]: pin["registered_side"] for pin in block4})
        evaluated = [pin for pin in block4 if pin.get("evaluation_status") == "evaluated"]
        sided = [pin for pin in evaluated if pin["registered_side"] != "none"]
        as_registered = [pin for pin in sided if pin["resolved_value"] == pin["registered_side"]]
        self.assertEqual((38, 35, 25), (len(evaluated), len(sided), len(as_registered)))

    def test_no_emitted_commitment_carries_a_probability(self):
        allowed = set(refresh_pipeline._COMMITMENT_PUBLIC_FIELDS) | {"axis", "registered_side"}
        pinned = list(_corridor_probabilities().values()) + list(_operational_probabilities().values())
        self.assertEqual(31, len(pinned))
        for pin in _carried():
            self.assertLessEqual(set(pin), allowed, pin["ledger_id"])
            self.assertEqual([], _floats(pin), pin["ledger_id"])
            text = json.dumps(pin)
            for probability in pinned:
                self.assertNotIn(f"{probability:g}", text, pin["ledger_id"])
                self.assertNotIn(repr(probability), text, pin["ledger_id"])


def _corridor_probabilities() -> dict[str, float]:
    """Block 5 point probability by target, read straight off the corridor ledger."""
    corridor = next(
        block for block in json.loads(CORRIDOR_LEDGER.read_text(encoding="utf-8"))["blocks"]
        if block["pinned_at"] == "2026-09-01"
    )
    return {
        point["target"]: calibration_resolver.midpoint(point["risk_adj_50"])
        for point in corridor["points"]
    }


def _operational_probabilities() -> dict[str, float]:
    """Blocks 6 and 7 pinned probability by public pin id, read straight off the ledger."""
    out = {}
    for block in json.loads(OPERATIONAL_LEDGER.read_text(encoding="utf-8"))["blocks"]:
        prefix = "ST7" if block["block_id"].endswith(":structural") else "OP6"
        for pin in block["points"]:
            out[f"{prefix}-{pin['pin_id'].split(':')[-1]}"] = pin["probability"]
    return out


# The lean each Blocks 5-7 public question states, in the question's own words.
_QUESTION_SIDE = {
    "the registered forecast leans YES": "yes",
    "The registered forecast leans YES.": "yes",
    "The registered forecast leans NO.": "no",
    "The registered forecast is close to even.": "none",
}


def _side_stated_in(question: str) -> str:
    stated = {side for phrase, side in _QUESTION_SIDE.items() if phrase in question}
    if len(stated) != 1:
        raise AssertionError(f"question states {len(stated)} leans: {question!r}")
    return stated.pop()


class LaterBlockSideTests(unittest.TestCase):
    """Blocks 5 to 7 take the side their public question registered."""

    def _rows(self) -> list[dict]:
        return [
            row for row in json.loads(PUBLIC_RECORD.read_text(encoding="utf-8"))["commitments"]
            if row["registered_at"] == "2026-09-01"
        ]

    def test_each_side_is_the_lean_its_public_question_states(self):
        rows = self._rows()
        self.assertEqual(31, len(rows))
        for row in rows:
            self.assertEqual(
                _side_stated_in(row["public_question"]),
                registered_side.registered_side(row),
                row["ledger_id"],
            )

    def test_sides_rederive_from_the_pinned_ledgers(self):
        # The same lean, recomputed from the ledgers read here, joined by pin id and
        # target rather than through public_register's numbering.
        corridor_p, operational_p = _corridor_probabilities(), _operational_probabilities()
        sides = {}
        for row in self._rows():
            key = row.get("pin_id", row["target_geography"])
            p = operational_p[key] if "pin_id" in row else corridor_p[key]
            sides[key] = registered_side.registered_side(row)
            self.assertEqual(registered_side.LEAN_SIDE[public_register.lean(p)], sides[key], row["ledger_id"])

        self.assertEqual({"yes"}, {sides[target] for target in corridor_p})
        blocks_6_7 = [sides[pin_id] for pin_id in operational_p]
        self.assertEqual((9, 12, 4), tuple(blocks_6_7.count(side) for side in ("yes", "no", "none")))

    def test_close_to_even_pins_carry_no_side(self):
        # Four pins sit near 0.5 on either side, and each public question says "is close to
        # even". A reader was told no side, so none is attached after the fact.
        close_to_even = {
            "OP6-lab-above-20",
            "ST7-lab-throughput-700",
            "ST7-nk-isolation-300",
            "ST7-cadence-arrivals-at-least-29",
        }
        later = {
            pin["pin_id"]: pin["registered_side"]
            for pin in _carried()
            if pin["registered_at"] == "2026-09-01"
        }
        self.assertEqual(close_to_even, {pin_id for pin_id, side in later.items() if side == "none"})

    def test_every_lean_the_register_can_write_has_a_side(self):
        self.assertEqual(
            {public_register.lean(p) for p in (0.1, 0.5, 0.9)},
            set(registered_side.LEAN_SIDE),
        )


class FailLoudTests(unittest.TestCase):
    def test_an_unmapped_pin_raises_rather_than_defaulting(self):
        with self.assertRaises(ValueError):
            registered_side.registered_side({"ledger_id": "bdbv-2026-cal-088", "pin_id": "OP8-x"})
        with self.assertRaises(ValueError):
            registered_side.registered_side({"ledger_id": "cal-016"})
        # A Block 4 threshold pin whose sign Section 5.3 does not fix.
        with self.assertRaises(ValueError):
            registered_side.registered_side(
                {"ledger_id": "bdbv-2026-cal-024", "public_value_or_tier": "threshold:>=1"}
            )
        # A later-block row naming a different pin than the ledger does at that id.
        with self.assertRaises(ValueError):
            registered_side.registered_side({"ledger_id": "bdbv-2026-cal-063", "pin_id": "OP6-zones-above-70"})

    def test_the_carry_forward_refuses_a_pin_without_a_side(self):
        record = json.loads(PUBLIC_RECORD.read_text(encoding="utf-8"))
        stray = dict(next(row for row in record["commitments"] if row.get("pin_id") == "SP1"))
        stray["ledger_id"] = "bdbv-2026-cal-099"
        record["commitments"].append(stray)
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "commitments.json"
            path.write_text(json.dumps(record), encoding="utf-8")
            with mock.patch.object(refresh_pipeline, "PUBLIC_COMMITMENTS_PATH", path):
                with self.assertRaisesRegex(ValueError, "bdbv-2026-cal-099"):
                    refresh_pipeline.carry_forward_commitments(AS_OF)


if __name__ == "__main__":
    unittest.main()
