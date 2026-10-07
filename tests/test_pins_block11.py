# SPDX-License-Identifier: Apache-2.0
"""Block 11 (international spread): generation, registration facts and resolution."""
from __future__ import annotations

import copy
import datetime as dt
import json
import unittest

from lovs.forecast import opsresolver, pins_block11 as p11
from lovs.forecast import public_register

LEDGER = p11.LEDGER


def _ledger_block() -> dict:
    ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
    return next(b for b in ledger["blocks"] if b["block_id"] == p11.BLOCK11_ID)


class GenerationTest(unittest.TestCase):
    def test_committed_block_regenerates_exactly(self) -> None:
        # Every probability, rule, window and method is generated; a hand edit fails here.
        self.assertEqual(_ledger_block(), p11.build_block())

    def test_generation_is_deterministic(self) -> None:
        self.assertEqual(json.dumps(p11.build_block()), json.dumps(p11.build_block()))

    def test_substrate_hash_is_pinned_and_fails_closed(self) -> None:
        registry, reference = p11.load_registry(), p11.load_reference()
        self.assertEqual(p11.SUBSTRATE_SHA256, p11.substrate_digest(registry, reference))
        tampered = copy.deepcopy(registry)
        tampered["reports"][0]["cumulative_confirmed"] += 1
        tampered["reports"][0]["new_confirmed"] += 1
        with self.assertRaises(p11.RegistrationError):
            p11.build_block(tampered, reference)

    def test_reports_after_the_cutoff_do_not_move_the_prices(self) -> None:
        registry, reference = p11.load_registry(), p11.load_reference()
        later = copy.deepcopy(registry)
        later["reports"].append({
            "report_id": "test-later", "country": "RWA", "reported_on": "2026-10-20",
            "confirmed_on": None, "cumulative_confirmed": 1, "new_confirmed": 1,
            "sources": [{"publisher": "test"}]})
        self.assertEqual(p11.build_block(registry, reference), p11.build_block(later, reference))

    def test_hazard_matches_the_closed_form(self) -> None:
        block = p11.build_block()
        for pin in block["points"]:
            gen = pin["generator"]
            if pin["method"] == p11.REFERENCE_METHOD:
                expected = (gen["with_local_transmission"] + 0.5) / (gen["episodes"] + 1)
            else:
                rate = gen["per_period_probability"]
                expected = 1 - (1 - rate) ** p11.WINDOW_PERIODS
            self.assertAlmostEqual(pin["probability"], round(expected, 4), places=4, msg=pin["pin_id"])

    def test_each_hazard_question_is_priced_by_both_methods(self) -> None:
        by_question: dict[str, set[str]] = {}
        for pin in p11.build_block()["points"]:
            by_question.setdefault(pin["question_id"], set()).add(pin["method"])
        for qid, methods in by_question.items():
            if qid == "block11:kenya-further-case":
                self.assertEqual({p11.REFERENCE_METHOD}, methods)
            else:
                self.assertEqual({p11.RECENT, p11.STATIONARY}, methods, qid)

    def test_periods_cover_the_declaration_and_end_before_the_window(self) -> None:
        spans = p11.periods()
        self.assertEqual(dt.date(2026, 10, 7), spans[0][1])
        self.assertLessEqual(spans[-1][0], p11.OUTBREAK_DECLARED)
        self.assertTrue(all((e - s).days == 6 for s, e in spans))
        self.assertTrue(all(a[0] - b[1] == dt.timedelta(days=1) for a, b in zip(spans, spans[1:])))


class RegistrationFactsTest(unittest.TestCase):
    def test_window_opens_the_day_after_the_deadline(self) -> None:
        p11.check_timing()
        block = _ledger_block()
        self.assertEqual(p11.WINDOW_OPENS, block["window_opens"])
        self.assertEqual(p11.REGISTRATION_DEADLINE_UTC, block["registration"]["registration_deadline_utc"])

    def test_a_receipt_after_the_deadline_is_refused(self) -> None:
        receipt = _ledger_block()["registration"].get("registration_receipt")
        if receipt is None:
            self.skipTest("receipt is appended after the registration push")
        deadline = dt.datetime.fromisoformat(p11.REGISTRATION_DEADLINE_UTC.replace("Z", "+00:00"))
        public = dt.datetime.fromisoformat(receipt["public_at_utc"].replace("Z", "+00:00"))
        self.assertLessEqual(public, deadline)

    def test_registry_refuses_a_falling_count_or_an_uncited_report(self) -> None:
        registry = p11.load_registry()
        broken = copy.deepcopy(registry)
        broken["reports"][1]["sources"] = []
        with self.assertRaises(p11.RegistrationError):
            p11.validate_registry(broken)
        uga = [r for r in registry["reports"] if r["country"] == "UGA"]
        falling = copy.deepcopy(registry)
        target = next(r for r in falling["reports"] if r["report_id"] == uga[-1]["report_id"])
        target["cumulative_confirmed"] = 1
        with self.assertRaises(p11.RegistrationError):
            p11.validate_registry(falling)


