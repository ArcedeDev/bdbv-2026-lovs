"""Blocks 8, 9 and 10: generated, paired, blind to anything after the cutoff."""
from __future__ import annotations

import copy
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

from lovs.forecast import opsforecast as of
from lovs.forecast import opsresolver as ores
from lovs.forecast import pins_block8 as p8

LEDGER = p8.LEDGER
APPENDED_AFTER_PINNING = ("outcome", "resolved_as_of", "outcome_series", "resolution_provenance")


def _october_blocks(ledger):
    ids = {p8.BLOCK8_ID, p8.BLOCK9_ID, p8.BLOCK10_ID}
    return [b for b in ledger["blocks"] if b["block_id"] in ids]


def _strip_appended(block):
    """A pinned block as generated: drop only fields appended after pinning."""
    block = copy.deepcopy(block)
    block.get("registration", {}).pop("registration_receipt", None)
    for pin in block["points"]:
        for key in APPENDED_AFTER_PINNING:
            pin.pop(key, None)
    return block


class RegenerationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
        cls.generated = p8.build_blocks()

    def test_the_ledger_holds_exactly_what_the_generator_produces(self):
        committed = [_strip_appended(b) for b in _october_blocks(self.ledger)]
        self.assertEqual(committed, self.generated,
                         "a Block 8-10 field differs from its generator")

    def test_generation_is_deterministic(self):
        self.assertEqual(json.dumps(p8.build_blocks()), json.dumps(self.generated))

    def test_blocks_follow_block_7_with_their_labels(self):
        labels = [b.get("label") for b in self.ledger["blocks"]]
        self.assertEqual(self.ledger["blocks"][0]["block_id"], "operational-block:bdbv-uga-cod-2026:2026-09-01")
        self.assertEqual(labels[-3:], ["Block 8", "Block 9", "Block 10"])
        for block in _october_blocks(self.ledger):
            self.assertEqual(self.ledger["_meta"]["generators"][block["block_id"]], "lovs/forecast/pins_block8.py")


class PairingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.blocks = p8.build_blocks()

    def test_every_paired_question_is_priced_by_both_methods_at_one_threshold(self):
        for block, challenger in ((self.blocks[0], p8.DRIFT), (self.blocks[1], p8.RECENT_LEVELS)):
            by_question: dict[str, list[dict]] = {}
            for pin in block["points"]:
                by_question.setdefault(pin["question_id"], []).append(pin)
            for question, pins in by_question.items():
                self.assertEqual(sorted(p["method"] for p in pins), sorted([p8.INCUMBENT, challenger]), question)
                self.assertEqual(len({p["threshold"] for p in pins}), 1, question)
                self.assertEqual({p["role"] for p in pins}, {"incumbent", "challenger"}, question)

    def test_metric_counts_match_the_decision_rule_denominators(self):
        self.assertEqual(len({p["metric"] for p in self.blocks[0]["points"]}), 4)
        self.assertEqual(len({p["metric"] for p in self.blocks[1]["points"]}), 9)
        self.assertIn("at least 3 of the 4 metrics", self.blocks[0]["pre_registration"]["decision_rule"])
        self.assertIn("at least 6 of the 9 metrics", self.blocks[1]["pre_registration"]["decision_rule"])

    def test_prices_rise_with_the_threshold_within_a_metric_and_method(self):
        for block in self.blocks[:2]:
            series: dict[tuple, list[tuple]] = {}
            for pin in block["points"]:
                series.setdefault((pin["metric"], pin["method"]), []).append((pin["threshold"], pin["probability"]))
            for key, pairs in series.items():
                prices = [p for _, p in sorted(pairs)]
                self.assertEqual(prices, sorted(prices), key)

    def test_low_count_rungs_rise_with_the_count(self):
        rungs = [p for p in self.blocks[2]["points"] if p["method"] == "termination_bootstrap"]
        prices = [p["probability"] for p in sorted(rungs, key=lambda p: p["threshold"])]
        self.assertEqual(prices, sorted(prices))
        self.assertEqual([p["threshold"] for p in sorted(rungs, key=lambda p: p["threshold"])],
                         list(p8.LOW_COUNT_RUNGS))

    def test_no_pin_carries_a_bias_test_flag_or_an_unknown_role(self):
        for block in self.blocks:
            for pin in block["points"]:
                self.assertFalse(pin["bias_test"])
                self.assertIn(pin["role"], ("incumbent", "challenger", "record"))


