# SPDX-License-Identifier: Apache-2.0
"""Block 12 (international spread, second of the monthly series): generation and resolution."""
from __future__ import annotations

import copy
import datetime as dt
import json
import unittest

from lovs.forecast import opsresolver, pins_block11 as p11, pins_block12 as p12

LEDGER = p12.LEDGER
APPENDED_AT_RESOLUTION = ("outcome", "resolved_as_of", "outcome_reports", "coverage_reviewed_at",
                          "resolution_provenance", "resolution_basis")


def _ledger_block() -> dict:
    ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
    return next(b for b in ledger["blocks"] if b["block_id"] == p12.BLOCK12_ID)


def _report(rid, country, reported, confirmed=None, cumulative=1, new=1):
    return {"report_id": rid, "country": country, "reported_on": reported, "confirmed_on": confirmed,
            "cumulative_confirmed": cumulative, "new_confirmed": new, "sources": [{"publisher": "test"}]}


class GenerationTest(unittest.TestCase):
    def test_committed_block_regenerates_exactly(self) -> None:
        block = _ledger_block()
        block["registration"].pop("registration_receipt", None)
        for pin in block["points"]:
            for key in APPENDED_AT_RESOLUTION:
                pin.pop(key, None)
        self.assertEqual(block, p12.build_block())

    def test_generation_is_deterministic(self) -> None:
        self.assertEqual(json.dumps(p12.build_block()), json.dumps(p12.build_block()))

    def test_substrate_hash_is_pinned_and_fails_closed(self) -> None:
        registry = p11.load_registry()
        self.assertEqual(p12.SUBSTRATE_SHA256, p12.substrate_digest(registry))
        tampered = copy.deepcopy(registry)
        target = next(r for r in tampered["reports"] if r["report_id"] == "nga-2026-10-20")
        target["reported_on"] = "2026-10-21"
        with self.assertRaises(p12.RegistrationError):
            p12.build_block(tampered)

    def test_reports_and_transfers_recorded_later_do_not_move_the_prices(self) -> None:
        registry = p11.load_registry()
        later = copy.deepcopy(registry)
        # Frozen by report id: a report dated on the cutoff but appended after the freeze, a
        # later report and a later transfer leave every price unchanged.
        later["reports"] += [_report("test-cutoff-day", "SSD", "2026-11-10"),
                             _report("test-later", "RWA", "2026-11-20")]
        later["medical_evacuations"].append({"country": "ESP", "on": "2026-11-21", "note": "test"})
        self.assertEqual(p12.build_block(registry), p12.build_block(later))

    def test_an_undated_transfer_fails_closed(self) -> None:
        registry = copy.deepcopy(p11.load_registry())
        registry["medical_evacuations"].append({"country": "ESP", "note": "test"})
        with self.assertRaises(p12.RegistrationError):
            p12.build_block(registry)

    def test_hazard_matches_the_closed_form(self) -> None:
        for pin in p12.build_block()["points"]:
            rate = pin["generator"]["per_period_probability"]
            self.assertAlmostEqual(pin["probability"], round(1 - (1 - rate) ** p12.WINDOW_PERIODS, 4),
                                   places=4, msg=pin["pin_id"])

    def test_four_questions_each_priced_by_both_methods_and_no_onward_question(self) -> None:
        by_question: dict[str, set[str]] = {}
        for pin in p12.build_block()["points"]:
            by_question.setdefault(pin["question_id"], set()).add(pin["method"])
        self.assertEqual({"block12:new-country", "block12:new-neighbour", "block12:new-non-neighbour",
                          "block12:uganda-case"}, set(by_question))
        for methods in by_question.values():
            self.assertEqual({p12.RECENT, p12.STATIONARY}, methods)

    def test_periods_cover_the_declaration_and_end_before_the_window(self) -> None:
        spans = p12.periods()
        self.assertEqual(dt.date(2026, 11, 12), spans[0][1])
        self.assertLessEqual(spans[-1][0], p12.OUTBREAK_DECLARED)
        self.assertEqual(26, len(spans))
        self.assertTrue(all((e - s).days == 6 for s, e in spans))

    def test_methods_and_rule_are_block_11s(self) -> None:
        block = p12.build_block()
        for name in ("RECENT_PERIODS", "PERIOD_DAYS", "HORIZON_DAYS", "WINDOW_PERIODS", "EVIDENCE_GRACE_DAYS"):
            self.assertEqual(getattr(p11, name), getattr(p12, name), name)
        self.assertEqual(p11.build_block()["pre_registration"]["decision_rule"],
                         block["pre_registration"]["decision_rule"])
        self.assertEqual(p11.build_block()["pre_registration"]["primary_endpoint"],
                         block["pre_registration"]["primary_endpoint"])

    def test_known_asymmetry_is_read_from_the_prices(self) -> None:
        block = p12.build_block()
        text = block["pre_registration"]["known_asymmetry"]
        prices = {}
        for pin in block["points"]:
            prices.setdefault(pin["question_id"].split(":")[1], {})[pin["method"]] = pin["probability"]
        for key, p in prices.items():
            side = "higher" if p[p12.RECENT] > p[p12.STATIONARY] else "lower"
            clause = text.split("; ")[0] if side == "higher" else text.split("; ")[1]
            self.assertIn(key, clause, key)