def _events(extra_reports=(), reviewed_through="2026-11-04", reviewed_at="2026-11-07"):
    events = copy.deepcopy(p11.load_registry())
    events["reports"].extend(extra_reports)
    events["coverage_reviews"] = [{"reviewed_at": reviewed_at, "reviewed_through": reviewed_through,
                                   "sources_checked": ["test"]}]
    return events


def _report(rid, country, reported, confirmed=None, cumulative=1, new=1):
    return {"report_id": rid, "country": country, "reported_on": reported, "confirmed_on": confirmed,
            "cumulative_confirmed": cumulative, "new_confirmed": new, "sources": [{"publisher": "test"}]}


class ResolutionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.block = _ledger_block()
        self.pins = {p["pin_id"].split(":")[-1]: p for p in self.block["points"]}

    def _resolve(self, key, events, as_of="2026-11-08"):
        return opsresolver.resolve_pin(self.pins[key], self.block, [], dt.date.fromisoformat(as_of), events)

    def test_pending_through_the_evidence_grace(self) -> None:
        self.assertEqual(opsresolver.STATUS_PENDING,
                         self._resolve("intl-new-country-recent13", _events(), "2026-11-07")["status"])

    def test_no_coverage_review_means_unscoreable_not_no(self) -> None:
        events = _events(reviewed_through="2026-10-30")
        self.assertEqual(opsresolver.STATUS_UNREVIEWED,
                         self._resolve("intl-new-country-recent13", events)["status"])

    def test_quiet_reviewed_window_resolves_no(self) -> None:
        for key in self.pins:
            self.assertEqual(opsresolver.STATUS_NO, self._resolve(key, _events())["status"], key)

    def test_new_neighbour_resolves_the_neighbour_and_any_country_questions_only(self) -> None:
        events = _events([_report("rwa-1", "RWA", "2026-10-20")])
        got = {k: self._resolve(k, events)["status"] for k in self.pins}
        for key in ("intl-new-country-recent13", "intl-new-neighbour-stationary"):
            self.assertEqual(opsresolver.STATUS_YES, got[key])
        for key in ("intl-new-non-neighbour-recent13", "intl-uganda-case-recent13", "intl-kenya-further-case"):
            self.assertEqual(opsresolver.STATUS_NO, got[key])

    def test_a_further_kenya_case_resolves_kenya_but_not_new_country(self) -> None:
        events = _events([_report("ken-2", "KEN", "2026-10-15", cumulative=2, new=1)])
        self.assertEqual(opsresolver.STATUS_YES, self._resolve("intl-kenya-further-case", events)["status"])
        self.assertEqual(opsresolver.STATUS_NO, self._resolve("intl-new-country-recent13", events)["status"])

    def test_late_report_counts_only_with_an_in_window_confirmation(self) -> None:
        late_in = _events([_report("uga-late", "UGA", "2026-11-06", "2026-11-03", cumulative=21)])
        late_out = _events([_report("uga-late", "UGA", "2026-11-06", "2026-11-05", cumulative=21)])
        self.assertEqual(opsresolver.STATUS_YES, self._resolve("intl-uganda-case-recent13", late_in)["status"])
        self.assertEqual(opsresolver.STATUS_NO, self._resolve("intl-uganda-case-recent13", late_out)["status"])

    def test_a_report_decided_before_the_window_voids_the_question(self) -> None:
        before = _events([_report("ken-2", "KEN", "2026-10-07", cumulative=2)])
        # Reported on the cutoff day: part of the substrate, so not void, and before the window: NO.
        self.assertEqual(opsresolver.STATUS_NO, self._resolve("intl-kenya-further-case", before)["status"])
        early = _events([_report("ken-2", "KEN", "2026-10-09", "2026-10-07", cumulative=2)])
        self.assertEqual(opsresolver.STATUS_VOID, self._resolve("intl-kenya-further-case", early)["status"])

    def test_existing_blocks_are_untouched_by_the_event_path(self) -> None:
        ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
        others = [b for b in ledger["blocks"] if b["block_id"] != p11.BLOCK11_ID]
        self.assertTrue(all(p.get("shape") != "event" for b in others for p in b["points"]))


class PublicRowsTest(unittest.TestCase):
    def test_rows_carry_no_probability_and_match_the_record(self) -> None:
        rows = public_register.international_block_rows()
        self.assertEqual(len(_ledger_block()["points"]), len(rows))
        record = json.loads((p11.REPO / "data" / "public_calibration_commitments.json").read_text())
        committed = {r["ledger_id"]: r for r in record["commitments"]}
        for row in rows:
            self.assertEqual(committed[row["ledger_id"]], row)
            self.assertNotIn("probability", json.dumps(row))
        self.assertEqual("bdbv-2026-cal-225", rows[0]["ledger_id"])

    def test_every_row_has_an_axis_and_a_side(self) -> None:
        from lovs.forecast.registered_side import registered_side

        axes = public_register.axis_by_pin()
        for row in public_register.international_block_rows():
            self.assertEqual("international", axes[row["pin_id"]])
            self.assertIn(registered_side(row), ("yes", "no", "none"))


if __name__ == "__main__":
    unittest.main()
