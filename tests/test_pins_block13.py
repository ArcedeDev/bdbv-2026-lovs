# SPDX-License-Identifier: Apache-2.0
"""Block 13 (trajectory): the second paired test of the 28-day drift method."""
from __future__ import annotations

import copy
import datetime as dt
import json
import unittest

from lovs.forecast import opsresolver, pins_block8 as p8, pins_block13 as p13
from lovs import forecast_scoring as fs

APPENDED_AT_RESOLUTION = ("outcome", "resolved_as_of", "outcome_series", "resolution_provenance")


def _ledger() -> dict:
    return json.loads(p13.LEDGER.read_text(encoding="utf-8"))


def _ledger_block() -> dict:
    return next(b for b in _ledger()["blocks"] if b["block_id"] == p13.BLOCK13_ID)


class GenerationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.block = p13.build_block()

    def test_committed_block_regenerates_exactly(self) -> None:
        block = _ledger_block()
        block["registration"].pop("registration_receipt", None)
        for pin in block["points"]:
            for key in APPENDED_AT_RESOLUTION:
                pin.pop(key, None)
        self.assertEqual(block, self.block)

    def test_substrate_is_hash_pinned_and_cut_off(self) -> None:
        rows = p13.load_substrate()
        late = copy.deepcopy(rows) + [dict(rows[-1], data_as_of="2026-11-08", published_at="2026-11-10")]
        with self.assertRaises(p13.RegistrationError):
            p13.build_block(late)

    def test_methods_and_question_shape_are_block_8s(self) -> None:
        for name in ("HORIZON_DAYS", "LEVELS", "PATHS", "BLOCK", "DRIFT_DAYS"):
            self.assertEqual(getattr(p8, name), getattr(p13, name), name)
        self.assertEqual([(m.key, m.series, m.step, m.kind) for m in p8.BLOCK8_METRICS],
                         [(m.key, m.series, m.step, m.kind) for m in p13.BLOCK13_METRICS])
        for m in p13.BLOCK13_METRICS:
            self.assertIn("on or before 2026-12-08", m.question)

    def test_roles_follow_block_8s_rule(self) -> None:
        roles = {(p["method"], p["role"]) for p in self.block["points"]}
        self.assertEqual({(p13.DRIFT, "incumbent"), (p13.LEVEL, "challenger")}, roles)

    def test_each_question_is_paired_at_one_threshold(self) -> None:
        by_q: dict[str, list[dict]] = {}
        for pin in self.block["points"]:
            by_q.setdefault(pin["question_id"], []).append(pin)
        for qid, pins in by_q.items():
            self.assertEqual({p13.DRIFT, p13.LEVEL}, {p["method"] for p in pins}, qid)
            self.assertEqual(1, len({p["threshold"] for p in pins}), qid)

    def test_prices_rise_with_the_threshold(self) -> None:
        for method in (p13.DRIFT, p13.LEVEL):
            for metric in {p["metric"] for p in self.block["points"]}:
                ladder = sorted((p["threshold"], p["probability"]) for p in self.block["points"]
                                if p["method"] == method and p["metric"] == metric)
                self.assertEqual([x[1] for x in ladder], sorted(x[1] for x in ladder), (method, metric))

    def test_seeds_are_new_and_pin_ids_unique_across_the_ledger(self) -> None:
        earlier = {pin["generator"].get("seed") for b in _ledger()["blocks"] if b["block_id"] != p13.BLOCK13_ID
                   for pin in b["points"]}
        seeds = {pin["generator"]["seed"] for pin in self.block["points"]}
        self.assertFalse(seeds & earlier)
        ids = [pin["pin_id"] for b in _ledger()["blocks"] for pin in b["points"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_no_question_is_decided_by_sitrep_178(self) -> None:
        # SitRep 178 (data 2026-11-08) was public before the pin date but is not substrate.
        sr178 = {"confirmed_total": 10990, "confirmed_deaths_total": 5354, "cumulative_recovered": 2951,
                 "health_zones_touched": 67}
        for pin in self.block["points"]:
            self.assertGreater(pin["threshold"], sr178[pin["metric"]], pin["pin_id"])

    def test_decision_rule_names_its_evaluation(self) -> None:
        rule = self.block["pre_registration"]["decision_rule"]
        self.assertIn("paired_block_decision(need=3, of_metrics=4)", rule)
        self.assertIn("level bootstrap returns", rule)
        # Passing the level bootstrap first makes the difference drift minus level, Block 8's sign.
        diff = fs.paired_metric_differences([("m", 0.8, 0.2, 0)])
        self.assertLess(diff["m"], 0)


class ResolutionTest(unittest.TestCase):
    def test_pending_then_resolves_from_a_covering_series(self) -> None:
        block = _ledger_block()
        pin = block["points"][0]
        rows = p13.load_substrate()
        self.assertEqual(opsresolver.STATUS_PENDING,
                         opsresolver.resolve_pin(pin, block, rows, dt.date(2026, 12, 8))["status"])
        stale = opsresolver.resolve_pin(pin, block, rows, dt.date(2026, 12, 9))
        self.assertEqual(opsresolver.STATUS_STALE, stale["status"])
        last = dict(rows[-1], data_as_of="2026-12-08", published_at="2026-12-10", sitrep="208",
                    confirmed_total=pin["threshold"])
        got = opsresolver.resolve_pin(pin, block, rows + [last], dt.date(2026, 12, 11))
        self.assertEqual(opsresolver.STATUS_YES, got["status"])

    def test_void_rule_finds_questions_decided_before_the_receipt(self) -> None:
        block = _ledger_block()
        rows = p13.load_substrate()
        self.assertEqual([], p8.questions_decided_before(block, rows, dt.date(2026, 11, 12)))
        early = dict(rows[-1], data_as_of="2026-11-10", published_at="2026-11-12", sitrep="180",
                     confirmed_total=13000)
        decided = p8.questions_decided_before(block, rows + [early], dt.date(2026, 11, 12))
        self.assertIn("block13:confirmed-le-12650", decided)
        self.assertNotIn("block13:confirmed-le-13050", decided)


if __name__ == "__main__":
    unittest.main()