class RegistrationFactsTest(unittest.TestCase):
    def test_timing(self) -> None:
        p12.check_timing()
        block = _ledger_block()
        self.assertEqual("2026-11-13", block["window_opens"])
        self.assertEqual("2026-12-10T23:59:59Z", block["resolves_at"])

    def test_country_sets_are_the_registry_facts_at_the_cutoff(self) -> None:
        registry = p11.load_registry()
        p12.check_country_sets(registry)
        self.assertIn("NGA", p12.BASELINE_COUNTRIES)
        self.assertNotIn("UGA", [c for c in p12.DRC_LAND_NEIGHBOURS if c not in p12.BASELINE_COUNTRIES])
        for code in (*p12.BASELINE_COUNTRIES, *p12.EVACUATION_ONLY, *p12.DRC_LAND_NEIGHBOURS):
            self.assertIn(code, p12.COUNTRY_NAMES)

    def test_every_substrate_report_is_dated_by_the_cutoff(self) -> None:
        reports = p12.substrate_reports(p11.load_registry())
        self.assertEqual(len(p12.SUBSTRATE_REPORT_IDS), len(reports))
        self.assertTrue(all(r["reported_on"] <= p12.SOURCE_CUTOFF for r in reports))

    def test_block_11_is_unchanged(self) -> None:
        ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
        block11 = next(b for b in ledger["blocks"] if b["block_id"] == p11.BLOCK11_ID)
        self.assertEqual(block11["points"][0]["question_id"], "block11:new-country")
        self.assertNotEqual({p["pin_id"] for p in block11["points"]},
                            {p["pin_id"] for p in _ledger_block()["points"]})


def _events(extra=(), reviewed_through="2026-12-13", reviewed_at="2026-12-14"):
    events = copy.deepcopy(p11.load_registry())
    events["reports"] = p12.substrate_reports(events) + list(extra)
    events["retractions"], events["attributions"] = [], []
    events["coverage_reviews"] = [{"reviewed_at": reviewed_at, "reviewed_through": reviewed_through,
                                   "sources_checked": ["test"]}]
    return events


class ResolutionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.block = copy.deepcopy(_ledger_block())
        self.block["registration"].setdefault("registration_receipt", {"public_at_utc": "2026-11-12T12:00:00Z"})
        self.pins = {p["pin_id"].split(":")[-1]: p for p in self.block["points"]}

    def _resolve(self, key, events, as_of="2026-12-14"):
        return opsresolver.resolve_pin(self.pins[key], self.block, [], dt.date.fromisoformat(as_of), events)

    def test_pending_through_the_grace_and_unscoreable_without_review(self) -> None:
        self.assertEqual(opsresolver.STATUS_PENDING,
                         self._resolve("intl-new-country-recent13", _events(), "2026-12-13")["status"])
        self.assertEqual(opsresolver.STATUS_UNREVIEWED,
                         self._resolve("intl-new-country-recent13",
                                       _events(reviewed_through="2026-12-10"))["status"])

    def test_quiet_reviewed_window_resolves_no(self) -> None:
        for key in self.pins:
            self.assertEqual(opsresolver.STATUS_NO, self._resolve(key, _events())["status"], key)

    def test_a_nigeria_report_is_no_longer_a_new_country(self) -> None:
        events = _events([_report("nga-2", "NGA", "2026-11-20", cumulative=2)])
        for key in ("intl-new-country-recent13", "intl-new-non-neighbour-recent13"):
            self.assertEqual(opsresolver.STATUS_NO, self._resolve(key, events)["status"], key)

    def test_a_new_neighbour_resolves_the_neighbour_and_any_country_questions(self) -> None:
        events = _events([_report("ssd-1", "SSD", "2026-11-20")])
        got = {k: self._resolve(k, events)["status"] for k in self.pins}
        for key in ("intl-new-country-stationary", "intl-new-neighbour-recent13"):
            self.assertEqual(opsresolver.STATUS_YES, got[key])
        for key in ("intl-new-non-neighbour-recent13", "intl-uganda-case-stationary"):
            self.assertEqual(opsresolver.STATUS_NO, got[key])

    def test_a_report_in_the_series_gap_voids_the_questions_it_decides(self) -> None:
        events = _events([_report("rwa-1", "RWA", "2026-11-11")])
        self.assertEqual(opsresolver.STATUS_VOID, self._resolve("intl-new-neighbour-recent13", events)["status"])
        self.assertEqual(opsresolver.STATUS_NO, self._resolve("intl-uganda-case-recent13", events)["status"])

    def test_a_late_receipt_is_void(self) -> None:
        self.block["registration"]["registration_receipt"] = {"public_at_utc": "2026-11-13T00:00:01Z"}
        self.assertEqual(opsresolver.STATUS_VOID, self._resolve("intl-uganda-case-recent13", _events())["status"])


if __name__ == "__main__":
    unittest.main()
