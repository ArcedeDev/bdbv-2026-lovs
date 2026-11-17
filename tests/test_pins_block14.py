# SPDX-License-Identifier: Apache-2.0
"""Blocks 14 and 15: Block 9's comparison and Block 10's questions, re-priced through SitRep 185."""
from __future__ import annotations

import copy
import datetime as dt
import json
import unittest

from lovs import forecast_scoring as fs
from lovs.forecast import opsforecast as of
from lovs.forecast import opsresolver, pins_block7 as p7, pins_block8 as p8, pins_block14 as p14

APPENDED_AT_RESOLUTION = ("outcome", "resolved_as_of", "outcome_series", "resolution_provenance")


def _ledger() -> dict:
    return json.loads(p14.LEDGER.read_text(encoding="utf-8"))


def _ledger_blocks() -> list[dict]:
    blocks = {b["block_id"]: b for b in _ledger()["blocks"]}
    return [blocks[block_id] for block_id in p14.BLOCK_IDS]


def _strip(block: dict) -> dict:
    block = copy.deepcopy(block)
    block["registration"].pop("registration_receipt", None)
    for pin in block["points"]:
        for key in APPENDED_AT_RESOLUTION:
            pin.pop(key, None)
    return block


class GenerationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.blocks = p14.build_blocks()
        cls.b14, cls.b15 = cls.blocks

    def test_committed_blocks_regenerate_exactly(self) -> None:
        self.assertEqual([_strip(b) for b in _ledger_blocks()], self.blocks)

    def test_blocks_follow_block_13_with_their_labels_and_generator(self) -> None:
        ledger = _ledger()
        labels = [b.get("label") for b in ledger["blocks"]]
        at = labels.index("Block 14")
        self.assertEqual(labels[at - 1:at + 2], ["Block 13", "Block 14", "Block 15"])
        for block_id in p14.BLOCK_IDS:
            self.assertEqual(ledger["_meta"]["generators"][block_id], "lovs/forecast/pins_block14.py")

    def test_substrate_is_hash_pinned_and_cut_off(self) -> None:
        rows = p14.load_substrate()
        late = copy.deepcopy(rows) + [dict(rows[-1], data_as_of="2026-11-16", published_at="2026-11-18")]
        with self.assertRaises(p14.RegistrationError):
            p14.build_blocks(late)

    def test_substrate_keeps_every_block_13_row_and_never_fills_sitrep_172(self) -> None:
        earlier = json.loads(p14.PREVIOUS_SUBSTRATE.read_text(encoding="utf-8"))["rows"]
        rows = p14.load_substrate()
        self.assertEqual(earlier, rows[:len(earlier)])
        self.assertEqual(["178", "179", "180", "181", "182", "183", "184", "185"],
                         [r["sitrep"] for r in rows[len(earlier):]])
        self.assertNotIn("2026-11-02", {r["data_as_of"] for r in rows})
        self.assertNotIn("172", {r["sitrep"] for r in rows})

    def test_timing_and_horizon(self) -> None:
        self.assertEqual(31, (p14.WINDOW_END - dt.date.fromisoformat(p14.SOURCE_CUTOFF["data_as_of"])).days)
        self.assertEqual(p14.HORIZON_DAYS, 31)
        for block in self.blocks:
            self.assertEqual(("2026-11-18", "2026-11-19", "2026-12-16T23:59:59Z"),
                             (block["pinned_at"], block["window_opens"], block["resolves_at"]))
            self.assertEqual("2026-11-18T23:59:59Z", block["registration"]["registration_deadline_utc"])

    def test_methods_and_question_shape_are_block_9s(self) -> None:
        for name in ("HORIZON_DAYS", "LEVELS", "PATHS", "BLOCK", "LEVELS_DAYS", "FEED_REFERENCE_DAYS",
                     "LOW_COUNT_RUNGS"):
            self.assertEqual(getattr(p8, name), getattr(p14, name), name)
        kept = [m for m in p8.BLOCK9_METRICS if m.key not in ("isolation", "samples")]
        self.assertEqual([(m.key, m.series, m.step, m.kind, m.derived) for m in kept],
                         [(m.key, m.series, m.step, m.kind, m.derived) for m in p14.BLOCK14_METRICS])
        for m in p14.BLOCK14_METRICS:
            self.assertIn("on or before 2026-12-16", m.question)
            self.assertNotIn("2026-11-01", m.question)

    def test_questions_whose_basis_changed_name_it(self) -> None:
        by_key = {m.key: m.question for m in p14.BLOCK14_METRICS}
        for key in ("positivity", "nkisolation"):
            self.assertIn("reviewed record of that SitRep (data/sitrep_promotions)", by_key[key])
            self.assertIn("withholds as a source conflict does not count", by_key[key])
            self.assertNotIn("SitRep cover", by_key[key])
        self.assertIn("follow-up re-samples", by_key["positivity"])
        self.assertIn("provinces that report that day", by_key["alerts"])
        self.assertIn("the total counts", by_key["nkisolation"])
        self.assertIn("outside CTE/CT", by_key["nkisolation"])
        self.assertIn("UTC date it was first posted on insp.cd", by_key["lag"])
        self.assertNotIn("{t} days?", by_key["lag"])
        for pin in self.b14["points"]:
            key = pin["question_id"].split(":")[1].split("-le-")[0]
            if key in p14.BASIS_NOTES:
                self.assertIn(p14.BASIS_NOTES[key], pin["rationale"])

    def test_the_resolver_reads_what_the_questions_name(self) -> None:
        # Nord-Kivu: the total the reviewed record carries (SitRep 185: 514, not 343 in CTE/CT).
        # Lag: data day to the UTC date of the first insp.cd post, not the PDF's own date.
        rows = p14.load_substrate()
        nk = {o.date: o.value for o in p7.province_isolation_series(rows, "Nord-Kivu")}
        lag = {o.date: o.value for o in p7.publication_lag_series(rows)}
        self.assertEqual(514, nk[dt.date(2026, 11, 15)])
        checked = 0
        for path in sorted((p14.REPO / "data" / "sitrep_promotions").glob("sitrep-1[5-8]*.json")):
            record = json.loads(path.read_text(encoding="utf-8"))
            day = dt.date.fromisoformat(record["data_as_of"][:10])
            table = record["figures"]["operational_tables"]["patient_movement_by_province"]
            total = next(r["patients_in_isolation_end_day"] for r in table if r["province"] == "Nord-Kivu")
            if total is not None:
                self.assertEqual(total, nk[day])
            posted_at = record["source_receipt"].get("wordpress_published_at")
            if posted_at is not None:  # SitRep 150's receipt predates the field
                self.assertEqual(posted_at, record["published_at"])
                checked += 1
            posted = dt.date.fromisoformat(record["published_at"][:10])
            self.assertEqual((posted - day).days, lag[day])
        self.assertEqual(34, checked)

    def test_national_isolation_is_dropped_because_the_source_withholds_it(self) -> None:
        rows = p14.load_substrate()
        obs = of.series(rows, "hospital_isolation_total")
        self.assertEqual(dt.date(2026, 11, 8), obs[-1].date)
        self.assertEqual({"hospital_isolation_total", "samples_analyzed"},
                         {d["metric"] for d in self.b14["pre_registration"]["dropped_metrics"]})
        self.assertNotIn("hospital_isolation_total", {p["metric"] for p in self.b14["points"]})

    def test_samples_analysed_is_left_out(self) -> None:
        self.assertNotIn("samples", {m.key for m in p14.BLOCK14_METRICS})
        self.assertNotIn("samples_analyzed", {p["metric"] for p in self.b14["points"]})
        self.assertNotIn("samples_analyzed", self.b14["pre_registration"]["basis_notes"])
        self.assertEqual(66, len(self.b14["points"]))
        self.assertEqual(7, len({p["metric"] for p in self.b14["points"]}))

    def test_roles_are_block_9s(self) -> None:
        self.assertEqual({(p8.INCUMBENT, "incumbent"), (p8.RECENT_LEVELS, "challenger")},
                         {(p["method"], p["role"]) for p in self.b14["points"]})
        self.assertEqual({("termination_bootstrap", "record"), ("gap_bootstrap", "record")},
                         {(p["method"], p["role"]) for p in self.b15["points"]})

    def test_each_question_is_paired_at_one_threshold(self) -> None:
        by_q: dict[str, list[dict]] = {}
        for pin in self.b14["points"]:
            by_q.setdefault(pin["question_id"], []).append(pin)
        for qid, pins in by_q.items():
            self.assertEqual([p8.INCUMBENT, p8.RECENT_LEVELS], sorted(p["method"] for p in pins), qid)
            self.assertEqual(1, len({p["threshold"] for p in pins}), qid)
        self.assertEqual(len(p14.BLOCK14_METRICS), len({p["metric"] for p in self.b14["points"]}))

    def test_prices_rise_with_the_threshold(self) -> None:
        for method in (p8.INCUMBENT, p8.RECENT_LEVELS):
            for metric in {p["metric"] for p in self.b14["points"]}:
                ladder = sorted((p["threshold"], p["probability"]) for p in self.b14["points"]
                                if p["method"] == method and p["metric"] == metric)
                self.assertEqual([x[1] for x in ladder], sorted(x[1] for x in ladder), (method, metric))
        rungs = sorted((p["threshold"], p["probability"]) for p in self.b15["points"]
                       if p["method"] == "termination_bootstrap")
        self.assertEqual(list(p8.LOW_COUNT_RUNGS), [r[0] for r in rungs])
        self.assertEqual([r[1] for r in rungs], sorted(r[1] for r in rungs))

    def test_every_series_is_priced_from_the_cutoff_day(self) -> None:
        for pin in self.b14["points"] + self.b15["points"]:
            self.assertEqual("2026-11-15", pin["last_observed_date"], pin["pin_id"])

    def test_seeds_are_new_and_pin_ids_unique_across_the_ledger(self) -> None:
        earlier = {pin["generator"].get("seed") for b in _ledger()["blocks"] if b["block_id"] not in p14.BLOCK_IDS
                   for pin in b["points"]}
        seeds = {pin["generator"]["seed"] for b in self.blocks for pin in b["points"]}
        self.assertFalse(seeds & earlier)
        self.assertFalse(seeds & p8.RESERVED_SEEDS)
        self.assertEqual(2 * len(p14.BLOCK14_METRICS) + 2, len(seeds))
        ids = [pin["pin_id"] for b in _ledger()["blocks"] for pin in b["points"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_block_15_is_block_10_unchanged_but_for_its_window(self) -> None:
        block10 = next(b for b in _ledger()["blocks"] if b["block_id"] == p8.BLOCK10_ID)
        self.assertEqual(len(block10["points"]), len(self.b15["points"]))
        for old, new in zip(block10["points"], self.b15["points"]):
            self.assertEqual((old["method"], old["role"], old["metric"], old["shape"], old["threshold"]),
                             (new["method"], new["role"], new["metric"], new["shape"], new["threshold"]))
            self.assertEqual(old["question_id"].split(":")[1].split("-ge-")[0],
                             new["question_id"].split(":")[1].split("-ge-")[0])
            self.assertEqual(old["generator"]["method"].split(" over ")[0], new["generator"]["method"].split(" over ")[0])
        arrivals = next(p for p in self.b15["points"] if p["resolution_rule"]["rule"] == "arrivals_at_least")
        self.assertEqual(27, arrivals["resolution_rule"]["count"])  # 28 window days, 2026-11-19 to 2026-12-16

    def test_decision_rule_is_block_9s_on_seven_metrics(self) -> None:
        rule = self.b14["pre_registration"]["decision_rule"]
        self.assertIn("paired_block_decision(need=6, of_metrics=9)", rule)
        self.assertIn("5 of 7", rule)
        self.assertIn("ceil(6 x 7 / 9) = 5", rule)
        # Block 9's scaling: 5 of 7 when all are scoreable, 4 of 6, inconclusive below 6.
        self.assertEqual(4, fs.paired_block_decision({str(i): -0.1 for i in range(6)}, need=6,
                                                     of_metrics=9)["required_wins"])
        self.assertEqual(5, fs.paired_block_decision({str(i): -0.1 for i in range(7)}, need=6,
                                                     of_metrics=9)["required_wins"])
        self.assertEqual("inconclusive", fs.paired_block_decision({str(i): -0.1 for i in range(5)}, need=6,
                                                                  of_metrics=9)["outcome"])
        # Passing the incumbent first makes the difference challenger minus incumbent, Block 9's sign.
        self.assertLess(fs.paired_metric_differences([("m", 0.8, 0.2, 0)])["m"], 0)


class ResolutionTest(unittest.TestCase):
    def test_pending_then_resolves_from_a_covering_series(self) -> None:
        block = _ledger_blocks()[0]
        pin = next(p for p in block["points"] if p["metric"] == "contact_followup_percent")
        rows = p14.load_substrate()
        self.assertEqual(opsresolver.STATUS_PENDING,
                         opsresolver.resolve_pin(pin, block, rows, dt.date(2026, 12, 16))["status"])
        self.assertEqual(opsresolver.STATUS_STALE,
                         opsresolver.resolve_pin(pin, block, rows, dt.date(2026, 12, 17))["status"])
        last = dict(rows[-1], data_as_of="2026-12-16", published_at="2026-12-18", sitrep="216",
                    contact_followup_percent=pin["threshold"])
        got = opsresolver.resolve_pin(pin, block, rows + [last], dt.date(2026, 12, 19))
        self.assertEqual(opsresolver.STATUS_YES, got["status"])

    def test_no_data_day_before_the_window_decides_a_question(self) -> None:
        # SitReps 186 to 188 (data 2026-11-16 to 2026-11-18) fall before the window opens.
        b14, b15 = _ledger_blocks()
        rows = p14.load_substrate()
        for block in (b14, b15):
            self.assertEqual([], p8.questions_decided_before(block, rows, dt.date(2026, 11, 18)))
        pre = [dict(rows[-1], data_as_of=f"2026-11-{d}", published_at=f"2026-11-{d + 2}", sitrep=str(170 + d),
                    new_confirmed_today=0) for d in (16, 17, 18)]
        tail = [dict(rows[-1], data_as_of=(dt.date(2026, 11, 19) + dt.timedelta(days=i)).isoformat(),
                     published_at="2026-12-18", sitrep=str(300 + i), new_confirmed_today=50)
                for i in range(28)]
        zero = next(p for p in b15["points"] if p["question_id"] == "block15:newcases-any-le-0")
        got = opsresolver.resolve_pin(zero, b15, rows + pre + tail, dt.date(2026, 12, 19))
        self.assertEqual(opsresolver.STATUS_NO, got["status"])


if __name__ == "__main__":
    unittest.main()
