"""Deadline gate: the resolution-evidence feed must stay ahead of what is due.

Two of the three 2026 calibration stalls were input starvation, not design
failure. data/calibration-resolution-evidence.json stopped being fed on
2026-06-20; Blocks 3 and 4 then sat unresolved past their dates for months
while every other test stayed green, because nothing in the suite asserted
that the feed still covered the commitments the ledger had made.

This is that assertion. It starts failing once a pinned point falls due
without evidence coverage, and keeps failing until the feed is refreshed. A
failure here is a DATA alarm, not a code regression: the fix is to feed
data/calibration-resolution-evidence.json, not to change this file.

"Due" is keyed on data availability, not on the calendar. Reaching a block's
resolution date does not mean data covering that date exists: sources publish
data for day D some days after D, and we receive it after that. A block falls
due once a reviewed SitRep promotion covers its resolution date, or once the
declared publication-lag ceiling has passed without one (the promotion
pipeline itself has stalled, which is the alarm this gate exists for).
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from lovs import sitrep_promotions  # noqa: E402
from lovs.forecast import opsforecast as of  # noqa: E402

LEDGER = REPO / "data" / "calibration-ledger.json"
OPS_LEDGER = REPO / "data" / "operational-calibration-ledger.json"
EVIDENCE = REPO / "data" / "calibration-resolution-evidence.json"
# Dated extracts; the 2026-09-01 one is the frozen substrate the operational
# pins were generated from, and later coverage arrives as a NEW dated file.
OPS_SERIES_GLOB = "operational-series-*.json"

# Across the 119 reviewed promotions through SitRep 138, published_at trails
# data_as_of by 1 to 4 days. Past this ceiling, the absence of any reviewed
# promotion covering a resolution date is itself the stall to report.
PUBLICATION_LAG_CEILING = dt.timedelta(days=7)

COVERAGE_RULE = (
    "Set _meta.as_of to the DATA COVERAGE date: the latest day the cited sources\n"
    "cover through. Never the retrieval date. A retrieval date claims coverage the\n"
    "feed does not have, defeats the resolver's stale-feed guard, and scores false\n"
    "NOs on data that ends before the window closed.\n"
)


def _date(value: str) -> dt.date:
    return dt.date.fromisoformat(value[:10])


def _released_data_days() -> list[dt.date]:
    """Sorted data days of the reviewed SitRep promotions, via the canonical loader.

    The loader refuses an unreviewed or model-unready payload outright, so a
    candidate can never pull the trigger.
    """
    return sorted({_date(row["data_as_of"]) for row in sitrep_promotions.load_reviewed_promotions()})


def _series_covers_through(rows: list[dict], released: list[dt.date]) -> dt.date | None:
    """The last data day through which an operational extract is complete.

    An extract covers a day only if it carries a row for every released SitRep
    data day up to it; a row dated past the newest released SitRep is not data.
    So a partial extract, or one padded with a stray or mistyped row, cannot
    claim coverage it does not have.
    """
    have = {_date(r["data_as_of"]) for r in rows if r.get("data_as_of")}
    gaps = [day for day in released if day not in have]
    limit = gaps[0] - dt.timedelta(days=1) if gaps else released[-1]
    return max((day for day in have if day <= limit), default=None)


def _why_due(resolves: dt.date, newest_data: dt.date, today: dt.date) -> str | None:
    """Why a resolution date has fallen due, or None while it has not.

    The window includes the resolution day, and data covering it is published
    days later. So the date falls due when released data reaches it, or when the
    publication-lag ceiling passes without any; the calendar alone never does it.
    """
    if newest_data >= resolves:
        return f"reviewed SitRep data covers through {newest_data}"
    if today > resolves + PUBLICATION_LAG_CEILING:
        return (f"no reviewed SitRep covers {resolves} and the "
                f"{PUBLICATION_LAG_CEILING.days}-day publication-lag ceiling has passed")
    return None


def _due_unresolved(ledger: dict, label: str, newest_data: dt.date,
                    today: dt.date) -> list[tuple[str, dt.date, str]]:
    """(point label, resolution date, why due) for every open point that has fallen due."""
    due = []
    for block in ledger["blocks"]:
        if block.get("status") != "active":
            continue
        resolves = _date(block["resolves_at"])
        why = _why_due(resolves, newest_data, today)
        if why is not None:
            due += [(p[label], resolves, why) for p in block["points"] if "outcome" not in p]
    return due


class FeedLivenessGate(unittest.TestCase):
    def setUp(self):
        self.ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
        self.evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
        self.feed_as_of = _date(self.evidence["_meta"]["as_of"])
        self.newest_data = _released_data_days()[-1]
        self.today = dt.date.today()

    def test_feed_covers_every_point_that_has_fallen_due(self):
        due = _due_unresolved(self.ledger, "corridor", self.newest_data, self.today)
        if not due:
            return
        latest = max(r for _, r, _ in due)
        self.assertGreaterEqual(
            self.feed_as_of,
            latest,
            "\n\nRESOLUTION-EVIDENCE FEED IS STALE.\n"
            f"  feed as_of (coverage): {self.feed_as_of}\n"
            f"  latest due date:       {latest}\n"
            f"  points due, unresolved: {len(due)}\n"
            + "".join(f"    {c}  (resolves {r}; due: {w})\n"
                      for c, r, w in sorted(due, key=lambda x: x[1]))
            + "\nThe resolver will return unscoreable_stale_feed for these rather than\n"
            "inventing false negatives, so nothing is silently mis-scored -- but the\n"
            "window is lost until the feed is refreshed. Feed\n"
            "data/calibration-resolution-evidence.json for the targets above.\n"
            + COVERAGE_RULE
            + "Do not edit this test to make it pass.",
        )

    def test_feed_is_not_drifting_far_behind_an_open_block(self):
        """Warn well before the deadline, not on it.

        An open block whose resolution date is inside two weeks and whose feed
        has not been touched since before the block was pinned is the shape the
        June stall had. Catching it here leaves time to gather public evidence,
        which for six cross-border targets is a day of work, not an afternoon.
        """
        soon = self.today + dt.timedelta(days=14)
        at_risk = []
        for block in self.ledger["blocks"]:
            if block.get("status") != "active":
                continue
            resolves = _date(block["resolves_at"])
            pinned = _date(block["pinned_at"])
            open_points = [p for p in block["points"] if "outcome" not in p]
            if open_points and self.today <= resolves <= soon and self.feed_as_of < pinned:
                at_risk.append((block["block_id"], resolves, len(open_points)))
        self.assertEqual(
            at_risk,
            [],
            "\n\nFEED HAS NOT BEEN TOUCHED SINCE THESE BLOCKS WERE PINNED, AND THEY\n"
            "RESOLVE WITHIN 14 DAYS:\n"
            + "".join(f"    {b}  resolves {r}  ({n} open points)\n" for b, r, n in at_risk)
            + f"  feed as_of: {self.feed_as_of}\n"
            "\nStart gathering resolution evidence now and refresh the feed.\n"
            + COVERAGE_RULE
            + "Do not edit this test to make it pass.",
        )

    def test_every_open_target_has_a_reachable_evidence_shape(self):
        """A target with no entry at all fails loudly; verify that stays true."""
        entries = {e["target_zone"] for e in self.evidence["evidence"]}
        open_targets = {
            p["target"]
            for b in self.ledger["blocks"]
            if b.get("status") == "active"
            for p in b["points"]
            if "outcome" not in p
        }
        missing = sorted(open_targets - entries)
        self.assertEqual(
            missing,
            [],
            f"\n\nOpen calibration points target zones with no evidence entry: {missing}\n"
            "These score unscoreable_no_feed (loudly, by design). Add an entry per\n"
            "target before the resolution date.",
        )


class OperationalSeriesLivenessGate(unittest.TestCase):
    """The same deadline gate, for the operational ledger's series.

    The operational resolver refuses to score NO from a series that stops short
    of the resolution date, so a stale series cannot invent false negatives.
    But it still loses the window. This fails first.
    """

    def setUp(self):
        self.ledger = json.loads(OPS_LEDGER.read_text(encoding="utf-8"))
        released = _released_data_days()
        # The best complete dated extract counts, so a new extract is seen
        # without touching the frozen one.
        self.series_as_of = max(
            filter(None, (_series_covers_through(of.load_rows(path), released)
                          for path in (REPO / "data").glob(OPS_SERIES_GLOB))),
            default=None,
        )
        self.assertIsNotNone(self.series_as_of, "no operational extract is complete for any day")
        self.newest_data = released[-1]
        self.today = dt.date.today()

    def test_series_covers_every_operational_pin_that_has_fallen_due(self):
        due = _due_unresolved(self.ledger, "pin_id", self.newest_data, self.today)
        if not due:
            return
        latest, why = max((r, w) for _, r, w in due)
        self.assertGreaterEqual(
            self.series_as_of,
            latest,
            "\n\nOPERATIONAL SERIES IS STALE.\n"
            f"  series covers through: {self.series_as_of}\n"
            f"  latest due date:       {latest} (due: {why})\n"
            f"  pins due, unresolved:  {len(due)}\n"
            "\nRe-extract a NEW dated data/operational-series-<date>.json from the SitRep\n"
            "packets in projects/lovs-evidence-mcp/data/sitrep-packets/ (never edit the\n"
            "frozen 2026-09-01 extract), then re-run lovs/forecast/opsresolver.py. A\n"
            "series covers through its newest row's data_as_of, never the extraction\n"
            "date, and only through the last day with no released SitRep missing.\n"
            "Do not edit this test to make it pass.",
        )


class DueIsKeyedOnDataAvailability(unittest.TestCase):
    """The due rule itself, pinned on the 2026-10-01 resolution of Blocks 5 to 7."""

    RESOLVES = dt.date(2026, 10, 1)
    LATEST = dt.date(2026, 9, 29)  # SitRep 138, published 2026-10-01

    def test_reaching_the_resolution_date_is_not_enough(self):
        for today in (dt.date(2026, 10, 1), dt.date(2026, 10, 2), dt.date(2026, 10, 8)):
            self.assertIsNone(_why_due(self.RESOLVES, self.LATEST, today), today)

    def test_released_data_covering_the_resolution_date_makes_it_due(self):
        for newest in (dt.date(2026, 10, 1), dt.date(2026, 10, 3)):
            self.assertIsNotNone(_why_due(self.RESOLVES, newest, dt.date(2026, 10, 3)))

    def test_a_stalled_promotion_pipeline_still_raises_the_alarm(self):
        self.assertIsNotNone(_why_due(self.RESOLVES, self.LATEST, dt.date(2026, 10, 9)))

    def _ledger(self, label):
        return {"blocks": [{"block_id": "b", "status": "active",
                            "pinned_at": "2026-09-01", "resolves_at": "2026-10-01T23:59:59Z",
                            "points": [{label: "p"}]}]}

    def _run(self, gate_cls, method, label, newest, **coverage):
        gate = gate_cls(method)
        gate.ledger = self._ledger(label)
        gate.newest_data, gate.today = newest, dt.date(2026, 10, 3)
        for name, value in coverage.items():
            setattr(gate, name, value)
        getattr(gate, method)()

    def test_both_gates_turn_red_only_when_released_data_covers_the_date(self):
        cases = (
            (FeedLivenessGate, "test_feed_covers_every_point_that_has_fallen_due",
             "corridor", {"feed_as_of": dt.date(2026, 9, 15)}, "DATA COVERAGE"),
            (OperationalSeriesLivenessGate,
             "test_series_covers_every_operational_pin_that_has_fallen_due",
             "pin_id", {"series_as_of": dt.date(2026, 8, 28)}, "never the extraction"),
        )
        for gate_cls, method, label, stale, says in cases:
            self._run(gate_cls, method, label, self.LATEST, **stale)
            with self.assertRaises(AssertionError) as caught:
                self._run(gate_cls, method, label, self.RESOLVES, **stale)
            self.assertIn(says, str(caught.exception))
            fresh = {name: self.RESOLVES for name in stale}
            self._run(gate_cls, method, label, self.RESOLVES, **fresh)

    def test_an_extract_covers_only_through_its_first_missing_sitrep(self):
        released = [dt.date(2026, 9, d) for d in (27, 28, 29)] + [self.RESOLVES]

        def rows(*days):
            return [{"data_as_of": f"2026-{m:02d}-{d:02d}"} for m, d in days]

        complete = rows((9, 27), (9, 28), (9, 29), (10, 1))
        self.assertEqual(_series_covers_through(complete, released), self.RESOLVES)
        # A gap stops coverage the day before the missing SitRep.
        self.assertEqual(_series_covers_through(rows((9, 27), (9, 29), (10, 1)), released),
                         dt.date(2026, 9, 27))
        # A partial extract that skips the early SitReps covers nothing.
        self.assertIsNone(_series_covers_through(rows((9, 29), (10, 1)), released))
        # Rows past the newest released SitRep (a stray or mistyped date) are not data.
        padded = rows((9, 27), (9, 28), (9, 29)) + [{"data_as_of": "2027-10-01"}]
        self.assertEqual(_series_covers_through(padded, released[:3]), self.LATEST)

if __name__ == "__main__":
    unittest.main()