class IdentityTests(unittest.TestCase):
    def test_pin_ids_are_unique_across_the_whole_ledger(self):
        ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
        ids = [p["pin_id"] for b in ledger["blocks"] for p in b["points"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_seeds_avoid_every_earlier_block_and_each_other(self):
        seeds: dict[int, set] = {}
        for block in p8.build_blocks():
            for pin in block["points"]:
                seeds.setdefault(pin["generator"]["seed"], set()).add((pin["metric"], pin["method"]))
        self.assertFalse(set(seeds) & p8.RESERVED_SEEDS)
        for seed, users in seeds.items():
            self.assertEqual(len(users), 1, f"seed {seed} shared by {users}")

    def test_a_derived_seed_on_a_reserved_value_is_refused(self):
        original = p8.RESERVED_SEEDS
        try:
            p8.RESERVED_SEEDS = frozenset({p8.derived_seed("block8", "zones", p8.DRIFT)})
            with self.assertRaises(p8.RegistrationError):
                p8.derived_seed("block8", "zones", p8.DRIFT)
        finally:
            p8.RESERVED_SEEDS = original


class BlindingTests(unittest.TestCase):
    """The registration fails closed if its inputs or timing move."""

    def test_the_substrate_is_byte_for_byte_the_registered_one(self):
        self.assertTrue(p8.load_substrate())
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "series.json"
            path.write_bytes(p8.SUBSTRATE.read_bytes() + b" ")
            with self.assertRaises(p8.RegistrationError):
                p8.load_substrate(path)

    def test_an_input_row_past_the_cutoff_is_refused(self):
        rows = p8.load_substrate()
        later = dict(rows[-1], data_as_of="2026-10-02", published_at="2026-10-04", sitrep="141")
        with self.assertRaises(p8.RegistrationError):
            p8.build_blocks(rows + [later])

    def test_window_opens_the_utc_day_after_the_deadline(self):
        deadline = dt.datetime.fromisoformat(p8.REGISTRATION_DEADLINE_UTC.replace("Z", "+00:00"))
        self.assertEqual(dt.date.fromisoformat(p8.WINDOW_OPENS), deadline.date() + dt.timedelta(days=1))
        self.assertLessEqual(dt.date.fromisoformat(p8.PINNED_AT), deadline.date())
        for block in p8.build_blocks():
            self.assertEqual(block["window_opens"], p8.WINDOW_OPENS)
            self.assertEqual(block["registration"]["inputs"],
                             [{"path": p8.SUBSTRATE_RELATIVE, "sha256": p8.SUBSTRATE_SHA256}])

    def test_a_moved_window_is_refused(self):
        original = p8.WINDOW_OPENS
        try:
            p8.WINDOW_OPENS = p8.PINNED_AT
            with self.assertRaises(p8.RegistrationError):
                p8.check_timing()
        finally:
            p8.WINDOW_OPENS = original

    def test_a_recorded_receipt_is_no_later_than_the_deadline(self):
        def utc(text):
            return dt.datetime.fromisoformat(text.replace("Z", "+00:00"))

        ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
        for block in _october_blocks(ledger):
            receipt = block["registration"].get("registration_receipt")
            if receipt is None:
                continue
            self.assertLessEqual(utc(receipt["public_at_utc"]),
                                 utc(block["registration"]["registration_deadline_utc"]))

    def test_no_pin_is_resolvable_from_the_substrate(self):
        """Every question reads days after the substrate ends, so it can only be pending or stale."""
        ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
        rows = p8.load_substrate()
        newest = max(dt.date.fromisoformat(r["data_as_of"][:10]) for r in rows)
        self.assertLess(newest, dt.date.fromisoformat(p8.WINDOW_OPENS))
        for as_of in (dt.date(2026, 10, 15), dt.date(2026, 11, 5)):
            report = ores.build_report(ledger, rows, as_of)
            for pin in report["pins"]:
                if pin["block_id"] in (p8.BLOCK8_ID, p8.BLOCK9_ID, p8.BLOCK10_ID):
                    self.assertIn(pin["status"], (ores.STATUS_PENDING, ores.STATUS_STALE), pin["pin_id"])


class WindowTests(unittest.TestCase):
    """The resolver reads Block 10's window from window_opens, as the generator priced it."""

    def _rows(self, low_day: dt.date, low_value: float):
        rows = list(p8.load_substrate())
        day = dt.date(2026, 10, 2)
        while day <= dt.date(2026, 11, 2):
            rows.append({"sitrep": f"t{day}", "data_as_of": day.isoformat(),
                         "published_at": (day + dt.timedelta(days=1)).isoformat(),
                         "new_confirmed_today": low_value if day == low_day else 50.0})
            day += dt.timedelta(days=1)
        return rows

    def _zero_rung(self, rows):
        ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
        block = next(b for b in ledger["blocks"] if b["block_id"] == p8.BLOCK10_ID)
        pin = next(p for p in block["points"] if p["question_id"] == "block10:newcases-any-le-0")
        return ores.resolve_pin(pin, block, rows, dt.date(2026, 11, 2))

    def test_a_zero_day_before_the_window_does_not_count(self):
        before = dt.date.fromisoformat(p8.WINDOW_OPENS) - dt.timedelta(days=1)
        got = self._zero_rung(self._rows(before, 0.0))
        self.assertEqual(got["status"], ores.STATUS_NO)
        self.assertEqual(got["window_opens"], p8.WINDOW_OPENS)

    def test_a_zero_day_inside_the_window_does(self):
        got = self._zero_rung(self._rows(dt.date.fromisoformat(p8.WINDOW_OPENS), 0.0))
        self.assertEqual(got["status"], ores.STATUS_YES)

    def test_window_opens_outside_the_block_is_refused(self):
        ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
        block = dict(next(b for b in ledger["blocks"] if b["block_id"] == p8.BLOCK10_ID),
                     window_opens="2026-11-05")
        with self.assertRaises(ValueError):
            ores.resolve_pin(block["points"][0], block, self._rows(dt.date(2026, 10, 9), 50.0),
                             dt.date(2026, 11, 2))

    def test_blocks_without_window_opens_read_from_the_pin_date(self):
        ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
        seven = next(b for b in ledger["blocks"] if b.get("label") == "Block 7")
        self.assertNotIn("window_opens", seven)
        rows = of.load_rows(of.DEFAULT_SERIES)
        got = ores.resolve_pin(seven["points"][0], seven, rows, dt.date(2026, 9, 15))
        self.assertEqual(got["window_opens"], seven["pinned_at"])



class VoidRuleTests(unittest.TestCase):
    """A cumulative question already settled by a pre-registration publication is void."""

    def _rows(self, value, published):
        rows = list(p8.load_substrate())
        rows.append({"sitrep": "901", "data_as_of": "2026-10-02", "published_at": published,
                     "confirmed_total": value})
        return rows

    def _block8(self):
        return next(b for b in p8.build_blocks() if b["block_id"] == p8.BLOCK8_ID)

    def test_a_total_past_a_threshold_before_registration_voids_that_question(self):
        block = self._block8()
        lowest = min(p["threshold"] for p in block["points"] if p["metric"] == "confirmed_total")
        void = p8.questions_decided_before(block, self._rows(lowest + 1, "2026-10-03"), dt.date(2026, 10, 4))
        self.assertIn(f"block8:confirmed-le-{lowest}", void)

    def test_a_publication_after_registration_voids_nothing(self):
        block = self._block8()
        lowest = min(p["threshold"] for p in block["points"] if p["metric"] == "confirmed_total")
        self.assertEqual(p8.questions_decided_before(block, self._rows(lowest + 1, "2026-10-06"), dt.date(2026, 10, 5)), [])

    def test_the_substrate_alone_voids_nothing(self):
        for block in p8.build_blocks():
            self.assertEqual(p8.questions_decided_before(block, p8.load_substrate(), dt.date(2026, 10, 5)), [])


class ResolverHeadlineTests(unittest.TestCase):
    def test_challenger_pins_stay_out_of_the_resolver_headline(self):
        ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
        rows = list(p8.load_substrate())
        day = dt.date(2026, 10, 2)
        while day <= dt.date(2026, 11, 2):
            rows.append({"sitrep": f"s{day}", "data_as_of": day.isoformat(),
                         "published_at": (day + dt.timedelta(days=1)).isoformat(),
                         "confirmed_total": 10400.0, "confirmed_deaths_total": 5100.0,
                         "cumulative_recovered": 2900.0, "health_zones_touched": 66.0})
            day += dt.timedelta(days=1)
        summary = ores.build_report(ledger, rows, dt.date(2026, 11, 2))["summary"]
        trajectory = summary["by_block"][p8.BLOCK8_ID]
        self.assertEqual(trajectory["challenger"]["resolved"], trajectory["incumbent"]["resolved"])
        self.assertEqual(summary["challenger_resolved_count"],
                         sum(roles.get("challenger", {}).get("resolved", 0) for roles in summary["by_block"].values()))
        self.assertEqual(summary["resolved_count"],
                         sum(cell["resolved"] for roles in summary["by_block"].values()
                             for role, cell in roles.items() if role != "challenger"))
        self.assertNotIn("independent", summary["reliability_note"].replace("not independent", ""))


if __name__ == "__main__":
    unittest.main()
